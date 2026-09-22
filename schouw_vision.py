"""Beeldherkenning voor de schouw — één frame tegelijk.

Een schouwrit levert beelden: frames uit een dashcamvideo, of foto's die een
inspecteur met zijn telefoon maakt. Dit bestand kijkt naar één zo'n beeld en
geeft terug wat er te zien is, uitgedrukt in de meetlatten van
`crow_schouw` en de objecttypen van `car2023`. Het rekent niets uit en slaat
niets op; het scoren gebeurt in `crow_schouw.beoordeel_vak`.

**Alleen wat visueel eenduidig is.** De prompt vraagt bewust niet om
oordelen die je van een foto niet kunt maken. Of een lichtmast scheef staat is
te zien; of hij nog voldoet aan NEN 1010 niet. Of er zwerfafval ligt is te
zien; of dat vandaag of vorige week is gevallen niet. Alles wat verder gaat dan
waarneming is een oordeel, en dat blijft bij de inspecteur.

**De AI kijkt, de mens beslist.** Elke waarneming draagt een `zekerheid` en
`beoordeling_nodig`. Boven een drempel mag een waarneming automatisch
doorstromen naar een score; daaronder komt hij in een lijst die iemand nakijkt.
Een schouw die zichzelf afvinkt is geen schouw, en bij een geschil met een
aannemer is "de computer zei het" geen onderbouwing.

**Privacy is een poort, geen aanbeveling.** Een straatbeeld bevat gezichten en
kentekens. Die horen geblurd te zijn vóórdat het beeld het pand verlaat, en dat
is hier geen vriendelijk verzoek: `analyseer_frame` weigert een beeld dat niet
als geblurd is aangemerkt. Zonder die poort stuur je onbedoeld
persoonsgegevens naar een verwerker buiten de EU, en dat is precies het soort
ding dat een gemeente je nooit vergeeft.

**Schade aan de weg wordt aangewezen, niet alleen benoemd.** Voor
verhardingsschade krijgt de herkenning de complete CROW-schadecatalogus uit
`crow_wegschade` mee, en per schade een `kader`: waar in het beeld hij zit. Het
scherm tekent daar een rood vlak. Dat kader is een benadering van het model,
geen meting in pixels -- goed genoeg om te zien wélke scheur bedoeld wordt,
niet om hem mee te meten.

Let op de kosten: elk frame is een aparte vision-aanroep. Een rit van tien
minuten levert bij één frame per seconde zeshonderd aanroepen op. Kies het
frame-interval op afstand (bijvoorbeeld elke tien meter) in plaats van op tijd,
en overweeg voor de veelvoorkomende objecten een eigen getraind model met dit
bestand als vangnet voor de twijfelgevallen.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

import crow_schouw as cs
import crow_wegschade as cw

SCHOUW_VISION_VERSION = "schouw-vision.v4-2026-09"

# Het model voor de schouw. Eigen omgevingsvariabele, zodat de schouw niet
# meeverandert als iemand CLAUDE_MODEL voor de inspecties omzet. Elke paar
# seconden een beeld maakt dit een drukke route: daarom lage `effort` (zie
# _verzoek_extra) in plaats van een kleiner model.
MODEL_STANDAARD = "claude-opus-5"

# Meer schades per beeld dan dit is bijna altijd een model dat elk steentje
# apart gaat benoemen. Dan liever de duidelijkste acht.
MAX_WEGSCHADE_PER_BEELD = 8

# Elk object krijgt een kader, ook als het in orde is. Dat kost tekst in het
# antwoord, en tekst kost tijd: bij twaalf objecten blijft een beeld binnen de
# paar seconden. Meer dan dit staat er zelden scherp genoeg in beeld om iets
# over te zeggen.
MAX_OBJECTEN_PER_BEELD = 12

OBJECT_NIVEAUS = ("A+", "A", "B", "C", "D")

# Grondigheid uit de instellingen van de organisatie -> effort van het model.
_EFFORT = {"snel": "low", "grondig": "medium"}

# Wat er van een beeld wordt gevraagd. "alles" is de lopende schouw: objecten,
# vervuiling en schade. "wegdek" is de rijstand: alleen schade aan de
# verharding, op een uitsnede van het wegdek. Daar wacht niemand op het
# antwoord, dus het model mag er iets langer over doen.
STANDEN = ("alles", "wegdek")
_EFFORT_WEGDEK = {"snel": "medium", "grondig": "high"}

# Boven deze zekerheid mag een waarneming zonder tussenkomst doorstromen naar
# een beeldkwaliteitsscore. Bewust hoog: een onterecht "schoon" kost een
# gemeente niets, een onterecht "vervuild" kost een aannemer geld.
DREMPEL_AUTOMATISCH = 0.80


class NietGeblurd(RuntimeError):
    """Het beeld is niet als privacy-gecontroleerd aangemerkt."""


# Objecten die je op een straatbeeld kunt aanwijzen. De eerste tien kent
# car2023 ook; de rest staat er omdat ze in elk straatbeeld zitten en een
# inspecteur ze in het beeld verwacht terug te zien.
OBJECT_TYPES: list[str] = [
    "verkeersbord", "lichtmast", "bewegwijzering", "afvalbak", "zitbank",
    "paal_poller", "fietsenrek", "abri", "hekwerk", "boom",
    "verkeerslicht", "trottoirband", "kolk", "putdeksel",
]

# Zoals een inspecteur ze noemt, voor het scherm.
OBJECT_NAMEN: dict[str, str] = {
    "verkeersbord": "Verkeersbord", "lichtmast": "Lichtmast",
    "bewegwijzering": "Bewegwijzering", "afvalbak": "Afvalbak",
    "zitbank": "Zitbank", "paal_poller": "Paal", "fietsenrek": "Fietsenrek",
    "abri": "Abri", "hekwerk": "Hekwerk", "boom": "Boom",
    "verkeerslicht": "Verkeerslicht", "trottoirband": "Trottoirband",
    "kolk": "Kolk", "putdeksel": "Putdeksel",
}

_OBJECT_ASPECTEN = ["heelheid", "reinheid", "stabiliteit", "functie"]


def _systeem_prompt(instellingen: Optional[dict] = None) -> str:
    """De prompt vraagt om detectieklasse plus drager, niet om een meetlat.

    Een model ziet "hier zit graffiti op die nutskast", niet
    "bekladding.nutskast". De vertaling naar de meetlat doet crow_schouw; hier
    vragen we alleen wat er te zien is en waarop. Dat scheelt een lange lijst
    codes in de prompt en levert betrouwbaardere antwoorden op.
    """
    klassen = "\n".join(
        f"- {k['code']} ({k['naam']}): waarop -> {', '.join(k['dragers'])}"
        for k in cs.detectieklassen() if k["code"] != "verharding")
    inst = instellingen or {}
    verhardingen = inst.get("verhardingen", list(cw.VERHARDINGEN))
    objecttypen = inst.get("objecttypen", OBJECT_TYPES)
    max_objecten = inst.get("max_objecten", MAX_OBJECTEN_PER_BEELD)

    if verhardingen:
        namen = ", ".join(v for v in cw.VERHARDINGEN if v in verhardingen)
        schade_blok = f"""SCHADE AAN DE VERHARDING meld je NIET onder "gebied" maar onder "wegschade",
