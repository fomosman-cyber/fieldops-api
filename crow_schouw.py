"""Beeldkwaliteit op gebiedsniveau — de "schoon"-kant van de CROW-schouw.

`car2023.py` beoordeelt losse objecten: een verkeersbord, een lichtmast, een
bank. Dat is de "heel"-kant. Een schouw gaat ook over wat er *tussen en op* de
objecten zit: zwerfafval, onkruid tussen de klinkers, graffiti op een nutskast,
bijplaatsing naast een container. Dat wordt per **straat of vak** beoordeeld in
plaats van per asset.

Beide gebruiken dezelfde schaal en vullen elkaar aan.

**Een meetlat is een verschijnsel op een drager, niet alleen een verschijnsel.**
Dat is de belangrijkste eigenschap van dit bestand en het kostte een ronde om te
zien. De Kwaliteitscatalogus Openbare Ruimte kent geen meetlat "graffiti", maar
graffiti op een afvalbak, op een verkeersbord, op een nutskast, op een lichtmast
en op een viaduct -- vijf meetlatten. Terecht, want de norm én de maatregel
verschillen: een sticker van een bord halen is iets anders dan een viaduct
reinigen. Hetzelfde geldt voor zwerfafval (op gesloten verharding, op
elementenverharding, in groen, in water) en voor scheefstand (borddrager,
lichtmast, hekwerk, paal).

Een gemeente die op deze catalogus contracteert rekent per meetlat af. Wie ze
samenvoegt tot één "bekladding" levert een rapport waar geen aannemer op
aangesproken kan worden.

**Detectieklassen staan daar los van.** Wat een camera of een inspecteur
herkent -- "hier zit graffiti op" -- is grover dan de meetlat. `DETECTIEKLASSEN`
vertaalt daarom van waarneming naar meetlat, met de drager als sleutel. Zo blijft
de invoer eenvoudig en de uitvoer contractwaardig.

**Over de grenswaarden.** De catalogus legt per meetlat vast hoeveel stuks of
welk percentage nog bij welk niveau hoort. Die tabellen zijn auteursrechtelijk
beschermd en staan hier dus niet in. Belangrijker: in de praktijk is het bestek
leidend en dat wijkt regelmatig af. Dit bestand levert de *structuur* en laat de
organisatie haar eigen grenzen invullen via `Drempels`.

Data-only: geen database, geen FastAPI, zodat hij los testbaar is. Zie
`car2023` voor de objectkant en `nen2767_scoring` voor conditie.
"""

from __future__ import annotations

from typing import Optional

SCHOUW_VERSION = "crow-schouw.v2-2026-09-dragers"

from car2023 import (BEELDKWALITEIT_KLASSEN, DEFAULT_AMBITIE,  # noqa: F401
                     KLASSE_CODES, _KLASSE_SCORE, _SCORE_KLASSE)


# ---------------------------------------------------------------------------
# Gebiedstypen
# ---------------------------------------------------------------------------

GEBIEDSTYPEN: dict[str, dict] = {
    "centrum": {"naam": "Centrum en winkelgebied", "gangbare_ambitie": "A",
                "uitleg": "Veel publiek, vervuiling valt direct op."},
    "woonwijk": {"naam": "Woonwijk", "gangbare_ambitie": "B",
                 "uitleg": "Het meest voorkomende ambitieniveau in bestekken."},
    "bedrijventerrein": {"naam": "Bedrijventerrein", "gangbare_ambitie": "C",
                         "uitleg": "Weinig verblijf, vooral doorgaand verkeer."},
    "hoofdweg": {"naam": "Hoofdweg en invalsroute", "gangbare_ambitie": "B",
                 "uitleg": "Visitekaartje, maar lastig te onderhouden door de "
                           "verkeersintensiteit."},
    "park": {"naam": "Park en groengebied", "gangbare_ambitie": "B"},
    "station": {"naam": "Stationsgebied en OV-knooppunt", "gangbare_ambitie": "A"},
    "buitengebied": {"naam": "Buitengebied", "gangbare_ambitie": "C"},
}


# ---------------------------------------------------------------------------
# Dragers — waarop of waarin een verschijnsel zich voordoet
# ---------------------------------------------------------------------------

