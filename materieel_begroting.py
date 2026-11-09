"""Van werkzaamheden naar materieel, uren en CO2 -- door het te laten uitrekenen.

De registratie achteraf stond er al: welke machine, hoeveel uur, hoeveel
getankt. Wat ontbrak is de kant ervoor. Een uitvoerder weet wat hij gaat doen
("450 m2 asfalt frezen en herstraten") maar niet uit zijn hoofd hoeveel
draaiuren dat is, welke machines erbij horen en wat dat aan uitstoot betekent.
Dat laat hij hier uitrekenen.

**Het blijft een voorstel.** Alles wat hieruit komt is bewerkbaar en wordt pas
iets zodra de gebruiker het overneemt. Daarom staat er `bron` bij: "claude" als
het is uitgerekend, "leeg" als er geen AI beschikbaar is. In dat tweede geval
verzinnen we niets -- dan komt er een lege begroting terug met de eigen
machines erin en vult de uitvoerder de uren zelf. Een kengetal dat wij
verzinnen ziet eruit als kennis en is het niet.

**Het rekent met de machines van de organisatie.** Het register gaat mee in de
vraag, zodat er "Rupskraan 8t" uit komt en niet "een graafmachine". Staat er
niets in het register, dan mag er materieel worden voorgesteld op soort; die
regels krijgen geen materieel_id en zijn dus losse regels, precies zoals een
eenmalige huur.

Ontwerpregel overgenomen van toolbox_ai: een AI-storing mag niemand blokkeren.
Zonder sleutel, bij een netwerkfout of bij een onleesbaar antwoord komt er een
bruikbaar (leeg) voorstel terug en zegt `bron` eerlijk wat er is gebeurd.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import materieel as mt

PROMPT_VERSIE = "v1.0-begroting"

DEFAULT_MODEL = os.environ.get("BEGROTING_MODEL", "claude-opus-5")
MAX_TOKENS = 3000

# Eenheden waarin werk wordt opgegeven. Bewust kort: dit is wat er in een
# bestekregel of op een bon staat.
EENHEDEN: dict[str, str] = {
    "m2": "m²",
    "m1": "m¹",
    "m3": "m³",
    "stuks": "stuks",
    "ton": "ton",
    "uur": "uur",
}

SYSTEEM_PROMPT = (
    "Je bent werkvoorbereider in de Nederlandse grond-, weg- en waterbouw. Je "
    "schat welk materieel er nodig is voor een omschreven klus en hoeveel "
    "draaiuren dat per machine kost.\n\n"
    "Regels:\n"
    "- Kies bij voorkeur uit het materieel dat de organisatie zelf heeft. Geef "
    "dan het id mee dat erbij staat.\n"
    "- Heeft de organisatie iets niet wat wel nodig is, stel het dan voor "
    "zonder id; dat wordt dan een eenmalige huur.\n"
    "- Draaiuren zijn uren dat de machine daadwerkelijk draait, niet de duur "
    "van de klus. Een kraan die een dag op de klus staat draait zelden acht uur.\n"
    "- Wees terughoudend: liever vier machines die er echt bij horen dan tien "
    "die misschien van pas komen.\n"
    "- Weet je iets niet, zet het dan niet in de lijst. Een verzonnen regel "
    "kost buiten een rit terug.\n"
    "- Antwoord in het Nederlands."
)

BEGROTING_SCHEMA = {
    "type": "object",
    "properties": {
        "regels": {
            "type": "array",
            "description": "Het materieel dat voor deze klus nodig is.",
            "items": {
                "type": "object",
                "properties": {
                    "materieel_id": {
                        "type": ["string", "null"],
                        "description": "Het id uit het register van de organisatie, of null als het materieel daar niet in staat.",
                    },
                    "naam": {
                        "type": "string",
                        "description": "Naam van de machine, gelijk aan die in het register als er een id is.",
                    },
                    "draaiuren": {
                        "type": "number",
                        "description": "Geschatte draaiuren voor de hele klus, met een halve uur nauwkeurig.",
                    },
                    "toelichting": {
                        "type": "string",
                        "description": "Eén korte zin: waarvoor deze machine nodig is en waar de uren op gebaseerd zijn.",
                    },
                },
                "required": ["naam", "draaiuren", "toelichting"],
            },
        },
        "samenvatting": {
            "type": "string",
            "description": "Twee zinnen over hoe deze begroting is opgebouwd en waar de grootste onzekerheid zit.",
        },
    },
    "required": ["regels", "samenvatting"],
}


def werk_als_tekst(regels: list[dict]) -> str:
    """De werkregels als leesbare opsomming voor in de vraag."""
    uit = []
    for r in regels or []:
        aantal = r.get("aantal")
        eenheid = EENHEDEN.get(r.get("eenheid") or "", r.get("eenheid") or "")
        omschrijving = (r.get("werk") or "").strip()
        if not omschrijving:
            continue
        if aantal:
            uit.append(f"- {omschrijving}: {aantal:g} {eenheid}".rstrip())
        else:
            uit.append(f"- {omschrijving}")
    return "\n".join(uit)


def register_als_tekst(register: list[dict]) -> str:
    """Het eigen materieel, zodat de begroting de machines van de klant noemt."""
    if not register:
        return "(de organisatie heeft nog geen materieel in het register)"
    regels = []
    for m in register[:60]:
        stuk = f"- id={m.get('id')} | {m.get('naam')}"
        if m.get("soort_naam"):
            stuk += f" | {m['soort_naam']}"
        if m.get("energiedrager_naam"):
            stuk += f" | {m['energiedrager_naam']}"
        if m.get("verbruik_per_uur"):
            stuk += f" | {m['verbruik_per_uur']:g} {m.get('eenheid') or ''}/draaiuur"
        else:
            stuk += " | verbruik onbekend"
        regels.append(stuk)
    return "\n".join(regels)


def _leeg(register: list[dict], reden: str) -> dict:
    """Geen AI: wel de eigen machines, geen uren.

    Dit is bewust geen half voorstel. Uren die wij verzinnen zien eruit als een
    berekening en zijn een gok; de uitvoerder weet het beter dan wij.
    """
    return {
        "bron": "leeg",
        "reden": reden,
        "samenvatting": ("Er is geen AI ingesteld op deze omgeving, dus er is niets "
                         "uitgerekend. Kies hieronder zelf de machines en vul de "
                         "draaiuren in; de liters en de CO₂ rekenen we wel uit."),
        "regels": [{
            "materieel_id": m.get("id"),
            "naam": m.get("naam"),
            "draaiuren": None,
            "toelichting": "",
        } for m in (register or [])[:8]],
    }


def _lees_json(tekst: str) -> Optional[dict]:
    if not tekst:
        return None
    try:
        return json.loads(tekst)
    except ValueError:
        pass
    begin, eind = tekst.find("{"), tekst.rfind("}")
    if begin == -1 or eind <= begin:
        return None
    try:
        return json.loads(tekst[begin:eind + 1])
    except ValueError:
        return None


def _normaliseer(data: dict, register: list[dict]) -> Optional[dict]:
    """Haal eruit wat bruikbaar is; weiger de rest.

    Een id dat niet in het register staat wordt weggegooid in plaats van
    overgenomen: anders hangt een begrotingsregel aan een machine die niet
    bestaat, en dan klopt het rapport erover ook niet.
    """
    if not isinstance(data, dict):
        return None
    ruwe = data.get("regels")
    if not isinstance(ruwe, list):
        return None

    bekend = {m.get("id"): m for m in (register or [])}
    regels = []
    for r in ruwe:
        if not isinstance(r, dict):
            continue
        naam = (r.get("naam") or "").strip()
        if not naam:
            continue
        mid = r.get("materieel_id")
        if mid not in bekend:
            mid = None
        uren = r.get("draaiuren")
        if isinstance(uren, bool) or not isinstance(uren, (int, float)) or uren < 0:
            uren = None
        elif uren > 500:
            # Een begroting van meer dan 500 draaiuren voor één machine is geen
            # dagwerk meer; dan is er iets misgelezen in de aantallen.
            uren = None
        regels.append({
            "materieel_id": mid,
            "naam": (bekend[mid]["naam"] if mid else naam)[:160],
            "draaiuren": round(float(uren), 2) if uren is not None else None,
            "toelichting": (r.get("toelichting") or "").strip()[:300],
        })

    if not regels:
        return None
    return {"regels": regels, "samenvatting": (data.get("samenvatting") or "").strip()[:600]}


def _roep_claude(api_key: str, vraag: str) -> tuple:
    """De feitelijke aanroep, apart zodat tests hem kunnen vervangen via
    `monkeypatch.setattr("materieel_begroting._roep_claude", ...)` zonder dat de
    anthropic-SDK geïnstalleerd hoeft te zijn."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    basis = {
        "model": DEFAULT_MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEEM_PROMPT,
        "messages": [{"role": "user", "content": vraag}],
    }
    try:
        response = client.messages.create(
            **basis,
            output_config={"format": {"type": "json_schema", "schema": BEGROTING_SCHEMA}},
        )
    except TypeError:
        response = client.messages.create(**{**basis, "messages": [{
            "role": "user",
            "content": vraag + ("\n\nAntwoord uitsluitend met een JSON-object met de "
                                "sleutels regels en samenvatting."),
        }]})
    tekst = next((b.text for b in response.content if getattr(b, "type", None) == "text"), "")
    return tekst, getattr(response, "model", None)


