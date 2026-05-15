"""BAG-koppeling — adres-zoekfunctie via PDOK Locatieserver.

De BAG (Basisregistratie Adressen en Gebouwen) is een open Rijksregister
beheerd door het Kadaster. PDOK biedt een publieke Locatieserver-API
zonder auth-vereisten — ideaal voor adres-suggesties bij asset-creatie.

Endpoints:
  GET /api/bag/suggest?q=<query>       Type-ahead suggesties (adres / postcode / plaats)
  GET /api/bag/lookup?id=<adresid>     Volledige adres-details met coördinaten
  GET /api/bag/reverse?lat=&lng=       Reverse-geocode coördinaat naar adres

PDOK Locatieserver-docs: https://www.pdok.nl/restful-api/locatieserver
Tarief: gratis, geen rate-limit voor normaal gebruik.

Multi-tenant: deze endpoints zijn alleen-lezen, geen org-scope nodig.
RBAC: authenticated users (geen anonieme calls naar onze server).
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import httpx

from models import User
from auth import get_current_user

router = APIRouter(prefix="/api/bag", tags=["BAG / PDOK"])

PDOK_BASE = "https://api.pdok.nl/bzk/locatieserver/search/v3_1"
HTTP_TIMEOUT = 8.0  # PDOK is meestal snel; bij meer dan 8s val terug


@router.get("/suggest")
async def bag_suggest(
    q: str = Query(..., min_length=2, max_length=120),
    rows: int = Query(10, ge=1, le=20),
    type_filter: Optional[str] = Query(None, description="adres|postcode|woonplaats"),
    current_user: User = Depends(get_current_user),
):
    """Type-ahead suggesties via PDOK Locatieserver `/suggest`.

    Voorbeeld: q='Westland' → suggesties met administratieve eenheden,
    plaatsen, straten. Use voor adres-autocomplete in asset-creatie.
    """
    params = {"q": q, "rows": rows, "df": "tekst"}
    if type_filter:
        params["fq"] = f"type:{type_filter}"
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as cli:
            r = await cli.get(f"{PDOK_BASE}/suggest", params=params)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"PDOK niet bereikbaar: {e}")

    # Normalize voor frontend
    docs = (data.get("response") or {}).get("docs") or []
    return {
        "query": q,
        "count": len(docs),
        "items": [
            {
                "id": d.get("id"),
                "type": d.get("type"),
                "weergavenaam": d.get("weergavenaam"),
                "score": d.get("score"),
            } for d in docs
        ],
        "provider": "PDOK Locatieserver v3.1",
    }


@router.get("/lookup")
async def bag_lookup(
    id: str = Query(..., description="PDOK adres-ID uit /suggest"),
    current_user: User = Depends(get_current_user),
):
    """Haal volledige adres-details op (coördinaten WGS84 + RD-coördinaat)."""
    params = {"id": id, "fl": "*"}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as cli:
            r = await cli.get(f"{PDOK_BASE}/lookup", params=params)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"PDOK niet bereikbaar: {e}")

    docs = (data.get("response") or {}).get("docs") or []
    if not docs:
        raise HTTPException(status_code=404, detail="Adres niet gevonden")
    d = docs[0]
    # Centroïde in WGS84 voor map-pin
    cent = d.get("centroide_ll", "")
    lat, lng = None, None
    if cent and cent.startswith("POINT"):
        try:
            inner = cent.replace("POINT(", "").replace(")", "").strip()
            parts = inner.split()
            lng = float(parts[0]); lat = float(parts[1])
        except Exception:
            pass
    return {
        "id": d.get("id"),
        "type": d.get("type"),
        "weergavenaam": d.get("weergavenaam"),
        "straatnaam": d.get("straatnaam"),
        "huisnummer": d.get("huisnummer"),
        "postcode": d.get("postcode"),
        "woonplaatsnaam": d.get("woonplaatsnaam"),
        "gemeentenaam": d.get("gemeentenaam"),
        "provincienaam": d.get("provincienaam"),
        "lat": lat, "lng": lng,
        "bag_nummeraanduiding": d.get("nummeraanduiding_id"),
        "bag_verblijfsobject": d.get("verblijfsobject_id"),
        "provider": "PDOK Locatieserver v3.1 + Kadaster BAG",
    }


@router.get("/reverse")
async def bag_reverse(
    lat: float = Query(...),
    lng: float = Query(...),
    rows: int = Query(5, ge=1, le=20),
    current_user: User = Depends(get_current_user),
):
    """Reverse-geocode WGS84-coördinaat naar nabije adressen.

    Voor inspecteurs die op locatie GPS-coördinaat hebben maar geen
    adres weten — geef top-N nabije adressen terug.
    """
    params = {"lat": lat, "lon": lng, "rows": rows, "type": "adres"}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as cli:
            r = await cli.get(f"{PDOK_BASE}/reverse", params=params)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"PDOK niet bereikbaar: {e}")

    docs = (data.get("response") or {}).get("docs") or []
    return {
        "lat": lat, "lng": lng,
        "count": len(docs),
        "items": [
            {
                "id": d.get("id"),
                "weergavenaam": d.get("weergavenaam"),
                "afstand_m": d.get("afstand"),
                "type": d.get("type"),
            } for d in docs
        ],
        "provider": "PDOK Locatieserver v3.1",
    }