DRAGERS: dict[str, str] = {
    # ondergronden
    "gesloten_verharding":  "Gesloten verharding (asfalt, beton)",
    "elementenverharding":  "Elementenverharding (klinkers, tegels)",
    "goot_rand":            "Goot en verhardingsrand",
    "groen":                "Groenvak en beplanting",
    "gras":                 "Gras",
    "water":                "Water en oever",
    "boomspiegel":          "Boomspiegel",
    # objecten
    "afvalbak":             "Afvalbak",
    "container":            "Wijk- of ondergrondse container",
    "verkeersbord":         "Verkeersbord",
    "bord_drager":          "Bord- en meubilairdrager",
    "lichtmast":            "Lichtmast en verlichting",
    "nutskast":             "Nuts- en schakelkast",
    "hekwerk":              "Hekwerk en geleide-element",
    "abri":                 "Abri en wachtvoorziening",
    "zitbank":              "Zitbank",
    "obstakel":             "Paal, poller of ander obstakel",
    # kunstwerken
    "viaduct_brug":         "Viaduct en brug",
    "keermuur_scherm":      "Keermuur en geluidsscherm",
    "tunnel":               "Tunnel en onderdoorgang",
    # overig
    "kolk":                 "Kolk en straatput",
    "markering":            "Wegmarkering",
}


# ---------------------------------------------------------------------------
# Verschijnselen — wat je waarneemt
# ---------------------------------------------------------------------------

VERSCHIJNSELEN: dict[str, dict] = {
    "zwerfafval":   {"naam": "Zwerfafval", "groep": "schoon",
                     "eenheid": "stuks_per_100m2"},
    "grofvuil":     {"naam": "Grofvuil en dumping", "groep": "schoon",
                     "eenheid": "stuks_per_locatie"},
    "bijplaatsing": {"naam": "Bijplaatsing", "groep": "schoon",
                     "eenheid": "stuks_per_locatie"},
    "veegvuil":     {"naam": "Veegvuil en natuurlijk afval", "groep": "schoon",
                     "eenheid": "percentage_oppervlak"},
    "bekladding":   {"naam": "Beplakking en graffiti", "groep": "schoon",
                     "eenheid": "percentage_oppervlak"},
    "onkruid":      {"naam": "Onkruid", "groep": "groen",
                     "eenheid": "percentage_oppervlak"},
    "gras_hoogte":  {"naam": "Grashoogte", "groep": "groen",
                     "eenheid": "hoogte_cm"},
    "overgroei":    {"naam": "Overgroei en uitgroei", "groep": "groen",
                     "eenheid": "percentage_oppervlak"},
    "blad":         {"naam": "Bladophoping", "groep": "schoon",
                     "eenheid": "percentage_oppervlak"},
    "scheefstand":  {"naam": "Scheefstand en standzekerheid", "groep": "heel",
                     "eenheid": "klasse_direct"},
    "slijtage":     {"naam": "Slijtage en zichtbaarheid", "groep": "heel",
                     "eenheid": "klasse_direct"},
    "verharding_heel": {"naam": "Heelheid verharding", "groep": "heel",
                        "eenheid": "stuks_per_100m2"},
    "verstopping":  {"naam": "Verstopping", "groep": "heel",
                     "eenheid": "percentage_oppervlak"},
    "uitwerpselen": {"naam": "Uitwerpselen", "groep": "schoon",
                     "eenheid": "stuks_per_100m2"},
}


def _m(verschijnsel: str, drager: str, *, detecteerbaar: bool = True,
       uitleg: Optional[str] = None) -> dict:
    v = VERSCHIJNSELEN[verschijnsel]
    return {
        "code": f"{verschijnsel}.{drager}",
        "verschijnsel": verschijnsel,
        "drager": drager,
        "naam": f"{v['naam']} — {DRAGERS[drager]}",
        "groep": v["groep"],
        "eenheid": v["eenheid"],
        "detecteerbaar": detecteerbaar,
        "uitleg": uitleg,
    }


# `klasse_direct` betekent: het model of de inspecteur geeft meteen A t/m D in
# plaats van een telling. Bij scheefstand en slijtage is dat de enige zinnige
# invoer -- niemand meet graden scheefstand op straat.