def begroot(*, omschrijving: str, werkregels: list[dict], register: list[dict],
            eigen_factoren: Optional[dict] = None) -> dict:
    """Reken een klus door naar materieel, draaiuren, brandstof en CO2.

    Gooit nooit. Bij een storing komt er een leeg voorstel terug en zegt `bron`
    waarom.
    """
    werk = werk_als_tekst(werkregels)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    if not api_key:
        voorstel = _leeg(register, "geen ANTHROPIC_API_KEY ingesteld")
    elif not werk and not (omschrijving or "").strip():
        voorstel = _leeg(register, "geen werkzaamheden opgegeven")
    else:
        vraag = (
            f"Werkzaamheden:\n{werk or '(niet uitgesplitst)'}\n\n"
            f"Toelichting van de uitvoerder:\n{(omschrijving or '').strip() or '(geen)'}\n\n"
            f"Materieel van deze organisatie:\n{register_als_tekst(register)}\n\n"
            "Geef het materieel dat hiervoor nodig is met de geschatte draaiuren."
        )
        try:
            tekst, model_id = _roep_claude(api_key, vraag)
            schoon = _normaliseer(_lees_json(tekst) or {}, register)
            if schoon is None:
                voorstel = _leeg(register, "het antwoord was niet te gebruiken")
            else:
                voorstel = {"bron": "claude", "reden": "",
                            "model_id": model_id or DEFAULT_MODEL,
                            "prompt_versie": PROMPT_VERSIE, **schoon}
        except Exception as fout:
            voorstel = _leeg(register, f"{type(fout).__name__}")

    return reken_door(voorstel, register, eigen_factoren)


