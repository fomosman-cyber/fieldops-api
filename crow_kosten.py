"""
CROW + GWWkosten lookup-module.

Statische mapping van CROW-schadebeeld + klasse → maatregel + GWWkosten-orde.
Geen externe API-call — gewoon hardcoded reference data die regelmatig
ge-update kan worden naarmate GWWkosten-prijzen veranderen.

Bron: CROW 146a (asfalt) + CROW 146b (elementen) + GWWkosten kostenkengetallen.
Laatst bijgewerkt: mei 2026.

Usage:
    from crow_kosten import lookup_maatregel, klasse_to_categorie

    advies = lookup_maatregel(
        schadegroep="samenhang",
        schadebeeld="scheurvorming-langs",
        klasse="M2"
    )
    # -> {"categorie": "KO", "maatregel": "Vullen polymeer",
    #     "kosten_orde": "€5–15 / m¹", "termijn_weken": 24}
"""
from typing import Optional, Dict, Any


# ════════════════════════════════════════════════════════════
# CROW 146 — schadebeeld-catalogus per asset-type
# ════════════════════════════════════════════════════════════

SCHADEGROEPEN_ASFALT = {
    "samenhang": ["scheurvorming-langs", "scheurvorming-dwars", "scheurvorming-kruis",
                  "scheurvorming-rand", "scheurvorming-groep"],
    "textuur": ["rafeling", "spaltverlies"],
    "vlakheid": ["spoorvorming", "oneffenheden", "kuilen", "deformatie"],
    "watergevoeligheid": ["bitumen-uittreden"],
}

SCHADEGROEPEN_ELEMENTEN = {
    "vlakheid": ["verzakking", "oneffenheden", "opdrukking"],
    "voegen": ["voegwijdte", "voegvulling-gebrek"],
    "stenen": ["gebroken-stenen", "ontbrekende-stenen", "los-liggend"],
}

SCHADEGROEPEN_BETON = {
    "vlakheid": ["voegovergangen", "oneffenheid"],
    "voegen": ["voeg-degradatie", "kit-gebreken"],
    "samenhang": ["scheurvorming", "breuk", "betondegradatie"],
}

ALL_SCHADEBEELDEN = (
    [(g, b) for g, beelden in SCHADEGROEPEN_ASFALT.items() for b in beelden] +
    [(g, b) for g, beelden in SCHADEGROEPEN_ELEMENTEN.items() for b in beelden] +
    [(g, b) for g, beelden in SCHADEGROEPEN_BETON.items() for b in beelden]
)


# ════════════════════════════════════════════════════════════
# CROW-klasse-matrix → onderhouds-categorie
# ════════════════════════════════════════════════════════════

# Ernst × omvang = klasse
ERNST_CODES = ["L", "M", "E"]
OMVANG_CODES = ["1", "2", "3"]
ALL_KLASSEN = [f"{e}{o}" for e in ERNST_CODES for o in OMVANG_CODES]

# Klasse → categorie volgens CROW 147 maatregeltoets
KLASSE_NAAR_CATEGORIE = {
    "L1": "observatie", "L2": "observatie", "L3": "observatie",
    "M1": "KO",         "M2": "KO",         "M3": "GO",
    "E1": "GO",         "E2": "GO",         "E3": "acuut",
}

# Termijn (weken) waarbinnen actie moet starten
KLASSE_NAAR_TERMIJN_WEKEN = {
    "L1": None, "L2": None, "L3": None,  # observatie — volgende GVI
    "M1": 52,   "M2": 24,   "M3": 18,    # binnen jaarcyclus
    "E1": 12,   "E2": 8,    "E3": 1,     # acuut
}


def klasse_to_categorie(klasse: str) -> str:
    """L1..E3 → 'observatie' | 'KO' | 'GO' | 'acuut'."""
    return KLASSE_NAAR_CATEGORIE.get(klasse.upper(), "observatie")


def klasse_to_termijn(klasse: str) -> Optional[int]:
    """L1..E3 → aantal weken termijn (None = geen actie)."""
    return KLASSE_NAAR_TERMIJN_WEKEN.get(klasse.upper())