met het schadebeeld uit deze catalogus. Kies eerst het verhardingstype ({namen})
en daarna een schadebeeld dat bij dat type hoort. Andere verhardingen laat je
bij deze schouw weg:
{cw.prompt_tekst(verhardingen)}

Per schade:
- "ernst": L, M of E volgens de beschrijving bij dat schadebeeld
- "omvang": hoeveel van de ZICHTBARE verharding het beslaat -- 1 plaatselijk,
  2 over een deel, 3 over het grootste deel
- "kader": waar de schade in het beeld zit, als [x_min, y_min, x_max, y_max],
  elk een getal tussen 0 en 1 als fractie van de breedte en hoogte van het
  beeld, gemeten vanaf linksboven. Leg het kader strak om het beschadigde deel.
  Bij een lange scheur loopt het kader over de hele zichtbare lengte.
- losse schades apart; hetzelfde schadebeeld op twee plekken is twee keer
- hooguit {MAX_WEGSCHADE_PER_BEELD} schades per beeld, de duidelijkste eerst"""
    else:
        schade_blok = ('SCHADE AAN DE VERHARDING hoef je bij deze schouw niet te melden; '
                       'laat "wegschade" leeg.')

    if objecttypen and max_objecten > 0:
        object_blok = f"""OBJECTEN: benoem ELK object uit deze lijst dat duidelijk in beeld staat, ook
