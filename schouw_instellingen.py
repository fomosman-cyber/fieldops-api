"""Instellingen voor de beeldherkenning van de schouw, per organisatie.

Wat de camera herkent en hoe streng, stelt de beheerder van een organisatie in.
Een aannemer die alleen asfalt onderhoudt, hoeft de camera niet naar klinkers
te laten kijken; minder herkennen is sneller en goedkoper.

**De standaard is het huidige gedrag.** Een organisatie die niets instelt,
merkt niets.

**Grenzen die niet over te stellen zijn.** Verpixelen van mensen kan strenger
dan de standaard, nooit soepeler: een gemist gezicht is geen instelling maar
een privacylek. Het automatisch meetellen van waarnemingen kan niet onder 70%:
daaronder is de score meer model dan waarneming.

De camera-instellingen (lens, zoom, beeldgrootte, ritme) zijn per toestel en
staan in het scherm zelf, niet hier.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import crow_wegschade as cw
import schouw_vision as sv

GRONDIGHEID = {
    "snel": {"naam": "Snel", "effort": "low",
             "uitleg": "Een beeld per paar seconden; goed genoeg voor de meeste schouwen."},
    "grondig": {"naam": "Grondig", "effort": "medium",
                "uitleg": "Beter oordeel over twijfelgevallen, een paar seconden langer per beeld."},
}

STANDAARD: dict[str, Any] = {
    "objecttypen": list(sv.OBJECT_TYPES),
    "verhardingen": list(cw.VERHARDINGEN),
    "grondigheid": "snel",
    "drempel_automatisch": sv.DREMPEL_AUTOMATISCH,
    "max_objecten": sv.MAX_OBJECTEN_PER_BEELD,
    "verpixel_mens": 0.30,
    "verpixel_voertuig": 0.50,
}

# (minimum, maximum) per getal. Voor verpixelen: een LAGERE drempel is
# strenger (er wordt eerder verpixeld). Mensen kunnen dus alleen strenger.
GRENZEN: dict[str, tuple[float, float]] = {
    "drempel_automatisch": (0.70, 0.95),
    "max_objecten": (0, sv.MAX_OBJECTEN_PER_BEELD),
    "verpixel_mens": (0.10, 0.30),
    "verpixel_voertuig": (0.30, 0.70),
}


class OngeldigeInstelling(ValueError):
    """Een instelling die niet bestaat of buiten de grenzen valt."""


def valideer(invoer: dict) -> dict:
    """Controleer een nieuwe set instellingen en vul aan met de standaard.

    Onbekende sleutels en waarden buiten de grenzen worden geweigerd, niet
    stilletjes bijgeknipt: wie 0,6 invult en 0,7 krijgt, denkt dat hij iets
    anders heeft ingesteld dan er gebeurt.
    """
    if not isinstance(invoer, dict):
        raise OngeldigeInstelling("Instellingen moeten een object zijn")
    onbekend = set(invoer) - set(STANDAARD)
    if onbekend:
        raise OngeldigeInstelling(f"Onbekende instelling: {', '.join(sorted(onbekend))}")

    uit = dict(STANDAARD)
    uit.update(invoer)

    if not isinstance(uit["objecttypen"], list):
        raise OngeldigeInstelling("objecttypen moet een lijst zijn")
    vreemd = [t for t in uit["objecttypen"] if t not in sv.OBJECT_TYPES]
    if vreemd:
        raise OngeldigeInstelling(f"Onbekend objecttype: {', '.join(map(str, vreemd))}")
    uit["objecttypen"] = [t for t in sv.OBJECT_TYPES if t in uit["objecttypen"]]

    if not isinstance(uit["verhardingen"], list):
        raise OngeldigeInstelling("verhardingen moet een lijst zijn")
    vreemd = [v for v in uit["verhardingen"] if v not in cw.VERHARDINGEN]
    if vreemd:
        raise OngeldigeInstelling(f"Onbekende verharding: {', '.join(map(str, vreemd))}")
    uit["verhardingen"] = [v for v in cw.VERHARDINGEN if v in uit["verhardingen"]]

    if uit["grondigheid"] not in GRONDIGHEID:
        raise OngeldigeInstelling("grondigheid moet 'snel' of 'grondig' zijn")

    for sleutel, (laag, hoog) in GRENZEN.items():
        waarde = uit[sleutel]
        if isinstance(waarde, bool) or not isinstance(waarde, (int, float)):
            raise OngeldigeInstelling(f"{sleutel} moet een getal zijn")
        if not laag <= waarde <= hoog:
            raise OngeldigeInstelling(f"{sleutel} moet tussen {laag:g} en {hoog:g} liggen")
    uit["max_objecten"] = int(uit["max_objecten"])
    return uit


def lees(org: Optional[Any]) -> dict:
    """De instellingen van een organisatie, of de standaard.

    Een kapotte of verouderde opgeslagen instelling mag de schouw niet slopen:
    dan geldt de standaard, zoals bij de grenswaarden.
    """
    ruw = getattr(org, "schouw_instellingen", None) if org is not None else None
    if not ruw:
        return dict(STANDAARD)
    try:
        return valideer(json.loads(ruw))
    except (ValueError, TypeError):
        return dict(STANDAARD)


def beschrijving() -> dict:
    """Wat het scherm nodig heeft om de instellingen te tonen."""
    return {
        "objecttypen": [{"code": t, "naam": sv.OBJECT_NAMEN.get(t, t)} for t in sv.OBJECT_TYPES],
        "verhardingen": [{"code": k, "naam": v["naam"]} for k, v in cw.VERHARDINGEN.items()],
        "grondigheid": [{"code": k, "naam": v["naam"], "uitleg": v["uitleg"]}
                        for k, v in GRONDIGHEID.items()],
        "grenzen": {k: {"min": a, "max": b} for k, (a, b) in GRENZEN.items()},
        "standaard": STANDAARD,
    }
