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
from typing import Optional

import crow_schouw as cs

SCHOUW_VISION_VERSION = "schouw-vision.v1-2026-09"

# Boven deze zekerheid mag een waarneming zonder tussenkomst doorstromen naar
# een beeldkwaliteitsscore. Bewust hoog: een onterecht "schoon" kost een
# gemeente niets, een onterecht "vervuild" kost een aannemer geld.
DREMPEL_AUTOMATISCH = 0.80


class NietGeblurd(RuntimeError):
    """Het beeld is niet als privacy-gecontroleerd aangemerkt."""


# Objecten die je op een straatbeeld kunt aanwijzen en die car2023 kent.
OBJECT_TYPES: list[str] = [
    "verkeersbord", "lichtmast", "bewegwijzering", "afvalbak", "zitbank",
    "paal_poller", "fietsenrek", "abri", "hekwerk", "boom",
]

_OBJECT_ASPECTEN = ["heelheid", "reinheid", "stabiliteit", "functie"]


def _systeem_prompt() -> str:
    """De prompt vraagt om detectieklasse plus drager, niet om een meetlat.

    Een model ziet "hier zit graffiti op die nutskast", niet
    "bekladding.nutskast". De vertaling naar de meetlat doet crow_schouw; hier
    vragen we alleen wat er te zien is en waarop. Dat scheelt een lange lijst
    codes in de prompt en levert betrouwbaardere antwoorden op.
    """
    klassen = "\n".join(
        f"- {k['code']} ({k['naam']}): waarop -> {', '.join(k['dragers'])}"
        for k in cs.detectieklassen())
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
- bij scheefstand, markering en verharding geef je geen getal maar een letter in
  "klasse": A (recht/strak), B (licht), C (duidelijk), D (sterk)

OBJECTEN die je los mag benoemen: {', '.join(OBJECT_TYPES)}
Per object mag je per aspect ({', '.join(_OBJECT_ASPECTEN)}) melden of er iets
opvalt. Beoordeel alleen wat zichtbaar is. Beoordeel niet of iets aan een norm
voldoet, of iets veilig is, of hoe oud het is.

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
      "zekerheid": 0.74, "toelichting": "mast helt duidelijk"}}
  ],
  "objecten": [
    {{"type": "afvalbak", "aspect": "reinheid", "waarneming": "bak zit vol",
      "zekerheid": 0.72}}
  ]
}}

Zet "bruikbaar" op false als het beeld te donker, te onscherp of te vol is om
iets zinnigs over te zeggen, en vul dan "reden_onbruikbaar" in. Dat is een
nuttig antwoord: het vertelt de gebruiker dat er op dit stuk niets gemeten is,
in plaats van dat het schoon zou zijn."""


def _leeg(reden: str) -> dict:
    return {
        "bruikbaar": False,
        "reden_onbruikbaar": reden,
        "gebied": [],
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


def _schoon(rauw: dict) -> dict:
    """Antwoord opschonen en vertalen naar meetlatten.

    Een waarneming zonder herleidbare meetlat -- verzonnen klasse, of graffiti
    zonder drager -- verdwijnt niet, maar krijgt `meetlat: None` en
    `beoordeling_nodig: True`. Dan ziet iemand hem in de beoordeellijst en kan
    hij de drager alsnog invullen. Weggooien zou betekenen dat de AI iets zag en
    niemand het te weten komt.
    """
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
            "beoordeling_nodig": (code is None or zekerheid is None
                                  or zekerheid < DREMPEL_AUTOMATISCH),
        })

    objecten = []
    for o in (rauw.get("objecten") or []):
        if o.get("type") not in OBJECT_TYPES:
            continue
        aspect = o.get("aspect")
        if aspect not in _OBJECT_ASPECTEN:
            aspect = None
        zekerheid = _getal(o.get("zekerheid"), 0.0, 1.0)
        objecten.append({
            "type": o["type"],
            "aspect": aspect,
            "waarneming": (o.get("waarneming") or "")[:300] or None,
            "zekerheid": zekerheid,
            "beoordeling_nodig": zekerheid is None or zekerheid < DREMPEL_AUTOMATISCH,
        })

    return {
        "bruikbaar": bool(rauw.get("bruikbaar", True)),
        "reden_onbruikbaar": rauw.get("reden_onbruikbaar") or None,
        "gebied": gebied,
        "objecten": objecten,
        "_versie": SCHOUW_VISION_VERSION,
    }


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
                    context: Optional[str] = None) -> dict:
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

    inhoud: list[dict] = [{
        "type": "image",
        "source": {"type": "base64", "media_type": image_media_type,
                   "data": _base64(image_bytes)},
    }]
    if context:
        inhoud.append({"type": "text", "text": context[:1000]})

    try:
        rauw_tekst, model_id = _roep_aan(sleutel, inhoud)
    except Exception as exc:  # noqa: BLE001 — één kapot frame stopt geen rit
        return _leeg(f"analyse mislukt: {exc}"[:200])

    try:
        uit = _schoon(_parse(rauw_tekst))
    except Exception as exc:  # noqa: BLE001
        return _leeg(f"antwoord niet te lezen: {exc}"[:200])

    uit["_model_id"] = model_id
    return uit


def _base64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode("ascii")


def _roep_aan(sleutel: str, inhoud: list[dict]) -> tuple[str, Optional[str]]:
    """SDK met HTTP-terugval, gelijk aan inspections.py."""
    model = os.environ.get("SCHOUW_MODEL") or os.environ.get("CLAUDE_MODEL") \
        or "claude-sonnet-4-6"
    systeem = [{"type": "text", "text": _systeem_prompt(),
                "cache_control": {"type": "ephemeral"}}]

    try:
        import anthropic
    except ImportError:
        import httpx
        with httpx.Client(timeout=60.0) as cli:
            r = cli.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": sleutel,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": model, "max_tokens": 1500, "system": systeem,
                      "messages": [{"role": "user", "content": inhoud}]},
            )
            r.raise_for_status()
            body = r.json()
        tekst = "".join(b.get("text", "") for b in body.get("content", [])
                        if b.get("type") == "text")
        return tekst, body.get("model")

    client = anthropic.Anthropic(api_key=sleutel)
    msg = client.messages.create(
        model=model, max_tokens=1500, system=systeem,
        messages=[{"role": "user", "content": inhoud}])
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
        for w in (f.get("gebied") or []):
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
