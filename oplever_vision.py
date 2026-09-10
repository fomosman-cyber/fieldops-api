"""Beeldherkenning voor de opleverronde — één frame tegelijk.

Bij een oplevering loopt of rijdt iemand het werk af en noteert wat er nog niet
klaar of niet goed is. Dat heet een restpunt, en die lijst is waar de discussie
met de aannemer over gaat. Punt voor punt intypen kan, maar dan gebeurt het
vaak niet: het is regenachtig, het zijn er dertig, en de telefoon zit in een
jaszak. Dit bestand kijkt naar een beeld van dat werk en zegt wat het ziet.

Zusje van `schouw_vision`, met dezelfde regels en een ander onderwerp. De schouw
meet beeldkwaliteit van de openbare ruimte tegen CROW-meetlatten; hier kijken we
naar **opgeleverd werk**: ligt de bestrating vlak, zijn de voegen dicht, staat
het meubilair recht, is het bouwafval weg.

**De AI stelt voor, de mens bevestigt.** Elk gevonden punt komt binnen als
voorstel met een zekerheid. Niets gaat automatisch de definitieve
restpuntenlijst in. Dat is geen voorzichtigheid maar noodzaak: die lijst is een
contractstuk waar geld aan hangt, en bij een geschil is "de computer zei het"
geen onderbouwing. Een lijst die zichzelf afvinkt is geen oplevering.

**Alleen wat visueel eenduidig is.** Of een tegel losligt is te zien. Of de
fundering eronder deugt niet. Of er zand op de weg ligt is te zien; of dat van
deze aannemer komt niet. Alles wat verder gaat dan waarneming is een oordeel, en
dat blijft bij degene die opleverbevoegd is.

**Privacy is een poort, geen aanbeveling.** Een opleverronde loopt over straat
en langs woningen; er staan mensen en auto's in beeld. Net als bij de schouw
weigert `analyseer_frame` een beeld dat niet als gecontroleerd is aangemerkt.

Let op de kosten: elk frame is een aparte vision-aanroep. Laat de gebruiker
zelf het moment kiezen (een knop) of schiet op afstand in plaats van op tijd.
Een ronde van een half uur op één frame per seconde is achttienhonderd
aanroepen, en dat is voor een rijtje restpunten nergens voor nodig.
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Optional

OPLEVER_VISION_VERSIE = "oplever-vision.v1-2026-09"

# Standaardmodel. Zelfde keuze als schouw_vision: overschrijfbaar per omgeving,
# zodat een nieuwer model niet wacht op een release.
STANDAARD_MODEL = os.environ.get("OPLEVER_VISION_MODEL", "claude-sonnet-5")


class NietGecontroleerd(RuntimeError):
    """Beeld is niet als privacy-gecontroleerd aangemerkt."""


# ─────────────────────────────────────────────────────────────────────────────
# Wat een restpunt kan zijn
# ─────────────────────────────────────────────────────────────────────────────
# Bewust de taal van de straat en niet van een norm: een uitvoerder zegt "die
# band ligt scheef", niet "afwijking maatvoering lijnvoering conform RAW". De
# koppeling naar een bestekspost is werk voor de mens die het punt bevestigt.

RESTPUNT_KLASSEN: list[dict] = [
    {"code": "verharding_los", "naam": "Losliggend of wiebelend element",
     "waar": ["elementenverharding", "trottoir", "inrit", "fietspad"]},
    {"code": "verharding_hoogte", "naam": "Hoogteverschil of verzakking",
     "waar": ["elementenverharding", "asfalt", "aansluiting op put of kolk"]},
    {"code": "verharding_ontbreekt", "naam": "Ontbrekend element of gat",
     "waar": ["elementenverharding", "trottoir", "berm"]},
    {"code": "voeg_open", "naam": "Voeg niet gevuld of naad open",
     "waar": ["elementenverharding", "asfaltnaad", "aansluiting op band"]},
    {"code": "band_verschoven", "naam": "Band of goot niet op lijn of hoogte",
     "waar": ["trottoirband", "goot", "inritband"]},
    {"code": "put_niet_vlak", "naam": "Put of kolk niet vlak in het werk",
     "waar": ["straatkolk", "inspectieput", "afsluiterkast"]},
    {"code": "markering_ontbreekt", "naam": "Markering ontbreekt of is onvolledig",
     "waar": ["rijbaan", "fietspad", "parkeervak", "oversteek"]},
    {"code": "markering_onjuist", "naam": "Markering scheef, vuil of verkeerd geplaatst",
     "waar": ["rijbaan", "fietspad", "parkeervak"]},
    {"code": "beschadiging", "naam": "Beschadiging aan nieuw of bestaand werk",
     "waar": ["band", "verharding", "gevel", "boom", "hekwerk", "meubilair"]},
    {"code": "afwerking", "naam": "Aansluiting of talud niet afgewerkt",
     "waar": ["aansluiting op bestaand werk", "talud", "berm", "kantopsluiting"]},
    {"code": "groen_niet_afgewerkt", "naam": "Groenvak niet aangevuld of beplanting beschadigd",
     "waar": ["plantvak", "gazon", "boomspiegel"]},
    {"code": "meubilair", "naam": "Straatmeubilair scheef, los of beschadigd",
     "waar": ["paal", "bord", "bank", "afvalbak", "fietsenrek", "lichtmast"]},
    {"code": "opruimen", "naam": "Bouwafval, zand of materieel nog aanwezig",
     "waar": ["rijbaan", "trottoir", "berm", "plantvak"]},
    {"code": "afzetting", "naam": "Tijdelijke verkeersmaatregel nog aanwezig",
     "waar": ["rijbaan", "trottoir", "fietspad"]},
]

KLASSEN_OP_CODE: dict[str, dict] = {k["code"]: k for k in RESTPUNT_KLASSEN}

# Hoe zwaar een punt weegt. Bepaalt of het de oplevering blokkeert of een
# punt voor de nazorglijst is. De AI mag dit voorstellen; de opleverbevoegde
# stelt het vast -- daar hangt af of er wel of niet wordt opgeleverd.
ERNST_NIVEAUS: dict[str, str] = {
    "licht": "Licht — afwerking, mag mee naar de nazorglijst",
    "matig": "Matig — herstellen voor definitieve oplevering",
    "zwaar": "Zwaar — blokkeert oplevering of is onveilig",
}

# Onder deze zekerheid gaat een voorstel sowieso naar de nakijklijst en nooit
# rechtstreeks in beeld als "gevonden punt". Boven de drempel is het nog steeds
# een voorstel -- alleen eentje waar de inspecteur sneller doorheen loopt.
DREMPEL_NAKIJKEN = 0.65


def klassen() -> list[dict]:
    return list(RESTPUNT_KLASSEN)


def is_geconfigureerd() -> bool:
    """Of er op deze omgeving een sleutel staat om echt te kijken."""
    return bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())


# ─────────────────────────────────────────────────────────────────────────────
# De prompt
# ─────────────────────────────────────────────────────────────────────────────

def _systeem_prompt(context: Optional[str] = None) -> str:
    lijst = "\n".join(
        f"- {k['code']} ({k['naam']}): typisch bij {', '.join(k['waar'])}"
        for k in RESTPUNT_KLASSEN)
    return f"""Je kijkt naar één beeld van pas uitgevoerd werk in de Nederlandse
