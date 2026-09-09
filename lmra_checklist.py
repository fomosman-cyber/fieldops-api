"""Controlevragen voor de LMRA — de Laatste Minuut Risico Analyse.

Een LMRA is geen inspectie. Het is het laatste rondje dat de man of vrouw die
het werk gaat doen zelf maakt, vlak voordat het gereedschap aan gaat: klopt het
hier nog met wat we hadden afgesproken? De werkplekinspectie kijkt naar de
werkplek als geheel en wordt door de uitvoerder of KAM gedaan; de LMRA gaat over
deze taak, nu, en wordt gedaan door wie hem uitvoert. Daarom mag iedereen er een
starten en hoeft er geen leidinggevende aan te pas te komen.

Het verschil dat er buiten toe doet: een NEE in de werkplekinspectie is een
actiepunt voor later. Een NEE in de LMRA betekent dat je niet begint. Je neemt
een maatregel en kijkt opnieuw, of je stopt en belt. Vandaar dat elk antwoord
een maatregel-veld heeft en de LMRA eindigt op een oordeel: veilig om te
starten, of niet starten.

De lijst is kort gehouden. Een LMRA die vijf minuten kost wordt niet gedaan, en
een niet-gedane LMRA beschermt niemand. Elf vragen, allemaal ja/nee/nvt en
positief geformuleerd -- "is X in orde?" -- zodat NEE altijd het aandachtspunt
is. Een lijst waarin de ene vraag andersom werkt dan de andere levert fouten op
bij iemand die in de regen langs de weg staat.

Over de normverwijzingen: hier staat alleen wat vaststaat. Waar de regeling
duidelijk is maar het exacte artikel niet, staat de regeling zonder
artikelnummer. Een verzonnen artikelnummer is erger dan geen verwijzing.
"""

from __future__ import annotations

LMRA_VERSION = "lmra.v1-2026-09"

# Volgorde is hoe je het buiten afloopt: eerst jezelf en de taak, dan de plek,
# dan het materieel, dan de mensen om je heen.
CATEGORIEEN: dict[str, str] = {
    "taak": "De taak",
    "mens": "Mens en middelen",
    "werkplek": "Werkplek en verkeer",
    "omgeving": "Omgeving en derden",
    "noodgeval": "Als het misgaat",
}

VRAGEN: list[dict] = [
    # ── De taak ─────────────────────────────────────────────────────
    {
        "code": "LMRA.OPDRACHT",
        "categorie": "taak",
        "vraag": "Weet je precies welke taak je gaat doen en hoe?",
        "uitleg": "Als je het niet in je eigen woorden kunt uitleggen, weet je het niet. Vraag het na.",
        "norm_ref": "VCA",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "LMRA.WIJZIGING",
        "categorie": "taak",
        "vraag": "Is de situatie nog hetzelfde als bij de werkvoorbereiding of toolbox?",
        "uitleg": "Ander weer, andere plek, ander materieel of andere ploeg? Dan gelden de oude afspraken niet zomaar.",
        "norm_ref": "VCA",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "LMRA.BEVOEGD",
        "categorie": "taak",
        "vraag": "Ben je opgeleid en bevoegd voor dit werk en dit materieel?",
        "uitleg": "Denk aan VCA, heftruck, hoogwerker, verkeersmaatregelen, graafwerk.",
        "norm_ref": "Arbowet art. 8",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    # ── Mens en middelen ────────────────────────────────────────────
    {
        "code": "LMRA.FIT",
        "categorie": "mens",
        "vraag": "Ben je fit genoeg om dit veilig te doen?",
        "uitleg": "Ziek, moe, medicijnen, of met je hoofd ergens anders. Dit is geen strikvraag -- meld het.",
        "norm_ref": "Arbowet art. 11",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "LMRA.PBM",
        "categorie": "mens",
        "vraag": "Draag je de juiste PBM en zijn ze heel?",
        "uitleg": "Helm, veiligheidsschoenen, signaalkleding, handschoenen, bril, gehoorbescherming.",
        "norm_ref": "Arbobesluit hfst. 8",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "LMRA.GEREEDSCHAP",
        "categorie": "mens",
        "vraag": "Is je gereedschap en materieel in orde en gekeurd?",
        "uitleg": "Kijk naar beschadigingen, ontbrekende afscherming en de keuringssticker.",
        "norm_ref": "Arbobesluit art. 7.4a",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    # ── Werkplek en verkeer ─────────────────────────────────────────
    {
        "code": "LMRA.AFZETTING",
        "categorie": "werkplek",
        "vraag": "Staat de afzetting goed en klopt hij met de verkeersmaatregel?",
        "uitleg": "Sta je zelf in de veilige zone? Kan een auto of fietser er per ongeluk in rijden?",
        "norm_ref": "CROW 96b",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "LMRA.ONDERGROND",
        "categorie": "werkplek",
        "vraag": "Is de ondergrond en de plek waar je staat veilig?",
        "uitleg": "Denk aan gaten, taluds, water, gladheid en de stabiliteit onder machines.",
        "norm_ref": "Arbobesluit hfst. 3",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "LMRA.KABELS",
        "categorie": "werkplek",
        "vraag": "Is bij grondwerk bekend wat er in de grond ligt, en is dat gecontroleerd?",
        "uitleg": "KLIC-melding aanwezig en proefsleuven gedaan. Niet van toepassing als je niet graaft.",
        "norm_ref": "WIBON",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    # ── Omgeving en derden ──────────────────────────────────────────
    {
        "code": "LMRA.DERDEN",
        "categorie": "omgeving",
        "vraag": "Kunnen omstanders, collega's of ander werk geen gevaar lopen of veroorzaken?",
        "uitleg": "Denk aan bewoners, schoolgaande kinderen, en werk boven of naast je.",
        "norm_ref": "Arbowet art. 10",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    # ── Als het misgaat ─────────────────────────────────────────────
    {
        "code": "LMRA.NOOD",
        "categorie": "noodgeval",
        "vraag": "Weet je wat je doet als het misgaat, en kun je hulp bereiken?",
        "uitleg": "Vluchtweg vrij, EHBO en blusmiddel bereikbaar, telefoon bereik, en je weet het adres van deze plek.",
        "norm_ref": "Arbowet art. 3 lid 1e",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
]

VRAGEN_PER_CODE: dict[str, dict] = {v["code"]: v for v in VRAGEN}

# Het oordeel waarmee een LMRA wordt afgesloten. Bewust maar twee waarden: je
# begint wel of je begint niet. "Deels" bestaat niet als je op de weg staat.
OORDELEN: dict[str, str] = {
    "veilig": "Veilig om te starten",
    "niet_starten": "Niet starten — eerst opgelost of overlegd",
}


def checklist() -> dict:
    """De vragenlijst zoals de app hem toont, gegroepeerd op categorie."""
    return {
        "versie": LMRA_VERSION,
        "categorieen": [
            {
                "key": key,
                "label": label,
                "vragen": [v for v in VRAGEN if v["categorie"] == key],
            }
            for key, label in CATEGORIEEN.items()
        ],
        "oordelen": [{"key": k, "label": v} for k, v in OORDELEN.items()],
        "aantal_vragen": len(VRAGEN),
    }