MEETLATTEN: list[dict] = [
    # ── Zwerfafval, per ondergrond ──────────────────────────────────
    _m("zwerfafval", "gesloten_verharding"),
    _m("zwerfafval", "elementenverharding"),
    _m("zwerfafval", "groen"),
    _m("zwerfafval", "gras"),
    _m("zwerfafval", "water",
       uitleg="Drijfvuil en afval op de oever. Vanaf de kant lastig te zien; "
              "beoordeel alleen wat je echt kunt waarnemen."),
    _m("veegvuil", "goot_rand",
       uitleg="Blad, zand en fijn afval dat zich in de goot verzamelt. De "
              "meetlat waar een veegronde op wordt afgerekend."),

    # ── Grofvuil en bijplaatsing ────────────────────────────────────
    _m("grofvuil", "gesloten_verharding"),
    _m("grofvuil", "elementenverharding"),
    _m("grofvuil", "groen"),
    _m("bijplaatsing", "container",
       uitleg="Zakken, dozen en huisraad rond een container. Vraagt vrijwel "
              "altijd een aparte melding, geen veegronde."),

    # ── Beplakking en graffiti, per drager ──────────────────────────
    _m("bekladding", "afvalbak"),
    _m("bekladding", "container"),
    _m("bekladding", "verkeersbord",
       uitleg="Alleen het bordvlak. Een beplakte paal valt onder bord_drager."),
    _m("bekladding", "bord_drager"),
    _m("bekladding", "lichtmast"),
    _m("bekladding", "nutskast"),
    _m("bekladding", "abri"),
    _m("bekladding", "viaduct_brug"),
    _m("bekladding", "keermuur_scherm"),
    _m("bekladding", "tunnel"),

    # ── Onkruid en groen ────────────────────────────────────────────
    _m("onkruid", "gesloten_verharding"),
    _m("onkruid", "elementenverharding"),
    _m("onkruid", "obstakel",
       uitleg="Onkruid tegen palen, masten en meubilair. Vaak de plek waar de "
              "borstelmachine niet komt."),
    _m("onkruid", "goot_rand"),
    _m("onkruid", "groen"),
    _m("onkruid", "boomspiegel"),
    _m("gras_hoogte", "gras",
       uitleg="Beoordeel tegen de afgesproken maaifrequentie, niet tegen een "
              "vast getal."),
    _m("overgroei", "elementenverharding",
       uitleg="Beplanting die over het trottoir groeit en de doorloopbreedte "
              "beperkt."),
    _m("blad", "gesloten_verharding"),
    _m("blad", "elementenverharding",
       uitleg="Seizoensgebonden. Beoordeel tegen de afspraak voor het seizoen."),

    # ── Heelheid en verkeer ─────────────────────────────────────────
    _m("scheefstand", "lichtmast"),
    _m("scheefstand", "bord_drager"),
    _m("scheefstand", "obstakel"),
    _m("scheefstand", "hekwerk"),
    _m("slijtage", "markering",
       uitleg="Zichtbaarheid van de markering. Bij nat wegdek en tegenlicht "
              "onbetrouwbaar te beoordelen."),
    _m("verharding_heel", "elementenverharding",
       uitleg="Losliggende of ontbrekende elementen en struikelgevaar. Bij een "
              "echt gebrek hoort een CROW 146-inspectie, niet alleen een "
              "beeldscore."),
    _m("verharding_heel", "gesloten_verharding"),
    _m("verstopping", "kolk",
       uitleg="Dichtgeslibde of overgroeide kolk. Van bovenaf beperkt te zien; "
              "een dichte kolk merk je pas bij regen."),

    # ── Niet uit beeld te halen ─────────────────────────────────────
    _m("uitwerpselen", "elementenverharding", detecteerbaar=False,
       uitleg="Niet betrouwbaar uit beeld te halen; blijft handwerk."),
    _m("uitwerpselen", "gras", detecteerbaar=False),
]

_MEETLAT_CODES = {m["code"] for m in MEETLATTEN}
_PER_CODE = {m["code"]: m for m in MEETLATTEN}


# ---------------------------------------------------------------------------
# Detectieklassen — van waarneming naar meetlat
# ---------------------------------------------------------------------------
# Een camera of een inspecteur ziet "hier ligt afval" of "hier zit graffiti op".
# Welke meetlat dat is, hangt af van de drager. Deze laag maakt de invoer
# eenvoudig zonder de uitvoer grof te maken.