def parse_klasse(ernst: str, omvang: str) -> str:
    """('M', '2') → 'M2'."""
    e = ernst.strip().upper()[:1]
    o = str(omvang).strip()[:1]
    if e not in ERNST_CODES or o not in OMVANG_CODES:
        raise ValueError(f"Invalid CROW class: ernst={ernst!r}, omvang={omvang!r}")
    return f"{e}{o}"


# ════════════════════════════════════════════════════════════
# GWWkosten — maatregel-lookup per (schadebeeld, klasse)
# ════════════════════════════════════════════════════════════

# Format: (schadebeeld, klasse) → {maatregel, kosten_orde, eenheid, gw_term}
# Kosten zijn realistische marktprijzen mei 2026 — voor procurement-grade
# raadpleeg GWWkosten.nl.

MAATREGEL_LOOKUP: Dict[tuple, Dict[str, Any]] = {
    # ─── ASFALT — samenhang (scheurvorming) ───
    ("scheurvorming-langs", "M1"):    {"maatregel": "Vullen polymeer",       "kosten_orde": "€5–10 / m¹",   "eenheid": "m¹", "gw_term": "Vullen polymeer (cold-pour)"},
    ("scheurvorming-langs", "M2"):    {"maatregel": "Vullen polymeer",       "kosten_orde": "€8–15 / m¹",   "eenheid": "m¹", "gw_term": "Vullen polymeer (cold-pour)"},
    ("scheurvorming-langs", "M3"):    {"maatregel": "Oppervlaktebehandeling","kosten_orde": "€15–25 / m²",  "eenheid": "m²", "gw_term": "Oppervlaktebehandeling DGAD"},
    ("scheurvorming-langs", "E1"):    {"maatregel": "Pleksgewijze reparatie","kosten_orde": "€150–400 / plek","eenheid": "plek","gw_term": "Pleksgewijze reparatie frees-vul"},
    ("scheurvorming-langs", "E2"):    {"maatregel": "Deklaag vervangen",     "kosten_orde": "€20–40 / m²",  "eenheid": "m²", "gw_term": "Aanbrengen dicht asfaltbeton (deklaag)"},
    ("scheurvorming-langs", "E3"):    {"maatregel": "Tussen+deklaag vervangen","kosten_orde":"€30–50 / m²", "eenheid":"m²",  "gw_term":"Aanbrengen tussen- en deklaag dicht asfaltbeton"},

    ("scheurvorming-dwars", "M1"):    {"maatregel": "Vullen polymeer",       "kosten_orde": "€5–10 / m¹",   "eenheid": "m¹", "gw_term": "Vullen polymeer"},
    ("scheurvorming-dwars", "M2"):    {"maatregel": "Vullen polymeer",       "kosten_orde": "€8–15 / m¹",   "eenheid": "m¹", "gw_term": "Vullen polymeer"},
    ("scheurvorming-dwars", "M3"):    {"maatregel": "Oppervlaktebehandeling","kosten_orde": "€15–25 / m²",  "eenheid": "m²", "gw_term": "Oppervlaktebehandeling DGAD"},
    ("scheurvorming-dwars", "E1"):    {"maatregel": "Pleksgewijze reparatie","kosten_orde": "€150–400 / plek","eenheid":"plek","gw_term":"Pleksgewijze reparatie"},
    ("scheurvorming-dwars", "E2"):    {"maatregel": "Deklaag vervangen",     "kosten_orde": "€20–40 / m²",  "eenheid": "m²", "gw_term": "Aanbrengen dicht asfaltbeton"},
    ("scheurvorming-dwars", "E3"):    {"maatregel": "Tussen+deklaag vervangen","kosten_orde":"€30–50 / m²", "eenheid":"m²",  "gw_term":"Aanbrengen tussen- en deklaag"},

    ("scheurvorming-kruis", "M2"):    {"maatregel": "Slembehandeling",       "kosten_orde": "€10–18 / m²",  "eenheid": "m²", "gw_term": "Slembehandeling"},
    ("scheurvorming-kruis", "M3"):    {"maatregel": "Oppervlaktebehandeling","kosten_orde": "€15–25 / m²",  "eenheid": "m²", "gw_term": "Oppervlaktebehandeling DGAD"},
    ("scheurvorming-kruis", "E1"):    {"maatregel": "Deklaag vervangen",     "kosten_orde": "€20–40 / m²",  "eenheid": "m²", "gw_term": "Deklaag dicht asfaltbeton"},
    ("scheurvorming-kruis", "E2"):    {"maatregel": "Tussen+deklaag vervangen","kosten_orde":"€30–50 / m²", "eenheid":"m²",  "gw_term":"Tussen- en deklaag asfaltbeton"},
    ("scheurvorming-kruis", "E3"):    {"maatregel": "Volledig profiel",      "kosten_orde": "€80–150 / m²", "eenheid": "m²", "gw_term": "Volledig profiel + fundering"},

    ("scheurvorming-rand", "M2"):     {"maatregel": "Voegvulling",           "kosten_orde": "€40–80 / voeg","eenheid": "voeg","gw_term":"Voegvulling lassen"},
    ("scheurvorming-rand", "E1"):     {"maatregel": "Voegvulling + frees-vul","kosten_orde":"€80–150 / voeg","eenheid":"voeg","gw_term":"Voegvulling + reparatie"},

    ("scheurvorming-groep", "M3"):    {"maatregel": "Deklaag vervangen",     "kosten_orde": "€20–40 / m²",  "eenheid": "m²", "gw_term": "Deklaag dicht asfaltbeton"},
    ("scheurvorming-groep", "E1"):    {"maatregel": "Tussen+deklaag vervangen","kosten_orde":"€30–50 / m²", "eenheid":"m²",  "gw_term":"Tussen- en deklaag"},
    ("scheurvorming-groep", "E2"):    {"maatregel": "Volledig profiel",      "kosten_orde": "€80–150 / m²", "eenheid": "m²", "gw_term": "Volledig profiel + fundering"},
    ("scheurvorming-groep", "E3"):    {"maatregel": "Reconstructie",         "kosten_orde": "€150–300 / m²","eenheid": "m²", "gw_term": "Reconstructie"},

    # ─── ASFALT — textuur (rafeling) ───
    ("rafeling", "L2"):               {"maatregel": "Observatie",            "kosten_orde": "€0",            "eenheid": "—", "gw_term": "—"},
    ("rafeling", "M1"):               {"maatregel": "Slembehandeling",       "kosten_orde": "€8–15 / m²",   "eenheid": "m²", "gw_term": "Slembehandeling"},
    ("rafeling", "M2"):               {"maatregel": "Slembehandeling",       "kosten_orde": "€10–18 / m²",  "eenheid": "m²", "gw_term": "Slembehandeling"},
    ("rafeling", "M3"):               {"maatregel": "Oppervlaktebehandeling","kosten_orde": "€15–25 / m²",  "eenheid": "m²", "gw_term": "Oppervlaktebehandeling DGAD"},
    ("rafeling", "E1"):               {"maatregel": "Deklaag vervangen",     "kosten_orde": "€20–40 / m²",  "eenheid": "m²", "gw_term": "Aanbrengen dicht asfaltbeton"},
    ("rafeling", "E2"):               {"maatregel": "Deklaag vervangen",     "kosten_orde": "€25–45 / m²",  "eenheid": "m²", "gw_term": "Aanbrengen dicht asfaltbeton"},
    ("rafeling", "E3"):               {"maatregel": "Tussen+deklaag vervangen","kosten_orde":"€30–50 / m²", "eenheid":"m²",  "gw_term":"Tussen- en deklaag"},

    # ─── ASFALT — vlakheid ───
    ("spoorvorming", "M2"):           {"maatregel": "Profielcorrectie SMA",  "kosten_orde": "€15–30 / m²",  "eenheid": "m²", "gw_term": "Profielcorrectie SMA"},
    ("spoorvorming", "M3"):           {"maatregel": "Deklaag vervangen",     "kosten_orde": "€25–45 / m²",  "eenheid": "m²", "gw_term": "Deklaag asfaltbeton"},
    ("spoorvorming", "E1"):           {"maatregel": "Tussen+deklaag vervangen","kosten_orde":"€35–55 / m²", "eenheid":"m²",  "gw_term":"Tussen- en deklaag"},
    ("spoorvorming", "E2"):           {"maatregel": "Volledig profiel",      "kosten_orde": "€80–150 / m²", "eenheid": "m²", "gw_term": "Volledig profiel"},
    ("spoorvorming", "E3"):           {"maatregel": "Reconstructie",         "kosten_orde": "€150–300 / m²","eenheid": "m²", "gw_term": "Reconstructie"},

    ("oneffenheden", "M1"):           {"maatregel": "Pleksgewijze reparatie","kosten_orde": "€150–300 / plek","eenheid":"plek","gw_term":"Pleksgewijze reparatie"},
    ("oneffenheden", "M2"):           {"maatregel": "Pleksgewijze reparatie","kosten_orde": "€200–400 / plek","eenheid":"plek","gw_term":"Pleksgewijze reparatie"},
    ("oneffenheden", "E1"):           {"maatregel": "Deklaag vervangen",     "kosten_orde": "€20–40 / m²",  "eenheid": "m²", "gw_term": "Deklaag asfaltbeton"},

    ("kuilen", "M2"):                 {"maatregel": "Pleksgewijze reparatie","kosten_orde": "€200–400 / plek","eenheid":"plek","gw_term":"Pleksgewijze reparatie"},
    ("kuilen", "E1"):                 {"maatregel": "Pleksgewijze reparatie + frees","kosten_orde":"€300–600 / plek","eenheid":"plek","gw_term":"Frees-vul reparatie"},
    ("kuilen", "E3"):                 {"maatregel": "Spoed-reparatie",       "kosten_orde": "€300–800 / plek","eenheid":"plek","gw_term":"Spoed-reparatie veiligheidsmaatregel"},

    ("deformatie", "M2"):             {"maatregel": "Profielcorrectie",      "kosten_orde": "€20–35 / m²",  "eenheid": "m²", "gw_term": "Profielcorrectie SMA"},
    ("deformatie", "E1"):             {"maatregel": "Deklaag vervangen",     "kosten_orde": "€25–45 / m²",  "eenheid": "m²", "gw_term": "Deklaag asfaltbeton"},

    # ─── ELEMENTENVERHARDING (klinkers/tegels) ───
    ("verzakking", "M1"):             {"maatregel": "Klinker-herstraten",    "kosten_orde": "€20–40 / m²",  "eenheid": "m²", "gw_term": "Herstraten elementen lokaal"},
    ("verzakking", "M2"):             {"maatregel": "Klinker-herstraten",    "kosten_orde": "€30–50 / m²",  "eenheid": "m²", "gw_term": "Herstraten elementen"},
    ("verzakking", "E1"):             {"maatregel": "Klinker-herstraten + funderingscorrectie","kosten_orde":"€40–80 / m²","eenheid":"m²","gw_term":"Herstraten + funderingscorrectie"},

    ("voegwijdte", "M2"):             {"maatregel": "Voegen invegen",        "kosten_orde": "€3–8 / m²",    "eenheid": "m²", "gw_term": "Voegen invegen"},
    ("voegvulling-gebrek", "M2"):     {"maatregel": "Voegen invegen",        "kosten_orde": "€3–8 / m²",    "eenheid": "m²", "gw_term": "Voegen invegen"},

    ("gebroken-stenen", "M2"):        {"maatregel": "Stenen vervangen",      "kosten_orde": "€8–15 / steen","eenheid": "steen","gw_term":"Stenen vervangen"},
    ("ontbrekende-stenen", "E1"):     {"maatregel": "Stenen vervangen + voegen invegen","kosten_orde":"€12–20 / steen","eenheid":"steen","gw_term":"Stenen vervangen + invegen"},
}