def reken_door(voorstel: dict, register: list[dict],
                eigen_factoren: Optional[dict]) -> dict:
    """Van draaiuren naar liters en kilogrammen, met de bestaande rekenregel.

    Hier geldt hetzelfde als bij een dagregel: zonder verbruik per uur geen
    getal. Het verschil is dat een begroting per definitie een schatting is --
    er is nog niets getankt -- dus 'gemeten' komt hier niet voor.
    """
    bekend = {m.get("id"): m for m in (register or [])}
    uit = []
    berekeningen = []
    for r in voorstel.get("regels", []):
        stuk = bekend.get(r.get("materieel_id")) or {}
        drager = stuk.get("energiedrager") or "diesel"
        per_uur = stuk.get("verbruik_per_uur")
        som = mt.bereken(energiedrager=drager, draaiuren=r.get("draaiuren"),
                         verbruik_per_uur=per_uur, eigen_factoren=eigen_factoren)
        berekeningen.append(som)
        uit.append({
            **r,
            "energiedrager": drager,
            "energiedrager_naam": mt.ENERGIEDRAGERS.get(drager, {}).get("naam", drager),
            "eenheid": som.eenheid or mt.ENERGIEDRAGERS.get(drager, {}).get("eenheid", ""),
            "verbruik_per_uur": per_uur,
            "brandstof_hoeveelheid": som.hoeveelheid,
            "co2_kg": som.kg,
            "co2_methode": som.methode,
            "co2_uitleg": som.uitleg(),
        })

    voorstel["regels"] = uit
    voorstel["totaal"] = mt.totaal(berekeningen)
    voorstel["totaal_draaiuren"] = round(
        sum(r.get("draaiuren") or 0 for r in uit), 2)
    return voorstel
