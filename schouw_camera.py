"""Camera-instellingen van de schouw: hoe de telefoon opneemt.

Dit is de tegenhanger van ``schouw_instellingen``. Dat bestand gaat over wat de
camera *herkent* en geldt voor de hele organisatie; dit gaat over hoe hij
*opneemt* en volgt de gebruiker.

**Waarom ze de gebruiker volgen en niet het toestel.** Ze stonden alleen in de
browser van dat ene toestel. Een nieuwe telefoon, een gewiste browser of een
tweede toestel begon weer op de standaard, en een ploeg was niet in één keer
goed te zetten. Wie zijn beeldgrootte en ritme een keer goed heeft, hoort dat
niet opnieuw te hoeven uitzoeken.

**De lens is de uitzondering en staat hier niet in.** Een lens is een
``deviceId`` van de browser: op een ander toestel wijst hij nergens naar. Die
blijft lokaal.

**De organisatie mag een startpunt zetten.** Wie zelf nog niets heeft
ingesteld, krijgt de standaard van zijn organisatie. Zodra hij zelf iets kiest
is dat van hem, ook als de beheerder de organisatiestandaard later wijzigt --
anders verschuift onder een lopende rit de manier van opnemen.
"""

from __future__ import annotations

import json
from typing import Any, Optional

# Wat het scherm aanbiedt. Alleen deze waarden worden aangenomen: een
# beeldgrootte die de telefoon niet kent levert een zwart beeld, en een ritme
# dat niemand herkent laat de opname stilstaan.
GROOTTES: tuple[int, ...] = (960, 1280, 1920)
RITMES: tuple[str, ...] = ("tijd-3", "tijd-5", "tijd-10",
                           "afstand-5", "afstand-10", "afstand-25")
RIJAFSTANDEN: tuple[int, ...] = (5, 10, 15, 20)

STANDAARD: dict[str, Any] = {
    "grootte": 1280,
    "ritme": "tijd-3",
    "scherp": True,
    "zelfde": True,
    "rijAfstand": 10,
    "wegdekBoven": 0.4,
    # Leeg = de camera houdt zijn eigen zoom. Een getal is wat de gebruiker
    # heeft ingesteld; het bereik verschilt per toestel, dus bij het toepassen
    # wordt het bijgeknipt tot wat die camera kan.
    "zoom": None,
}

# Grenzen voor de getallen die geen vaste keuzelijst hebben.
WEGDEK_MIN, WEGDEK_MAX = 0.3, 0.6
ZOOM_MAX = 20.0


class OngeldigeInstelling(ValueError):
    """Een instelling die niet bestaat of buiten de grenzen valt."""


def valideer(invoer: Any) -> dict:
    """Controleer een set instellingen en vul aan met de standaard.

    Onbekende sleutels worden geweigerd in plaats van genegeerd. Dat is
    dezelfde keuze als bij de herkenning: wie iets instuurt wat niet wordt
    opgeslagen, denkt dat hij iets heeft vastgelegd wat er niet staat.
    """
    if not isinstance(invoer, dict):
        raise OngeldigeInstelling("Instellingen moeten een object zijn")
    onbekend = set(invoer) - set(STANDAARD)
    if onbekend:
        raise OngeldigeInstelling(f"Onbekende instelling: {', '.join(sorted(onbekend))}")

    uit = dict(STANDAARD)
    uit.update(invoer)

    if uit["grootte"] not in GROOTTES:
        raise OngeldigeInstelling(f"grootte moet een van {GROOTTES} zijn")
    if uit["ritme"] not in RITMES:
        raise OngeldigeInstelling(f"ritme moet een van {RITMES} zijn")
    if uit["rijAfstand"] not in RIJAFSTANDEN:
        raise OngeldigeInstelling(f"rijAfstand moet een van {RIJAFSTANDEN} zijn")

    for sleutel in ("scherp", "zelfde"):
        if not isinstance(uit[sleutel], bool):
            raise OngeldigeInstelling(f"{sleutel} moet ja of nee zijn")

    wegdek = uit["wegdekBoven"]
    if isinstance(wegdek, bool) or not isinstance(wegdek, (int, float)):
        raise OngeldigeInstelling("wegdekBoven moet een getal zijn")
    if not WEGDEK_MIN <= wegdek <= WEGDEK_MAX:
        raise OngeldigeInstelling(
            f"wegdekBoven moet tussen {WEGDEK_MIN:g} en {WEGDEK_MAX:g} liggen")
    uit["wegdekBoven"] = round(float(wegdek), 2)

    zoom = uit["zoom"]
    if zoom is not None:
        if isinstance(zoom, bool) or not isinstance(zoom, (int, float)):
            raise OngeldigeInstelling("zoom moet een getal zijn of leeg")
        if not 0 < zoom <= ZOOM_MAX:
            raise OngeldigeInstelling(f"zoom moet tussen 0 en {ZOOM_MAX:g} liggen")
        uit["zoom"] = round(float(zoom), 2)

    uit["grootte"] = int(uit["grootte"])
    uit["rijAfstand"] = int(uit["rijAfstand"])
    return uit


def _lees_json(ruw: Optional[str]) -> Optional[dict]:
    """Een opgeslagen set, of None als er niets bruikbaars staat.

    Een kapotte of verouderde opgeslagen waarde mag de schouw niet slopen:
    dan geldt wat eronder ligt, net als bij de herkenning.
    """
    if not ruw:
        return None
    try:
        waarde = json.loads(ruw)
    except (ValueError, TypeError):
        return None
    if not isinstance(waarde, dict):
        return None
    try:
        return valideer({k: v for k, v in waarde.items() if k in STANDAARD})
    except OngeldigeInstelling:
        return None


def organisatie_standaard(org: Optional[Any]) -> Optional[dict]:
    """Het startpunt dat de beheerder heeft gezet, of None."""
    return _lees_json(getattr(org, "schouw_camera_standaard", None) if org else None)


def lees(user: Optional[Any], org: Optional[Any] = None) -> dict:
    """De instellingen die voor deze gebruiker gelden.

    Eigen keuze gaat voor de organisatiestandaard, en die gaat voor de
    standaard van het pakket.
    """
    eigen = _lees_json(getattr(user, "schouw_camera", None) if user else None)
    if eigen is not None:
        return eigen
    van_org = organisatie_standaard(org)
    if van_org is not None:
        return van_org
    return dict(STANDAARD)
