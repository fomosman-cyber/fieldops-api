"""CROW 146a/b schadebeelden — verharding-inspectie.

CROW 146a: Visuele inspectie van asfalt-verharding
CROW 146b: Visuele inspectie van elementen-verharding (klinkers, betontegels)

Bron: CROW publicatie 146 (2010) — basis voor de Standaard 2015 onderhoudsmethoden.

Per schadebeeld geven we:
  - code: stable id (gebruikt in DB als crow_146_code)
  - naam: NL display-naam
  - klasse_voorbeelden: typische ernst-klassen (L1..E3, niet exclusief)
  - maatregel: advies uit CROW 146-maatregel-bibliotheek
  - gw_term: aansluiting op GWW-kostengids 2024 voor MJOP

Heuristieken (kleur + helderheid + asset.subtype) zijn een MVP-stub.
Productie-grade detectie vraagt computer-vision model (Azure Custom Vision /
GCP AutoML met ~5000 gelabelde foto's per schadebeeld).
"""
from __future__ import annotations
from typing import Optional


# CROW 146a — Asfaltverharding
CROW_146A_SCHADEBEELDEN = [
    {
        "code": "rafeling",
        "naam": "Rafeling",
        "klasse_voorbeelden": ["L1", "L2", "L3"],
        "maatregel": "Slem-coating (L1-L2) of dunne deklaag (L3)",
        "gw_term": "Oppervlaktebehandeling DGAD",
        "uitleg": "Steenslag komt los uit toplaag; rafelvorming aan oppervlak",
    },
    {
        "code": "langsscheur",
        "naam": "Langsscheur (in rij-richting)",
        "klasse_voorbeelden": ["M1", "M2", "M3"],
        "maatregel": "Cold-pour polymeer-vulling (M1-M2) of frees-vul (M3)",
        "gw_term": "Vullen polymeer (cold-pour)",
        "uitleg": "Scheur evenwijdig aan rijrichting, vaak in wielspoor",
    },
    {
        "code": "dwarsscheur",
        "naam": "Dwarsscheur (haaks op rij-richting)",
        "klasse_voorbeelden": ["M1", "M2", "M3"],
        "maatregel": "Polymeer-vulling + monitoring",
        "gw_term": "Vullen polymeer (cold-pour)",
        "uitleg": "Scheur haaks op rijrichting, vaak temperatuur-gerelateerd",
    },
    {
        "code": "scheurnet",
        "naam": "Scheurnet / huidvorming",
        "klasse_voorbeelden": ["M2", "M3", "E1"],
        "maatregel": "Pleksgewijze freesreparatie of dunne deklaag",
        "gw_term": "Pleksgewijze reparatie frees-vul",
        "uitleg": "Netwerk van scheuren — alligator-cracking; structurele schade",
    },
    {
        "code": "kuilvorming",
        "naam": "Kuilvorming (potholes)",
        "klasse_voorbeelden": ["E1", "E2", "E3"],
        "maatregel": "Acute pleksgewijze reparatie (cold-mix of warm-mix)",
        "gw_term": "Pleksgewijze reparatie frees-vul",
        "uitleg": "Gat in oppervlak; veiligheidsrisico voor weggebruikers",
    },
    {
        "code": "spoorvorming",
        "naam": "Spoorvorming (rutting)",
        "klasse_voorbeelden": ["M2", "M3", "E1"],
        "maatregel": "Frees + nieuwe deklaag op aangetast traject",
        "gw_term": "Frezen + asfaltdeklaag",
        "uitleg": "Permanente vervorming onder belasting (zwaar verkeer)",
    },
    {
        "code": "randschade",
        "naam": "Randschade / kantenbreuk",
        "klasse_voorbeelden": ["L2", "L3", "M1"],
        "maatregel": "Kant-vulling + eventueel kantopsluiting verbeteren",
        "gw_term": "Kantopsluiting herstellen",
        "uitleg": "Beschadiging aan rand wegdek, vaak door berm-erosie",
    },
    {
        "code": "voegoverhoogte",
        "naam": "Voegoverhoogte / hellingverlies",
        "klasse_voorbeelden": ["M1", "M2"],
        "maatregel": "Voeg herstellen + verflauwing aanbrengen",
        "gw_term": "Voegherstel",
        "uitleg": "Aansluiting tussen lagen of bij overgangen niet vlak",
    },
]

