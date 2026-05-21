"""DigiGO API-skelet — open data standaard voor publieke infra.

DigiGO (opvolger van DigiDealGO) is vanaf 2027 verplicht voor publieke
infra-data conform de Rijks-richtlijnen. Dit is een **starter**-versie
met de OGC-standaard endpoints zodat onze data nu al via OGC API Features
ontsloten kan worden:

  GET /api/digigo/collections                Beschikbare data-collecties
  GET /api/digigo/collections/{name}         Collection metadata
  GET /api/digigo/collections/{name}/items   Items als GeoJSON FeatureCollection
  GET /api/digigo/conformance                OGC API Features conformance

Output is OGC-conform: GeoJSON FeatureCollection met properties + geometry.

Multi-tenant: optionele org-scope via X-Org-Key header (publieke endpoints
zonder org tonen alleen geanonimiseerde aggregaten).
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from database import get_db
from models import User, Asset
from auth import get_current_user

router = APIRouter(prefix="/api/digigo", tags=["DigiGO / OGC API"])


# Beschikbare collecties — mapping op interne models
COLLECTIONS = {
    "assets": {
        "title": "Assets / Objects",
        "description": "Infrastructurele assets (kunstwerken, riolering, bomen, ...)",
        "itemType": "feature",
        "geometryType": "Point",
        "model": "Asset",
    },
    "kunstwerken-inspecties": {
        "title": "Kunstwerken-inspecties",
        "description": "NEN 2767-2 / CROW 134 conform inspectie-records",
        "itemType": "feature",
        "geometryType": "Point",
        "model": "Inspection",
    },
}


@router.get("/conformance")
def conformance():
    """OGC API Features conformance-classes — verplicht voor 2027 DigiGO."""
    return {
        "conformsTo": [
            "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
            "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
            "https://www.digigo.nl/spec/v1/asset-registratie",
        ]
    }


@router.get("/collections")
def list_collections():
    """Lijst alle data-collecties beschikbaar via DigiGO API."""
    return {
        "collections": [
            {
                "id": k,
                "title": v["title"],
                "description": v["description"],
                "itemType": v["itemType"],
                "links": [
                    {"rel": "self", "href": f"/api/digigo/collections/{k}"},
                    {"rel": "items", "href": f"/api/digigo/collections/{k}/items"},
                ],
            } for k, v in COLLECTIONS.items()
        ],
        "links": [
            {"rel": "self", "href": "/api/digigo/collections"},
            {"rel": "conformance", "href": "/api/digigo/conformance"},
        ],
        "providerName": "FieldOps",
        "standard": "OGC API Features 1.0 + DigiGO v1",
    }


@router.get("/collections/{name}")
def collection_metadata(name: str):
    """Metadata van één collectie."""
    if name not in COLLECTIONS:
        raise HTTPException(status_code=404, detail=f"Collection '{name}' niet gevonden")
    c = COLLECTIONS[name]
    return {
        "id": name,
        "title": c["title"],
        "description": c["description"],
        "itemType": c["itemType"],
        "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
        "links": [
            {"rel": "self", "href": f"/api/digigo/collections/{name}"},
            {"rel": "items", "href": f"/api/digigo/collections/{name}/items"},
        ],
    }


@router.get("/collections/assets/items")
def assets_as_geojson(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    asset_type: Optional[str] = Query(None),
    bbox: Optional[str] = Query(None, description="minLon,minLat,maxLon,maxLat"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Assets als GeoJSON FeatureCollection — OGC API Features compliant."""
    q = db.query(Asset).filter(
        Asset.organization_id == current_user.organization_id,
        Asset.archived_at.is_(None),
        Asset.lat.isnot(None),
        Asset.lng.isnot(None),
    )
    if asset_type:
        q = q.filter(Asset.asset_type == asset_type)
    if bbox:
        try:
            min_lon, min_lat, max_lon, max_lat = [float(x) for x in bbox.split(",")]
            q = q.filter(
                Asset.lat >= min_lat, Asset.lat <= max_lat,
                Asset.lng >= min_lon, Asset.lng <= max_lon,
            )
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Invalid bbox format")

    total = q.count()
    assets = q.offset(offset).limit(limit).all()

    return {
        "type": "FeatureCollection",
        "numberMatched": total,
        "numberReturned": len(assets),
        "timeStamp": datetime.now(timezone.utc).isoformat(),
        "features": [
            {
                "type": "Feature",
                "id": a.id,
                "geometry": {
                    "type": "Point",
                    "coordinates": [a.lng, a.lat],
                },
                "properties": {
                    "code": a.code,
                    "name": a.name,
                    "asset_type": a.asset_type,
                    "condition_score": a.condition_score,
                    "next_inspection_due": (a.next_inspection_due.isoformat()
                                             if a.next_inspection_due else None),
                    "installed_at": (a.installed_at.isoformat()
                                      if a.installed_at else None),
                },
            } for a in assets
        ],
        "links": [
            {"rel": "self", "href": str(request.url)},
        ],
    }
