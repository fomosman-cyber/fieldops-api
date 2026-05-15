"""Meerjaren Onderhoudsplan (MJOP) — kosten-katalogus per asset-type + score.

Geeft per `(asset_type, condition_score)` een **maatregel** + indicatieve
**kostenrange** (€ min – € max). Gebaseerd op CROW publicatie 145
(Onderhoudskosten infrastructuur) + RAW-indexen 2024-2025 + GWW-kostengids
2024 voor gemeentelijke kunstwerken.

Het is een **indicatie** — voor exacte ramingen heb je RAW-besteksbescheid
nodig per project. Het MJOP is een meerjaren-overzicht voor
begrotings-onderbouwing, geen aanbestedingsdocument.

Versie-tag wordt meegegeven in elke export voor traceability.
"""
from __future__ import annotations
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Kosten-katalogus per (asset_type, score)
# ─────────────────────────────────────────────────────────────────────────────
#
# Format: kosten_per_type[asset_type][score] = {
#     "maatregel": "Beschrijving",
#     "min_eur": 0, "max_eur": 0,
#     "unit": "per object" | "per m" | "per m2",
#     "norm_ref": "CROW xxx / NEN xxx"
# }
#
# Score 1-2 → typisch geen kosten (preventief budget elders)
# Score 3   → klein onderhoud
# Score 4   → groot onderhoud
# Score 5   → renovatie
# Score 6   → vervanging (acuut)

