"""NEN 2767-2 conditie-score berekening.

NEN 2767 hanteert een 6-punts conditie-schaal:
    1 = uitstekend (nieuwbouw)
    2 = goed
    3 = redelijk
    4 = matig
    5 = slecht
    6 = zeer slecht

Per gebrek wordt geclassificeerd met drie assen:
    ernst       1 = gering, 2 = serieus, 3 = ernstig
    intensiteit 1 = beginstadium, 2 = gevorderd, 3 = eindstadium
    omvang_klasse  1 = <2%, 2 = 2-10%, 3 = 10-30%, 4 = 30-70%, 5 = >70%

De officiële NEN-tabel is geen open data, daarom hanteren wij een
benaderende formule die in lijn is met het schema in NEN 2767-1 Bijlage A:

    base[ernst]    + intensiteit_bump  + omvang_bump

Resultaat wordt geclipt naar [1, 6]. Voor compliance-doeleinden wordt de
exacte formule (versie + datum) meegelogd in de audit-trail zodat een
toekomstige tabel-wijziging traceerbaar is.

Aggregatie van defect-score → element-score → object-score volgt de
"slechtste-gebrek-regel" (worst-defect). Dit is gangbaar voor visuele
periodieke inspecties; voor risk-based prioritering kan een gewogen
formule worden ingezet (zie predictive.py).
"""
from __future__ import annotations
from typing import Iterable, Optional


SCORING_VERSION = "nen2767-2.v1.0-2026"

# Conditie-schaal labels (NL) — voor PDF + UI
CONDITIE_LABELS = {
    1: "Uitstekend",
    2: "Goed",
    3: "Redelijk",
    4: "Matig",
    5: "Slecht",
    6: "Zeer slecht",
}

CONDITIE_KLEUREN = {
    1: "#16a34a",  # groen
    2: "#65a30d",  # licht-groen
    3: "#ca8a04",  # geel-oranje
    4: "#ea580c",  # oranje
    5: "#dc2626",  # rood
    6: "#7f1d1d",  # donker-rood
}

# Ernst → basis-score
_ERNST_BASE = {1: 1, 2: 3, 3: 5}
# Intensiteit → bump
_INTENSITEIT_BUMP = {1: 0, 2: 1, 3: 1}
# Omvang-klasse → bump (groter dan 30% van element-oppervlak → +1)
_OMVANG_BUMP = {1: 0, 2: 0, 3: 0, 4: 1, 5: 1}


def _clip(score: int) -> int:
    return max(1, min(6, score))


def defect_to_score(ernst: Optional[int],
                    intensiteit: Optional[int],
                    omvang_klasse: Optional[int]) -> Optional[int]:
    """Bereken defect-score (1-6) uit NEN 2767-2 ernst/intensiteit/omvang.

    Returnt None als één van de drie waarden ontbreekt — de UI kan dan
    aangeven dat het gebrek nog onvolledig is.

    >>> defect_to_score(1, 1, 1)
    1
    >>> defect_to_score(3, 3, 5)
    6
    >>> defect_to_score(2, 2, 3)
    4
    >>> defect_to_score(None, 2, 3) is None
    True
    """
    if ernst is None or intensiteit is None or omvang_klasse is None:
        return None
    if ernst not in _ERNST_BASE: return None
    if intensiteit not in _INTENSITEIT_BUMP: return None
    if omvang_klasse not in _OMVANG_BUMP: return None
    return _clip(_ERNST_BASE[ernst] + _INTENSITEIT_BUMP[intensiteit] + _OMVANG_BUMP[omvang_klasse])


def omvang_klasse_from_percentage(pct: Optional[float]) -> Optional[int]:
    """Map een percentage (0-100) op omvang-klasse 1-5.

    >>> omvang_klasse_from_percentage(1.5)
    1
    >>> omvang_klasse_from_percentage(5)
    2
    >>> omvang_klasse_from_percentage(20)
    3
    >>> omvang_klasse_from_percentage(50)
    4
    >>> omvang_klasse_from_percentage(80)
    5
    """
    if pct is None:
        return None
    if pct < 2: return 1
    if pct < 10: return 2
    if pct < 30: return 3
    if pct < 70: return 4
    return 5