def lookup_maatregel(
    schadebeeld: str,
    klasse: str,
    schadegroep: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Lookup de aanbevolen maatregel + GWWkosten-koppeling voor een (schadebeeld, klasse) combinatie.

    Returns dict met:
      - categorie: 'observatie' | 'KO' | 'GO' | 'acuut'
      - maatregel: korte maatregel-naam
      - gw_term: officiële GWWkosten / CROW-RAW term voor in bestek
      - kosten_orde: indicatie (bv "€5–15 / m¹")
      - eenheid: 'm¹' | 'm²' | 'plek' | 'voeg' | 'steen' | '—'
      - termijn_weken: int of None
      - klasse, schadebeeld, schadegroep (echo)
    """
    klasse = klasse.strip().upper()
    schadebeeld = schadebeeld.strip().lower()
    categorie = klasse_to_categorie(klasse)
    termijn = klasse_to_termijn(klasse)

    # Try exact match
    advies = MAATREGEL_LOOKUP.get((schadebeeld, klasse))

    # Fallback: zelfde schadebeeld, naburige klasse
    if not advies:
        for fallback_klasse in _nearby_klassen(klasse):
            advies = MAATREGEL_LOOKUP.get((schadebeeld, fallback_klasse))
            if advies:
                break

    # Last resort: generic per categorie
    if not advies:
        advies = _GENERIC_PER_CATEGORIE.get(categorie, _GENERIC_PER_CATEGORIE["observatie"])

    return {
        "categorie": categorie,
        "klasse": klasse,
        "schadebeeld": schadebeeld,
        "schadegroep": schadegroep,
        "termijn_weken": termijn,
        **advies,
    }


def _nearby_klassen(klasse: str):
    """Probeer naburige klassen — als M2 niet bestaat probeer M1 en M3."""
    if len(klasse) != 2:
        return []
    e, o = klasse[0], klasse[1]
    nearby = []
    # Probeer zelfde ernst, andere omvang
    for o2 in ["1", "2", "3"]:
        if o2 != o:
            nearby.append(f"{e}{o2}")
    return nearby


_GENERIC_PER_CATEGORIE = {
    "observatie": {
        "maatregel": "Observatie", "gw_term": "—",
        "kosten_orde": "€0", "eenheid": "—",
    },
    "KO": {
        "maatregel": "Klein onderhoud (techniek nader te bepalen)", "gw_term": "Klein onderhoud — overig",
        "kosten_orde": "€5–50 / m²", "eenheid": "m²",
    },
    "GO": {
        "maatregel": "Groot onderhoud (deklaag of meer)", "gw_term": "Groot onderhoud asfalt — overig",
        "kosten_orde": "€15–150 / m²", "eenheid": "m²",
    },
    "acuut": {
        "maatregel": "Acuut: veiligheidsmaatregel + spoed-GO", "gw_term": "Acuut onderhoud + veiligheidsmaatregel",
        "kosten_orde": "€150+ / m²", "eenheid": "m²",
    },
}


# ════════════════════════════════════════════════════════════
# Risk-score weighting per CROW-klasse — voor predictive
# ════════════════════════════════════════════════════════════

KLASSE_RISK_POINTS = {
    "L1": 0,  "L2": 2,  "L3": 5,
    "M1": 8,  "M2": 14, "M3": 22,
    "E1": 30, "E2": 38, "E3": 48,
}


def klasse_to_risk_points(klasse: str) -> int:
    """L1..E3 → risk-score punten (0-48)."""
    return KLASSE_RISK_POINTS.get(klasse.strip().upper(), 0)


# ════════════════════════════════════════════════════════════
# Job Orchestration: maatregel → skill mapping (v3.0)
# ════════════════════════════════════════════════════════════

# Skill-codes — uniek per techniek, gebruikt voor skill-based assignment
SKILL_CODES = {
    "VULLEN_POLYMEER":      "Vullen polymeer (cold-pour scheurvulling)",
    "SLEMBEHANDELING":      "Slembehandeling (slurry-seal)",
    "VOEGVULLING":          "Voegvulling (lassen tussen banen)",
    "PLEKSGEWIJZE":         "Pleksgewijze reparatie (frees-vul)",
    "ASFALT_DEKLAAG":       "Aanbrengen dicht asfaltbeton (deklaag)",
    "ASFALT_TUSSENDEKLAAG": "Tussen+deklaag asfaltbeton",
    "ASFALT_PROFIELCORR":   "Profielcorrectie SMA",
    "ASFALT_PROFIEL_VOL":   "Volledig profiel + fundering",
    "ASFALT_RECONSTRUCTIE": "Reconstructie / full rebuild",
    "OPPERVLAKTEBEHANDELING": "Oppervlaktebehandeling DGAD",
    "KLINKER_HERSTRATEN":   "Klinker-herstraten",
    "VOEGEN_INVEGEN":       "Voegen invegen elementen",
    "STENEN_VERVANGEN":     "Stenen vervangen / aanleggen",
    "SPOED_REPARATIE":      "Spoed-reparatie veiligheidsmaatregel",
    "OBSERVATIE":           "Observatie / GVI (geen ingreep)",
}

# Maatregel-naam (zoals in MAATREGEL_LOOKUP) → skill-code
MAATREGEL_TO_SKILL = {
    "Vullen polymeer":                       "VULLEN_POLYMEER",
    "Slembehandeling":                       "SLEMBEHANDELING",
    "Voegvulling":                           "VOEGVULLING",
    "Voegvulling + frees-vul":               "VOEGVULLING",
    "Pleksgewijze reparatie":                "PLEKSGEWIJZE",
    "Pleksgewijze reparatie + frees":        "PLEKSGEWIJZE",
    "Oppervlaktebehandeling":                "OPPERVLAKTEBEHANDELING",
    "Deklaag vervangen":                     "ASFALT_DEKLAAG",
    "Tussen+deklaag vervangen":              "ASFALT_TUSSENDEKLAAG",
    "Profielcorrectie SMA":                  "ASFALT_PROFIELCORR",
    "Profielcorrectie":                      "ASFALT_PROFIELCORR",
    "Volledig profiel":                      "ASFALT_PROFIEL_VOL",
    "Reconstructie":                         "ASFALT_RECONSTRUCTIE",
    "Klinker-herstraten":                    "KLINKER_HERSTRATEN",
    "Klinker-herstraten + funderingscorrectie": "KLINKER_HERSTRATEN",
    "Voegen invegen":                        "VOEGEN_INVEGEN",
    "Stenen vervangen":                      "STENEN_VERVANGEN",
    "Stenen vervangen + voegen invegen":     "STENEN_VERVANGEN",
    "Spoed-reparatie":                       "SPOED_REPARATIE",
    "Observatie":                            "OBSERVATIE",
}


def maatregel_to_skill(maatregel: str) -> str | None:
    """Geef de skill-code terug die nodig is voor een maatregel.

    Bv: 'Vullen polymeer' -> 'VULLEN_POLYMEER'
    Onbekende maatregelen retourneren None (= geen skill-eis).
    """
    if not maatregel:
        return None
    return MAATREGEL_TO_SKILL.get(maatregel.strip())


# ════════════════════════════════════════════════════════════
# Productiviteit-baseline per skill (uren per eenheid)
# ════════════════════════════════════════════════════════════

# Realistische productiviteit per skill bij solo-job (zonder clustering)
# Bron: branche-cijfers GWWkosten + ervaring NL aannemers, mei 2026
PRODUCTIVITY_PER_SKILL = {
    # (uren_per_eenheid, eenheid, setup_uren_per_dag)
    "VULLEN_POLYMEER":      (0.025, "m¹", 1.0),   # 40 m¹/uur · 1u setup
    "SLEMBEHANDELING":      (0.020, "m²", 1.5),   # 50 m²/uur · 1.5u setup
    "VOEGVULLING":          (0.250, "voeg", 0.5),  # 4 voegen/uur
    "PLEKSGEWIJZE":         (1.500, "plek", 0.5),  # 1.5u/plek
    "OPPERVLAKTEBEHANDELING": (0.015, "m²", 2.0),  # 67 m²/uur · DGAD
    "ASFALT_DEKLAAG":       (0.012, "m²", 2.0),    # 83 m²/uur
    "ASFALT_TUSSENDEKLAAG": (0.020, "m²", 2.5),
    "ASFALT_PROFIEL_VOL":   (0.040, "m²", 3.0),
    "KLINKER_HERSTRATEN":   (0.080, "m²", 1.0),    # 12.5 m²/uur
    "VOEGEN_INVEGEN":       (0.010, "m²", 0.5),
    "STENEN_VERVANGEN":     (0.150, "steen", 0.5),
}


def estimate_cluster_hours(skill_code: str, total_units: float) -> tuple[float, float]:
    """Geef (geclusterde uren, baseline uren) terug voor een job-cluster.

    Geclusterd: 1× setup + N × productiviteit (efficiency-ideaal)
    Baseline: per losse melding eigen setup → veel hoger
    Verschil = productiviteit-winst van clustering.
    """
    if skill_code not in PRODUCTIVITY_PER_SKILL or total_units <= 0:
        return (0.0, 0.0)
    rate, _eenheid, setup = PRODUCTIVITY_PER_SKILL[skill_code]
    clustered = setup + (total_units * rate)
    # Baseline: assumes 4 trips per day = 4× setup-overhead vs clustered
    baseline = (setup * 4) + (total_units * rate * 1.15)  # 15% extra door reizen
    return (round(clustered, 1), round(baseline, 1))