grond-, weg- en waterbouw. Iemand loopt een opleverronde en wil weten wat er nog
niet klaar of niet goed is.

Je taak is waarnemen, niet oordelen. Meld wat je ziet en hoe zeker je bent. Wat
je niet duidelijk kunt zien laat je weg -- een lege lijst is een geldig antwoord
en beter dan een gok. Werk dat er goed uitziet meld je niet: deze lijst gaat
alleen over wat nog aandacht vraagt.

WAT JE MAG MELDEN:
{lijst}

PLEK is belangrijk. "Er ligt een tegel los" zonder te zeggen waar is niet terug
te vinden op de bouwplaats. Beschrijf de plek zoals iemand hem zou aanwijzen:
"voor de inrit van nummer 12", "bij de kolk in de bocht", "linkerzijde
trottoir". Kun je dat niet, laat het veld dan leeg en zet de zekerheid laag.

ERNST schat je in als:
- "licht": afwerking, cosmetisch, kan mee naar de nazorglijst
- "matig": hoort hersteld voor definitieve oplevering
- "zwaar": onveilig of blokkeert het gebruik (open gat, losse put, ontbrekende
  markering op een kruising)
Twijfel je, kies dan de lichtere. Te zwaar inschatten kost de aannemer geld dat
hij misschien niet hoort te betalen.

