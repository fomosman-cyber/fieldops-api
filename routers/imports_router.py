"""GeoJSON- en shapefile-import voor wegvakken en punt-assets.

Endpoints (multipart upload):
  POST /api/imports/geojson    — GeoJSON FeatureCollection
  POST /api/imports/shapefile  — shapefile als .zip (.shp + .dbf + .shx) of los .shp

Beide voeden dezelfde pipeline (_process_features). Per feature:
  - LineString / MultiLineString  -> wegvak-asset (is_segment=True) met lengte,
    bewaarde geometrie en bounding-box
  - Point (en Polygon -> centroid)  -> punt-asset
  - asset_type automatisch IMBOR-geclassificeerd uit de properties (of via een
    opgegeven default), met herkomst-vermelding
  - centroid als lat/lng; RD (EPSG:28992) auto-herkend + geconverteerd naar WGS84

`dry_run=true` geeft een preview (niets opgeslagen). Bestaande asset-codes binnen
de organisatie worden overgeslagen (geen duplicaten).

Dit is de 'slimme' importer; /api/assets/import/geojson blijft de simpele
punt-importer die expliciete code + asset_type per feature vereist.
"""
from __future__ import annotations

import io
import json
import zipfile
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from audit import ACTION, log_action
from auth import get_current_user
from database import get_db
from imbor_taxonomy import get_type_info
from models import Asset, User
from permissions import can_manage_assets
# RD->WGS84 conversie hergebruiken van de CSV-importer (zelfde benadering).
from routers.assets_router import _rd_to_wgs84
from wegvak_geometry import linestring_length_m

router = APIRouter(prefix="/api/imports", tags=["Imports"])

# Veiligheidsplafond: voorkom dat één upload de DB plat legt.
_MAX_FEATURES = 50_000

# RD (Rijksdriehoek, EPSG:28992) bereik in meters — X ~0-300k, Y ~300k-650k.
_RD_X_RANGE = (0.0, 300_000.0)
_RD_Y_RANGE = (300_000.0, 650_000.0)

# IMBOR-classificatie: trefwoord -> IMBOR-code (eerste match wint, volgorde = prioriteit).
_IMBOR_KEYWORDS: list[tuple[str, str]] = [
    ("fietspad", "fietspad"),
    ("fiets", "fietspad"),
    ("voetpad", "trottoir"),
    ("trottoir", "trottoir"),
    ("voetgang", "trottoir"),
    ("parkeer", "parkeervlak"),
    ("rotonde", "rotonde"),
    ("drempel", "verkeersdrempel"),
    ("plateau", "verkeersdrempel"),
    ("klinker", "wegvak_elementen"),
    ("element", "wegvak_elementen"),
    ("tegel", "wegvak_elementen"),
    ("gebakken", "wegvak_elementen"),
    ("bestrating", "wegvak_elementen"),
    ("asfalt", "wegvak_asfalt"),
    ("asphalt", "wegvak_asfalt"),
    ("rijbaan", "wegvak_asfalt"),
    ("rijweg", "wegvak_asfalt"),
    ("weg", "wegvak_asfalt"),
    ("straat", "wegvak_asfalt"),
]

_CODE_KEYS = ["code", "objectnummer", "objectid", "wvk_id", "wegvak_id", "id", "identificatie"]
_NAME_KEYS = ["naam", "name", "straatnaam", "wegnaam", "omschrijving", "stt_naam"]
_TYPE_KEYS = ["asset_type", "imbor_type", "imbor", "objecttype", "type", "functie",
              "bgt_functie", "wegtype", "verharding", "verhardingstype", "soort"]


def _first_prop(props: dict, keys: list[str]) -> Optional[str]:
    """Eerste niet-lege property-waarde die (case-insensitive) op een van de
    sleutels matcht."""
    lower = {k.lower(): v for k, v in props.items()}
    for k in keys:
        v = lower.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return None


