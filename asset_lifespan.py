"""Richtwaarden verwachte levensduur per asset-type + conditie-schatting.

Bronnen: CROW levensduur-kentallen / NEN 2767 / GWW-praktijk. Dit zijn
RICHTWAARDEN om de Voorspeller te kunnen voeden waar een echte aanlegdatum of
levensduur ontbreekt — bedoeld als startpunt, te vervangen door werkelijke
data (import) of een echte inspectie zodra beschikbaar.

Pure data + functies — geen DB-IO, volledig unit-testbaar.
"""
from __future__ import annotations
from typing import Optional


# Typische technische levensduur in jaren per (genormaliseerd) asset-type.
DEFAULT_LIFESPAN_YEARS = {
    # Verharding
    "wegvak": 30, "wegdek": 30, "verharding": 30, "asfalt": 30, "rijbaan": 30, "fietspad": 30,
    "elementenverharding": 40, "klinker": 40, "bestrating": 40, "tegel": 40, "trottoir": 40,
    # Kunstwerken
    "brug": 80, "viaduct": 80, "aquaduct": 100, "tunnel": 100,
    "sluis": 50, "stuw": 50, "gemaal": 50, "duiker": 60,
    "kademuur": 80, "kade": 80, "kunstwerk": 80,
    # Verlichting & elektra
    "lichtmast": 40, "lantaarnpaal": 40, "verlichting": 40, "armatuur": 20,
    # Riolering
    "riolering": 60, "riool": 60, "vrijvervalriool": 60, "rioolput": 60,
    "inspectieput": 60, "put": 60, "kolk": 45, "overstort": 60,
    # Groen
    "boom": 60, "laanboom": 60,
    # Speel, meubilair & verkeer
    "speeltoestel": 15, "straatmeubilair": 20, "meubilair": 20, "bank": 20,
    "verkeersbord": 15, "bebording": 15, "bord": 15, "geleiderail": 25, "vangrail": 25,
    # Markering & water
    "wegmarkering": 8, "markering": 8, "belijning": 8,
    "beschoeiing": 25, "damwand": 50,
}

_DEFAULT_FALLBACK = 30


def default_lifespan_for(asset_type: Optional[str]) -> Optional[int]:
    """Richtwaarde verwachte levensduur (jaren) voor dit asset-type.

    Eerst exacte match, dan keyword-match (substring), anders een veilige
    fallback van 30 jaar. None alleen bij een leeg asset_type.

    >>> default_lifespan_for("brug")
    80
    >>> default_lifespan_for("Wegvak (asfalt)")
    30
    >>> default_lifespan_for("speeltoestel")
    15
    >>> default_lifespan_for("iets onbekends")
    30
    >>> default_lifespan_for(None) is None
    True
    """
    if not asset_type:
        return None
    key = asset_type.strip().lower()
    if key in DEFAULT_LIFESPAN_YEARS:
        return DEFAULT_LIFESPAN_YEARS[key]
    for kw, yrs in DEFAULT_LIFESPAN_YEARS.items():
        if kw in key:
            return yrs
    return _DEFAULT_FALLBACK


# CROW-klasse → geschatte NEN 2767-conditie (1=als nieuw .. 5=zeer slecht).
# Grof: licht (L) ~2, matig (M) 3-4, ernstig (E) 5.
_CROW_KLASSE_TO_CONDITIE = {
    "L1": 2, "L2": 2, "L3": 3,
    "M1": 3, "M2": 4, "M3": 4,
    "E1": 5, "E2": 5, "E3": 5,
}


def conditie_from_crow_klasse(klasse: Optional[str]) -> Optional[int]:
    """Schat een NEN 2767-conditie (1-5) uit een CROW-klasse.

    >>> conditie_from_crow_klasse("L1")
    2
    >>> conditie_from_crow_klasse("M2")
    4
    >>> conditie_from_crow_klasse("E3")
    5
    >>> conditie_from_crow_klasse(None) is None
    True
    """
    if not klasse:
        return None
    return _CROW_KLASSE_TO_CONDITIE.get(klasse.strip().upper())
