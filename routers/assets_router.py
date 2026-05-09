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
from typing import Optional, List, Tuple

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
        # NWB-Wegvakken (sinds v3.4)
        "geometry_geojson": asset.geometry_geojson,
        "length_m": asset.length_m,
        "is_segment": getattr(asset, "is_segment", False),
        "nwb_wvk_id": asset.nwb_wvk_id,
        "nwb_wvk_begdat": asset.nwb_wvk_begdat,
        "nwb_jte_id_beg": asset.nwb_jte_id_beg,
        "nwb_jte_id_end": asset.nwb_jte_id_end,
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


@router.get("/map")
def list_assets_for_map(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    project_id: Optional[str] = Query(None, description="Filter op project"),
    only_segments: bool = Query(True, description="True (default) = alleen wegvakken; False = alle assets met geometry"),
):
    """Lichtgewicht endpoint voor map-rendering.

    Geeft alleen de velden die nodig zijn om wegvakken te tekenen + kleuren:
    `id`, `code`, `name`, `asset_type`, `condition_score`, `geometry_geojson`,
    `length_m`, `nwb_wvk_id`, `nwb_wvk_begdat`, `project_id`, `parent_asset_id`,
    `open_meldingen_count`. Latency-vriendelijk bij 1000+ wegvakken.
    """
    q = db.query(Asset).filter(
        Asset.organization_id == current_user.organization_id,
        Asset.archived_at.is_(None),
        Asset.geometry_geojson.isnot(None),
    )
    if only_segments:
        q = q.filter(Asset.is_segment == True)  # noqa: E712 - SQLAlchemy boolean compare
    if project_id:
        q = q.filter(Asset.project_id == project_id)

    assets = q.all()
    if not assets:
        return []

    ids = [a.id for a in assets]
    open_counts = dict(
        db.query(Melding.asset_id, func.count(Melding.id))
          .filter(Melding.asset_id.in_(ids), Melding.status != "afgerond")
          .group_by(Melding.asset_id).all()
    )

    return [{
        "id": a.id,
        "code": a.code,
        "name": a.name,
        "asset_type": a.asset_type,
        "condition_score": a.condition_score,
        "geometry_geojson": a.geometry_geojson,
        "length_m": a.length_m,
        "is_segment": getattr(a, "is_segment", False),
        "nwb_wvk_id": a.nwb_wvk_id,
        "nwb_wvk_begdat": a.nwb_wvk_begdat,
        "project_id": a.project_id,
        "parent_asset_id": a.parent_asset_id,
        "open_meldingen_count": open_counts.get(a.id, 0),
    } for a in assets]


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
def delete_asset(
    asset_id: str,
    request: Request,
    hard: bool = Query(False, description="True = permanent verwijderen. False = archiveren (default, soft)."),
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Verwijder asset. Default = soft-archive. Met `?hard=true` permanent.

    Bij hard-delete worden referenties safe ge-orphand:
    - Meldingen: asset_id wordt null (melding zelf blijft bestaan voor audit)
    - AI-analyses: asset_id wordt null (analyse-record blijft)
    - Child-assets: parent_asset_id wordt null
    - JobCluster relaties: niet getroffen (link via melding)
    De audit-log entry blijft permanent — alleen de asset-row verdwijnt.
    """
    asset = db.query(Asset).filter(
        Asset.id == asset_id,
        Asset.organization_id == current_user.organization_id,
    ).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset niet gevonden")

    snapshot = {
        "code": asset.code, "asset_type": asset.asset_type,
        "name": asset.name, "lat": asset.lat, "lng": asset.lng,
        "condition_score": asset.condition_score,
    }

    if not hard:
        # Soft archive (existing behavior)
        if asset.archived_at:
            return {"message": "Reeds gearchiveerd", "deleted": False}
        asset.archived_at = datetime.now(timezone.utc)
        db.commit()
        log_action(db, request, current_user,
                   action=ACTION.ASSET_ARCHIVE, entity_type="asset", entity_id=asset.id,
                   before=snapshot)
        return {"message": f"Asset '{asset.code}' gearchiveerd", "deleted": False}

    # HARD DELETE — cascade-orphan
    from models import Melding, AIAnalysis
    melding_count = (db.query(Melding)
                       .filter(Melding.asset_id == asset_id)
                       .update({Melding.asset_id: None}, synchronize_session=False))
    aianalysis_count = (db.query(AIAnalysis)
                          .filter(AIAnalysis.asset_id == asset_id)
                          .update({AIAnalysis.asset_id: None}, synchronize_session=False))
    children_count = (db.query(Asset)
                        .filter(Asset.parent_asset_id == asset_id)
                        .update({Asset.parent_asset_id: None}, synchronize_session=False))
    db.delete(asset)
    db.commit()

    log_action(db, request, current_user,
               action=ACTION.ASSET_DELETE, entity_type="asset", entity_id=asset_id,
               before=snapshot,
               extra={
                   "orphaned_meldingen": melding_count,
                   "orphaned_ai_analyses": aianalysis_count,
                   "orphaned_children": children_count,
               })
    return {
        "message": f"Asset '{snapshot['code']}' permanent verwijderd",
        "deleted": True,
        "orphaned": {
            "meldingen": melding_count,
            "ai_analyses": aianalysis_count,
            "children": children_count,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Wegvak-override: split + merge (fase 4 NWB-architectuur)
# ─────────────────────────────────────────────────────────────────────────────

import wegvak_geometry as wgeom
from pydantic import BaseModel, Field


class SplitRequest(BaseModel):
    split_points: List[List[float]] = Field(..., description="Lijst [lng, lat]-punten waar het wegvak gesplitst moet worden")
    code_suffixes: Optional[List[str]] = Field(None, description="Optioneel: per stuk een code-suffix. Default: A, B, C…")


class MergeRequest(BaseModel):
    asset_ids: List[str] = Field(..., min_length=2, description="2+ wegvak-IDs om samen te voegen")
    new_code: Optional[str] = None
    new_name: Optional[str] = None
    project_id: Optional[str] = None


def _nearest_piece_idx(lat: Optional[float], lng: Optional[float],
                       pieces: List[List[List[float]]]) -> int:
    """Vind de index van het LineString-stuk dat het dichtst bij (lat,lng) ligt.

    Gebruikt midden-coord van elk stuk als referentie. Bij ontbrekende coords
    valt terug op stuk 0.
    """
    if lat is None or lng is None or not pieces:
        return 0
    best_idx, best_dist = 0, None
    for i, piece in enumerate(pieces):
        if not piece:
            continue
        mid = piece[len(piece) // 2]
        d = wgeom.haversine_meters([lng, lat], mid)
        if best_dist is None or d < best_dist:
            best_dist, best_idx = d, i
    return best_idx


def _piece_center(piece: List[List[float]]) -> Tuple[Optional[float], Optional[float]]:
    if not piece:
        return None, None
    mid = piece[len(piece) // 2]
    return mid[1], mid[0]   # lat, lng


def _default_suffixes(n: int) -> List[str]:
    """A, B, C, ..., Z, AA, AB, …  Genoeg voor 26+ stukken."""
    out = []
    for i in range(n):
        if i < 26:
            out.append(chr(ord("A") + i))
        else:
            out.append(chr(ord("A") + (i // 26) - 1) + chr(ord("A") + (i % 26)))
    return out


@router.post("/{asset_id}/split")
def split_wegvak(
    asset_id: str,
    payload: SplitRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Splits een wegvak op één of meer punten — fase 4 override.

    Workflow:
      1. Origineel wegvak wordt gearchiveerd (audit-traceable).
      2. Per resulterend stuk komt een nieuwe Asset met sub-WVK_ID
         (bv. `WVK-12345-A`) en eigen geometry + length_m.
      3. Open meldingen worden verplaatst naar het dichtstbijzijnde nieuwe stuk
         (op basis van melding-coords vs piece-center).
      4. Children-assets idem — herkoppeld aan dichtstbijzijnde stuk.

    Permissies: alleen admin/manager — splits zijn definitief.
    """
    if not can_manage_assets(current_user):
        raise HTTPException(status_code=403, detail="Alleen admins/managers mogen wegvakken splitsen")

    asset = db.query(Asset).filter(
        Asset.id == asset_id,
        Asset.organization_id == current_user.organization_id,
        Asset.archived_at.is_(None),
    ).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Wegvak niet gevonden")
    if not asset.geometry_geojson:
        raise HTTPException(status_code=400, detail="Asset heeft geen geometry — splitsen niet mogelijk")
    if not payload.split_points:
        raise HTTPException(status_code=400, detail="Geef minimaal 1 split-punt op")

    try:
        geom = json.loads(asset.geometry_geojson)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=400, detail="Asset geometry is geen valide JSON")
    if geom.get("type") != "LineString" or not geom.get("coordinates"):
        raise HTTPException(status_code=400, detail="Splitsen werkt alleen op LineString-geometry")

    try:
        pieces = wgeom.split_linestring_at_points(geom["coordinates"], payload.split_points)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if len(pieces) < 2:
        raise HTTPException(status_code=400, detail="Splits leverde geen meerdere stukken op")

    # Bepaal suffixes
    suffixes = payload.code_suffixes or _default_suffixes(len(pieces))
    if len(suffixes) < len(pieces):
        suffixes = suffixes + _default_suffixes(len(pieces) - len(suffixes))[len(suffixes):]

    # Maak nieuwe assets
    base_code = asset.code
    base_wvk = asset.nwb_wvk_id or ""
    new_assets: List[Asset] = []
    for i, piece in enumerate(pieces):
        suf = suffixes[i]
        new_code = f"{base_code}-{suf}"
        # Code-uniciteit
        if db.query(Asset).filter(
            Asset.organization_id == current_user.organization_id,
            Asset.code == new_code,
            Asset.archived_at.is_(None),
        ).first():
            new_code = f"{base_code}-{suf}-{i+1}"  # collision suffix

        center_lat, center_lng = _piece_center(piece)
        length_m = wgeom.linestring_length_m(piece)

        child = Asset(
            code=new_code,
            name=(f"{asset.name} ({suf})" if asset.name else new_code),
            asset_type=asset.asset_type,
            organization_id=current_user.organization_id,
            created_by=current_user.id,
            project_id=asset.project_id,
            parent_asset_id=asset.parent_asset_id,   # erft de parent — niet origineel zelf
            lat=center_lat, lng=center_lng,
            location_description=asset.location_description,
            condition_score=asset.condition_score,
            installed_at=asset.installed_at,
            expected_lifespan_years=asset.expected_lifespan_years,
            properties_json=asset.properties_json,
            geometry_geojson=json.dumps({"type": "LineString", "coordinates": piece}, separators=(",", ":")),
            length_m=length_m,
            is_segment=True,
            nwb_wvk_id=(f"{base_wvk}-{suf}" if base_wvk else None),
            nwb_wvk_begdat=asset.nwb_wvk_begdat,
            nwb_jte_id_beg=(asset.nwb_jte_id_beg if i == 0 else None),
            nwb_jte_id_end=(asset.nwb_jte_id_end if i == len(pieces) - 1 else None),
        )
        db.add(child); db.flush()
        new_assets.append(child)

    # Migrate meldingen — elk naar dichtstbijzijnde piece
    meldingen = db.query(Melding).filter(Melding.asset_id == asset_id).all()
    melding_migrations = []
    for m in meldingen:
        idx = _nearest_piece_idx(m.lat, m.lng, pieces)
        m.asset_id = new_assets[idx].id
        melding_migrations.append({"melding_id": m.id, "→": new_assets[idx].id})

    # Migrate child-assets — idem
    child_assets = db.query(Asset).filter(
        Asset.parent_asset_id == asset_id,
        Asset.archived_at.is_(None),
    ).all()
    child_migrations = []
    for c in child_assets:
        idx = _nearest_piece_idx(c.lat, c.lng, pieces)
        c.parent_asset_id = new_assets[idx].id
        child_migrations.append({"child_asset_id": c.id, "→": new_assets[idx].id})

    # Archive origineel
    asset.archived_at = datetime.now(timezone.utc)

    db.commit()
    for a in new_assets:
        db.refresh(a)

    log_action(db, request, current_user,
               action=ACTION.ASSET_SPLIT, entity_type="asset", entity_id=asset_id,
               before={"code": base_code, "wvk_id": base_wvk},
               extra={
                   "pieces": len(pieces),
                   "new_asset_ids": [a.id for a in new_assets],
                   "meldingen_migrated": len(melding_migrations),
                   "children_migrated": len(child_migrations),
               })

    return {
        "archived_id": asset_id,
        "created": [_to_response(a) for a in new_assets],
        "meldingen_migrated": len(melding_migrations),
        "children_migrated": len(child_migrations),
    }