KOSTEN = {
    # ───────── KUNSTWERKEN ─────────
    "brug": {
        1: {"maatregel": "Periodieke inspectie", "min_eur": 800, "max_eur": 1500},
        2: {"maatregel": "Preventief onderhoud (reiniging, voegen)", "min_eur": 2000, "max_eur": 5000},
        3: {"maatregel": "Klein onderhoud (voegovergangen, leuningen)", "min_eur": 8000, "max_eur": 25000},
        4: {"maatregel": "Groot onderhoud (oppervlakbehandeling, voegen, opleggingen)", "min_eur": 50000, "max_eur": 150000},
        5: {"maatregel": "Renovatie (constructief herstel)", "min_eur": 200000, "max_eur": 600000},
        6: {"maatregel": "Vervanging brug (constructie)", "min_eur": 800000, "max_eur": 3000000},
    },
    "viaduct": {
        1: {"maatregel": "Periodieke inspectie", "min_eur": 1000, "max_eur": 2000},
        2: {"maatregel": "Preventief onderhoud", "min_eur": 3000, "max_eur": 7000},
        3: {"maatregel": "Klein onderhoud (voegen, kantopsluiting)", "min_eur": 10000, "max_eur": 30000},
        4: {"maatregel": "Groot onderhoud", "min_eur": 80000, "max_eur": 200000},
        5: {"maatregel": "Renovatie", "min_eur": 300000, "max_eur": 800000},
        6: {"maatregel": "Vervanging viaduct", "min_eur": 1500000, "max_eur": 5000000},
    },
    "tunnel": {
        1: {"maatregel": "Periodieke inspectie", "min_eur": 5000, "max_eur": 12000},
        2: {"maatregel": "Preventief onderhoud (verlichting, ventilatie)", "min_eur": 15000, "max_eur": 40000},
        3: {"maatregel": "Klein onderhoud", "min_eur": 50000, "max_eur": 150000},
        4: {"maatregel": "Groot onderhoud (techniek + civil)", "min_eur": 200000, "max_eur": 600000},
        5: {"maatregel": "Renovatie", "min_eur": 1000000, "max_eur": 5000000},
        6: {"maatregel": "Vervanging tunneldak/wanden", "min_eur": 5000000, "max_eur": 20000000},
    },
    "sluis": {
        1: {"maatregel": "Periodieke inspectie", "min_eur": 2000, "max_eur": 4000},
        2: {"maatregel": "Preventief onderhoud (deuren, hydrauliek)", "min_eur": 8000, "max_eur": 20000},
        3: {"maatregel": "Klein onderhoud (afdichtingen)", "min_eur": 25000, "max_eur": 60000},
        4: {"maatregel": "Groot onderhoud (deurkozijnen, mechaniek)", "min_eur": 150000, "max_eur": 400000},
        5: {"maatregel": "Renovatie sluisdeuren / kolk", "min_eur": 500000, "max_eur": 1500000},
        6: {"maatregel": "Vervanging sluis", "min_eur": 2000000, "max_eur": 8000000},
    },
    "duiker": {
        1: {"maatregel": "Periodieke inspectie", "min_eur": 300, "max_eur": 800},
        2: {"maatregel": "Preventief onderhoud (reiniging)", "min_eur": 800, "max_eur": 2000},
        3: {"maatregel": "Reiniging + kleine reparatie", "min_eur": 2500, "max_eur": 8000},
        4: {"maatregel": "Reliner / partial renovatie", "min_eur": 12000, "max_eur": 35000},
        5: {"maatregel": "Renovatie (relining of vervanging stuk)", "min_eur": 40000, "max_eur": 120000},
        6: {"maatregel": "Vervanging duiker", "min_eur": 80000, "max_eur": 250000},
    },
    "kademuur": {
        1: {"maatregel": "Periodieke inspectie", "min_eur": 1500, "max_eur": 3000},
        2: {"maatregel": "Preventief onderhoud", "min_eur": 4000, "max_eur": 10000},
        3: {"maatregel": "Klein onderhoud (voegen, kespen)", "min_eur": 20000, "max_eur": 60000},
        4: {"maatregel": "Groot onderhoud", "min_eur": 100000, "max_eur": 300000},
        5: {"maatregel": "Renovatie kademuur per 50m", "min_eur": 400000, "max_eur": 1200000},
        6: {"maatregel": "Vervanging kademuur per 50m", "min_eur": 1500000, "max_eur": 5000000},
    },
    "gemaal": {
        1: {"maatregel": "Periodieke inspectie + functietest", "min_eur": 1500, "max_eur": 3500},
        2: {"maatregel": "Preventief onderhoud pompen", "min_eur": 5000, "max_eur": 12000},
        3: {"maatregel": "Klein onderhoud (lagers, afdichtingen)", "min_eur": 15000, "max_eur": 40000},
        4: {"maatregel": "Groot onderhoud (pomp-revisie)", "min_eur": 50000, "max_eur": 150000},
        5: {"maatregel": "Renovatie installatie", "min_eur": 200000, "max_eur": 600000},
        6: {"maatregel": "Vervanging gemaal", "min_eur": 800000, "max_eur": 2500000},
    },

    # ───────── RIOLERING (NEN 3399 schaal 1-5) ─────────
    "riolering": {
        1: {"maatregel": "Periodieke inspectie", "min_eur": 5, "max_eur": 15, "unit": "per m"},
        2: {"maatregel": "Reiniging + lichte inspectie", "min_eur": 15, "max_eur": 35, "unit": "per m"},
        3: {"maatregel": "Reiniging + partial reparatie", "min_eur": 50, "max_eur": 150, "unit": "per m"},
        4: {"maatregel": "Relining / partial renovatie", "min_eur": 200, "max_eur": 500, "unit": "per m"},
        5: {"maatregel": "Vervanging streng (open ontgraving)", "min_eur": 800, "max_eur": 2500, "unit": "per m"},
    },

    # ───────── BOMEN (VTA risicoklasse 1-5) ─────────
    "boom": {
        1: {"maatregel": "Periodieke VTA-inspectie", "min_eur": 25, "max_eur": 50, "unit": "per boom"},
        2: {"maatregel": "Preventieve snoei", "min_eur": 80, "max_eur": 200, "unit": "per boom"},
        3: {"maatregel": "Begeleidings-/onderhoudssnoei", "min_eur": 150, "max_eur": 400, "unit": "per boom"},
        4: {"maatregel": "Veiligheidssnoei + her-inspectie 3 mnd", "min_eur": 250, "max_eur": 700, "unit": "per boom"},
        5: {"maatregel": "Kappen + herplanting", "min_eur": 800, "max_eur": 2500, "unit": "per boom"},
        6: {"maatregel": "Acuut kappen + opruimen + herplanting", "min_eur": 1200, "max_eur": 3500, "unit": "per boom"},
    },

    # ───────── OPENBARE VERLICHTING (NEN 3140) ─────────
    "verlichting": {
        1: {"maatregel": "5-jaarlijkse NEN 3140 keuring", "min_eur": 50, "max_eur": 100, "unit": "per mast"},
        2: {"maatregel": "Lampvervanging + kleine reparaties", "min_eur": 150, "max_eur": 300, "unit": "per mast"},
        3: {"maatregel": "LED-conversie", "min_eur": 400, "max_eur": 800, "unit": "per mast"},
        4: {"maatregel": "Armatuur + mast onderhoud", "min_eur": 800, "max_eur": 1800, "unit": "per mast"},
        5: {"maatregel": "Vervanging mast + armatuur", "min_eur": 2000, "max_eur": 4000, "unit": "per mast"},
        6: {"maatregel": "Volledige vervanging incl. fundering + bekabeling", "min_eur": 3500, "max_eur": 7000, "unit": "per mast"},
    },
    "lantaarnpaal": None,  # alias — gevuld onderaan

    # ───────── SPEELTOESTELLEN (NEN-EN 1176) ─────────
    # NEN-EN 1176 gebruikt A/B/C/D klasses — we mappen naar 1-6 (A=1, B=3, C=4, D=6)
    "speeltoestel": {
        1: {"maatregel": "Jaarlijkse routine-inspectie", "min_eur": 50, "max_eur": 100, "unit": "per toestel"},
        2: {"maatregel": "Klein onderhoud + smering", "min_eur": 100, "max_eur": 250, "unit": "per toestel"},
        3: {"maatregel": "Onderdeel-vervanging (ketting, lager)", "min_eur": 300, "max_eur": 800, "unit": "per toestel"},
        4: {"maatregel": "Onveilig — reparatie binnen 1 mnd", "min_eur": 500, "max_eur": 1500, "unit": "per toestel"},
        5: {"maatregel": "Vervanging toestel", "min_eur": 2500, "max_eur": 8000, "unit": "per toestel"},
        6: {"maatregel": "Direct afsluiten + vervangen", "min_eur": 3500, "max_eur": 12000, "unit": "per toestel"},
    },

    # ───────── WEGDEK (CROW 146) ─────────
    "wegvak": {
        1: {"maatregel": "Periodieke visuele inspectie", "min_eur": 1, "max_eur": 3, "unit": "per m2"},
        2: {"maatregel": "Onderhoudssnede + cosmetisch", "min_eur": 5, "max_eur": 12, "unit": "per m2"},
        3: {"maatregel": "Slijtlaag", "min_eur": 18, "max_eur": 35, "unit": "per m2"},
        4: {"maatregel": "Deklaag vervangen", "min_eur": 40, "max_eur": 80, "unit": "per m2"},
        5: {"maatregel": "Tussenlaag + deklaag", "min_eur": 80, "max_eur": 150, "unit": "per m2"},
        6: {"maatregel": "Volledige reconstructie wegvak", "min_eur": 150, "max_eur": 300, "unit": "per m2"},
    },

    # ───────── NEN 2767-4 installaties + CROW 145 ─────────
    "fontein": {
        1: {"maatregel": "Periodieke inspectie", "min_eur": 500, "max_eur": 1200},
        2: {"maatregel": "Reiniging + filter-vervanging", "min_eur": 1500, "max_eur": 4000},
        3: {"maatregel": "Pomp/leidingen klein onderhoud", "min_eur": 5000, "max_eur": 15000},
        4: {"maatregel": "Pomp-revisie + dichtingen", "min_eur": 15000, "max_eur": 40000},
        5: {"maatregel": "Renovatie installatie + bassin", "min_eur": 50000, "max_eur": 150000},
        6: {"maatregel": "Volledige vervanging fontein", "min_eur": 150000, "max_eur": 500000},
    },
    "kunstgrasveld": {
        1: {"maatregel": "Periodieke inspectie + kleine reparatie", "min_eur": 2, "max_eur": 5, "unit": "per m2"},
        2: {"maatregel": "Reiniging + infill bijvullen", "min_eur": 5, "max_eur": 10, "unit": "per m2"},
        3: {"maatregel": "Naden + lijnen herstellen", "min_eur": 15, "max_eur": 25, "unit": "per m2"},
        4: {"maatregel": "Toplaag deelvervanging", "min_eur": 30, "max_eur": 50, "unit": "per m2"},
        5: {"maatregel": "Toplaag vervangen", "min_eur": 60, "max_eur": 90, "unit": "per m2"},
        6: {"maatregel": "Toplaag + shockpad vervangen", "min_eur": 80, "max_eur": 120, "unit": "per m2"},
    },
    "wegmarkering": {
        1: {"maatregel": "Periodieke retroreflectie-meting", "min_eur": 0.5, "max_eur": 1, "unit": "per m"},
        2: {"maatregel": "Cosmetische bijwerking", "min_eur": 1.5, "max_eur": 3, "unit": "per m"},
        3: {"maatregel": "Markering herstellen (locaal)", "min_eur": 4, "max_eur": 8, "unit": "per m"},
        4: {"maatregel": "Markering herstrijken", "min_eur": 8, "max_eur": 15, "unit": "per m"},
        5: {"maatregel": "Vervanging volledige belijning + thermoplast", "min_eur": 15, "max_eur": 30, "unit": "per m"},
        6: {"maatregel": "Vervanging + reflectie-kralen + kattenogen", "min_eur": 25, "max_eur": 50, "unit": "per m"},
    },
}

