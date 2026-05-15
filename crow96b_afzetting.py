"""CROW 96b — verkeersmaatregelen bij wegwerkzaamheden.

Genereert een afzettingsplan op basis van wegtype + werk-categorie. Per
type werk de standaard CROW 96b-figuur + benodigde verkeersborden +
maatregelen. Voor uitvoerders die op locatie een veilige afzetting moeten
plaatsen.

Volgens CROW publicatie 96b — Werk in uitvoering — verkeersmaatregelen.
Op de openbare weg verplicht bij elke ingreep > 4 uur.
"""
from __future__ import annotations
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Werk-categorieën met benodigde standaard-figuur
# ─────────────────────────────────────────────────────────────────────────────

# Figuur-nummer verwijst naar CROW 96b-pagina (1-9 voor binnen bebouwde kom,
# 10-30 voor buiten bebouwde kom). Hieronder de meest gangbare voor gemeentes:

AFZETTING_FIGUREN = {
    # ── Binnen bebouwde kom (50 km/u of lager) ──
    "kortdurend_trottoir": {
        "naam": "Kortdurend werk op trottoir",
        "figuur": "CROW 96b fig. 02",
        "duur_max_uur": 4,
        "wegtype": "voetpad / trottoir",
        "borden": ["L09 (rood/wit afzetbalk)", "B02 (werk in uitvoering)"],
        "veiligheid": ["Reflectiehesje", "Werkschoenen"],
        "lkw_voertuig": False,
    },
    "kortdurend_fietspad": {
        "naam": "Kortdurend werk op fietspad",
        "figuur": "CROW 96b fig. 04",
        "duur_max_uur": 4,
        "wegtype": "fietspad",
        "borden": ["L02 (fietsers afstappen)", "L09 (afzetbalk)", "B02"],
        "veiligheid": ["Reflectiehesje", "Helm bij hoofdwerk"],
        "lkw_voertuig": False,
    },
    "kortdurend_rijbaan_binnen": {
        "naam": "Kortdurend werk op rijbaan (binnen bebouwde kom)",
        "figuur": "CROW 96b fig. 06",
        "duur_max_uur": 4,
        "wegtype": "rijbaan ≤ 50 km/u",
        "borden": ["B02 (werk in uitvoering)", "F10 (rij-richting)", "L09"],
        "veiligheid": ["Hesje", "Helm", "Veiligheidsschoenen", "LKW indien > 2 personen"],
        "lkw_voertuig": True,
    },
    "langdurend_rijbaan_binnen": {
        "naam": "Langdurig werk binnen bebouwde kom (> 4 uur)",
        "figuur": "CROW 96b fig. 08",
        "duur_max_uur": None,
        "wegtype": "rijbaan ≤ 50 km/u",
        "borden": ["B02", "A01 (omleiding)", "G07 (rijbaan-versmalling)",
                   "L09", "Verkeers-regelaar bij > 100 voertuigen/uur"],
        "veiligheid": ["Volledige TVM"],
        "lkw_voertuig": True,
        "vergunning_nodig": True,
    },

    # ── Buiten bebouwde kom (80 km/u of hoger) ──
    "kortdurend_buiten": {
        "naam": "Kortdurend werk buiten bebouwde kom",
        "figuur": "CROW 96b fig. 14",
        "duur_max_uur": 4,
        "wegtype": "rijbaan 80 km/u",
        "borden": ["B02", "A01 (omleidings-bord)", "A07 (smaller wegvak)",
                   "L09 + LKW-voertuig verplicht"],
        "veiligheid": ["LKW-voertuig achterop", "Reflectiehesje", "Helm"],
        "lkw_voertuig": True,
        "vergunning_nodig": True,
    },
    "langdurend_buiten": {
        "naam": "Langdurig werk buiten bebouwde kom",
        "figuur": "CROW 96b fig. 22",
        "duur_max_uur": None,
        "wegtype": "rijbaan 80 km/u",
        "borden": ["Volledige TVM met snelheidsverlaging",
                   "G07 + G09 (rijbaan-omleiding)",
                   "L02 + L09 met LED-verlichting",
                   "Vooraanwijzing 100m + 200m + 400m"],
        "veiligheid": ["TVM-coördinator op locatie"],
        "lkw_voertuig": True,
        "vergunning_nodig": True,
    },

    # ── Specifiek voor inspectie / klein onderhoud ──
    "inspectie_brug": {
        "naam": "Inspectie kunstwerk (brug/viaduct)",
        "figuur": "CROW 96b fig. 02 + locale aanpassing",
        "duur_max_uur": 8,
        "wegtype": "kunstwerk",
        "borden": ["B02", "L09 bij water-werkzaamheden"],
        "veiligheid": ["Zwemvest bij waterzijde", "Valharnas bij hoog werk"],
        "lkw_voertuig": False,
    },
    "spoedreparatie": {
        "naam": "Spoedreparatie (acute melding)",
        "figuur": "CROW 96b fig. 06 — versneld",
        "duur_max_uur": 4,
        "wegtype": "varieert",
        "borden": ["B02 mobiel", "L09 mobiel", "Gemeentelijke melding"],
        "veiligheid": ["TVM minimaal binnen 1 uur"],
        "lkw_voertuig": True,
    },
}


def get_afzetting(work_type: Optional[str]) -> Optional[dict]:
    """Geef CROW 96b afzettings-figuur voor werktype.

    >>> get_afzetting("inspectie_brug")["figuur"]
    'CROW 96b fig. 02 + locale aanpassing'
    >>> get_afzetting("onbekend") is None
    True
    """
    if not work_type:
        return None
    key = work_type.strip().lower().replace(" ", "_").replace("-", "_")
    return AFZETTING_FIGUREN.get(key)


def list_categories() -> list[dict]:
    """Lijst alle beschikbare werk-categorieën voor UI-dropdown."""
    return [{"key": k, **v} for k, v in AFZETTING_FIGUREN.items()]


CROW96B_VERSION = "crow96b.v1.0-2026-05"
