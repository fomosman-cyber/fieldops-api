"""Config-endpoints voor frontend-runtime — public, geen auth.

Bevat alleen waarden die OK zijn om in de browser te leven (Google Maps API
key is bedoeld voor client-side; restrict 'm in Google Cloud Console op je
domein zodat hij niet elders bruikbaar is)."""

import os
from fastapi import APIRouter

router = APIRouter(prefix="/api/config", tags=["Config"])


@router.get("/maps-key")
def maps_key():
    """Google Maps Platform API-key voor Places autocomplete + Street View.

    Configureer via Google Cloud Console:
    1. Maak nieuwe API-key
    2. Restrict "Application restrictions" → HTTP referrers → *.fieldopsapp.nl/*
    3. Restrict "API restrictions" → alleen 'Places API', 'Maps JavaScript API'
    4. Plaats key in Render env: GOOGLE_MAPS_API_KEY=...
    """
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    return {
        "key": key or None,
        "configured": bool(key),
        "libraries": ["places"],
    }