DETECTIEKLASSEN: dict[str, dict] = {
    "afval_los": {
        "naam": "Los zwerfafval",
        "verschijnsel": "zwerfafval",
        "dragers": ["gesloten_verharding", "elementenverharding", "groen",
                    "gras", "water"],
        "standaard_drager": "elementenverharding",
    },
    "afval_grof": {
        "naam": "Grofvuil en dumping",
        "verschijnsel": "grofvuil",
        "dragers": ["gesloten_verharding", "elementenverharding", "groen"],
        "standaard_drager": "elementenverharding",
    },
    "afval_bijplaatsing": {
        "naam": "Bijplaatsing bij een container",
        "verschijnsel": "bijplaatsing",
        "dragers": ["container"],
        "standaard_drager": "container",
    },
    "randvervuiling": {
        "naam": "Vervuiling langs de rand",
        "verschijnsel": "veegvuil",
        "dragers": ["goot_rand"],
        "standaard_drager": "goot_rand",
    },
    "graffiti": {
        "naam": "Beplakking en graffiti",
        "verschijnsel": "bekladding",
        "dragers": ["afvalbak", "container", "verkeersbord", "bord_drager",
                    "lichtmast", "nutskast", "abri", "viaduct_brug",
                    "keermuur_scherm", "tunnel"],
        "standaard_drager": None,   # zonder drager niet te scoren
    },
    "onkruid": {
        "naam": "Onkruid",
        "verschijnsel": "onkruid",
        "dragers": ["gesloten_verharding", "elementenverharding", "obstakel",
                    "goot_rand", "groen", "boomspiegel"],
        "standaard_drager": "elementenverharding",
    },
    "gras": {
        "naam": "Grashoogte",
        "verschijnsel": "gras_hoogte",
        "dragers": ["gras"],
        "standaard_drager": "gras",
    },
    "overgroei": {
        "naam": "Overgroei over het trottoir",
        "verschijnsel": "overgroei",
        "dragers": ["elementenverharding"],
        "standaard_drager": "elementenverharding",
    },
    "blad": {
        "naam": "Bladophoping",
        "verschijnsel": "blad",
        "dragers": ["gesloten_verharding", "elementenverharding"],
        "standaard_drager": "elementenverharding",
    },
    "scheefstand": {
        "naam": "Scheefstand",
        "verschijnsel": "scheefstand",
        "dragers": ["lichtmast", "bord_drager", "obstakel", "hekwerk"],
        "standaard_drager": None,
    },
    "markering": {
        "naam": "Slijtage wegmarkering",
        "verschijnsel": "slijtage",
        "dragers": ["markering"],
        "standaard_drager": "markering",
    },
    "verharding": {
        "naam": "Schade aan de verharding",
        "verschijnsel": "verharding_heel",
        "dragers": ["elementenverharding", "gesloten_verharding"],
        "standaard_drager": "elementenverharding",
    },
    "kolk": {
        "naam": "Verstopte kolk",
        "verschijnsel": "verstopping",
        "dragers": ["kolk"],
        "standaard_drager": "kolk",
    },
}


class OnbekendeDrager(ValueError):
    """De drager past niet bij deze detectieklasse."""


def meetlat_voor(detectieklasse: str, drager: Optional[str] = None) -> Optional[str]:
    """Detectieklasse plus drager omzetten naar een meetlatcode.

    Zonder drager valt hij terug op de standaard, als die er is. Bij graffiti en
    scheefstand is er bewust geen standaard: "er zit graffiti" zonder te zeggen
    waarop is niet te scoren en niet te verhelpen. Dan komt er None terug en
    hoort de waarneming naar de beoordeellijst.
    """
    klasse = DETECTIEKLASSEN.get(detectieklasse)
    if klasse is None:
        return None
    gekozen = drager or klasse["standaard_drager"]
    if gekozen is None:
        return None
    if gekozen not in klasse["dragers"]:
        raise OnbekendeDrager(
            f"'{gekozen}' hoort niet bij detectieklasse '{detectieklasse}'; "
            f"kies uit: {', '.join(klasse['dragers'])}")
    code = f"{klasse['verschijnsel']}.{gekozen}"
    return code if code in _MEETLAT_CODES else None


def detectieklassen(groep: Optional[str] = None) -> list[dict]:
    uit = []
    for code, k in DETECTIEKLASSEN.items():
        v = VERSCHIJNSELEN[k["verschijnsel"]]
        if groep and v["groep"] != groep:
            continue
        uit.append({"code": code, **k, "groep": v["groep"],
                    "eenheid": v["eenheid"]})
    return uit


# ---------------------------------------------------------------------------
# Drempels
# ---------------------------------------------------------------------------

