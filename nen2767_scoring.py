"""NEN 2767-4 conditie-score berekening (conditiemeting infrastructuur).

NEN 2767 hanteert een 6-punts conditie-schaal:
    1 = uitstekend (nieuwbouw)   4 = matig
    2 = goed                     5 = slecht
    3 = redelijk                 6 = zeer slecht

Per gebrek wordt geclassificeerd met drie assen:
    ernst (belang)  1 = gering, 2 = serieus, 3 = ernstig  (ligt vast in de
                    NEN 2767-4-2 gebrekenlijst per gebrek)
    intensiteit     1 = beginstadium, 2 = gevorderd, 3 = eindstadium
    omvang_klasse   1 = <2%, 2 = 2-10%, 3 = 10-30%, 4 = 30-70%, 5 = >70%

BEREKENING (conform NEN 2767-1/-4 methodiek; geverifieerd tegen het
Rgd-Handboek Onderhoudsinspecties én een NEN 2767-4-cursusdeck, incl.
een doorgerekend voorbeeld):

1. Één gebrek → conditie (1-6): GEEN vermenigvuldiging, maar een OPZOEK-
   matrix per ernstklasse. De ernst kiest de matrix, intensiteit × omvang
   kruisen daarin. Drie matrices (ernstig loopt tot 6, serieus tot 5,
   gering tot 4). Zie `_MATRIX`.

2. Meerdere gebreken → elementconditie: correctiefactor gewogen naar
   OMVANG% (NIET "slechtste gebrek wint"). Per gebrek een correctiefactor
   (1,00 … 2,00), gewogen naar het aangetaste percentage; het onaangetaste
   restpercentage telt als conditie 1 (factor 1,00). Zie
   `element_score_from_defects`.

VERSIE-historie:
  v1.0  additieve heuristiek + worst-defect (benadering; niet norm-conform)
  v2.0  officiële I/O-matrices + correctiefactor-aggregatie (dit bestand)

OPEN follow-ups (gedocumenteerd, nog niet geïmplementeerd):
  - locatiefactor primair/secundair/tertiair bouwdeel (opstap ±1)
  - verzorgingskwaliteit ("heel & schoon", los van technische conditie)
  - objectaggregatie gewogen naar vervangingswaarde (nu nog worst-element,
    omdat vervangingswaarde per element nog niet wordt vastgelegd)
Exacte matrix-cellen/herleidingsgrenzen: te bevestigen tegen de betaalde
NEN 2767-1/-4-2. De SCORING_VERSION wordt in de audit-trail meegelogd.
"""
from __future__ import annotations
from typing import Iterable, Optional, Sequence, Tuple


SCORING_VERSION = "nen2767-4.v2.0-2026-matrix-aggregatie"

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

# ── NEN 2767 I/O-matrices: ernstklasse kiest de matrix, [intensiteit][omvang] ──
# Rijen = intensiteit 1..3 (begin/gevorderd/eind); kolommen = omvang_klasse 1..5.
# Ernstig loopt tot 6, serieus tot 5, gering tot 4 ("diagonale verschuiving").
_MATRIX = {
    3: [  # ernstig
        [1, 1, 2, 3, 4],   # beginstadium
        [1, 2, 3, 4, 5],   # gevorderd
        [2, 3, 4, 5, 6],   # eindstadium
    ],
    2: [  # serieus
        [1, 1, 1, 2, 3],
        [1, 1, 2, 3, 4],
        [1, 2, 3, 4, 5],
    ],
    1: [  # gering
        [1, 1, 1, 1, 2],
        [1, 1, 1, 2, 3],
        [1, 1, 2, 3, 4],
    ],
}

# Conditie → correctiefactor (NEN 2767-1 bijl. B / -4). Voor omvang-gewogen
# aggregatie van meerdere gebreken op één element.
CORRECTIEFACTOR = {1: 1.00, 2: 1.02, 3: 1.10, 4: 1.30, 5: 1.70, 6: 2.00}

# Representatief percentage per omvangklasse — alleen gebruikt als een gebrek
# wel een klasse maar geen exact percentage heeft (klasse-midden).
_OMVANG_PCT_REP = {1: 1.0, 2: 6.0, 3: 20.0, 4: 50.0, 5: 85.0}


def _clip(score: int) -> int:
    return max(1, min(6, score))