# Aliassen
KOSTEN["lantaarnpaal"] = KOSTEN["verlichting"]
KOSTEN["kunstwerk"] = KOSTEN["brug"]  # generieke kunstwerk → brug-default
KOSTEN["wegdek"] = KOSTEN["wegvak"]
KOSTEN["verharding"] = KOSTEN["wegvak"]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_maatregel(asset_type: Optional[str],
                  conditie_score: Optional[int]) -> Optional[dict]:
    """Bepaal maatregel + kostenrange voor (asset_type, score).

    Returns None als type onbekend of score < 1.

    >>> m = get_maatregel("brug", 4)
    >>> m["min_eur"]
    50000
    >>> m["max_eur"]
    150000
    >>> get_maatregel("boom", 5)["maatregel"]
    'Kappen + herplanting'
    >>> get_maatregel("onbekend", 3) is None
    True
    >>> get_maatregel("brug", None) is None
    True
    """
    if not asset_type or not conditie_score:
        return None
    key = asset_type.strip().lower()
    if key not in KOSTEN:
        return None
    catalog = KOSTEN[key]
    score_clipped = max(1, min(int(conditie_score), 6))
    # NEN 3399 = 1-5 only
    if key == "riolering" and score_clipped > 5:
        score_clipped = 5
    entry = catalog.get(score_clipped)
    if not entry:
        return None
    # Default unit
    out = dict(entry)
    out.setdefault("unit", "per object")
    out["score"] = score_clipped
    out["asset_type"] = key
    return out


def estimate_total(maatregel: dict, multiplier: float = 1.0) -> dict:
    """Bereken min/max totaal met optionele multiplier (lengte/oppervlak).

    >>> m = get_maatregel("riolering", 4)  # 200-500 per m
    >>> t = estimate_total(m, 50)  # 50m streng
    >>> t["min_total"]
    10000
    >>> t["max_total"]
    25000
    """
    return {
        **maatregel,
        "multiplier": multiplier,
        "min_total": int(maatregel["min_eur"] * multiplier),
        "max_total": int(maatregel["max_eur"] * multiplier),
    }


def is_actionable(score: Optional[int]) -> bool:
    """True voor scores die in MJOP moeten — typisch score 3+.

    Score 1-2 = geen actief budget nodig (preventief gewone routine).
    """
    if score is None:
        return False
    return int(score) >= 3


KOSTEN_VERSION = "mjop-kosten.v1.0-2026-05"