def classify_imbor(props: dict, geom_type: str, default_type: Optional[str]) -> tuple[str, str]:
    """Bepaal het IMBOR asset_type uit de feature-properties.

    Returns (imbor_code, herkomst) waarbij herkomst in {expliciet, trefwoord,
    default, fallback}.
    """
    lower = {k.lower(): v for k, v in props.items()}
    # 1) expliciete, geldige IMBOR-code in de properties
    for k in _TYPE_KEYS:
        v = lower.get(k)
        if v and get_type_info(str(v).strip()):
            return str(v).strip(), "expliciet"
    # 2) trefwoord-heuristiek over alle type-achtige waarden
    haystack = " ".join(str(lower[k]).lower() for k in _TYPE_KEYS if lower.get(k))
    for kw, code in _IMBOR_KEYWORDS:
        if kw in haystack:
            return code, "trefwoord"
    # 3) opgegeven default
    if default_type and get_type_info(default_type):
        return default_type, "default"
    # 4) fallback op geometrie-type
    if geom_type in ("LineString", "MultiLineString"):
        return "wegvak_asfalt", "fallback"
    return "overig", "fallback"


def _is_rd(pt: list[float]) -> bool:
    return (_RD_X_RANGE[0] <= pt[0] <= _RD_X_RANGE[1]
            and _RD_Y_RANGE[0] <= pt[1] <= _RD_Y_RANGE[1])


def _rd_point(pt: list[float]) -> list[float]:
    lat, lng = _rd_to_wgs84(pt[0], pt[1])
    return [lng, lat]


def _convert_geometry(geom: dict) -> Optional[dict]:
    """Normaliseer een GeoJSON-geometrie naar WGS84.

    Returns dict met:
      type:  'Point' | 'LineString' | 'MultiLineString'  (Polygon/MultiPolygon
             worden als Point-centroid teruggegeven)
      parts: list van coordinaten-lijsten ([lng,lat]) per onderdeel
      flat:  alle coordinaten plat (voor centroid/bbox)
      was_rd: of RD->WGS84 conversie is toegepast
    of None als de geometrie onbruikbaar is.
    """
    t = geom.get("type")
    c = geom.get("coordinates")
    if not isinstance(c, (list, tuple)):
        return None
    try:
        if t == "Point":
            if len(c) < 2:
                return None
            pt = [float(c[0]), float(c[1])]
            rd = _is_rd(pt)
            if rd:
                pt = _rd_point(pt)
            return {"type": t, "parts": [[pt]], "flat": [pt], "was_rd": rd}
        if t == "LineString":
            pts = [[float(p[0]), float(p[1])] for p in c if isinstance(p, (list, tuple)) and len(p) >= 2]
            if len(pts) < 2:
                return None
            rd = _is_rd(pts[0])
            if rd:
                pts = [_rd_point(p) for p in pts]
            return {"type": t, "parts": [pts], "flat": pts, "was_rd": rd}
        if t == "MultiLineString":
            parts: list[list[list[float]]] = []
            for line in c:
                if not isinstance(line, (list, tuple)):
                    continue
                pts = [[float(p[0]), float(p[1])] for p in line if isinstance(p, (list, tuple)) and len(p) >= 2]
                if len(pts) >= 2:
                    parts.append(pts)
            if not parts:
                return None
            rd = _is_rd(parts[0][0])
            if rd:
                parts = [[_rd_point(p) for p in part] for part in parts]
            flat = [p for part in parts for p in part]
            return {"type": t, "parts": parts, "flat": flat, "was_rd": rd}
        if t in ("Polygon", "MultiPolygon"):
            # Vlak-objecten (parkeervlak, plein, ...) -> centroid als punt-asset.
            rings = c if t == "Polygon" else [ring for poly in c for ring in poly]
            pts = [[float(p[0]), float(p[1])]
                   for ring in rings if isinstance(ring, (list, tuple))
                   for p in ring if isinstance(p, (list, tuple)) and len(p) >= 2]
            if not pts:
                return None
            rd = _is_rd(pts[0])
            if rd:
                pts = [_rd_point(p) for p in pts]
            cx = round(sum(p[0] for p in pts) / len(pts), 7)
            cy = round(sum(p[1] for p in pts) / len(pts), 7)
            return {"type": "Point", "parts": [[[cx, cy]]], "flat": pts, "was_rd": rd}
    except (TypeError, ValueError, IndexError):
        return None
    return None


