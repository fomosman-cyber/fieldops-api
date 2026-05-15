"""Risico-matrix volgens RAMS / ISO 31000.

Bereken een risico-score per asset op basis van:
  - Faalkans (op basis van conditiescore + leeftijd)
  - Schadepotentieel (op basis van asset-type + locatie)
  - Blootstelling (verkeersintensiteit, bezoekersaantal)

Resultaat: een 1-5 risico-klasse die de prioriteit voor onderhoud
bepaalt. Voor risico-gestuurd onderhoud (anders dan periodiek).

Methodiek: ISO 31000 + CROW handleiding asset management +
NEN 51001 (ISO 55000 lite).
"""
from __future__ import annotations
from typing import Optional
from datetime import datetime, timezone


# ─────────────────────────────────────────────────────────────────────────────
# Schadepotentieel per asset-type (kosten bij falen)
# 1 = laag (€), 5 = catastrofaal (€€€€€)
# ─────────────────────────────────────────────────────────────────────────────

SCHADE_BASE = {
    # Veiligheid-kritiek
    "brug": 5,
    "viaduct": 5,
    "tunnel": 5,
    "sluis": 4,
    "stuw": 4,
    "kademuur": 4,

    # Functioneel-kritiek
    "duiker": 3,
    "gemaal": 4,
    "riolering": 3,
    "verlichting": 2,
    "wegmarkering": 2,

    # Veiligheid + aansprakelijkheid
    "boom": 4,         # Zorgplicht art. 6:174 BW
    "speeltoestel": 5, # Wettelijk verplicht jaarlijks, schade kinderen
    "kunstgrasveld": 2,
    "fontein": 2,

    # Wegdek
    "wegvak": 3,
}


def schade_potentieel(asset_type: Optional[str]) -> int:
    """Schade-klasse 1-5 voor dit asset-type. Default 3 (medium)."""
    if not asset_type:
        return 3
    return SCHADE_BASE.get(asset_type.strip().lower(), 3)


# ─────────────────────────────────────────────────────────────────────────────
# Faalkans op basis van score + leeftijd + meldingen-historie
# ─────────────────────────────────────────────────────────────────────────────

def faalkans(conditie_score: Optional[int],
             installed_at: Optional[datetime] = None,
             expected_lifespan_years: Optional[int] = None,
             meldingen_12mnd: int = 0) -> int:
    """Bepaal faalkans-klasse 1-5.

    Inputs:
      conditie_score      : 1-6 (NEN 2767-2)
      installed_at        : installatie-datum
      expected_lifespan_years : verwachte levensduur
      meldingen_12mnd     : aantal meldingen laatste 12 mnd (proxy voor degradatie)

    >>> faalkans(1, None, None, 0)
    1
    >>> faalkans(6, None, None, 0)
    5
    >>> faalkans(3, None, None, 5)  # 5 meldingen: score 3->2 + meldingen +1 = 3
    3
    """
    # Basis op score (NEN 1-6 → faalkans 1-5)
    base = 1
    if conditie_score:
        score_map = {1: 1, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
        base = score_map.get(int(conditie_score), 2)

    # Boost bij veel meldingen (degradatie-signaal)
    if meldingen_12mnd >= 5:
        base = min(base + 1, 5)
    elif meldingen_12mnd >= 10:
        base = min(base + 2, 5)

    # Leeftijdsboost — als > 90% van verwachte levensduur
    if installed_at and expected_lifespan_years:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        ins = installed_at.replace(tzinfo=None) if installed_at.tzinfo else installed_at
        age_years = (now - ins).days / 365.25
        if age_years >= expected_lifespan_years * 0.9:
            base = min(base + 1, 5)

    return base


# ─────────────────────────────────────────────────────────────────────────────
# Blootstelling — hoe vaak/intensief gebruikt
# ─────────────────────────────────────────────────────────────────────────────

def blootstelling(asset_type: Optional[str],
                  property_dict: Optional[dict] = None) -> int:
    """Bepaal blootstelling-klasse 1-5.

    Voor wegvakken: op basis van verkeersintensiteit (uit NWB of properties)
    Voor speeltuinen: bezoekersaantallen
    Voor kunstwerken: typisch op basis van wegtype (snelweg = 5, woonstraat = 2)

    Default 3 (medium) als onbekend.
    """
    if property_dict and "verkeers_intensiteit" in property_dict:
        v = int(property_dict["verkeers_intensiteit"] or 0)
        if v >= 30000:
            return 5
        elif v >= 10000:
            return 4
        elif v >= 5000:
            return 3
        elif v >= 1000:
            return 2
        return 1

    # Default per type
    if asset_type:
        t = asset_type.strip().lower()
        if t in ("brug", "viaduct", "tunnel"):
            return 4  # gemiddelde aanname
        if t in ("speeltoestel",):
            return 4  # kinderen = hoge frequentie
        if t in ("kademuur", "fontein"):
            return 2
    return 3


# ─────────────────────────────────────────────────────────────────────────────
# Eindscore: risico = faalkans × schade × (blootstelling/5)
# Clip naar 1-5
# ─────────────────────────────────────────────────────────────────────────────

def risico_score(faalkans_v: int, schade_v: int, bloot_v: int) -> int:
    """Bereken risico-klasse 1-5.

    >>> risico_score(1, 1, 1)
    1
    >>> risico_score(5, 5, 5)
    5
    >>> risico_score(3, 3, 3)
    1
    """
    raw = (faalkans_v * schade_v * bloot_v) / 25.0  # max 5*5*5/25 = 5
    return max(1, min(round(raw), 5))


def risico_label(score: int) -> str:
    """Label voor UI."""
    return {
        1: "Zeer laag",
        2: "Laag",
        3: "Gemiddeld",
        4: "Hoog",
        5: "Zeer hoog (acuut)",
    }.get(score, "Onbekend")


def risico_color(score: int) -> str:
    """Kleur voor heatmap-cell."""
    return {
        1: "#16a34a",  # groen
        2: "#84cc16",  # licht-groen
        3: "#facc15",  # geel
        4: "#f97316",  # oranje
        5: "#dc2626",  # rood
    }.get(score, "#94a3b8")


def assess_asset(asset_type: Optional[str],
                 conditie_score: Optional[int],
                 installed_at: Optional[datetime] = None,
                 expected_lifespan_years: Optional[int] = None,
                 meldingen_12mnd: int = 0,
                 property_dict: Optional[dict] = None) -> dict:
    """Eind-assessment voor één asset. Voor batch-views.

    >>> r = assess_asset("brug", 4, None, None, 2, None)
    >>> r["faalkans"]
    3
    >>> r["schadepotentieel"]
    5
    >>> r["risico_score"] >= 2
    True
    """
    fk = faalkans(conditie_score, installed_at, expected_lifespan_years, meldingen_12mnd)
    sc = schade_potentieel(asset_type)
    bl = blootstelling(asset_type, property_dict)
    rs = risico_score(fk, sc, bl)
    return {
        "faalkans": fk,
        "schadepotentieel": sc,
        "blootstelling": bl,
        "risico_score": rs,
        "risico_label": risico_label(rs),
        "risico_color": risico_color(rs),
    }


RAMS_VERSION = "rams.v1.0-2026-05"