ZEKERHEID is een getal tussen 0 en 1. Wees streng: 0.9 of hoger alleen bij iets
dat scherp in beeld staat en onmiskenbaar is. Bij regen, tegenlicht,
bewegingsonscherpte, schaduw of een klein detail in de verte hoort de zekerheid
laag.

WAT JE NIET DOET:
- Niet beoordelen of iets aan een norm of bestek voldoet. Dat kun je van een
  foto niet zien en het is niet aan jou.
- Niet zeggen wie iets heeft veroorzaakt of wanneer.
- Niet raden wat eronder zit. Een verzakking zie je; de oorzaak niet.
- Geen personen, kentekens of huisnummers beschrijven als dat niet nodig is om
  de plek aan te wijzen.
{('' if not context else chr(10) + 'CONTEXT VAN HET WERK: ' + context[:500] + chr(10))}
Antwoord met uitsluitend geldige JSON, zonder toelichting eromheen:
{{
  "bruikbaar": true,
  "reden_onbruikbaar": null,
  "punten": [
    {{"klasse": "verharding_los", "plek": "trottoir voor de inrit",
      "ernst": "matig", "zekerheid": 0.83,
      "omschrijving": "twee tegels liggen los en wiebelen"}},
    {{"klasse": "opruimen", "plek": "berm links",
      "ernst": "licht", "zekerheid": 0.91,
      "omschrijving": "restant straatzand en een lege zak niet opgeruimd"}}
  ]
}}