# CROW 146b — Elementenverharding (klinkers, betontegels)
CROW_146B_SCHADEBEELDEN = [
    {
        "code": "zetting",
        "naam": "Zetting (verzakking in oppervlak)",
        "klasse_voorbeelden": ["L2", "M1", "M2"],
        "maatregel": "Opnieuw straten (lokale herstrating)",
        "gw_term": "Herstratingswerk per m2",
        "uitleg": "Lokale verzakking van straatwerk; vaak na grondwerk eronder",
    },
    {
        "code": "voegvulling_verlies",
        "naam": "Verlies van voegvulling",
        "klasse_voorbeelden": ["L1", "L2"],
        "maatregel": "Voegvulling aanvullen (split of zand)",
        "gw_term": "Voegvulling aanvullen",
        "uitleg": "Split/zand uit voegen verdwenen; vermindert stabiliteit",
    },
    {
        "code": "schade_element",
        "naam": "Beschadigd of ontbrekend element",
        "klasse_voorbeelden": ["L2", "M1", "M2"],
        "maatregel": "Vervangen losse elementen",
        "gw_term": "Vervangen elementen per stuk",
        "uitleg": "Gebroken klinker/tegel of ontbrekend element",
    },
    {
        "code": "scheefstaan",
        "naam": "Scheefstaan elementen",
        "klasse_voorbeelden": ["L2", "L3", "M1"],
        "maatregel": "Herstraten lokaal trottoir-/parkeerstrook-deel",
        "gw_term": "Herstratingswerk per m2",
        "uitleg": "Elementen niet vlak met omliggende; struikelgevaar",
    },
    {
        "code": "oppervlakteslijtage",
        "naam": "Oppervlakteslijtage (gladheid)",
        "klasse_voorbeelden": ["L1", "L2"],
        "maatregel": "Slijtage-laag aanbrengen of vervangen",
        "gw_term": "Element-vervanging slijtagedeel",
        "uitleg": "Polijst-effect op elementen; vermindert grip",
    },
    {
        "code": "begroeiing_voegen",
        "naam": "Begroeiing in voegen (onkruid)",
        "klasse_voorbeelden": ["L1"],
        "maatregel": "Borstelen + onkruidbestrijding",
        "gw_term": "Onkruidbeheersing per m2",
        "uitleg": "Cosmetisch maar versnelt verlies voegvulling",
    },
]


def _schadebeeld_by_code(verhardingstype: str, code: str) -> Optional[dict]:
    pool = CROW_146A_SCHADEBEELDEN if verhardingstype == "a" else CROW_146B_SCHADEBEELDEN
    for s in pool:
        if s["code"] == code:
            return s
    return None


def detect_verhardingstype(asset_type: Optional[str],
                            properties_hint: Optional[str] = None,
                            dominant_color_rgb: Optional[tuple[int, int, int]] = None,
                            ) -> str:
    """Bepaal a (asfalt) of b (elementen) — heuristisch.

    Voor MVP: gebruik asset-type/properties hint primair, val terug op
    dominante kleur (donker = asfalt, lichter/grijs = elementen).
    """
    if properties_hint:
        h = properties_hint.lower()
        if any(k in h for k in ("asfalt", "asphalt", "deklaag", "zoab", "dab")):
            return "a"
        if any(k in h for k in ("klinker", "tegel", "elemente", "bestrating", "betonstraatsteen")):
            return "b"

    # Asset-type hints
    if asset_type:
        at = asset_type.lower()
        if "wegvak" in at or "asfalt" in at:
            return "a"
        if "trottoir" in at or "klinker" in at or "tegel" in at:
            return "b"

    # Kleur-fallback (donker = asfalt)
    if dominant_color_rgb:
        r, g, b = dominant_color_rgb
        avg = (r + g + b) / 3
        if avg < 80:
            return "a"
        return "b"

    # Default: asfalt (meest voorkomend in NL openbare ruimte)
    return "a"