def _stored_geometry(conv: dict) -> Optional[str]:
    """GeoJSON-string van de (geconverteerde) lijn-geometrie voor opslag."""
    if conv["type"] == "LineString":
        geom = {"type": "LineString", "coordinates": conv["parts"][0]}
    elif conv["type"] == "MultiLineString":
        geom = {"type": "MultiLineString", "coordinates": conv["parts"]}
    else:
        return None
    return json.dumps(geom, separators=(",", ":"))


def _jsonsafe(v):
    """Maak een DBF-waarde JSON-serialiseerbaar (datums/bytes -> string)."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return str(v)


def _shapefile_to_features(raw: bytes, filename: str) -> list[dict]:
    """Lees een shapefile (.zip met .shp/.dbf/.shx, of los .shp) en geef een
    GeoJSON-achtige feature-lijst terug. Coordinaten blijven zoals ze zijn —
    RD wordt later in _convert_geometry herkend en omgezet."""
    try:
        import shapefile  # pyshp
    except ImportError:
        raise HTTPException(status_code=500,
                            detail="Shapefile-import niet beschikbaar (pyshp ontbreekt)")

    shp = dbf = shx = None
    if zipfile.is_zipfile(io.BytesIO(raw)):
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for n in zf.namelist():
                low = n.lower()
                if low.endswith(".shp"):
                    shp = io.BytesIO(zf.read(n))
                elif low.endswith(".dbf"):
                    dbf = io.BytesIO(zf.read(n))
                elif low.endswith(".shx"):
                    shx = io.BytesIO(zf.read(n))
        if shp is None:
            raise HTTPException(status_code=400, detail="ZIP bevat geen .shp-bestand")
    else:
        shp = io.BytesIO(raw)

    try:
        reader = shapefile.Reader(shp=shp, dbf=dbf, shx=shx)
        features: list[dict] = []
        if dbf is not None:
            for sr in reader.shapeRecords():
                rec = sr.record.as_dict() if sr.record is not None else {}
                props = {k: _jsonsafe(v) for k, v in rec.items()}
                features.append({"type": "Feature",
                                 "geometry": sr.shape.__geo_interface__,
                                 "properties": props})
        else:
            for shape in reader.shapes():
                features.append({"type": "Feature",
                                 "geometry": shape.__geo_interface__,
                                 "properties": {}})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Kan shapefile niet lezen: {e}")
    return features


def _process_features(db: Session, current_user: User, request: Request, features: list, *,
                      project_id: Optional[str], default_type: Optional[str],
                      code_prefix: str, dry_run: bool, filename: Optional[str], fmt: str) -> dict:
    """Gedeelde import-pipeline voor GeoJSON- én shapefile-features."""
    if len(features) > _MAX_FEATURES:
        raise HTTPException(status_code=400,
                            detail=f"Te veel features ({len(features)}); max {_MAX_FEATURES} per import")

    created = skipped = 0
    errors: list[dict] = []
    by_type: dict[str, int] = {}
    preview: list[dict] = []
    seq = 0

    existing_codes = {
        row[0] for row in
        db.query(Asset.code).filter(Asset.organization_id == current_user.organization_id)
    }
    seen_codes: set[str] = set()

    for i, feat in enumerate(features, start=1):
        if not isinstance(feat, dict):
            errors.append({"feature": i, "error": "feature is geen object"})
            continue
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        props = feat.get("properties") or {}
        if not isinstance(props, dict):
            props = {}

        conv = _convert_geometry(geom)
        if not conv:
            errors.append({"feature": i, "error": f"geen bruikbare geometrie (type={gtype})"})
            continue

        flat = conv["flat"]
        lngs = [p[0] for p in flat]
        lats = [p[1] for p in flat]
        lng = round(sum(lngs) / len(lngs), 7)
        lat = round(sum(lats) / len(lats), 7)
        bbox = [round(min(lngs), 7), round(min(lats), 7), round(max(lngs), 7), round(max(lats), 7)]

        is_line = conv["type"] in ("LineString", "MultiLineString")
        length_m = round(sum(linestring_length_m(part) for part in conv["parts"]), 1) if is_line else None

        asset_type, herkomst = classify_imbor(props, conv["type"], default_type)

        code = _first_prop(props, _CODE_KEYS)
        if not code:
            seq += 1
            code = f"{code_prefix}-{seq:04d}"

        if code in existing_codes or code in seen_codes:
            skipped += 1
            continue
        seen_codes.add(code)

        name = _first_prop(props, _NAME_KEYS)
        by_type[asset_type] = by_type.get(asset_type, 0) + 1
        if len(preview) < 10:
            preview.append({
                "feature": i, "code": code, "asset_type": asset_type, "herkomst": herkomst,
                "geom": conv["type"], "lat": lat, "lng": lng, "length_m": length_m,
                "naam": name, "rd_geconverteerd": conv["was_rd"],
            })

        if not dry_run:
            extra = dict(props)
            extra["bbox"] = bbox
            extra["_import"] = {
                "bron": fmt, "bestand": filename,
                "imbor_herkomst": herkomst, "rd_geconverteerd": conv["was_rd"],
            }
            db.add(Asset(
                code=code,
                name=name,
                asset_type=asset_type,
                lat=lat, lng=lng,
                location_description=name,
                project_id=project_id or None,
                organization_id=current_user.organization_id,
                created_by=current_user.id,
                geometry_geojson=_stored_geometry(conv),
                length_m=length_m,
                is_segment=is_line,
                properties_json=json.dumps(extra, ensure_ascii=False),
            ))
        created += 1

    if dry_run:
        db.rollback()
        return {
            "dry_run": True, "would_create": created, "skipped": skipped,
            "errors": errors, "total_features": len(features),
            "by_type": by_type, "preview": preview,
        }

    db.commit()
    log_action(db, request, current_user,
               action=ACTION.ASSET_BULK_IMPORT, entity_type="asset",
               extra={"created": created, "skipped": skipped, "errors": len(errors),
                      "filename": filename, "format": fmt, "by_type": by_type})
    return {
        "created": created, "skipped": skipped, "errors": errors,
        "total_features": len(features), "by_type": by_type, "preview": preview,
    }


@router.post("/geojson")
async def import_geojson(
    request: Request,
    file: UploadFile = File(..., description="GeoJSON FeatureCollection (WGS84 of RD/EPSG:28992)."),
    project_id: Optional[str] = Form(None),
    default_type: Optional[str] = Form(None, description="IMBOR-code als fallback-type."),
    code_prefix: str = Form("WV", description="Prefix voor auto-gegenereerde codes."),
    dry_run: bool = Form(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not can_manage_assets(current_user):
        raise HTTPException(status_code=403, detail="Geen rechten voor import")

    raw = await file.read()
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Ongeldig GeoJSON: {e}")

    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise HTTPException(status_code=400, detail="Verwacht een GeoJSON FeatureCollection")

    return _process_features(
        db, current_user, request, features,
        project_id=project_id, default_type=default_type,
        code_prefix=code_prefix, dry_run=dry_run, filename=file.filename, fmt="geojson",
    )


@router.post("/shapefile")
async def import_shapefile(
    request: Request,
    file: UploadFile = File(..., description="Shapefile als .zip (.shp + .dbf + .shx) of los .shp."),
    project_id: Optional[str] = Form(None),
    default_type: Optional[str] = Form(None, description="IMBOR-code als fallback-type."),
    code_prefix: str = Form("WV", description="Prefix voor auto-gegenereerde codes."),
    dry_run: bool = Form(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not can_manage_assets(current_user):
        raise HTTPException(status_code=403, detail="Geen rechten voor import")

    raw = await file.read()
    features = _shapefile_to_features(raw, file.filename or "")
    return _process_features(
        db, current_user, request, features,
        project_id=project_id, default_type=default_type,
        code_prefix=code_prefix, dry_run=dry_run, filename=file.filename, fmt="shapefile",
    )
