"""NEN-EN 1176 — Speeltoestellen-veiligheid classificatie.

NEN-EN 1176 is de Europese norm voor speeltoestellen in openbare ruimte.
4 inspectie-categorieën:
  A — Visuele inspectie (wekelijks-maandelijks) — zichtbare schade
  B — Operationele inspectie (1-3 maand) — toestand + slijtage
  C — Hoofdinspectie (jaarlijks) — structurele integriteit
  D — Acuut afsluiten (direct na ontdekking) — onveilig

Bron: NEN-EN 1176-1:2017 + nationale aanvulling 2025.

Heuristische classificatie op basis van asset-type + foto-features.
Productie-grade detectie vraagt een CV-model (Azure Custom Vision met
~3000 gelabelde speeltoestel-foto's).
"""
from __future__ import annotations
from typing import Optional


# Faaltypes per NEN-EN 1176 inspectie-categorie
NEN_EN_1176_FAALTYPES = [
    {
        "code": "houtrot_constructief",
        "naam": "Houtrot in dragende delen",
        "categorie": "D",  # Acuut afsluiten
        "klasse_voorbeelden": ["acuut", "hoog"],
        "maatregel": "Direct afsluiten + vervangen dragend element",
        "uitleg": "Verzachting/verkleuring/holte in palen of staanders",
        "kleur_hint": "donker, vlekken, groene mosaanwas",
    },
    {
        "code": "scherpe_randen",
        "naam": "Scherpe randen of uitstekende delen",
        "categorie": "B",
        "klasse_voorbeelden": ["acuut", "hoog"],
        "maatregel": "Bramen verwijderen / uitstekende schroef afzagen",
        "uitleg": "Risico op snijwond of haakletsel",
        "kleur_hint": "metaal-glans, roest",
    },
    {
        "code": "beklemming_risico",
        "naam": "Beklemmingsrisico opening",
        "categorie": "C",
        "klasse_voorbeelden": ["hoog"],
        "maatregel": "Opening dichten of openheid >230mm of <90mm maken",
        "uitleg": "Opening tussen 90-230mm = klemmen-zone voor kind-hoofd",
        "kleur_hint": "ronde of vierkante openingen in frame",
    },
    {
        "code": "valdemping_onvoldoende",
        "naam": "Onvoldoende valdemping ondergrond",
        "categorie": "B",
        "klasse_voorbeelden": ["hoog", "middel"],
        "maatregel": "Aanvullen rubber-tegels / valgrond verdikken tot 40cm",
        "uitleg": "Valhoogte > demping volgens HIC-tabel",
        "kleur_hint": "kale grond, weggespoeld zand, beschadigde tegels",
    },
    {
        "code": "ketting_slijtage",
        "naam": "Slijtage schommelketting / verbinding",
        "categorie": "B",
        "klasse_voorbeelden": ["middel", "hoog"],
        "maatregel": "Vervang ketting + S-haak; smering bij eerste tekenen",
        "uitleg": ">10% diameter-verlies of zichtbare verbuiging",
        "kleur_hint": "roest, glimmend versleten contact-punten",
    },
    {
        "code": "lager_defect",
        "naam": "Defect lager / piepend bewegend deel",
        "categorie": "B",
        "klasse_voorbeelden": ["middel"],
        "maatregel": "Smeren + monitoring; vervangen bij doorlopend geluid",
        "uitleg": "Mechanisch falen schommel-as, draaitoestel",
        "kleur_hint": "roest, droog metaal, kleur-vlek smeermiddel",
    },
    {
        "code": "verflagen_schilferend",
        "naam": "Schilferende verf / corrosie zichtbaar",
        "categorie": "A",
        "klasse_voorbeelden": ["laag", "middel"],
        "maatregel": "Schuren + grondverf + aflak; bij staal min. 80μm",
        "uitleg": "Cosmetisch eerst, dan structureel als roest doortast",
        "kleur_hint": "schilfers, mat oppervlak, kleurverschil",
    },
    {
        "code": "kabel_klimsysteem",
        "naam": "Beschadigde kabel klim-/touwsysteem",
        "categorie": "C",
        "klasse_voorbeelden": ["hoog"],
        "maatregel": "Vervang kabel-segment; controle kerntouw-integriteit",
        "uitleg": "Polyester-vezel breuk of staaldraad-kink",
        "kleur_hint": "vezeluiteinden zichtbaar, donker spot",
    },
    {
        "code": "graffiti_vandalisme",
        "naam": "Graffiti / vandalisme cosmetisch",
        "categorie": "A",
        "klasse_voorbeelden": ["laag"],
        "maatregel": "Reiniging met biologisch oplosmiddel + overschilderen",
        "uitleg": "Niet veiligheidskritisch, wel beheers-actie",
        "kleur_hint": "verfsporen, tekst, kleurklodders",
    },
]


