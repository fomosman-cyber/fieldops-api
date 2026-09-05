"""Validatie van huisstijl-velden (logo + kleur) van een organisatie.

Apart module omdat twee routers dezelfde regels nodig hebben: de org-admin die
zijn eigen huisstijl instelt (`org_router`) en de platform-eigenaar die dat voor
een klant doet (`admin_router`). Zonder gedeelde plek loopt dat uit elkaar en
kan de ene route iets accepteren dat de andere weigert.
"""
from __future__ import annotations

import re

from fastapi import HTTPException

# Een data-URL is ~4/3 van de bytes. 700.000 tekens komt neer op ongeveer
# 500 kB aan afbeelding. Groter wil je niet in een databasekolom hebben:
# het logo wordt bij elke pagina en elk PDF-rapport meegestuurd.
MAX_LOGO_TEKENS = 700_000

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def valideer_logo(waarde: str | None) -> str | None:
    """Geef het logo terug, of een 400 met uitleg waaróm het niet mag."""
    if not waarde:
        return None
    if not waarde.startswith("data:image/"):
        raise HTTPException(
            status_code=400,
            detail="Logo moet een afbeelding zijn (data:image/...)")
    if len(waarde) > MAX_LOGO_TEKENS:
        raise HTTPException(
            status_code=400,
            detail="Logo te groot (max 500 kB). Schaal de afbeelding eerst.")
    return waarde


def valideer_kleur(waarde: str | None) -> str | None:
    """Huisstijlkleur als hex. Wordt als CSS-variabele in het portaal gezet,
    dus alles wat geen hex is zou daar stilzwijgend niets doen."""
    if not waarde:
        return None
    kleur = waarde.strip()
    if not _HEX.match(kleur):
        raise HTTPException(
            status_code=400,
            detail="Kleur moet hex-formaat hebben, bijvoorbeeld #0284c7")
    return kleur