als het in orde is: {', '.join(t for t in OBJECT_TYPES if t in objecttypen)}
Per object:
- "niveau": hoe het eruitziet, op het slechtste zichtbare aspect
  ({', '.join(_OBJECT_ASPECTEN)}): A+ als nieuw, A in orde, B licht gebrek,
  C duidelijk gebrek, D sterk gebrek
- "kader": waar het object in het beeld staat, zelfde notatie als bij schade
- "aspect" en "waarneming" alleen als het niveau B of slechter is
- hooguit {max_objecten} objecten, de grootste en duidelijkste eerst
Beoordeel alleen wat zichtbaar is. Beoordeel niet of iets aan een norm
voldoet, of iets veilig is, of hoe oud het is."""
    else:
        object_blok = 'OBJECTEN hoef je bij deze schouw niet te benoemen; laat "objecten" leeg.'
    return f"""Je beoordeelt één beeld uit een schouw van de Nederlandse openbare ruimte.

Je taak is waarnemen, niet oordelen. Meld wat je ziet en hoe zeker je bent.
Wat je niet duidelijk kunt zien, laat je weg -- een lege lijst is een geldig
antwoord en beter dan een gok.

WAT JE MAG MELDEN, met de drager waarop je het ziet:
{klassen}

De DRAGER is verplicht als je hem kunt zien. "Er zit graffiti" zonder te zeggen
waarop is niet te verhelpen en niet te scoren. Zie je het niet zeker, laat de
drager dan weg en zet de zekerheid laag.

WAARDE:
- bij afval en grofvuil: het aantal stuks dat je in beeld telt
- bij onkruid, veegvuil, graffiti, blad en overgroei: het geschatte percentage
  van het zichtbare oppervlak van die drager
- bij gras: de geschatte hoogte in centimeters
- bij scheefstand en markering geef je geen getal maar een letter in
  "klasse": A (recht/strak), B (licht), C (duidelijk), D (sterk)

{schade_blok}

{object_blok}

KADERS: geef elk getal met twee decimalen. Ook bij "gebied" mag een kader,
als de waarneming op één plek zit (een stapel afval, een scheve mast).

ZEKERHEID is een getal tussen 0 en 1. Wees streng: geef 0.9 of hoger alleen bij
iets dat scherp in beeld staat en onmiskenbaar is. Bij regen, tegenlicht,
bewegingsonscherpte of een klein object in de verte hoort de zekerheid laag.

Antwoord met uitsluitend geldige JSON, zonder toelichting eromheen:
{{
  "bruikbaar": true,
  "reden_onbruikbaar": null,
  "gebied": [
    {{"klasse": "afval_los", "drager": "elementenverharding", "waarde": 3,
      "zekerheid": 0.86, "toelichting": "drie blikjes op het trottoir"}},
    {{"klasse": "scheefstand", "drager": "lichtmast", "klasse_niveau": "C",
      "kader": [0.71, 0.08, 0.78, 0.66],
      "zekerheid": 0.74, "toelichting": "mast helt duidelijk"}}
  ],
  "wegschade": [
    {{"verharding": "asfalt", "schadebeeld": "scheurvorming-langs",
      "ernst": "M", "omvang": "1", "kader": [0.42, 0.55, 0.61, 0.97],
      "zekerheid": 0.83, "toelichting": "open langsscheur in het rechter wielspoor"}}
  ],
  "objecten": [
    {{"type": "verkeersbord", "niveau": "A", "kader": [0.52, 0.30, 0.60, 0.45],
      "zekerheid": 0.9}},
    {{"type": "afvalbak", "niveau": "C", "kader": [0.12, 0.55, 0.20, 0.78],
      "aspect": "reinheid", "waarneming": "bak zit vol", "zekerheid": 0.72}}
  ]
}}

