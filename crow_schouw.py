"""Beeldkwaliteit op gebiedsniveau — de "schoon"-kant van de CROW-schouw.

`car2023.py` beoordeelt losse objecten: een verkeersbord, een lichtmast, een
bank. Dat is de "heel"-kant. Maar een schouw van de openbare ruimte gaat ook
over wat er *tussen* de objecten ligt: zwerfafval, onkruid tussen de klinkers,
graffiti op een muur, bijplaatsing naast een container. Dat hangt aan geen enkel
object en heeft dus een eigen meetlat, per **straat of vak** in plaats van per
asset.

Beide vullen elkaar aan en gebruiken dezelfde schaal: A+ tot en met D, met een
ambitieniveau dat de opdrachtgever kiest. Een gemeente contracteert vaak
verschillend per gebied — centrum hoger dan een bedrijventerrein — en rekent de
aannemer daarop af.

**Over de grenswaarden.** De CROW-kwaliteitscatalogus legt per meetlat vast
hoeveel stuks afval of welk percentage onkruid nog bij welk niveau hoort. Die
tabellen zijn auteursrechtelijk beschermd en staan hier dus **niet** in.
Belangrijker nog: ze zijn in de praktijk toch niet leidend, want in een bestek
staat wat opdrachtgever en aannemer zijn overeengekomen, en dat wijkt regelmatig
af. Deze module levert daarom de *structuur* — meetlatten, niveaus,
gebiedstypen, de slechtste-aspect-regel en de toetsing aan de ambitie — en laat
de organisatie haar eigen grenswaarden invullen via `Drempels`. Wie de catalogus
heeft, vult die over; wie een bestek heeft, vult het bestek over.

Deze module is data-only: geen database, geen FastAPI, zodat hij los testbaar
is. Zie ook [car2023] voor de objectkant en `nen2767_scoring` voor conditie.
"""

from __future__ import annotations

from typing import Optional

SCHOUW_VERSION = "crow-schouw.v1-2026-09"

# Dezelfde schaal als car2023; bewust niet opnieuw gedefinieerd maar wel hier
# herhaald als losse lijst, omdat de volgorde en de scores de rekenregels van
# deze module dragen.
from car2023 import (BEELDKWALITEIT_KLASSEN, DEFAULT_AMBITIE,  # noqa: F401
                     KLASSE_CODES, _KLASSE_SCORE, _SCORE_KLASSE)


# ---------------------------------------------------------------------------
# Gebiedstypen — bepalen welke ambitie gangbaar is
# ---------------------------------------------------------------------------

GEBIEDSTYPEN: dict[str, dict] = {
    "centrum": {
        "naam": "Centrum en winkelgebied",
        "gangbare_ambitie": "A",
        "uitleg": "Hoogste ambitie: veel publiek, en vervuiling valt direct op.",
    },
    "woonwijk": {
        "naam": "Woonwijk",
        "gangbare_ambitie": "B",
        "uitleg": "Basisniveau. Het meest voorkomende ambitieniveau in bestekken.",
    },
    "bedrijventerrein": {
        "naam": "Bedrijventerrein",
        "gangbare_ambitie": "C",
        "uitleg": "Lagere ambitie: weinig verblijf, vooral doorgaand verkeer.",
    },
    "hoofdweg": {
        "naam": "Hoofdweg en invalsroute",
        "gangbare_ambitie": "B",
        "uitleg": "Visitekaartje van de gemeente, maar lastig te onderhouden "
                  "door de verkeersintensiteit.",
    },
    "park": {
        "naam": "Park en groengebied",
        "gangbare_ambitie": "B",
    },
    "station": {
        "naam": "Stationsgebied en OV-knooppunt",
        "gangbare_ambitie": "A",
    },
    "industrie": {
        "naam": "Buitengebied",
        "gangbare_ambitie": "C",
    },
}


# ---------------------------------------------------------------------------
# Meetlatten — wat je per vak beoordeelt
# ---------------------------------------------------------------------------
# `eenheid` zegt waarin je telt; dat bepaalt hoe een waarneming uit een foto of
# een videoframe zich laat omrekenen naar een niveau.
# `groep` volgt de indeling die in bestekken gangbaar is: schoon, heel, groen.