def classify_crow146(*,
                     verhardingstype: str,           # 'a' of 'b' — auto-detect mogelijk
                     brightness: Optional[float],    # 0-255
                     dominant_color_rgb: Optional[tuple[int, int, int]],
                     element_context: Optional[str] = None,
                     ) -> list[dict]:
    """Heuristische rangschikking van CROW 146-schadebeelden.

    Werkt op verhardingstype + (optioneel) beeld-features. Voor productie-
    gebruik vervang door een CV-model (Azure Custom Vision / GCP AutoML).

    Returns: top-5 schadebeelden met confidence + maatregel.
    """
    pool = CROW_146A_SCHADEBEELDEN if verhardingstype == "a" else CROW_146B_SCHADEBEELDEN

    suggestions = []
    for s in pool:
        confidence = 30  # base — every kandidaat krijgt 30%
        reasons = ["crow-146-taxonomy"]
        code = s["code"]

        if verhardingstype == "a":
            # Donker oppervlak — asfalt-typische heuristieken
            if brightness is not None:
                if brightness < 60 and code in ("kuilvorming", "scheurnet", "spoorvorming"):
                    confidence += 20
                    reasons.append("donker oppervlak suggereert structurele schade")
                if brightness < 80 and code in ("langsscheur", "dwarsscheur"):
                    confidence += 12
                    reasons.append("schaduwrijke foto (mogelijk scheur)")
            if dominant_color_rgb:
                r, g, b = dominant_color_rgb
                if r > 100 and g > 80 and r > g + 20:  # oranje/bruin tint
                    if code in ("rafeling", "randschade"):
                        confidence += 10
                        reasons.append("oxidatie-tint zichtbaar")
        else:  # b — elementen
            if brightness is not None and brightness < 80:
                if code in ("zetting", "scheefstaan", "schade_element"):
                    confidence += 15
                    reasons.append("schaduwgebied suggereert oneffenheid")
            if dominant_color_rgb:
                r, g, b = dominant_color_rgb
                if g > r + 15 and g > 100:  # groen tint
                    if code == "begroeiing_voegen":
                        confidence += 25
                        reasons.append("groene tint suggereert begroeiing")

        # Element-context van kunstwerk-inspectie (bv. brug-dek)
        if element_context:
            ec = element_context.lower()
            if "dek" in ec and code in ("kuilvorming", "rafeling"):
                confidence += 5

        confidence = min(95, confidence)

        suggestions.append({
            "code": code,
            "naam": s["naam"],
            "confidence": confidence,
            "klasse_voorbeelden": s["klasse_voorbeelden"],
            "maatregel": s["maatregel"],
            "gw_term": s["gw_term"],
            "uitleg": s["uitleg"],
            "reasons": reasons,
        })

    # Sorteer op confidence
    suggestions.sort(key=lambda x: x["confidence"], reverse=True)
    return suggestions[:5]


def klasse_advies(klasse: str) -> dict:
    """Geef onderhoud-advies + termijn per CROW-klasse."""
    klasse = klasse.upper()
    advies_map = {
        "L1": ("Observeren — geen onmiddellijke actie", "12 maanden"),
        "L2": ("Lichte signalering — monitoren", "9 maanden"),
        "L3": ("Lichte schade — plan onderhoud", "6 maanden"),
        "M1": ("Matig — plan binnen onderhoudscyclus", "6 maanden"),
        "M2": ("Matig — verdient prioriteit komend kwartaal", "3 maanden"),
        "M3": ("Matig-ernstig — actie nodig", "2 maanden"),
        "E1": ("Ernstig — actie binnen 1 maand", "1 maand"),
        "E2": ("Ernstig — acute aandacht", "2 weken"),
        "E3": ("Zeer ernstig — afzetting + directe maatregel", "direct"),
    }
    advies, termijn = advies_map.get(klasse, ("Onbekende klasse", "-"))
    return {"klasse": klasse, "advies": advies, "termijn": termijn}