class Drempels:
    """Grenswaarden per meetlat, van hoog naar laag.

    Een drempel is de **bovengrens** van een niveau: de hoogste waarde die nog
    bij dat niveau hoort. Bij ``{"A+": 0, "A": 2, "B": 5, "C": 10}`` levert een
    telling van 4 dus B, en alles boven 10 wordt D.

    Meetlatten met eenheid ``klasse_direct`` (scheefstand, slijtage) hebben geen
    drempels nodig: daar geeft de waarnemer al een letter. Die gaan via
    ``directe_klasse`` het resultaat in.

    Drempels mogen ook per **verschijnsel** worden gezet in plaats van per
    meetlat. Een bestek dat één getal voor alle graffiti afspreekt hoeft dan niet
    tien keer hetzelfde in te vullen; een bestek dat viaducten anders behandelt
    zet die ene meetlat apart. De specifieke wint van de algemene.
    """

    def __init__(self, per_meetlat: Optional[dict[str, dict[str, float]]] = None,
                 per_verschijnsel: Optional[dict[str, dict[str, float]]] = None):
        self.per_meetlat = per_meetlat or {}
        self.per_verschijnsel = per_verschijnsel or {}

    def grenzen_voor(self, meetlat_code: str) -> Optional[dict[str, float]]:
        eigen = self.per_meetlat.get(meetlat_code)
        if eigen:
            return eigen
        m = _PER_CODE.get(meetlat_code)
        if m is None:
            return None
        return self.per_verschijnsel.get(m["verschijnsel"])

    def klasse_voor(self, meetlat_code: str,
                    waarde: Optional[float]) -> Optional[str]:
        """Waarde omzetten naar een niveau, of None als dat niet kan.

        Bewust geen aanname bij ontbrekende drempels: een schouw die stilzwijgend
        'B' invult waar niemand iets heeft ingesteld, is een schouw waar niemand
        iets aan heeft.
        """
        if waarde is None:
            return None
        grenzen = self.grenzen_voor(meetlat_code)
        if not grenzen:
            return None
        for klasse in KLASSE_CODES:                 # A+ ... D, hoog naar laag
            grens = grenzen.get(klasse)
            if grens is not None and waarde <= grens:
                return klasse
        return KLASSE_CODES[-1]

    def ingesteld_voor(self) -> list[str]:
        return sorted(k for k, v in self.per_meetlat.items() if v)


# ---------------------------------------------------------------------------
# Opzoeken
# ---------------------------------------------------------------------------

def meetlat(code: str) -> Optional[dict]:
    return _PER_CODE.get(code)


def meetlatten_voor(groep: Optional[str] = None,
                    verschijnsel: Optional[str] = None,
                    drager: Optional[str] = None,
                    alleen_detecteerbaar: bool = False) -> list[dict]:
    """Meetlatten filteren.

    ``alleen_detecteerbaar`` geeft de meetlatten die zich uit beeld laten
    afleiden. De rest blijft mensenwerk en hoort niet stilzwijgend leeg te
    blijven in een rapportage.
    """
    uit = MEETLATTEN
    if groep:
        uit = [m for m in uit if m["groep"] == groep]
    if verschijnsel:
        uit = [m for m in uit if m["verschijnsel"] == verschijnsel]
    if drager:
        uit = [m for m in uit if m["drager"] == drager]
    if alleen_detecteerbaar:
        uit = [m for m in uit if m["detecteerbaar"]]
    return uit


def gangbare_ambitie(gebiedstype: Optional[str]) -> str:
    g = GEBIEDSTYPEN.get(gebiedstype or "")
    return g["gangbare_ambitie"] if g else DEFAULT_AMBITIE


def slechtste_klasse(klassen: list[Optional[str]]) -> Optional[str]:
    """Een vak is zo schoon als zijn vuilste meetlat.

    Middelen zou een straat met één ernstig probleem en negen schone meetlatten
    laten slagen, en daar heeft een bewoner niets aan.
    """
    scores = [_KLASSE_SCORE[k] for k in klassen if k in _KLASSE_SCORE]
    if not scores:
        return None
    return _SCORE_KLASSE[min(scores)]


def voldoet(klasse: Optional[str], ambitie: str = DEFAULT_AMBITIE) -> Optional[bool]:
    if klasse not in _KLASSE_SCORE or ambitie not in _KLASSE_SCORE:
        return None
    return _KLASSE_SCORE[klasse] >= _KLASSE_SCORE[ambitie]


# ---------------------------------------------------------------------------
# Beoordelen
# ---------------------------------------------------------------------------