Zet "bruikbaar" op false als het beeld te donker, te onscherp of te vol is om
iets zinnigs over te zeggen, en vul dan "reden_onbruikbaar" in. Dat is een
nuttig antwoord: het vertelt de gebruiker dat er op dit stuk niets gemeten is,
in plaats van dat het schoon zou zijn."""


def _systeem_prompt_wegdek(instellingen: Optional[dict] = None) -> str:
    """Alleen het wegdek, gezien vanuit een rijdende auto.

    De meeste fouten bij wegschade zijn geen gemiste scheuren maar dingen die
    op schade lijken: belijning, schaduw, een nat vlak, een reparatie. Die
    staan er daarom met name in. Statisch, zodat de prompt in de cache blijft.
    """
    inst = instellingen or {}
    verhardingen = inst.get("verhardingen", list(cw.VERHARDINGEN))
    namen = ", ".join(v for v in cw.VERHARDINGEN if v in verhardingen) or "geen"
    return f"""Je beoordeelt het wegdek voor een schouw van de Nederlandse openbare weg.

HET BEELD is een uitsnede van het wegdek, gemaakt door een telefoon achter de
voorruit van een auto die rijdt. Onderaan is dichtbij (een paar meter voor de
auto), bovenaan verder weg (twintig meter of meer). Beoordeel vooral het
onderste twee derde: verder weg is te klein om de ernst te zien. Meld daar
alleen iets dat onmiskenbaar is, zoals een groot gat.

MELD ALLEEN SCHADE AAN DE VERHARDING ({namen}), met het schadebeeld uit
deze catalogus. Kies eerst het verhardingstype en daarna een schadebeeld dat
bij dat type hoort:
{cw.prompt_tekst(verhardingen)}

GEEN SCHADE -- dit lijkt er vaak op, maar meld je niet:
- wegmarkering en belijning: strepen, haaientanden, fietssymbolen, pijlen;
- schaduw van bomen, palen, gebouwen of auto's;
- natte plekken en plassen waar geen gat onder zit;
- een reparatievlak (een rechthoek nieuwer of donkerder asfalt) dat zelf heel
  is -- wel melden als er scheuren of rafeling in of langs zitten;
- putdeksels, kolken, roosters, tramrails en verkeersdrempels;
- de rechte naad tussen twee asfaltbanen of een aansluiting op een brug;
- blad, zand, grind of bandensporen die op het wegdek liggen.

Per schade:
- "verharding" en "schadebeeld" uit de catalogus;
- "ernst": L, M of E volgens de beschrijving bij dat schadebeeld;
- "omvang": hoeveel van het ZICHTBARE wegdek het beslaat -- 1 plaatselijk,
  2 over een deel, 3 over het grootste deel;
- "kader": [x_min, y_min, x_max, y_max] als fracties 0 tot 1 van deze
  uitsnede, vanaf linksboven, twee decimalen. Strak om het beschadigde deel;
  bij een lange scheur over de hele zichtbare lengte;
- "zekerheid" tussen 0 en 1. Streng: 0.9 of hoger alleen als het scherp in
  beeld is en onmiskenbaar. Bij bewegingsonscherpte, tegenlicht of regen laag;
- "toelichting": hooguit acht woorden, waar het zit.
Losse schades apart, de duidelijkste eerst, hooguit {MAX_WEGSCHADE_PER_BEELD}.

