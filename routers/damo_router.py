"""DAMO-koppeling — waterschap-equivalent van NWB voor wegen.

DAMO (Drinkwater, Afval & Milieu Object-model) is de open IMWA-Watergeven
data-standaard voor waterschappen — kunstwerken, watergangen, kunstwerken-
in-watergangen, gemalen, stuwen. Aangeleverd via PDOK Geoserver WFS.

Endpoints:
  GET /api/damo/zoek?gemeente=...           Zoek DAMO-objecten per gemeente
  GET /api/damo/kunstwerken/{id}            Detail kunstwerk-in-watergang
  POST /api/damo/import-kunstwerken         Bulk-import als Asset

Bron: PDOK WFS Geoserver
  https://geodata.nationaalgeoregister.nl/damo/wfs

Productie-versie: caching + bulk-import-job voor 1000+ objecten.
MVP: directe calls + per-object processing.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
import httpx

from database import get_db
from models import User, Asset
from auth import get_current_user
from audit import log_action

router = APIRouter(prefix="/api/damo", tags=["DAMO / Waterschap"])

DAMO_WFS_BASE = "https://geodata.nationaalgeoregister.nl/damo/wfs"
HTTP_TIMEOUT = 10.0


# Mapping van DAMO featureType → onze asset_type
DAMO_TYPE_MAP = {
    "damo:Gemaal": "gemaal",
    "damo:Stuw": "stuw",
    "damo:Sluis": "sluis",
    "damo:Kunstwerk": "kunstwerk",
    "damo:Duiker": "duiker",
    "damo:Brug": "brug",
}


@router.get("/featuretypes")
def list_damo_featuretypes(current_user: User = Depends(get_current_user)):
    """Beschikbare DAMO feature-types voor import."""
    return {
        "features": [
            {"name": k, "asset_type": v, "url": DAMO_WFS_BASE + f"?typeName={k}"}
            for k, v in DAMO_TYPE_MAP.items()
        ],
        "wfs_endpoint": DAMO_WFS_BASE,
        "standard": "DAMO 2.0 / IMWA-Watergeven",
    }


@router.get("/zoek")
async def damo_zoek(
    bbox: str = Query(..., description="WGS84 minLon,minLat,maxLon,maxLat"),
    typeName: str = Query("damo:Kunstwerk"),
    max_features: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
):
    """Zoek DAMO-objecten binnen een bounding box.

    Output: GeoJSON-FeatureCollection (gestandaardiseerd voor map-display).

    Voorbeeld bbox voor Westland: 4.10,51.93,4.30,52.04
    """
    if typeName not in DAMO_TYPE_MAP:
        raise HTTPException(status_code=400,
                            detail=f"typeName moet één van: {list(DAMO_TYPE_MAP)}")

    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeName": typeName,
        "bbox": bbox + ",EPSG:4326",
        "outputFormat": "application/json",
        "count": max_features,
        "srsName": "EPSG:4326",
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as cli:
            r = await cli.get(DAMO_WFS_BASE, params=params)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"DAMO niet bereikbaar: {e}")

    return {
        "type": "FeatureCollection",
        "feature_type": typeName,
        "count": len(data.get("features", [])),
        "bbox": bbox,
        "features": data.get("features", []),
        "provider": "PDOK DAMO WFS 2.0",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Bulk-import van DAMO-features als Assets
# ─────────────────────────────────────────────────────────────────────────────

class DamoImportRequest(BaseModel):
    bbox: str
    typeName: str = "damo:Kunstwerk"
    max_features: int = 100
    project_id: Optional[str] = None
    dry_run: bool = False


@router.post("/import-kunstwerken")
async def import_damo_kunstwerken(
    payload: DamoImportRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bulk-import DAMO-features binnen bbox als Assets.

    RBAC: alleen admin of manager.
    Idempotent: bij bestaande code (asset_code = DAMO feature-ID) wordt
    bestaand record bijgewerkt, niet gedupliceerd.
    """
    if current_user.role not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Alleen admin of manager")
    if payload.typeName not in DAMO_TYPE_MAP:
        raise HTTPException(status_code=400, detail="Onbekend typeName")

    asset_type = DAMO_TYPE_MAP[payload.typeName]

    # Haal features op via WFS
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeName": payload.typeName, "bbox": payload.bbox + ",EPSG:4326",
        "outputFormat": "application/json", "count": payload.max_features,
        "srsName": "EPSG:4326",
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as cli:
            r = await cli.get(DAMO_WFS_BASE, params=params)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"DAMO niet bereikbaar: {e}")

    features = data.get("features", [])
    created = 0
    updated = 0

    for f in features:
        props = f.get("properties", {})
        geom = f.get("geometry", {})
        feature_id = f.get("id") or props.get("identificatie") or props.get("OBJECTID")
        if not feature_id:
            continue

        # Centroid uit geometry (Point/Polygon)
        lat, lng = None, None
        if geom.get("type") == "Point":
            coords = geom.get("coordinates", [])
            if len(coords) >= 2:
                lng, lat = coords[0], coords[1]
        elif geom.get("type") == "Polygon":
            # Simpele centroïde — gemiddelde van eerste ring
            ring = geom.get("coordinates", [[]])[0]
            if ring:
                lng = sum(p[0] for p in ring) / len(ring)
                lat = sum(p[1] for p in ring) / len(ring)

        code = f"DAMO-{feature_id}"
        if payload.dry_run:
            created += 1
            continue

        existing = db.query(Asset).filter(
            Asset.organization_id == current_user.organization_id,
            Asset.code == code,
        ).first()
        if existing:
            if lat: existing.lat = lat
            if lng: existing.lng = lng
            updated += 1
        else:
            new_asset = Asset(
                code=code,
                name=props.get("naam") or props.get("Naam") or f"{asset_type.title()} {feature_id}",
                asset_type=asset_type,
                lat=lat, lng=lng,
                project_id=payload.project_id,
                organization_id=current_user.organization_id,
                created_by=current_user.id,
            )
            db.add(new_asset)
            created += 1

    if not payload.dry_run:
        db.commit()
        log_action(db, None, current_user,
                   action="damo.import",
                   entity_type="asset",
                   extra={"created": created, "updated": updated,
                          "typeName": payload.typeName, "bbox": payload.bbox})

    return {
        "ok": True,
        "dry_run": payload.dry_run,
        "fetched": len(features),
        "created": created,
        "updated": updated,
        "asset_type": asset_type,
        "provider": "PDOK DAMO WFS 2.0",
    }