def beoordeel_vak(waarnemingen: Optional[dict[str, float]] = None,
                  *,
                  drempels: Drempels,
                  directe_klassen: Optional[dict[str, str]] = None,
                  gebiedstype: Optional[str] = None,
                  ambitie: Optional[str] = None) -> dict:
    """Eén straat of vak beoordelen.

    ``waarnemingen`` is ``{meetlat_code: waarde}`` voor meetlatten die je telt of
    schat. ``directe_klassen`` is ``{meetlat_code: "A".."D"}`` voor meetlatten
    met eenheid ``klasse_direct`` -- scheefstand meet je niet, die zie je.

    Meetlatten zonder ingestelde drempels komen terug onder ``niet_beoordeeld``
    in plaats van te verdwijnen: dat is het verschil tussen "schoon" en "niet
    gekeken", en dat verschil hoort zichtbaar te zijn.
    """
    gekozen_ambitie = ambitie or gangbare_ambitie(gebiedstype)

    per_meetlat: dict[str, dict] = {}
    niet_beoordeeld: list[str] = []
    onbekend: list[str] = []

    def _leg_vast(code: str, klasse: str, waarde=None) -> None:
        m = _PER_CODE[code]
        per_meetlat[code] = {
            "naam": m["naam"],
            "verschijnsel": m["verschijnsel"],
            "drager": m["drager"],
            "groep": m["groep"],
            "eenheid": m["eenheid"],
            "waarde": waarde,
            "klasse": klasse,
            "voldoet": voldoet(klasse, gekozen_ambitie),
        }

    for code, waarde in (waarnemingen or {}).items():
        if code not in _MEETLAT_CODES:
            onbekend.append(code)
            continue
        klasse = drempels.klasse_voor(code, waarde)
        if klasse is None:
            niet_beoordeeld.append(code)
            continue
        _leg_vast(code, klasse, waarde)

    for code, klasse in (directe_klassen or {}).items():
        if code not in _MEETLAT_CODES:
            onbekend.append(code)
            continue
        if klasse not in _KLASSE_SCORE:
            niet_beoordeeld.append(code)
            continue
        _leg_vast(code, klasse)

    eind = slechtste_klasse([v["klasse"] for v in per_meetlat.values()])
    tekort = sorted(c for c, v in per_meetlat.items() if v["voldoet"] is False)

    return {
        "versie": SCHOUW_VERSION,
        "gebiedstype": gebiedstype,
        "ambitie": gekozen_ambitie,
        "per_meetlat": per_meetlat,
        "beeldkwaliteit": eind,
        "voldoet": voldoet(eind, gekozen_ambitie),
        "onder_ambitie": tekort,
        "niet_beoordeeld": sorted(set(niet_beoordeeld)),
        "onbekende_meetlatten": sorted(set(onbekend)),
    }


def samenvatting(vakken: list[dict]) -> dict:
    """Meerdere vakken samenvatten tot een gebiedsbeeld.

    Hier wordt bewust **wel** geteld en niet geminimaliseerd: op gebiedsniveau
    wil een opdrachtgever weten hoeveel procent van de vakken voldoet, niet dat
    het hele gebied D is omdat één straat het verpest. De slechtste-aspect-regel
    geldt binnen een vak, niet erboven.
    """
    beoordeeld = [v for v in vakken if v.get("beeldkwaliteit")]
    if not beoordeeld:
        return {"vakken": len(vakken), "beoordeeld": 0, "voldoet_pct": None,
                "verdeling": {}, "meest_tekort": []}

    verdeling: dict[str, int] = {}
    for v in beoordeeld:
        k = v["beeldkwaliteit"]
        verdeling[k] = verdeling.get(k, 0) + 1

    tekort_teller: dict[str, int] = {}
    for v in beoordeeld:
        for code in v.get("onder_ambitie", []):
            tekort_teller[code] = tekort_teller.get(code, 0) + 1

    voldoen = sum(1 for v in beoordeeld if v.get("voldoet"))
    return {
        "vakken": len(vakken),
        "beoordeeld": len(beoordeeld),
        "voldoet": voldoen,
        "voldoet_pct": round(100 * voldoen / len(beoordeeld), 1),
        "verdeling": {k: verdeling.get(k, 0) for k in KLASSE_CODES if verdeling.get(k)},
        "meest_tekort": [
            {"meetlat": c, "naam": (meetlat(c) or {}).get("naam"), "vakken": n}
            for c, n in sorted(tekort_teller.items(), key=lambda x: -x[1])
        ],
    }
