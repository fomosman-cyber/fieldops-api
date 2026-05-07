"""Asset Management — CRUD + hierarchie + bulk-import.

Assets zijn de fysieke objecten in de infrastructuur (lantaarnpaal, put,
wegvak, brug, sluis, ...). Meldingen worden hieraan gekoppeld via
`Melding.asset_id`.

Hierarchie: een asset kan een `parent_asset_id` hebben (wegvak → put → deksel).
De `/tree` endpoint geeft per project de boom-structuur.
"""

import csv
import io
import json
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from database import get_db
from models import Asset, Melding, User, Project
from schemas import AssetCreate, AssetUpdate, AssetResponse
from auth import get_current_user
from permissions import can_manage_assets, require_org_admin
from audit import log_action, ACTION

router = APIRouter(prefix="/api/assets", tags=["Assets"])


# ─────────────────────────────────────────────────────────────────────────────
# Serialisatie
# ─────────────────────────────────────────────────────────────────────────────

def _properties_in(props: Optional[dict]) -> Optional[str]:
    """dict → JSON-string voor opslag."""
    if props is None:
        return None
    try:
        return json.dumps(props, ensure_ascii=False)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="properties moet een serialiseerbaar dict zijn")


def _properties_out(s: Optional[str]) -> Optional[dict]:
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


def _to_response(asset: Asset, *, open_meldingen: int = 0, children: int = 0) -> dict:
    return {
        "id": asset.id,
        "code": asset.code,
        "name": asset.name,
        "asset_type": asset.asset_type,
        "lat": asset.lat,
        "lng": asset.lng,
        "location_description": asset.location_description,
        "parent_asset_id": asset.parent_asset_id,
        "project_id": asset.project_id,
        "installed_at": asset.installed_at,
        "expected_lifespan_years": asset.expected_lifespan_years,
        "condition_score": asset.condition_score,
        "last_inspection_at": asset.last_inspection_at,
        "properties": _properties_out(asset.properties_json),
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
        "open_meldingen_count": open_meldingen,
        "children_count": children,
    }


def _validate_condition(score: Optional[int]) -> None:
    if score is not None and not (1 <= score <= 5):
        raise HTTPException(status_code=400, detail="condition_score moet tussen 1 en 5 zijn (NEN 2767)")


def _validate_parent(db: Session, parent_id: Optional[str], current_user: User,
                     self_id: Optional[str] = None) -> None:
    if not parent_id:
        return
    parent = db.query(Asset).filter(
        Asset.id == parent_id,
        Asset.organization_id == current_user.organization_id,
    ).first()
    if not parent:
        raise HTTPException(status_code=400, detail="Parent-asset niet gevonden binnen je organisatie")
    if self_id and parent_id == self_id:
        raise HTTPException(status_code=400, detail="Een asset kan geen parent van zichzelf zijn")


# ─────────────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/", response_model=List[AssetResponse])
def list_assets(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    project_id: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    parent_asset_id: Optional[str] = Query(None, description="Geef 'root' om alleen top-level te krijgen"),
    q: Optional[str] = Query(None, description="Zoek in code, naam, locatie-omschrijving"),
    include_archived: bool = Query(False),
    limit: int = Query(200, ge=1, le=1000),
):
    """Lijst assets binnen de organisatie, met basale filters."""
    query = db.query(Asset).filter(Asset.organization_id == current_user.organization_id)

    if not include_archived:
        query = query.filter(Asset.archived_at.is_(None))
    if project_id:
        query = query.filter(Asset.project_id == project_id)
    if asset_type:
        query = query.filter(Asset.asset_type == asset_type)
    if parent_asset_id == "root":
        query = query.filter(Asset.parent_asset_id.is_(None))
    elif parent_asset_id:
        query = query.filter(Asset.parent_asset_id == parent_asset_id)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            (func.lower(Asset.code).like(like))
            | (func.lower(func.coalesce(Asset.name, "")).like(like))
            | (func.lower(func.coalesce(Asset.location_description, "")).like(like))
        )

    assets = query.order_by(Asset.code).limit(limit).all()
    if not assets:
        return []

    # Counts (open meldingen + children) in twee bulk-queries i.p.v. N+1
    ids = [a.id for a in assets]
    open_counts = dict(
        db.query(Melding.asset_id, func.count(Melding.id))
          .filter(Melding.asset_id.in_(ids), Melding.status != "afgerond")
          .group_by(Melding.asset_id).all()
    )
    child_counts = dict(
        db.query(Asset.parent_asset_id, func.count(Asset.id))
          .filter(Asset.parent_asset_id.in_(ids), Asset.archived_at.is_(None))
          .group_by(Asset.parent_asset_id).all()
    )

    return [_to_response(a,
                         open_meldingen=open_counts.get(a.id, 0),
                         children=child_counts.get(a.id, 0)) for a in assets]