def detect_categorie(
    asset_type: Optional[str],
    properties_hint: Optional[str],
) -> Optional[str]:
    """Schat de inspectie-categorie (A/B/C/D) op basis van context.

    Default: B (operationele inspectie) — meest voorkomende routine.
    """
    if not asset_type and not properties_hint:
        return "B"
    text = ((asset_type or "") + " " + (properties_hint or "")).lower()
    if any(w in text for w in ["acuut", "afsluiten", "gevaarlijk", "ongeluk"]):
        return "D"
    if any(w in text for w in ["jaarlijks", "hoofd", "structureel", "draagvermogen"]):
        return "C"
    if any(w in text for w in ["wekelijks", "visueel", "graffiti", "cosmetisch"]):
        return "A"
    return "B"


def klasse_advies(klasse: str) -> str:
    """Advies-tekst per klasse."""
    return {
        "acuut": "DIRECT AFSLUITEN — vervangen vóór heropening",
        "hoog":  "Binnen 7 dagen herstellen — tot dan markeer 'in onderhoud'",
        "middel": "Binnen 4 weken inplannen in onderhoudsronde",
        "laag":  "Meenemen in eerstvolgende routine-onderhoud",
    }.get(klasse, "—")


def classify_en1176(*,
                    asset_type: Optional[str] = None,
                    properties_hint: Optional[str] = None,
                    categorie: Optional[str] = None,
                    brightness: Optional[float] = None,
                    dominant_color_rgb: Optional[tuple[int, int, int]] = None,
                    ) -> list[dict]:
    """Top-N waarschijnlijke schadebeelden voor speeltoestel-inspectie.

    Filtert op categorie (A/B/C/D) en boost confidence op basis van foto-
    features + asset-context.
    """
    # Auto-detect categorie als niet gegeven
    cat = categorie if categorie in ("A", "B", "C", "D") else detect_categorie(asset_type, properties_hint)

    # Filter op categorie (of toon alle als 'B' is fallback — die staat in
    # tussenniveau en dekt de meeste opties)
    pool = [f for f in NEN_EN_1176_FAALTYPES if f["categorie"] == cat]
    if not pool:
        pool = NEN_EN_1176_FAALTYPES

    suggestions = []
    for f in pool:
        confidence = 35
        reasons = ["nen-en-1176-taxonomy"]
        code = f["code"]

        # Heuristieken: brightness + kleur
        if brightness is not None:
            if brightness < 80 and code in ("houtrot_constructief", "lager_defect"):
                confidence += 15
                reasons.append("donker oppervlak (mogelijk vocht/rot)")
            if brightness > 180 and code == "valdemping_onvoldoende":
                confidence += 10
                reasons.append("licht oppervlak (mogelijk kale grond)")

        if dominant_color_rgb:
            r, g, b = dominant_color_rgb
            if r > g + 30 and r > 100 and code in ("scherpe_randen", "ketting_slijtage", "verflagen_schilferend"):
                confidence += 15
                reasons.append("roest-tint zichtbaar")
            if g > r + 20 and g > 80 and code in ("houtrot_constructief", "graffiti_vandalisme"):
                confidence += 10
                reasons.append("groene tint (mos of verf)")

        # Asset-type hint
        if asset_type:
            at = asset_type.lower()
            if "schommel" in at and code in ("ketting_slijtage", "lager_defect"):
                confidence += 15
            elif "klim" in at and code in ("kabel_klimsysteem", "beklemming_risico"):
                confidence += 15
            elif "glij" in at and code in ("scherpe_randen", "verflagen_schilferend"):
                confidence += 10

        confidence = min(95, max(20, confidence))
        suggestions.append({
            "code": code,
            "naam": f["naam"],
            "categorie": f["categorie"],
            "confidence": confidence,
            "confidence_pct": confidence,
            "klasse_advies": (f["klasse_voorbeelden"] or [""])[0],
            "klasse_voorbeelden": f["klasse_voorbeelden"],
            "maatregel": f["maatregel"],
            "uitleg": f["uitleg"],
            "reason": " + ".join(reasons),
        })

    suggestions.sort(key=lambda s: -s["confidence"])
    return suggestions[:5]