Antwoord met uitsluitend geldige JSON:
{{"bruikbaar": true, "reden_onbruikbaar": null, "wegschade": [
  {{"verharding": "asfalt", "schadebeeld": "scheurvorming-langs", "ernst": "M",
    "omvang": "1", "kader": [0.42, 0.35, 0.61, 0.97], "zekerheid": 0.83,
    "toelichting": "langsscheur rechter wielspoor"}}]}}

Zet "bruikbaar" op false met een reden als het wegdek niet te beoordelen is:
te donker, bewogen, een voorligger of ander voertuig vult het beeld, of er is
geen wegdek te zien. Een lege lijst betekent: dit stuk is bekeken en er is
geen schade gezien."""


def _leeg(reden: str) -> dict:
    return {
        "bruikbaar": False,
        "reden_onbruikbaar": reden,
        "gebied": [],
        "wegschade": [],
        "objecten": [],
        "_versie": SCHOUW_VISION_VERSION,
        "_model_id": None,
    }


def _parse(ruw: str) -> dict:
    """JSON uit het antwoord halen, ook als er tekst omheen staat."""
    try:
        return json.loads(ruw)
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"\{.*\}", ruw, re.S)
    if not m:
        raise ValueError("geen JSON in het antwoord")
    return json.loads(m.group(0))


def _schoon(rauw: dict, instellingen: Optional[dict] = None) -> dict:
    """Antwoord opschonen en vertalen naar meetlatten.

    Een waarneming zonder herleidbare meetlat -- verzonnen klasse, of graffiti
    zonder drager -- verdwijnt niet, maar krijgt `meetlat: None` en
    `beoordeling_nodig: True`. Dan ziet iemand hem in de beoordeellijst en kan
    hij de drager alsnog invullen. Weggooien zou betekenen dat de AI iets zag en
    niemand het te weten komt.
    """
    inst = instellingen or {}
    drempel = inst.get("drempel_automatisch", DREMPEL_AUTOMATISCH)
    objecttypen = inst.get("objecttypen", OBJECT_TYPES)
    max_objecten = inst.get("max_objecten", MAX_OBJECTEN_PER_BEELD)

    gebied = []
    for w in (rauw.get("gebied") or []):
        klasse = w.get("klasse")
        if klasse not in cs.DETECTIEKLASSEN:
            continue
        drager = w.get("drager") or None
        try:
            code = cs.meetlat_voor(klasse, drager)
        except cs.OnbekendeDrager:
            code, drager = None, None      # drager past niet; laat beoordelen
        m = cs.meetlat(code) if code else None

        zekerheid = _getal(w.get("zekerheid"), 0.0, 1.0)
        niveau = w.get("klasse_niveau")
        if niveau not in ("A+", "A", "B", "C", "D"):
            niveau = None

        gebied.append({
            "klasse": klasse,
            "drager": drager,
            "meetlat": code,
            "naam": m["naam"] if m else cs.DETECTIEKLASSEN[klasse]["naam"],
            "eenheid": m["eenheid"] if m else None,
            "waarde": _getal(w.get("waarde")),
            "klasse_niveau": niveau,
            "zekerheid": zekerheid,
            "toelichting": (w.get("toelichting") or "")[:300] or None,
            "kader": _kader(w.get("kader")),
            "beoordeling_nodig": (code is None or zekerheid is None
                                  or zekerheid < drempel),
        })

    wegschade = _schoon_wegschade(rauw.get("wegschade") or [], drempel=drempel,
                                  verhardingen=inst.get("verhardingen"))
    if wegschade:
        # De oude, platte "verharding"-melding en een CROW-schadebeeld over
        # hetzelfde gat zouden dubbel tellen. Het schadebeeld zegt meer.
        gebied = [w for w in gebied if w["klasse"] != "verharding"]

    objecten = []
    # Wat de organisatie niet laat herkennen, bestaat voor deze schouw niet --
    # ook niet als het model het toch noemt.
    for o in [o for o in (rauw.get("objecten") or [])
              if isinstance(o, dict) and o.get("type") in objecttypen][:max_objecten]:
        aspect = o.get("aspect")
        if aspect not in _OBJECT_ASPECTEN:
            aspect = None
        zekerheid = _getal(o.get("zekerheid"), 0.0, 1.0)
        niveau = o.get("niveau") if o.get("niveau") in OBJECT_NIVEAUS else None
        objecten.append({
            "type": o["type"],
            "naam": OBJECT_NAMEN.get(o["type"], o["type"]),
            "niveau": niveau,
            "kader": _kader(o.get("kader")),
            "aspect": aspect,
            "waarneming": (o.get("waarneming") or "")[:300] or None,
            "zekerheid": zekerheid,
            "beoordeling_nodig": zekerheid is None or zekerheid < drempel,
        })

    return {
        "bruikbaar": bool(rauw.get("bruikbaar", True)),
        "reden_onbruikbaar": rauw.get("reden_onbruikbaar") or None,
        "gebied": gebied,
        "wegschade": wegschade,
        "objecten": objecten,
        "_versie": SCHOUW_VISION_VERSION,
    }


def _schoon_wegschade(lijst: list, *, drempel: float = DREMPEL_AUTOMATISCH,
                      verhardingen: Optional[list[str]] = None) -> list[dict]:
    """Wegschade opschonen en koppelen aan de schouw.

    Een schadebeeld dat niet bij het verhardingstype hoort (rafeling op
    klinkers) wordt weggegooid: dat is geen schade die de inspecteur kan
    bevestigen, maar een vergissing. Een kapot kader gooit de schade niet weg:
    dan is hij er wel, maar kunnen we hem niet aanwijzen.

    Elke schade telt mee als "heelheid verharding" op de juiste drager, met
    het niveau dat bij de ernst hoort. Zo gaat hij ook in de beeldkwaliteit mee
    zodra hij zeker genoeg is of bevestigd.
    """
    uit: list[dict] = []
    for w in lijst[:MAX_WEGSCHADE_PER_BEELD]:
        if not isinstance(w, dict):
            continue
        s = cw.zoek(w.get("verharding"), w.get("schadebeeld"))
        if not s or (verhardingen is not None and s["verharding"] not in verhardingen):
            continue
        ernst = w.get("ernst") if w.get("ernst") in cw.ERNST else None
        omvang = str(w.get("omvang")) if str(w.get("omvang")) in cw.OMVANG_BEELD else None
        drager = cw.VERHARDINGEN[s["verharding"]]["schouw_drager"]
        code = cs.meetlat_voor("verharding", drager)
        zekerheid = _getal(w.get("zekerheid"), 0.0, 1.0)
        uit.append({
            "klasse": "verharding",
            "drager": drager,
            "meetlat": code,
            "naam": s["naam"],
            "eenheid": None,
            "waarde": None,
            "klasse_niveau": cw.ERNST_NAAR_NIVEAU.get(ernst),
            "verharding": s["verharding"],
            "schadegroep": s["schadegroep"],
            "schadebeeld": s["schadebeeld"],
            "ernst": ernst,
            "omvang": omvang,
            "kader": _kader(w.get("kader")),
            "zekerheid": zekerheid,
            "toelichting": (w.get("toelichting") or "")[:300] or None,
            # Zonder ernst valt er niets te scoren; die moet een mens invullen.
            "beoordeling_nodig": (ernst is None or zekerheid is None
                                  or zekerheid < drempel),
        })
    return uit


def _kader(ruw) -> Optional[list[float]]:
    """[x_min, y_min, x_max, y_max] als fracties, of None.

    Getallen buiten 0..1 worden bijgeknipt; een omgekeerd of piepklein kader
    is geen plek maar ruis, en dan tekenen we liever niets dan iets verkeerds.
    """
    if not isinstance(ruw, (list, tuple)) or len(ruw) != 4:
        return None
    getallen = [_getal(x, 0.0, 1.0) for x in ruw]
    if any(g is None for g in getallen):
        return None
    x0, y0, x1, y1 = getallen
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        return None
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def _getal(waarde, minimum: Optional[float] = None,
           maximum: Optional[float] = None) -> Optional[float]:
    try:
        n = float(waarde)
    except (TypeError, ValueError):
        return None
    if minimum is not None and n < minimum:
        return minimum
    if maximum is not None and n > maximum:
        return maximum
    return n


def is_geconfigureerd() -> bool:
    return bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())


def analyseer_frame(*,
                    image_bytes: bytes,
                    image_media_type: str = "image/jpeg",
                    privacy_gecontroleerd: bool,
                    context: Optional[str] = None,
                    instellingen: Optional[dict] = None,
                    stand: str = "alles",
                    voorbeelden: Optional[list] = None) -> dict:
    """Eén schouwbeeld analyseren.

    ``privacy_gecontroleerd`` moet expliciet True zijn: het beeld is dan
    gecontroleerd op gezichten en kentekens. Dit is een poort en geen vlag --
    een straatbeeld gaat naar een verwerker buiten de EU, en dat mag niet met
    herkenbare personen erop.

    Zonder ``ANTHROPIC_API_KEY`` komt er een leeg, als onbruikbaar gemarkeerd
    antwoord terug. Bewust geen lege waarnemingslijst die als "schoon" leest:
    niets gemeten is niet hetzelfde als niets gevonden.
    """
    if not privacy_gecontroleerd:
        raise NietGeblurd(
            "Beeld is niet als privacy-gecontroleerd aangemerkt; "
            "blur eerst gezichten en kentekens")
    if not image_bytes:
        raise ValueError("image_bytes is verplicht")

    sleutel = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not sleutel:
        return _leeg("geen AI geconfigureerd op deze omgeving")

    # Voorbeelden uit eerdere ritten (schouw_leren) staan vóór het beeld, met
    # hun eigen cache-breekpunt: ze zijn bij elk beeld van de organisatie gelijk.
    inhoud: list[dict] = list(voorbeelden or []) + [{
        "type": "image",
        "source": {"type": "base64", "media_type": image_media_type,
                   "data": _base64(image_bytes)},
    }]
    if context:
        inhoud.append({"type": "text", "text": context[:1000]})
    if stand not in STANDEN:
        raise ValueError(f"onbekende stand: {stand}")

    start = time.perf_counter()
    try:
        rauw_tekst, model_id = _roep_aan(sleutel, inhoud, instellingen, stand=stand)
    except Exception as exc:  # noqa: BLE001 — één kapot frame stopt geen rit
        return _leeg(f"analyse mislukt: {exc}"[:200])

    try:
        rauw = _parse(rauw_tekst)
        if stand == "wegdek":
            # Wat het model buiten het wegdek toch noemt, hoort niet bij deze stand.
            rauw = {k: v for k, v in rauw.items() if k not in ("gebied", "objecten")}
        uit = _schoon(rauw, instellingen)
    except Exception as exc:  # noqa: BLE001
        return _leeg(f"antwoord niet te lezen: {exc}"[:200])

    uit["_model_id"] = model_id
    uit["_duur_ms"] = int((time.perf_counter() - start) * 1000)
    return uit


def _base64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode("ascii")


def _verzoek_extra(model: str, effort: str = "low") -> dict:
    """Wat er per model extra meegaat, als extra_body/extra_headers.

    Via extra_* werkt het op elke versie van de SDK: requirements.txt pint
    alleen een ondergrens. Lage effort omdat dit een drukke, tijdgevoelige
    route is; Haiku kent effort niet. De terugval bij een weigering alleen op
    de modellen die hem ondersteunen -- elders zou hij elk beeld laten falen.
    """
    body: dict = {}
    headers: dict = {}
    if not model.startswith("claude-haiku"):
        body["output_config"] = {"effort": effort}
    if model in ("claude-opus-5", "claude-fable-5-1"):
        body["fallbacks"] = "default"
        headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
    extra: dict = {}
    if body:
        extra["extra_body"] = body
    if headers:
        extra["extra_headers"] = headers
    return extra


def _roep_aan(sleutel: str, inhoud: list[dict],
              instellingen: Optional[dict] = None,
              stand: str = "alles") -> tuple[str, Optional[str]]:
    """Eén beeld naar het model. De catalogus staat in de system prompt en
    wordt gecachet: hij is bij elk beeld gelijk, dus na het eerste beeld
    betaal je hem voor een tiende."""
    import anthropic

    model = os.environ.get("SCHOUW_MODEL") or MODEL_STANDAARD
    grondigheid = (instellingen or {}).get("grondigheid")
    if stand == "wegdek":
        tekst, effort = _systeem_prompt_wegdek(instellingen), _EFFORT_WEGDEK.get(grondigheid, "medium")
    else:
        tekst, effort = _systeem_prompt(instellingen), _EFFORT.get(grondigheid, "low")
    systeem = [{"type": "text", "text": tekst, "cache_control": {"type": "ephemeral"}}]

    client = anthropic.Anthropic(api_key=sleutel)
    msg = client.messages.create(
        model=model, max_tokens=16000, system=systeem,
        messages=[{"role": "user", "content": inhoud}],
        **_verzoek_extra(model, effort))
    if getattr(msg, "stop_reason", None) == "refusal":
        raise RuntimeError("beeld niet beoordeeld: model weigerde")
    tekst = "".join(b.text for b in msg.content
                    if getattr(b, "type", None) == "text")
    return tekst, msg.model


def bundel_tot_waarnemingen(frames: list[dict],
                            *, alleen_zeker: bool = True) -> dict:
    """Frames van één vak samenvoegen tot waarnemingen voor `beoordeel_vak`.

    Per meetlat wordt de **slechtste** waarneming aangehouden, niet het
    gemiddelde. Een straat met één zwaar vervuild stuk is niet half schoon; de
    veegwagen moet er hoe dan ook heen. Dat sluit ook aan op de
    slechtste-aspect-regel binnen een vak.

    Getallen komen terug onder ``waarnemingen``, letters onder
    ``directe_klassen`` -- scheefstand en markering meet je niet, die zie je.
    Beide gaan zo rechtstreeks in ``crow_schouw.beoordeel_vak``.

    ``alleen_zeker`` laat waarnemingen onder de drempel buiten de score. Ze
    verdwijnen niet: ze komen terug onder ``te_beoordelen``, samen met alles wat
    geen meetlat kreeg omdat de drager ontbrak.
    """
    waarden: dict[str, float] = {}
    niveaus: dict[str, str] = {}
    te_beoordelen: list[dict] = []
    onbruikbaar = 0
    _rang = {"A+": 5, "A": 4, "B": 3, "C": 2, "D": 1}

    for f in frames:
        if not f.get("bruikbaar"):
            onbruikbaar += 1
            continue
        for w in (f.get("gebied") or []) + (f.get("wegschade") or []):
            if alleen_zeker and w.get("beoordeling_nodig"):
                te_beoordelen.append(w)
                continue
            code = w.get("meetlat")
            if not code:
                te_beoordelen.append(w)
                continue
            if w.get("waarde") is not None:
                waarden[code] = max(waarden.get(code, 0.0), float(w["waarde"]))
            elif w.get("klasse_niveau"):
                # Ook hier het slechtste, niet het laatste.
                huidig = niveaus.get(code)
                if huidig is None or _rang[w["klasse_niveau"]] < _rang[huidig]:
                    niveaus[code] = w["klasse_niveau"]
            else:
                te_beoordelen.append(w)

    return {
        "waarnemingen": waarden,
        "directe_klassen": niveaus,
        "te_beoordelen": te_beoordelen,
        "frames": len(frames),
        "onbruikbare_frames": onbruikbaar,
        "versie": SCHOUW_VISION_VERSION,
    }
