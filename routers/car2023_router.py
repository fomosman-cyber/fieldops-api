"""CAR2023 beeldkwaliteit-API — wegmeubilair-schouw (F3).

Endpoints:
  GET  /api/car2023/catalogus   klassen + aspecten + wegmeubilair-typen
  POST /api/car2023/score       beeldkwaliteit-eindoordeel voor één object
"""
from fastapi import APIRouter, Depends, HTTPException

import car2023
from auth import get_current_user
from models import User

router = APIRouter(prefix="/api/car2023", tags=["CAR2023 beeldkwaliteit"])


@router.get("/catalogus")
def catalogus(current_user: User = Depends(get_current_user)):
    """Beeldkwaliteitsklassen, schaalbalk-aspecten en wegmeubilair-typen voor de schouw."""
    return {
        "klassen": car2023.BEELDKWALITEIT_KLASSEN,
        "aspecten": car2023.ASPECTEN,
        "types": [
            {"key": k, "naam": v["naam"], "aspecten": v["aspecten"]}
            for k, v in car2023.WEGMEUBILAIR_TYPES.items()
        ],
        "default_ambitie": car2023.DEFAULT_AMBITIE,
    }


@router.post("/score")
def score(payload: dict, current_user: User = Depends(get_current_user)):
    """Bepaal de beeldkwaliteit van één geschouwd object.

    Body: {"objecttype"?: str, "aspect_klassen": {aspect_code: klasse}, "ambitie"?: str}
    Eindoordeel via de worst-aspect-regel; `voldoet` t.o.v. het ambitieniveau.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body moet een object zijn")
    aspect_klassen = payload.get("aspect_klassen") or {}
    if not isinstance(aspect_klassen, dict):
        raise HTTPException(status_code=400, detail="aspect_klassen moet een object zijn")
    ambitie = payload.get("ambitie") or car2023.DEFAULT_AMBITIE
    if ambitie not in car2023.KLASSE_CODES:
        raise HTTPException(status_code=400, detail=f"ambitie moet een van {car2023.KLASSE_CODES} zijn")
    return car2023.beoordeel(payload.get("objecttype"), aspect_klassen, ambitie)