MEETLATTEN: list[dict] = [
    {
        "code": "zwerfafval",
        "naam": "Zwerfafval",
        "groep": "schoon",
        "eenheid": "stuks_per_100m2",
        "uitleg": "Los afval op verharding en in de berm: verpakkingen, blikjes, "
                  "peuken, papier. Tel wat er ligt, niet wat er hoort.",
        "detecteerbaar": True,
    },
    {
        "code": "bijplaatsing",
        "naam": "Bijplaatsing naast containers",
        "groep": "schoon",
        "eenheid": "stuks_per_locatie",
        "uitleg": "Zakken, dozen en grofvuil naast een ondergrondse container. "
                  "Vraagt vrijwel altijd een aparte melding, geen veegronde.",
        "detecteerbaar": True,
    },
    {
        "code": "veegvuil",
        "naam": "Veegvuil en zand",
        "groep": "schoon",
        "eenheid": "percentage_oppervlak",
        "uitleg": "Aanslag in de goot en tegen de trottoirband.",
        "detecteerbaar": True,
    },
    {
        "code": "onkruid_verharding",
        "naam": "Onkruid op verharding",
        "groep": "schoon",
        "eenheid": "percentage_oppervlak",
        "uitleg": "Begroeiing tussen klinkers, in voegen en langs de band. "
                  "Sinds het verbod op chemische bestrijding de meest "
                  "besproken meetlat in bestekken.",
        "detecteerbaar": True,
    },
    {
        "code": "bekladding",
        "naam": "Bekladding en graffiti",
        "groep": "schoon",
        "eenheid": "percentage_oppervlak",
        "uitleg": "Op gevels, kasten, abri's en kunstwerken. Aanstootgevende "
                  "bekladding kent in de meeste bestekken een eigen, kortere "
                  "hersteltermijn -- leg dat vast in de toelichting.",
        "detecteerbaar": True,
    },
    {
        "code": "hondenpoep",
        "naam": "Uitwerpselen",
        "groep": "schoon",
        "eenheid": "stuks_per_100m2",
        "detecteerbaar": False,
        "uitleg": "Lastig betrouwbaar uit beeld te halen; blijft handwerk.",
    },
    {
        "code": "blad",
        "naam": "Blad",
        "groep": "schoon",
        "eenheid": "percentage_oppervlak",
        "uitleg": "Seizoensgebonden. Beoordeel tegen de afspraak voor het "
                  "seizoen, niet tegen de zomersituatie.",
        "detecteerbaar": True,
    },
    {
        "code": "onkruid_groen",
        "naam": "Onkruid in beplanting",
        "groep": "groen",
        "eenheid": "percentage_oppervlak",
        "detecteerbaar": True,
    },
    {
        "code": "gras",
        "naam": "Grasvegetatie",
        "groep": "groen",
        "eenheid": "hoogte_cm",
        "uitleg": "Maaihoogte tegenover de afgesproken maaifrequentie.",
        "detecteerbaar": True,
    },
    {
        "code": "verharding_heel",
        "naam": "Heelheid verharding",
        "groep": "heel",
        "eenheid": "stuks_per_100m2",
        "uitleg": "Losliggende of ontbrekende elementen, oneffenheden en "
                  "struikelgevaar. Bij een echt gebrek hoort een CROW 146-"
                  "inspectie, niet alleen een beeldscore.",
        "detecteerbaar": True,
    },
]

_MEETLAT_CODES = {m["code"] for m in MEETLATTEN}


# ---------------------------------------------------------------------------
# Drempels — per organisatie in te vullen
# ---------------------------------------------------------------------------

class Drempels:
    """Grenswaarden per meetlat, van hoog naar laag.

    Een drempel is de **bovengrens** van een niveau: de hoogste waarde die nog
    bij dat niveau hoort. Bij ``{"A+": 0, "A": 2, "B": 5, "C": 10}`` levert een
    telling van 4 dus niveau B, en alles boven 10 wordt D.

    De richting klopt voor alle meetlatten hier: meer afval, meer onkruid en
    hoger gras zijn allemaal slechter. Zou er ooit een meetlat bijkomen waar
    meer juist beter is, dan heeft die een eigen functie nodig -- niet een
    vlaggetje in deze.
    """

    def __init__(self, per_meetlat: Optional[dict[str, dict[str, float]]] = None):
        self.per_meetlat = per_meetlat or {}

    def klasse_voor(self, meetlat: str, waarde: Optional[float]) -> Optional[str]:
        """Waarde omzetten naar een niveau, of None als dat niet kan.

        None bij een ontbrekende waarde of ontbrekende drempels. Bewust geen
        aanname: een schouw die stilletjes 'B' invult waar niemand iets heeft
        ingesteld, is een schouw waar niemand iets aan heeft.
        """
        if waarde is None:
            return None
        grenzen = self.per_meetlat.get(meetlat)
        if not grenzen:
            return None
        for klasse in KLASSE_CODES:                 # A+ ... D, hoog naar laag
            grens = grenzen.get(klasse)
            if grens is not None and waarde <= grens:
                return klasse
        return KLASSE_CODES[-1]                     # boven alle grenzen = D

    def ingesteld_voor(self) -> list[str]:
        return sorted(k for k, v in self.per_meetlat.items() if v)


