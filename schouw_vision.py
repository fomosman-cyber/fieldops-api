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


# Wat we in een straatbeeld zoeken. De meetlat-codes komen uit crow_schouw; de
# objecttypen uit car2023. Zo landt elke waarneming in een bestaande scorelaag
# in plaats van in een eigen lijstje dat later niemand kan koppelen.
GEBIEDS_WAARNEMINGEN: list[dict] = [
    {"meetlat": "zwerfafval", "vraag": "los afval op verharding of in de berm",
     "eenheid": "aantal stuks in beeld"},
    {"meetlat": "bijplaatsing", "vraag": "zakken, dozen of grofvuil naast een container",
     "eenheid": "aantal stuks in beeld"},
    {"meetlat": "onkruid_verharding", "vraag": "begroeiing tussen klinkers, in voegen of langs de band",
     "eenheid": "geschat percentage van het zichtbare verhardingsoppervlak"},
    {"meetlat": "bekladding", "vraag": "graffiti of bekladding op gevels, kasten of abri's",
     "eenheid": "geschat percentage van het zichtbare geveloppervlak"},
    {"meetlat": "veegvuil", "vraag": "zand en aanslag in de goot of tegen de trottoirband",
     "eenheid": "geschat percentage van de zichtbare goot"},
    {"meetlat": "blad", "vraag": "bladophoping op verharding",
     "eenheid": "geschat percentage van het zichtbare verhardingsoppervlak"},
    {"meetlat": "onkruid_groen", "vraag": "onkruid in plantvakken",
     "eenheid": "geschat percentage van het zichtbare plantvak"},
]

# Objecten die je op een straatbeeld kunt aanwijzen en die car2023 kent.
OBJECT_TYPES: list[str] = [
    "verkeersbord", "lichtmast", "bewegwijzering", "afvalbak", "zitbank",
    "paal_poller", "fietsenrek", "abri", "hekwerk", "boom",
]

_OBJECT_ASPECTEN = ["heelheid", "reinheid", "stabiliteit", "functie"]


def _systeem_prompt() -> str:
    meetlatten = "\n".join(
        f"- {w['meetlat']}: {w['vraag']} ({w['eenheid']})"
        for w in GEBIEDS_WAARNEMINGEN)
    return f"""Je beoordeelt één beeld uit een schouw van de Nederlandse openbare ruimte.

Je taak is waarnemen, niet oordelen. Meld wat je ziet en hoe zeker je bent.
Wat je niet duidelijk kunt zien, laat je weg -- een lege lijst is een geldig
antwoord en beter dan een gok.

GEBIEDSWAARNEMINGEN (per meetlat, alleen als je het echt ziet):
{meetlatten}

OBJECTEN die je mag benoemen: {', '.join(OBJECT_TYPES)}
Per object mag je per aspect ({', '.join(_OBJECT_ASPECTEN)}) melden of er iets
opvalt. Beoordeel alleen wat zichtbaar is: een scheve mast, een gedeukt bord,
een volle afvalbak, een beklad paneel. Beoordeel niet of iets aan een norm
voldoet, of iets veilig is, of hoe oud het is.

ZEKERHEID is een getal tussen 0 en 1. Wees streng: geef 0.9 of hoger alleen bij
iets dat scherp in beeld staat en onmiskenbaar is. Bij regen, tegenlicht,
bewegingsonscherpte of een klein object in de verte hoort de zekerheid laag.

Antwoord met uitsluitend geldige JSON, zonder toelichting eromheen:
{{
  "bruikbaar": true,
  "reden_onbruikbaar": null,
  "gebied": [
    {{"meetlat": "zwerfafval", "waarde": 3, "zekerheid": 0.86, "toelichting": "drie blikjes in de berm"}}
  ],
  "objecten": [
    {{"type": "lichtmast", "aspect": "stabiliteit", "waarneming": "mast staat scheef",
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
    """Antwoord opschonen: onbekende meetlatten en objecttypen eruit.

    Een model dat een meetlat verzint die crow_schouw niet kent, levert een
    waarneming op die nooit gescoord kan worden. Die filteren we weg in plaats
    van hem mee te slepen tot hij ergens anders een fout veroorzaakt.
    """
    bekende_meetlatten = {m["meetlat"] for m in GEBIEDS_WAARNEMINGEN}

    gebied = []
    for w in (rauw.get("gebied") or []):
        code = w.get("meetlat")
        if code not in bekende_meetlatten or cs.meetlat(code) is None:
            continue
        zekerheid = _getal(w.get("zekerheid"), 0.0, 1.0)
        gebied.append({
            "meetlat": code,
            "naam": cs.meetlat(code)["naam"],
            "eenheid": cs.meetlat(code)["eenheid"],
            "waarde": _getal(w.get("waarde")),
            "zekerheid": zekerheid,
            "toelichting": (w.get("toelichting") or "")[:300] or None,
            "beoordeling_nodig": zekerheid is None or zekerheid < DREMPEL_AUTOMATISCH,
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

    ``alleen_zeker`` laat waarnemingen onder de drempel buiten de score. Ze
    verdwijnen niet: ze komen terug onder ``te_beoordelen``, zodat iemand ze kan
    nakijken in plaats van dat ze stilletjes wegvallen.
    """
    waarden: dict[str, float] = {}
    te_beoordelen: list[dict] = []
    onbruikbaar = 0

    for f in frames:
        if not f.get("bruikbaar"):
            onbruikbaar += 1
            continue
        for w in (f.get("gebied") or []):
            if w.get("waarde") is None:
                continue
            if alleen_zeker and w.get("beoordeling_nodig"):
                te_beoordelen.append(w)
                continue
            code = w["meetlat"]
            waarden[code] = max(waarden.get(code, 0.0), float(w["waarde"]))

    return {
        "waarnemingen": waarden,
        "te_beoordelen": te_beoordelen,
        "frames": len(frames),
        "onbruikbare_frames": onbruikbaar,
        "versie": SCHOUW_VISION_VERSION,
    }
