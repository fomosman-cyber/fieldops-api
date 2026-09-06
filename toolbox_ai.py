"""Toolbox-generatie met Claude.

Een toolbox is de korte veiligheidsbespreking die een uitvoerder houdt voordat
het werk begint. Deze module stelt de inhoud op uit twee bronnen:

    1. het onderwerp dat de uitvoerder zelf intypt;
    2. de context van het project — welke objecten er staan en welke meldingen
       en gebreken er openstaan.

Dat tweede is het punt. Een toolbox over "werken langs de rijbaan" die niet
weet dat er op dit project drie meldingen over een kapotte afzetting open
staan, is een algemeen praatje. Met die context wordt het een bespreking over
het werk van vandaag.

Ontwerpregel, overgenomen van inspections.py: een AI-storing mag de uitvoerder
nooit blokkeren. Zonder API-sleutel, bij een netwerkfout, bij een SDK die de
aanroep niet kent — altijd komt er een bruikbare toolbox terug. Dan een
sjabloon in plaats van maatwerk, en `bron` zegt eerlijk welke van de twee het
is, zodat het portaal en de PDF dat kunnen tonen.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

PROMPT_VERSION = "v1.0-toolbox"

# Eigen variabele, los van CLAUDE_MODEL dat de inspectie-analyse stuurt: die
# twee taken hoeven niet op hetzelfde model te zitten en een wijziging aan de
# een hoort de ander niet te raken.
DEFAULT_MODEL = os.environ.get("TOOLBOX_MODEL", "claude-opus-5")

MAX_TOKENS = 4000

# Terugvalinstructie voor SDK-versies die output_config nog niet kennen.
JSON_INSTRUCTIE = (
    "\n\nAntwoord uitsluitend met een JSON-object met de sleutels inleiding "
    "(string), risicos, maatregelen en bespreekpunten (alle drie een array van "
    "strings). Geen tekst eromheen."
)

# Het schema dat Claude moet invullen. Met output_config.format komt het
# antwoord schema-gevalideerd terug in plaats van als tekst waar wij JSON uit
# moeten vissen.
TOOLBOX_SCHEMA = {
    "type": "object",
    "properties": {
        "inleiding": {
            "type": "string",
            "description": "Twee of drie zinnen die het onderwerp inleiden en aan dit project koppelen.",
        },
        "risicos": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete risico's op deze werkplek. Vier tot zes stuks, elk een korte zin.",
        },
        "maatregelen": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Wat we doen om die risico's te beheersen. Even veel als er risico's zijn, in dezelfde volgorde.",
        },
        "bespreekpunten": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Drie tot vijf vragen die de uitvoerder aan de ploeg stelt. Open vragen, geen ja-nee.",
        },
    },
    "required": ["inleiding", "risicos", "maatregelen", "bespreekpunten"],
    "additionalProperties": False,
}

SYSTEEM_PROMPT = """Je stelt toolboxen op voor Nederlandse infrabeheerders en \
inspectiebureaus: aannemers die aan wegen, bruggen, riolering en openbare \
ruimte werken.

Een toolbox is geen veiligheidscursus. Het is vijf tot tien minuten praten met \
de ploeg voordat het werk begint. Schrijf daarnaar:

- Nederlands, korte zinnen, aanspreekvorm "je". Geen jargon dat een ZZP'er \
niet kent, geen Engelse termen waar een Nederlandse bestaat.
- Concreet en plaatsgebonden. "Passerend verkeer op 50 cm van de werkstrook" \
is bruikbaar; "let op de veiligheid" niet.
- Gebruik de projectcontext die je krijgt. Staan er openstaande meldingen over \
een gebrek dat vandaag risico oplevert, benoem dat dan expliciet.
- Maatregelen horen een-op-een bij de risico's, in dezelfde volgorde.
- Verwijs naar de Nederlandse praktijk waar dat klopt: CROW 96b voor \
wegafzettingen, VCA, RI&E. Verzin geen artikelnummers of normverwijzingen die \
je niet zeker weet — liever geen verwijzing dan een verkeerde.