# ---------------------------------------------------------------------------
# Beoordelen
# ---------------------------------------------------------------------------

def meetlat(code: str) -> Optional[dict]:
    for m in MEETLATTEN:
        if m["code"] == code:
            return m
    return None


def meetlatten_voor(groep: Optional[str] = None,
                    alleen_detecteerbaar: bool = False) -> list[dict]:
    """Meetlatten filteren.

    ``alleen_detecteerbaar`` geeft de meetlatten die zich uit beeld laten
    afleiden -- de deelverzameling die een videoschouw of een telefoonfoto kan
    voorzien. De rest blijft mensenwerk en hoort niet stilzwijgend leeg te
    blijven in een rapportage.
    """
    uit = MEETLATTEN
    if groep:
        uit = [m for m in uit if m["groep"] == groep]
    if alleen_detecteerbaar:
        uit = [m for m in uit if m.get("detecteerbaar")]
    return uit


def gangbare_ambitie(gebiedstype: Optional[str]) -> str:
    g = GEBIEDSTYPEN.get(gebiedstype or "")
    return g["gangbare_ambitie"] if g else DEFAULT_AMBITIE


def slechtste_klasse(klassen: list[Optional[str]]) -> Optional[str]:
    """Slechtste-aspect-regel, gelijk aan car2023.

    Een vak is zo schoon als zijn vuilste meetlat. Middelen zou een straat met
    één ernstig probleem en negen schone meetlatten laten slagen, en daar heeft
    een bewoner niets aan.
    """
    scores = [_KLASSE_SCORE[k] for k in klassen if k in _KLASSE_SCORE]
    if not scores:
        return None
    return _SCORE_KLASSE[min(scores)]


def voldoet(klasse: Optional[str], ambitie: str = DEFAULT_AMBITIE) -> Optional[bool]:
    if klasse not in _KLASSE_SCORE or ambitie not in _KLASSE_SCORE:
        return None
    return _KLASSE_SCORE[klasse] >= _KLASSE_SCORE[ambitie]


def beoordeel_vak(waarnemingen: dict[str, float],
                  *,
                  drempels: Drempels,
                  gebiedstype: Optional[str] = None,
                  ambitie: Optional[str] = None) -> dict:
    """Eén straat of vak beoordelen op alle meegegeven meetlatten.

    ``waarnemingen`` is ``{meetlat_code: waarde}``. Meetlatten waarvoor geen
    drempels zijn ingesteld komen terug onder ``niet_beoordeeld`` in plaats van
    stilletjes te verdwijnen: dat is het verschil tussen "schoon" en "niet
    gekeken", en dat verschil hoort zichtbaar te zijn.
    """
    gekozen_ambitie = ambitie or gangbare_ambitie(gebiedstype)

    per_meetlat: dict[str, dict] = {}
    niet_beoordeeld: list[str] = []
    onbekend: list[str] = []

    for code, waarde in (waarnemingen or {}).items():
        if code not in _MEETLAT_CODES:
            onbekend.append(code)
            continue
        klasse = drempels.klasse_voor(code, waarde)
        if klasse is None:
            niet_beoordeeld.append(code)
            continue
        m = meetlat(code)
        per_meetlat[code] = {
            "naam": m["naam"],
            "groep": m["groep"],
            "eenheid": m["eenheid"],
            "waarde": waarde,
            "klasse": klasse,
            "voldoet": voldoet(klasse, gekozen_ambitie),
        }

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
        "niet_beoordeeld": sorted(niet_beoordeeld),
        "onbekende_meetlatten": sorted(onbekend),
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