@router.get("/types")
def list_asset_types(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Distinct asset-types binnen de organisatie — handig voor UI-filters."""
    rows = (
        db.query(Asset.asset_type, func.count(Asset.id))
          .filter(Asset.organization_id == current_user.organization_id,
                  Asset.archived_at.is_(None))
          .group_by(Asset.asset_type).all()
    )
    return [{"asset_type": t, "count": c} for t, c in rows]


@router.get("/tree")
def get_asset_tree(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    project_id: Optional[str] = Query(None),
):
    """Boomstructuur per project — geneste dict, max 4 niveaus diep."""
    q = db.query(Asset).filter(
        Asset.organization_id == current_user.organization_id,
        Asset.archived_at.is_(None),
    )
    if project_id:
        q = q.filter(Asset.project_id == project_id)
    all_assets = q.order_by(Asset.code).all()

    # Build adjacency map
    children_of: dict[Optional[str], list[Asset]] = {}
    for a in all_assets:
        children_of.setdefault(a.parent_asset_id, []).append(a)

    def render(a: Asset, depth: int) -> dict:
        node = {
            "id": a.id, "code": a.code, "name": a.name, "asset_type": a.asset_type,
            "condition_score": a.condition_score, "lat": a.lat, "lng": a.lng,
            "children": [],
        }
        if depth < 4:
            node["children"] = [render(c, depth + 1) for c in children_of.get(a.id, [])]
        return node

    roots = children_of.get(None, [])
    return [render(r, 1) for r in roots]


@router.post("/", response_model=AssetResponse)
def create_asset(
    data: AssetCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not can_manage_assets(current_user):
        raise HTTPException(status_code=403, detail="Alleen admins en projectleiders mogen assets aanmaken")

    _validate_condition(data.condition_score)
    _validate_parent(db, data.parent_asset_id, current_user)

    # Code-uniciteit binnen organisatie
    existing = db.query(Asset).filter(
        Asset.organization_id == current_user.organization_id,
        Asset.code == data.code,
        Asset.archived_at.is_(None),
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Asset met code '{data.code}' bestaat al")

    asset = Asset(
        code=data.code, name=data.name, asset_type=data.asset_type,
        lat=data.lat, lng=data.lng, location_description=data.location_description,
        parent_asset_id=data.parent_asset_id, project_id=data.project_id,
        installed_at=data.installed_at, expected_lifespan_years=data.expected_lifespan_years,
        condition_score=data.condition_score, last_inspection_at=data.last_inspection_at,
        properties_json=_properties_in(data.properties),
        organization_id=current_user.organization_id, created_by=current_user.id,
    )
    db.add(asset); db.commit(); db.refresh(asset)

    log_action(db, request, current_user,
               action=ACTION.ASSET_CREATE, entity_type="asset", entity_id=asset.id,
               after={"code": asset.code, "asset_type": asset.asset_type, "project_id": asset.project_id})

    return _to_response(asset)


@router.get("/{asset_id}", response_model=AssetResponse)
def get_asset(
    asset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    asset = db.query(Asset).filter(
        Asset.id == asset_id,
        Asset.organization_id == current_user.organization_id,
    ).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset niet gevonden")

    open_meldingen = (
        db.query(func.count(Melding.id))
          .filter(Melding.asset_id == asset_id, Melding.status != "afgerond").scalar() or 0
    )
    children = (
        db.query(func.count(Asset.id))
          .filter(Asset.parent_asset_id == asset_id, Asset.archived_at.is_(None)).scalar() or 0
    )
    return _to_response(asset, open_meldingen=open_meldingen, children=children)


@router.put("/{asset_id}", response_model=AssetResponse)
def update_asset(
    asset_id: str,
    update: AssetUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not can_manage_assets(current_user):
        raise HTTPException(status_code=403, detail="Alleen admins en projectleiders mogen assets wijzigen")

    asset = db.query(Asset).filter(
        Asset.id == asset_id,
        Asset.organization_id == current_user.organization_id,
    ).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset niet gevonden")

    update_data = update.model_dump(exclude_unset=True)
    _validate_condition(update_data.get("condition_score"))
    if "parent_asset_id" in update_data:
        _validate_parent(db, update_data["parent_asset_id"], current_user, self_id=asset.id)

    is_inspection_update = "last_inspection_at" in update_data or "condition_score" in update_data

    before = {k: getattr(asset, k) for k in update_data.keys() if k != "properties"}
    if "properties" in update_data:
        before["properties"] = _properties_out(asset.properties_json)
        asset.properties_json = _properties_in(update_data.pop("properties"))

    for field, value in update_data.items():
        setattr(asset, field, value)
    db.commit(); db.refresh(asset)

    log_action(db, request, current_user,
               action=(ACTION.ASSET_INSPECTION if is_inspection_update else ACTION.ASSET_UPDATE),
               entity_type="asset", entity_id=asset.id,
               before=before, after=update_data)

    return _to_response(asset)


@router.delete("/{asset_id}")
def archive_asset(
    asset_id: str,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Soft-delete: zet `archived_at`. Behoudt history voor audit."""
    asset = db.query(Asset).filter(
        Asset.id == asset_id,
        Asset.organization_id == current_user.organization_id,
    ).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset niet gevonden")
    if asset.archived_at:
        return {"message": "Reeds gearchiveerd"}

    asset.archived_at = datetime.now(timezone.utc)
    db.commit()
    log_action(db, request, current_user,
               action=ACTION.ASSET_ARCHIVE, entity_type="asset", entity_id=asset.id,
               before={"code": asset.code, "asset_type": asset.asset_type})
    return {"message": f"Asset '{asset.code}' gearchiveerd"}


# ─────────────────────────────────────────────────────────────────────────────
# Bulk-import
# ─────────────────────────────────────────────────────────────────────────────

# Aliases voor veel-gebruikte kolomnamen in NL gemeente/aannemer-CSVs.
# Eerste match wint. Lowercase compare; ondersteunt spaties + underscores.
_CSV_ALIASES: dict[str, list[str]] = {
    "code":                 ["code", "objectnummer", "object_nummer", "objectid", "object_id",
                             "nummer", "id", "externe_code", "kenmerk", "asset_id", "asset_code"],
    "asset_type":           ["asset_type", "objecttype", "object_type", "type", "categorie",
                             "category", "soort", "klasse", "klasse_omschrijving"],
    "name":                 ["name", "naam", "omschrijving", "beschrijving", "description", "label"],
    "lat":                  ["lat", "latitude", "breedte", "wgs84_lat", "y_lat"],
    "lng":                  ["lng", "lon", "longitude", "lengte", "wgs84_lng", "wgs84_lon", "x_lng"],
    "location_description": ["location_description", "locatie", "locatie_omschrijving",
                             "adres", "straat", "plaats", "gemeente_locatie", "omschrijving_locatie"],
    "parent_code":          ["parent_code", "parent", "parent_id", "hoofdobject", "hoofdcode",
                             "ouder", "ouder_code"],
    "project_id":           ["project_id", "project"],
}

# RD-coördinaten herkenning: X meestal 0-300.000 m, Y meestal 300.000-650.000 m
_RD_X_RANGE = (0, 300000)
_RD_Y_RANGE = (300000, 650000)


def _rd_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Pseudo-conversie RD → WGS84. Gebruikt benadering goed genoeg voor weergave;
    gemeenten die echte precisie nodig hebben moeten zelf de juiste lat/lng meegeven."""
    # Polynome benadering uit https://thomasv.nl/rd-naar-wgs84
    dx = (x - 155000) * 1e-5
    dy = (y - 463000) * 1e-5
    lat = 52.15517440 + (3235.65389 * dy - 32.58297 * dx*dx - 0.24750 * dy*dy
                         - 0.84978 * dx*dx*dy - 0.06550 * dy*dy*dy
                         - 0.01709 * dx*dx*dy*dy - 0.00738 * dx
                         + 0.00530 * dx*dx*dx*dx + 0.00033 * dx*dx*dy*dy*dy
                         - 0.00012 * dx*dy) / 3600
    lng = 5.38720621 + (5260.52916 * dx + 105.94684 * dx*dy + 2.45656 * dx*dy*dy
                         + 0.81885 * dx*dx*dx + 0.05594 * dx*dy*dy*dy
                         - 0.05607 * dx*dx*dx*dy + 0.01199 * dy
                         + 0.00256 * dx*dx*dx*dy*dy - 0.00128 * dx*dy*dy*dy*dy
                         + 0.00022 * dy*dy - 0.00022 * dx*dx
                         + 0.00026 * dx*dx*dx*dx*dx) / 3600
    return lat, lng


def _normalize_key(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "_").replace("-", "_")


def _build_column_mapping(fieldnames: list[str]) -> tuple[dict[str, str], list[str]]:
    """Map onze interne velden → de kolomnaam in de CSV. Returnt (mapping, missing-required)."""
    available = {_normalize_key(c): c for c in (fieldnames or [])}
    mapping: dict[str, str] = {}
    for internal, aliases in _CSV_ALIASES.items():
        for a in aliases:
            if _normalize_key(a) in available:
                mapping[internal] = available[_normalize_key(a)]
                break
    missing = [k for k in ("code", "asset_type") if k not in mapping]
    return mapping, missing


def _get(row: dict, mapping: dict[str, str], internal: str) -> str:
    col = mapping.get(internal)
    if not col:
        return ""
    return (row.get(col) or "").strip()


def _parse_coords(row: dict, mapping: dict[str, str]) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Probeer eerst lat/lng. Als alleen X/Y aanwezig én ze in RD-range vallen,
    converteer naar WGS84. Returns (lat, lng, warning-or-None)."""
    lat_raw = _get(row, mapping, "lat")
    lng_raw = _get(row, mapping, "lng")
    if lat_raw and lng_raw:
        try:
            lat = float(lat_raw.replace(",", "."))
            lng = float(lng_raw.replace(",", "."))
            # Heuristiek: als beide getallen > 1000 (~RD-meters), converteer
            if abs(lat) > 1000 and abs(lng) > 1000:
                # In RD: x = lng-kolom, y = lat-kolom (bij sommige systemen)
                # We checken op range
                if (_RD_X_RANGE[0] <= lng <= _RD_X_RANGE[1]
                        and _RD_Y_RANGE[0] <= lat <= _RD_Y_RANGE[1]):
                    new_lat, new_lng = _rd_to_wgs84(lng, lat)
                    return new_lat, new_lng, None
                if (_RD_X_RANGE[0] <= lat <= _RD_X_RANGE[1]
                        and _RD_Y_RANGE[0] <= lng <= _RD_Y_RANGE[1]):
                    new_lat, new_lng = _rd_to_wgs84(lat, lng)
                    return new_lat, new_lng, None
            return lat, lng, None
        except ValueError:
            return None, None, "lat/lng niet numeriek"
    return None, None, None


@router.post("/import/csv")
async def import_assets_csv(
    request: Request,
    file: UploadFile = File(..., description="CSV. Auto-detecteert kolomnamen — code/objectnummer, type/objecttype/categorie, naam/omschrijving, lat-lng of RD x/y."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bulk-CSV-import met flexibele kolomdetectie.

    Auto-mapping van Nederlandse synoniemen:
      - code ⇐ code, objectnummer, nummer, asset_id, kenmerk, ...
      - asset_type ⇐ asset_type, objecttype, type, categorie, soort, ...
      - name ⇐ name, naam, omschrijving, beschrijving, ...
      - lat/lng ⇐ lat/lng, latitude/longitude, breedte/lengte, of RD x/y (auto-converted)
      - location_description ⇐ locatie, adres, straat, ...
      - parent_code ⇐ parent_code, parent, hoofdobject, ouder_code, ...

    Bestaande codes worden geüpdatet (idempotent), nieuwe codes aangemaakt.
    """
    if not can_manage_assets(current_user):
        raise HTTPException(status_code=403, detail="Geen rechten voor bulk-import")

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")  # BOM-tolerant
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp1252")  # Excel/Windows fallback
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Bestand moet UTF-8 of Windows-1252 zijn")

    # Probeer eerst comma, dan semicolon (Excel NL gebruikt vaak semicolon)
    sample = text[:2048]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    mapping, missing = _build_column_mapping(reader.fieldnames or [])
    if missing:
        # Helpfully: tonen wat er WEL in de CSV staat + welke aliases we kennen
        available_cols = list(reader.fieldnames or [])
        hint_lines = []
        for m in missing:
            aliases = _CSV_ALIASES[m][:6]
            hint_lines.append(f"'{m}' — verwachte kolomnaam: {', '.join(aliases)}")
        raise HTTPException(status_code=400, detail={
            "error": f"Verplichte kolommen niet gevonden: {missing}",
            "csv_columns": available_cols,
            "expected": hint_lines,
            "tip": "Hernoem 1 kolom in je CSV of stuur ons je kolomnamen — we voegen aliases toe.",
        })

    created = updated = 0
    errors: list[dict] = []

    code_to_id: dict[str, str] = {
        a.code: a.id for a in db.query(Asset).filter(
            Asset.organization_id == current_user.organization_id,
        ).all()
    }

    rows = list(reader)
    for i, row in enumerate(rows, start=2):
        code = _get(row, mapping, "code")
        asset_type = _get(row, mapping, "asset_type")
        if not code or not asset_type:
            errors.append({"row": i, "error": "code/asset_type ontbreken in deze rij"})
            continue

        lat, lng, coord_err = _parse_coords(row, mapping)
        if coord_err:
            errors.append({"row": i, "error": coord_err})

        name_val = _get(row, mapping, "name") or None
        loc_val = _get(row, mapping, "location_description") or None
        proj_val = _get(row, mapping, "project_id") or None

        existing = db.query(Asset).filter(
            Asset.organization_id == current_user.organization_id,
            Asset.code == code,
        ).first()

        if existing:
            existing.asset_type = asset_type
            existing.name = name_val or existing.name
            existing.lat = lat if lat is not None else existing.lat
            existing.lng = lng if lng is not None else existing.lng
            existing.location_description = loc_val or existing.location_description
            existing.project_id = proj_val or existing.project_id
            updated += 1
        else:
            new_asset = Asset(
                code=code, asset_type=asset_type, name=name_val,
                lat=lat, lng=lng,
                location_description=loc_val,
                project_id=proj_val,
                organization_id=current_user.organization_id,
                created_by=current_user.id,
            )
            db.add(new_asset)
            db.flush()
            code_to_id[code] = new_asset.id
            created += 1

    # Pass 2: parent koppelen op basis van parent_code
    for i, row in enumerate(rows, start=2):
        code = _get(row, mapping, "code")
        parent_code = _get(row, mapping, "parent_code")
        if not code or not parent_code:
            continue
        if parent_code not in code_to_id:
            errors.append({"row": i, "error": f"parent_code '{parent_code}' niet gevonden"})
            continue
        asset = db.query(Asset).filter(
            Asset.organization_id == current_user.organization_id,
            Asset.code == code,
        ).first()
        if asset and code_to_id[parent_code] != asset.id:
            asset.parent_asset_id = code_to_id[parent_code]

    db.commit()
    log_action(db, request, current_user,
               action=ACTION.ASSET_BULK_IMPORT, entity_type="asset",
               extra={"created": created, "updated": updated, "errors": len(errors),
                      "filename": file.filename, "delimiter": delimiter,
                      "mapping": mapping})

    return {"created": created, "updated": updated, "errors": errors,
            "detected_mapping": mapping, "delimiter": delimiter}


@router.post("/import/geojson")
async def import_assets_geojson(
    request: Request,
    file: UploadFile = File(..., description="GeoJSON FeatureCollection. Properties.code en properties.asset_type verplicht."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Import van GeoJSON FeatureCollection. Per Feature: properties moeten
    minimaal `code` en `asset_type` bevatten. Geometry wordt gelezen als Point;
    eerste coordinaat = lng, tweede = lat."""
    if not can_manage_assets(current_user):
        raise HTTPException(status_code=403, detail="Geen rechten voor bulk-import")

    raw = await file.read()
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Ongeldig GeoJSON: {e}")

    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise HTTPException(status_code=400, detail="Verwacht een GeoJSON FeatureCollection")

    created = updated = 0
    errors: list[dict] = []

    for i, f in enumerate(features, start=1):
        props = f.get("properties") or {}
        code = str(props.get("code") or "").strip()
        asset_type = str(props.get("asset_type") or "").strip()
        if not code or not asset_type:
            errors.append({"feature": i, "error": "properties.code en properties.asset_type verplicht"})
            continue

        geom = f.get("geometry") or {}
        lat = lng = None
        if geom.get("type") == "Point":
            coords = geom.get("coordinates") or []
            if len(coords) >= 2:
                lng, lat = float(coords[0]), float(coords[1])

        # Behoud overige properties als "properties_json"
        extra = {k: v for k, v in props.items()
                 if k not in {"code", "asset_type", "name", "location_description", "project_id"}}

        existing = db.query(Asset).filter(
            Asset.organization_id == current_user.organization_id,
            Asset.code == code,
        ).first()

        if existing:
            existing.asset_type = asset_type
            existing.name = props.get("name") or existing.name
            existing.lat = lat if lat is not None else existing.lat
            existing.lng = lng if lng is not None else existing.lng
            existing.location_description = props.get("location_description") or existing.location_description
            existing.project_id = props.get("project_id") or existing.project_id
            if extra:
                existing.properties_json = _properties_in(extra)
            updated += 1
        else:
            db.add(Asset(
                code=code, asset_type=asset_type, name=props.get("name") or None,
                lat=lat, lng=lng,
                location_description=props.get("location_description") or None,
                project_id=props.get("project_id") or None,
                properties_json=_properties_in(extra) if extra else None,
                organization_id=current_user.organization_id,
                created_by=current_user.id,
            ))
            created += 1

    db.commit()
    log_action(db, request, current_user,
               action=ACTION.ASSET_BULK_IMPORT, entity_type="asset",
               extra={"created": created, "updated": updated, "errors": len(errors),
                      "filename": file.filename, "format": "geojson"})

    return {"created": created, "updated": updated, "errors": errors}