def defect_to_score(ernst: Optional[int],
                    intensiteit: Optional[int],
                    omvang_klasse: Optional[int]) -> Optional[int]:
    """Conditie (1-6) van één gebrek via de NEN 2767 I/O-matrix.

    De ernstklasse kiest de matrix; intensiteit × omvang kruisen daarin.
    Returnt None als één van de drie waarden ontbreekt of ongeldig is — de
    UI kan dan aangeven dat het gebrek nog onvolledig is.

    >>> defect_to_score(1, 1, 1)   # gering, begin, <2%
    1
    >>> defect_to_score(3, 3, 5)   # ernstig, eind, >70%
    6
    >>> defect_to_score(3, 2, 4)   # ernstig, gevorderd, 30-70%  (deck-voorbeeld)
    4
    >>> defect_to_score(2, 2, 3)   # serieus, gevorderd, 10-30%
    2
    >>> defect_to_score(None, 2, 3) is None
    True
    """
    if ernst is None or intensiteit is None or omvang_klasse is None:
        return None
    if ernst not in _MATRIX: return None
    if intensiteit not in (1, 2, 3): return None
    if omvang_klasse not in (1, 2, 3, 4, 5): return None
    return _MATRIX[ernst][intensiteit - 1][omvang_klasse - 1]


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


def representatief_pct(omvang_klasse: Optional[int]) -> Optional[float]:
    """Representatief omvang-% (klasse-midden) voor als een gebrek wel een
    omvangklasse maar geen exact percentage heeft.

    >>> representatief_pct(4)
    50.0
    >>> representatief_pct(None) is None
    True
    """
    if omvang_klasse is None:
        return None
    return _OMVANG_PCT_REP.get(omvang_klasse)


def _relatief_naar_conditie(relatief: float) -> int:
    """Herleid de omvang-gewogen relatieve score naar conditie 1-6.

    Grenzen liggen tussen de opeenvolgende correctiefactoren in. Gevalideerd
    tegen het NEN 2767-4-cursusvoorbeeld (relatief 1,2144 → conditie 4).
    """
    if relatief <= 1.01: return 1
    if relatief <= 1.04: return 2
    if relatief <= 1.15: return 3
    if relatief <= 1.40: return 4
    if relatief <= 1.78: return 5
    return 6


def element_score_from_defects(
        defects: Sequence[Tuple[Optional[int], Optional[float]]]) -> Optional[int]:
    """Elementconditie (1-6) via de NEN 2767 correctiefactor-aggregatie.

    `defects` = lijst van (conditie 1-6, aangetast omvang-percentage). De
    correctiefactor per conditie wordt gewogen naar het aangetaste percentage;
    het onaangetaste restpercentage telt als conditie 1 (factor 1,00):

        relatief = ( Σ omvang%_i · factor(conditie_i) + rest% · 1,00 ) / 100

    Dit vervangt "slechtste gebrek wint": een klein ernstig gebrek weegt nu
    naar rato van zijn omvang mee, precies zoals in jouw leuning-vs-vangrail
    voorbeeld.

    >>> # Deck-voorbeeld: 20% c6 + 8% c3 + 32% c2 + 40% c1 -> 1,2144 -> 4
    >>> element_score_from_defects([(6, 20), (3, 8), (2, 32), (1, 40)])
    4
    >>> # Eén klein ernstig gebrek (conditie 6 op 2%): rest 98% is nieuw
    >>> element_score_from_defects([(6, 2)])
    2
    >>> # Zelfde conditie 6 maar over 60% van het element -> veel zwaarder
    >>> element_score_from_defects([(6, 60)])
    5
    >>> element_score_from_defects([]) is None
    True
    """
    bruikbaar = [(c, p) for (c, p) in defects
                 if c in CORRECTIEFACTOR and p is not None and p > 0]
    if not bruikbaar:
        return None
    som_pct = sum(p for _, p in bruikbaar)
    rest = max(0.0, 100.0 - som_pct)
    teller = sum(p * CORRECTIEFACTOR[c] for c, p in bruikbaar) + rest * 1.0
    relatief = teller / 100.0
    return _relatief_naar_conditie(relatief)


def element_score(defect_scores: Iterable[Optional[int]]) -> Optional[int]:
    """Aggregeer losse conditiescores naar één element-score (worst-defect).

    Fallback voor checklist-only elementen (score_1_6 zonder omvang%). Zodra
    er gebreken mét omvang zijn, gebruikt de caller `element_score_from_defects`.

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