def element_score(defect_scores: Iterable[Optional[int]]) -> Optional[int]:
    """Aggregeer defect-scores naar één element-score (worst-defect rule).

    Een element zonder defecten met score=None wordt impliciet behandeld als 1
    (uitstekend) door de caller — wij retourneren hier None om "geen data"
    expliciet te houden.

    >>> element_score([1, 3, 4]) == 4
    True
    >>> element_score([None, 2, None]) == 2
    True
    >>> element_score([None, None]) is None
    True
    >>> element_score([]) is None
    True
    """
    valid = [s for s in defect_scores if s is not None]
    if not valid:
        return None
    return max(valid)


def object_score(element_scores: Iterable[Optional[int]]) -> Optional[int]:
    """Aggregeer element-scores naar één object-conditiescore.

    Default: worst-element rule. Constructieve elementen (groep="constructief")
    wegen impliciet het zwaarst omdat een ernstig constructief gebrek meestal
    al de hoogste score heeft.

    >>> object_score([1, 1, 2, 3]) == 3
    True
    >>> object_score([None, 2]) == 2
    True
    >>> object_score([]) is None
    True
    """
    valid = [s for s in element_scores if s is not None]
    if not valid:
        return None
    return max(valid)


def conditie_label(score: Optional[int]) -> str:
    if score is None:
        return "—"
    return CONDITIE_LABELS.get(score, f"Score {score}")


def conditie_color(score: Optional[int]) -> str:
    if score is None:
        return "#94a3b8"
    return CONDITIE_KLEUREN.get(score, "#94a3b8")


def maatregel_advies(score: Optional[int]) -> dict:
    """Map object-conditie op een onderhouds-advies.

    Voor de PDF-rapportage en UI; conformiteit met CROW 134 maatregelmatrix.
    """
    if score is None:
        return {"categorie": "onbepaald", "termijn_jaren": None,
                "actie": "Inspectie onvolledig — vervolg nodig"}
    if score == 1:
        return {"categorie": "geen", "termijn_jaren": 6,
                "actie": "Geen onderhoud nodig; standaard her-inspectie binnen 6 jaar"}
    if score == 2:
        return {"categorie": "preventief", "termijn_jaren": 4,
                "actie": "Lichte preventieve maatregelen; her-inspectie binnen 4 jaar"}
    if score == 3:
        return {"categorie": "klein-onderhoud", "termijn_jaren": 2,
                "actie": "Klein onderhoud inplannen; her-inspectie binnen 2 jaar"}
    if score == 4:
        return {"categorie": "groot-onderhoud", "termijn_jaren": 1,
                "actie": "Groot onderhoud nodig binnen 12 maanden"}
    if score == 5:
        return {"categorie": "renovatie", "termijn_jaren": 1,
                "actie": "Renovatie / vervanging plannen; her-inspectie na 6 maanden"}
    if score == 6:
        return {"categorie": "acuut", "termijn_jaren": 0,
                "actie": "Acute maatregelen vereist — eventueel veiligheidsmaatregel"}
    return {"categorie": "onbepaald", "termijn_jaren": None, "actie": ""}


# Tekst-labels voor UI-pickers
ERNST_LABELS = {
    1: "Gering — beperkte invloed",
    2: "Serieus — functioneel relevant",
    3: "Ernstig — gevaar voor functie/veiligheid",
}

INTENSITEIT_LABELS = {
    1: "Beginstadium",
    2: "Gevorderd",
    3: "Eindstadium",
}

OMVANG_KLASSE_LABELS = {
    1: "<2% van element",
    2: "2 – 10%",
    3: "10 – 30%",
    4: "30 – 70%",
    5: ">70% van element",
}