Verzin geen feiten over het project die niet in de context staan."""


def _projectcontext(project_naam: str,
                    assets: Optional[list] = None,
                    meldingen: Optional[list] = None) -> str:
    """Zet de projectgegevens om in tekst die het model kan lezen.

    Bewust compact: het gaat om wat er staat en wat er stuk is, niet om een
    volledige database-dump.
    """
    regels = [f"Project: {project_naam}"]

    if assets:
        soorten: dict[str, int] = {}
        for a in assets:
            soort = (a.get("asset_type") or "onbekend").strip() or "onbekend"
            soorten[soort] = soorten.get(soort, 0) + 1
        omschrijving = ", ".join(f"{n}x {s}" for s, n in sorted(soorten.items()))
        regels.append(f"Objecten op dit project: {omschrijving}")
    else:
        regels.append("Objecten op dit project: geen geregistreerd")

    if meldingen:
        regels.append(f"Openstaande meldingen ({len(meldingen)}):")
        for m in meldingen[:12]:
            titel = (m.get("titel") or m.get("title") or "").strip()
            prio = (m.get("prioriteit") or m.get("priority") or "").strip()
            cat = (m.get("categorie") or m.get("category") or "").strip()
            deel = " · ".join(x for x in (prio, cat) if x)
            regels.append(f"  - {titel}" + (f" ({deel})" if deel else ""))
        if len(meldingen) > 12:
            regels.append(f"  - ... en nog {len(meldingen) - 12} andere")
    else:
        regels.append("Openstaande meldingen: geen")

    return "\n".join(regels)


def _sjabloon(onderwerp: str, project_naam: str,
              meldingen: Optional[list] = None) -> dict:
    """Bruikbare toolbox zonder AI.

    Dit is geen placeholder: als er geen sleutel is of de API ligt eruit, moet
    de uitvoerder hier nog steeds mee de bouwplaats op kunnen. Daarom een echt
    stramien dat hij zelf aanvult, en de openstaande meldingen erbij — die
    kennen we ook zonder model.
    """
    risicos = [
        "Passerend verkeer of langsrijdend materieel op de werkplek.",
        "Struikelen en uitglijden door materiaal, kabels of gladheid.",
        "Onvoldoende zicht op elkaar bij machinaal werk.",
        "Weersomstandigheden: regen, wind, kou of juist hitte.",
    ]
    maatregelen = [
        "Afzetting volgens CROW 96b, en controleer of die er aan het eind van de dag nog net zo bij staat.",
        "Looppaden vrijhouden, materiaal op een vaste plek, gladde delen strooien of afzetten.",
        "Oogcontact met de machinist voordat je binnen zijn bereik komt; hesje aan.",
        "Kleding en werktempo aanpassen aan het weer; bij onwerkbaar weer stoppen en melden.",
    ]

    if meldingen:
        risicos.insert(0, f"Er staan {len(meldingen)} meldingen open op dit project die het werk kunnen raken.")
        maatregelen.insert(0, "Loop de openstaande meldingen langs voordat je begint en bepaal welke vandaag in de weg zitten.")

    return {
        "inleiding": (
            f"Vandaag bespreken we {onderwerp} op {project_naam}. "
            "Loop dit punt voor punt door met de ploeg en vul aan wat je op deze "
            "specifieke plek ziet."
        ),
        "risicos": risicos,
        "maatregelen": maatregelen,
        "bespreekpunten": [
            "Wat is hier vandaag het grootste risico volgens jullie?",
            "Wie doet wat als er iets misgaat, en wie belt er?",
            "Is er iets veranderd sinds gisteren waar we rekening mee moeten houden?",
        ],
        "bron": "sjabloon",
        "model_id": None,
        "prompt_versie": PROMPT_VERSION,
    }


def _lees_json(tekst: str) -> Optional[dict]:
    """Haal een JSON-object uit een antwoord dat er omheen kan praten."""
    tekst = (tekst or "").strip()
    if not tekst:
        return None
    try:
        return json.loads(tekst)
    except (ValueError, TypeError):
        pass
    # Model heeft er tekst omheen gezet of een codeblok gebruikt.
    match = re.search(r"\{.*\}", tekst, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (ValueError, TypeError):
        return None


def _normaliseer(data: dict) -> Optional[dict]:
    """Controleer dat het model geleverd heeft wat we nodig hebben."""
    if not isinstance(data, dict):
        return None

    inleiding = data.get("inleiding")
    if not isinstance(inleiding, str) or not inleiding.strip():
        return None

    uit = {"inleiding": inleiding.strip()}
    for veld in ("risicos", "maatregelen", "bespreekpunten"):
        waarde = data.get(veld)
        if not isinstance(waarde, list):
            return None
        regels = [str(x).strip() for x in waarde if str(x).strip()]
        if not regels:
            return None
        uit[veld] = regels
    return uit


def _roep_claude(api_key: str, vraag: str) -> tuple:
    """De feitelijke API-aanroep, apart gehouden zodat tests hem kunnen
    vervangen via `monkeypatch.setattr("toolbox_ai._roep_claude", ...)` zonder
    dat de anthropic-SDK geinstalleerd hoeft te zijn.

    Geeft (antwoordtekst, model_id) terug en mag gerust gooien -- de aanroeper
    vangt alles af en valt terug op het sjabloon.
    """
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
            output_config={"format": {"type": "json_schema", "schema": TOOLBOX_SCHEMA}},
        )
    except TypeError:
        # Oudere SDK kent output_config nog niet. Dan vragen we het antwoord als
        # JSON in de prompt en parsen we het zelf -- zelfde resultaat, alleen
        # zonder de garantie vooraf.
        response = client.messages.create(
            **{**basis, "messages": [{
                "role": "user",
                "content": vraag + JSON_INSTRUCTIE,
            }]},
        )

    tekst = next((b.text for b in response.content if getattr(b, "type", None) == "text"), "")
    return tekst, getattr(response, "model", None)


def genereer_toolbox(*, onderwerp: str, project_naam: str,
                     assets: Optional[list] = None,
                     meldingen: Optional[list] = None) -> dict:
    """Stel een toolbox op. Geeft altijd een bruikbaar resultaat terug.

    De sleutel `bron` zegt of dit van Claude komt ("claude") of het sjabloon is
    ("sjabloon"). Roep deze functie nooit aan in een pad dat een exception naar
    de gebruiker laat lekken — dat is hier niet nodig, hij gooit er geen.
    """
    onderwerp = (onderwerp or "").strip() or "veilig werken op de bouwplaats"
    project_naam = (project_naam or "").strip() or "dit project"

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return _sjabloon(onderwerp, project_naam, meldingen)

    context = _projectcontext(project_naam, assets, meldingen)
    vraag = (
        f"Stel een toolbox op over: {onderwerp}\n\n"
        f"Context van het werk:\n{context}\n\n"
        "Houd de toolbox zo dicht mogelijk bij dit project en dit onderwerp."
    )

    try:
        tekst, model_id = _roep_claude(api_key, vraag)
        data = _normaliseer(_lees_json(tekst) or {})
        if data is None:
            return _sjabloon(onderwerp, project_naam, meldingen)

        data["bron"] = "claude"
        data["model_id"] = model_id or DEFAULT_MODEL
        data["prompt_versie"] = PROMPT_VERSION
        return data

    except Exception:
        # Een ontbrekende SDK, netwerkfout, quotafout of onleesbaar antwoord mag
        # een uitvoerder die op de bouwplaats staat niet tegenhouden.
        return _sjabloon(onderwerp, project_naam, meldingen)