Zet "bruikbaar" op false als het beeld te donker, te onscherp of te vol is om
iets zinnigs over te zeggen, en vul dan "reden_onbruikbaar" in. Dat is een
nuttig antwoord: het vertelt de gebruiker dat er op dit stuk niets is bekeken,
in plaats van dat het in orde zou zijn."""


# ─────────────────────────────────────────────────────────────────────────────
# Analyse
# ─────────────────────────────────────────────────────────────────────────────

def _leeg(reden: str) -> dict:
    """Een als onbruikbaar gemarkeerd antwoord.

    Bewust geen lege puntenlijst die als "niets gevonden" leest: niets bekeken
    is niet hetzelfde als niets gevonden, en dat verschil hoort de gebruiker te
    zien voordat hij een oplevering tekent.
    """
    return {"bruikbaar": False, "reden_onbruikbaar": reden, "punten": [],
            "_versie": OPLEVER_VISION_VERSIE}


def analyseer_frame(*,
                    image_bytes: bytes,
                    image_media_type: str = "image/jpeg",
                    privacy_gecontroleerd: bool,
                    context: Optional[str] = None) -> dict:
    """Eén opleverbeeld analyseren.

    ``privacy_gecontroleerd`` moet expliciet True zijn. Een opleverronde loopt
    over straat en langs woningen; het beeld gaat naar een verwerker buiten de
    EU en dat mag niet met herkenbare personen erop.

    Zonder ``ANTHROPIC_API_KEY`` komt er een leeg, als onbruikbaar gemarkeerd
    antwoord terug -- niet een schone lijst.
    """
    if not privacy_gecontroleerd:
        raise NietGecontroleerd(
            "Beeld is niet als privacy-gecontroleerd aangemerkt; "
            "zorg dat er geen herkenbare personen of kentekens in beeld staan")
    if not image_bytes:
        raise ValueError("image_bytes is verplicht")

    sleutel = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not sleutel:
        return _leeg("geen AI geconfigureerd op deze omgeving")

    inhoud = [{
        "type": "image",
        "source": {"type": "base64", "media_type": image_media_type,
                   "data": base64.b64encode(image_bytes).decode("ascii")},
    }]

    try:
        rauw, model_id = _roep_aan(sleutel, inhoud, context)
    except Exception as exc:  # noqa: BLE001 — één kapot frame stopt geen ronde
        return _leeg(f"analyse mislukt: {exc}"[:200])

    try:
        uit = _schoon(_parse(rauw))
    except Exception as exc:  # noqa: BLE001
        return _leeg(f"antwoord niet te lezen: {exc}"[:200])

    uit["_model_id"] = model_id
    uit["_versie"] = OPLEVER_VISION_VERSIE
    return uit


def _roep_aan(sleutel: str, inhoud: list[dict],
              context: Optional[str]) -> tuple[str, Optional[str]]:
    """De vision-aanroep. Zelfde vorm als schouw_vision, inclusief de
    http-terugval voor omgevingen zonder de anthropic-bibliotheek."""
    systeem = _systeem_prompt(context)
    model = STANDAARD_MODEL
    try:
        import anthropic
    except ImportError:
        import httpx
        with httpx.Client(timeout=60) as client:
            r = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": sleutel,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": model, "max_tokens": 1500, "system": systeem,
                      "messages": [{"role": "user", "content": inhoud}]})
            r.raise_for_status()
            body = r.json()
            return body["content"][0]["text"], body.get("model")

    client = anthropic.Anthropic(api_key=sleutel)
    bericht = client.messages.create(
        model=model, max_tokens=1500, system=systeem,
        messages=[{"role": "user", "content": inhoud}])
    return bericht.content[0].text, getattr(bericht, "model", model)


def _parse(ruw: str) -> dict:
    """JSON uit het antwoord halen, ook als er tekst omheen staat."""
    tekst = (ruw or "").strip()
    if tekst.startswith("```"):
        tekst = re.sub(r"^```(?:json)?\s*|\s*```$", "", tekst).strip()
    try:
        return json.loads(tekst)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", tekst, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _schoon(rauw: dict) -> dict:
    """Alleen bekende klassen, geldige ernst en een zekerheid tussen 0 en 1.

    Een model dat een klasse verzint of ernst "catastrofaal" teruggeeft mag de
    lijst niet vervuilen; zo'n punt valt eruit in plaats van dat het met een
    gokwaarde doorstroomt.
    """
    punten = []
    for p in (rauw.get("punten") or []):
        if not isinstance(p, dict):
            continue
        code = (p.get("klasse") or "").strip()
        if code not in KLASSEN_OP_CODE:
            continue
        ernst = (p.get("ernst") or "").strip().lower()
        if ernst not in ERNST_NIVEAUS:
            ernst = "licht"          # bij twijfel de lichtste, zie de prompt
        omschrijving = (p.get("omschrijving") or "").strip()
        if not omschrijving:
            continue                 # een punt zonder omschrijving is onbruikbaar
        punten.append({
            "klasse": code,
            "klasse_naam": KLASSEN_OP_CODE[code]["naam"],
            "plek": (p.get("plek") or "").strip()[:255] or None,
            "ernst": ernst,
            "omschrijving": omschrijving[:500],
            "zekerheid": _zekerheid(p.get("zekerheid")),
        })

    bruikbaar = bool(rauw.get("bruikbaar", True))
    return {
        "bruikbaar": bruikbaar,
        "reden_onbruikbaar": (rauw.get("reden_onbruikbaar") or None) if not bruikbaar else None,
        "punten": punten,
    }


def _zekerheid(waarde) -> float:
    try:
        z = float(waarde)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, z))


def moet_nagekeken(zekerheid: Optional[float]) -> bool:
    """Of dit voorstel eerst langs een mens moet voordat het meetelt.

    Ook boven de drempel blijft het een voorstel -- dit zegt alleen of het
    scherm het als 'gevonden' of als 'nakijken' presenteert.
    """
    if zekerheid is None:
        return True
    return float(zekerheid) < DREMPEL_NAKIJKEN
