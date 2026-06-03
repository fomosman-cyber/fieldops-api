"""CAR2023 beeldkwaliteit — wegmeubilair-schouw.

Beeldkwaliteit (CROW-systematiek, kwaliteitscatalogus openbare ruimte) beoordeelt
de openbare ruimte op een vaste schaal A+ … D in plaats van de NEN 2767-conditie
(1-6). Een schouw van wegmeubilair (verkeersborden, lichtmasten, banken, afvalbakken,
palen, abri's …) scoort per object enkele aspecten (heelheid, reinheid, stabiliteit,
functievervulling) en bepaalt het eindoordeel via de worst-aspect-regel.

Gemeenten kiezen een ambitieniveau (vaak B = 'Basis'); een object voldoet als de
beeldkwaliteit gelijk of hoger is dan de ambitie.

Deze module is data-only (geen DB-IO / FastAPI) zodat hij los testbaar is; de API
zit in routers/car2023_router.py.
"""
from __future__ import annotations

from typing import Optional

# Beeldkwaliteitsklassen — hoog naar laag. `score` alleen voor onderlinge vergelijking.
BEELDKWALITEIT_KLASSEN = [
    {"klasse": "A+", "naam": "Zeer hoog (nieuwstaat)", "score": 5},
    {"klasse": "A", "naam": "Hoog", "score": 4},
    {"klasse": "B", "naam": "Basis (voldoende)", "score": 3},
    {"klasse": "C", "naam": "Laag (onvoldoende)", "score": 2},
    {"klasse": "D", "naam": "Zeer laag (slecht)", "score": 1},
]

KLASSE_CODES = [k["klasse"] for k in BEELDKWALITEIT_KLASSEN]
_KLASSE_SCORE = {k["klasse"]: k["score"] for k in BEELDKWALITEIT_KLASSEN}
_SCORE_KLASSE = {k["score"]: k["klasse"] for k in BEELDKWALITEIT_KLASSEN}

DEFAULT_AMBITIE = "B"

# Schaalbalk-aspecten (technische staat + verzorging) — de schouwer geeft per aspect
# een beeldkwaliteitsklasse.
ASPECTEN = [
    {"code": "heelheid", "naam": "Heelheid",
     "uitleg": "Schade, deuken, breuk, corrosie, ontbrekende delen."},
    {"code": "reinheid", "naam": "Reinheid / verzorging",
     "uitleg": "Graffiti, stickers, vuil, aanslag, onkruid rond de voet."},
    {"code": "stabiliteit", "naam": "Stabiliteit / stand",
     "uitleg": "Scheefstand, loszittend, verzakte of beschadigde fundering."},
    {"code": "functie", "naam": "Functievervulling",
     "uitleg": "Leesbaarheid/zichtbaarheid, werking, retroreflectie, volledigheid."},
]
_ASPECT_CODES = {a["code"] for a in ASPECTEN}

# Wegmeubilair-objecttypen met de relevante aspecten per type.
WEGMEUBILAIR_TYPES = {
    "verkeersbord":   {"naam": "Verkeersbord", "aspecten": ["heelheid", "reinheid", "stabiliteit", "functie"]},
    "lichtmast":      {"naam": "Lichtmast / armatuur", "aspecten": ["heelheid", "reinheid", "stabiliteit", "functie"]},
    "bewegwijzering": {"naam": "Bewegwijzering (ANWB)", "aspecten": ["heelheid", "reinheid", "stabiliteit", "functie"]},
    "afvalbak":       {"naam": "Afvalbak", "aspecten": ["heelheid", "reinheid", "stabiliteit"]},
    "zitbank":        {"naam": "Zitbank", "aspecten": ["heelheid", "reinheid", "stabiliteit"]},
    "paal_poller":    {"naam": "Paal / poller / afzetting", "aspecten": ["heelheid", "reinheid", "stabiliteit"]},
    "fietsenrek":     {"naam": "Fietsenrek / nietje", "aspecten": ["heelheid", "reinheid", "stabiliteit"]},
    "abri":           {"naam": "Abri / wachthuisje", "aspecten": ["heelheid", "reinheid", "stabiliteit", "functie"]},
    "hekwerk":        {"naam": "Hekwerk / leuning", "aspecten": ["heelheid", "reinheid", "stabiliteit"]},
    "speelaanleiding": {"naam": "Speelaanleiding (straatmeubilair)", "aspecten": ["heelheid", "reinheid", "stabiliteit"]},
}


def aspecten_voor(objecttype: Optional[str]) -> list[dict]:
    """Relevante aspect-definities voor een wegmeubilair-type (leeg bij onbekend)."""
    spec = WEGMEUBILAIR_TYPES.get((objecttype or "").strip().lower())
    if not spec:
        return []
    wanted = set(spec["aspecten"])
    return [a for a in ASPECTEN if a["code"] in wanted]


def overall_klasse(aspect_klassen: dict) -> Optional[str]:
    """Eindoordeel = slechtste aspect (worst-aspect-regel uit de beeldsystematiek).

    `aspect_klassen` = {aspect_code: klasse}. Onbekende aspecten/klassen worden
    genegeerd. Geeft None als er niets bruikbaars is.
    """
    scores = [_KLASSE_SCORE[v] for k, v in (aspect_klassen or {}).items()
              if k in _ASPECT_CODES and v in _KLASSE_SCORE]
    if not scores:
        return None
    return _SCORE_KLASSE[min(scores)]


def voldoet_aan_ambitie(klasse: Optional[str], ambitie: str = DEFAULT_AMBITIE) -> bool:
    """True als de beeldkwaliteit gelijk of hoger is dan het ambitieniveau."""
    if klasse not in _KLASSE_SCORE or ambitie not in _KLASSE_SCORE:
        return False
    return _KLASSE_SCORE[klasse] >= _KLASSE_SCORE[ambitie]


def beoordeel(objecttype: Optional[str], aspect_klassen: dict,
              ambitie: str = DEFAULT_AMBITIE) -> dict:
    """Volledige beoordeling van één geschouwd object."""
    overall = overall_klasse(aspect_klassen)
    relevante = {a["code"] for a in aspecten_voor(objecttype)}
    ontbrekend = sorted(relevante - set((aspect_klassen or {}).keys())) if relevante else []
    return {
        "objecttype": objecttype,
        "overall_klasse": overall,
        "ambitie": ambitie,
        "voldoet": voldoet_aan_ambitie(overall, ambitie),
        "ontbrekende_aspecten": ontbrekend,
    }