@router.post("/merge")
def merge_wegvakken(
    payload: MergeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Merge 2+ aangrenzende wegvakken tot één — fase 4 override.

    Logica:
      1. Valideer dat alle assets in dezelfde org zitten en LineString-geometry hebben.
      2. Probeer de polylines greedy te ketenen (5m endpoint-tolerantie).
      3. Maak een nieuw Asset met union-geometry + nieuwe code/naam.
      4. Verplaats alle meldingen + children naar het nieuwe asset.
      5. Archiveer de originelen.

    Permissies: alleen admin/manager.
    """
    if not can_manage_assets(current_user):
        raise HTTPException(status_code=403, detail="Alleen admins/managers mogen wegvakken mergen")

    if len(payload.asset_ids) < 2:
        raise HTTPException(status_code=400, detail="Geef minimaal 2 wegvak-IDs op")

    assets = db.query(Asset).filter(
        Asset.id.in_(payload.asset_ids),
        Asset.organization_id == current_user.organization_id,
        Asset.archived_at.is_(None),
    ).all()
    if len(assets) != len(payload.asset_ids):
        raise HTTPException(status_code=404, detail="Een of meer wegvakken niet gevonden")

    linestrings: List[List[List[float]]] = []
    for a in assets:
        if not a.geometry_geojson:
            raise HTTPException(status_code=400, detail=f"Asset {a.code} heeft geen geometry")
        try:
            g = json.loads(a.geometry_geojson)
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(status_code=400, detail=f"Asset {a.code} heeft invalide geometry")
        if g.get("type") != "LineString" or not g.get("coordinates"):
            raise HTTPException(status_code=400, detail=f"Asset {a.code} is geen LineString")
        linestrings.append(g["coordinates"])

    try:
        merged_coords = wgeom.merge_linestrings(linestrings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Nieuwe asset bouwen
    proto = assets[0]
    base_codes = [a.code for a in assets]
    base_wvks = [a.nwb_wvk_id for a in assets if a.nwb_wvk_id]

    new_code = payload.new_code or ("MERGE-" + "+".join(base_wvks)) if base_wvks else (payload.new_code or "+".join(base_codes))
    # Code-uniciteit
    if db.query(Asset).filter(
        Asset.organization_id == current_user.organization_id,
        Asset.code == new_code,
        Asset.archived_at.is_(None),
    ).first():
        # collision suffix met asset_ids hash
        new_code = f"{new_code}-{abs(hash(tuple(payload.asset_ids))) % 10000}"

    length_m = wgeom.linestring_length_m(merged_coords)
    center_lat, center_lng = _piece_center(merged_coords)

    # Conditie-score: de slechtste (hoogste) van de samenvoegers — meest urgente onderhoud wint
    cond_scores = [a.condition_score for a in assets if a.condition_score is not None]
    new_cond = max(cond_scores) if cond_scores else None

    new_asset = Asset(
        code=new_code,
        name=payload.new_name or proto.name,
        asset_type=proto.asset_type,
        organization_id=current_user.organization_id,
        created_by=current_user.id,
        project_id=payload.project_id or proto.project_id,
        parent_asset_id=proto.parent_asset_id,
        lat=center_lat, lng=center_lng,
        location_description=proto.location_description,
        condition_score=new_cond,
        geometry_geojson=json.dumps({"type": "LineString", "coordinates": merged_coords}, separators=(",", ":")),
        length_m=length_m,
        is_segment=True,
        nwb_wvk_id=("+".join(base_wvks) if base_wvks else None),
    )
    db.add(new_asset); db.flush()

    # Migrate meldingen
    asset_ids = [a.id for a in assets]
    melding_migrated = (db.query(Melding)
                          .filter(Melding.asset_id.in_(asset_ids))
                          .update({Melding.asset_id: new_asset.id}, synchronize_session=False))

    # Migrate child-assets (children van de oude assets)
    children_migrated = (db.query(Asset)
                            .filter(Asset.parent_asset_id.in_(asset_ids),
                                    Asset.archived_at.is_(None),
                                    Asset.id != new_asset.id)
                            .update({Asset.parent_asset_id: new_asset.id}, synchronize_session=False))

    # Archive originelen
    archived_at = datetime.now(timezone.utc)
    for a in assets:
        a.archived_at = archived_at

    db.commit(); db.refresh(new_asset)

    log_action(db, request, current_user,
               action=ACTION.ASSET_MERGE, entity_type="asset", entity_id=new_asset.id,
               extra={
                   "merged_from": asset_ids,
                   "merged_codes": base_codes,
                   "meldingen_migrated": melding_migrated,
                   "children_migrated": children_migrated,
               })

    return {
        "created": _to_response(new_asset),
        "archived_ids": asset_ids,
        "meldingen_migrated": melding_migrated,
        "children_migrated": children_migrated,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Bulk-import
# ─────────────────────────────────────────────────────────────────────────────

# Aliases voor veel-gebruikte kolomnamen in NL gemeente/aannemer-CSVs.
# Eerste match wint. Lowercase compare; ondersteunt spaties + underscores.
# Inclusief MOR+/MOR-Plus exports van NL-gemeenten (Haarlem etc.)
_CSV_ALIASES: dict[str, list[str]] = {
    "code":                 ["code", "objectnummer", "object_nummer", "objectid", "object_id",
                             "nummer", "uniek_nummer", "uniek nummer", "id", "externe_code",
                             "kenmerk", "asset_id", "asset_code", "wlo_nr", "wlo-nr"],
    "asset_type":           ["asset_type", "objecttype", "object_type", "type", "categorie",
                             "category", "soort", "klasse", "klasse_omschrijving",
                             "hoofdrubriek", "subrubriek", "rubriek"],
    "name":                 ["name", "naam", "omschrijving", "beschrijving", "description",
                             "label", "korte_omschrijving", "korte omschrijving"],
    "lat":                  ["lat", "latitude", "breedte", "wgs84_lat", "y_lat",
                             "objectcoordinatey", "coordinaaty", "coordinaat_y", "coordinatey"],
    "lng":                  ["lng", "lon", "longitude", "lengte", "wgs84_lng", "wgs84_lon",
                             "x_lng", "objectcoordinatex", "coordinaatx", "coordinaat_x",
                             "coordinatex"],
    "location_description": ["location_description", "locatie", "locatie_omschrijving",
                             "adres", "straat", "plaats", "gemeente_locatie",
                             "omschrijving_locatie", "buurt", "wijk"],
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


def _build_column_mapping(fieldnames: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Map onze interne velden → ALLE matchende kolomnamen in de CSV (in alias-volgorde).
    Per-row valt later de leesfunctie terug op de volgende alias als de primaire leeg is.
    """
    available = {_normalize_key(c): c for c in (fieldnames or [])}
    mapping: dict[str, list[str]] = {}
    for internal, aliases in _CSV_ALIASES.items():
        cols = [available[_normalize_key(a)] for a in aliases if _normalize_key(a) in available]
        if cols:
            mapping[internal] = cols
    missing = [k for k in ("code", "asset_type") if k not in mapping]
    return mapping, missing


def _get(row: dict, mapping: dict[str, list[str]], internal: str) -> str:
    """Probeer kolommen in alias-volgorde — eerste niet-lege waarde wint."""
    cols = mapping.get(internal) or []
    for col in cols:
        v = (row.get(col) or "").strip()
        if v:
            return v
    return ""


# GPS-coords embedded in vrije tekst (bv. MOR+ locatie-veld):
# Voorbeelden: "52.385° N, 4.6319° E" / "lat 52.39, lng 4.63" / "(52.385, 4.6319)"
import re
_GPS_TEXT_RE = re.compile(
    r"(?P<lat>\d{1,2}[\.,]\d{3,8})\s*°?\s*N?\s*[,;\s]+\s*(?P<lng>\d{1,2}[\.,]\d{3,8})\s*°?\s*E?",
    re.IGNORECASE,
)


def _parse_gps_from_text(text: str) -> tuple[Optional[float], Optional[float]]:
    """Probeer een lat/lng-paar uit vrije tekst te halen. NL-bereik: lat 50-54, lng 3-8."""
    if not text:
        return None, None
    for m in _GPS_TEXT_RE.finditer(text):
        try:
            lat = float(m.group("lat").replace(",", "."))
            lng = float(m.group("lng").replace(",", "."))
        except ValueError:
            continue
        # Sanity-check: NL-grenzen
        if 50.0 <= lat <= 54.0 and 3.0 <= lng <= 8.0:
            return lat, lng
        # Soms staan ze omgewisseld
        if 50.0 <= lng <= 54.0 and 3.0 <= lat <= 8.0:
            return lng, lat
    return None, None


def _parse_coords(row: dict, mapping: dict[str, list[str]]) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Probeer in volgorde:
       1. Expliciete lat/lng kolommen
       2. RD X/Y-conversie als de waarden in RD-range vallen
       3. GPS-pattern in de locatie-tekst
    """
    lat_raw = _get(row, mapping, "lat")
    lng_raw = _get(row, mapping, "lng")
    if lat_raw and lng_raw:
        try:
            lat = float(lat_raw.replace(",", "."))
            lng = float(lng_raw.replace(",", "."))
            if abs(lat) > 1000 and abs(lng) > 1000:
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
            pass  # val terug op tekst-parsing
    # Fallback: zoek GPS in locatie-tekst
    loc_text = _get(row, mapping, "location_description")
    lat, lng = _parse_gps_from_text(loc_text)
    if lat is not None and lng is not None:
        return lat, lng, None
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

        # Combineer locatie-velden indien beschikbaar — handig voor MOR+/gemeente-data
        # waar straat + huisnummer + buurt + wijk in losse kolommen staan.
        loc_val = _get(row, mapping, "location_description") or None
        if not loc_val:
            parts = []
            for key in ("straat", "huisnummer", "buurt", "wijk"):
                norm = _normalize_key(key)
                # Zoek de originele kolomnaam in de DictReader-row
                for col in row.keys():
                    if _normalize_key(col) == norm:
                        v = (row.get(col) or "").strip()
                        if v:
                            parts.append(v)
                        break
            if parts:
                loc_val = " ".join(parts[:2]) + (" · " + " ".join(parts[2:]) if len(parts) > 2 else "")

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
