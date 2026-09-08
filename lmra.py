"""De laatste-minuut risicoanalyse (LMRA).

De toolbox gaat over het werk van vandaag, de werkplekinspectie over de
werkplek als geheel. Daartussen zit het moment dat er het meest misgaat: de
minuut voordat iemand daadwerkelijk begint. De situatie op dat moment is
zelden precies de situatie waar de toolbox over ging -- er staat een auto waar
gisteren niets stond, het regent, de collega die zou assisteren is er niet.

**De LMRA is een stopinstrument, geen formulier.** Dat is de enige reden dat
hij bestaat. Als iets niet in orde is, begin je niet. Daarom kan een LMRA in
dit systeem niet op "veilig" uitkomen zolang er een NEE in staat; zie
:func:`beoordeel`. Een lijst die je met een nee erin toch kunt afronden leert
mensen dat afvinken genoeg is, en dan is het instrument stuk.

**Een gestopte LMRA blijft gestopt.** Je lost het probleem op en doet daarna
een nieuwe. De oude wordt niet bijgewerkt naar veilig. Zo laat het dossier het
echte verloop zien: om 08:12 gestopt omdat de afzetting ontbrak, maatregel
genomen, om 08:25 opnieuw beoordeeld en veilig. Dat verhaal is precies wat een
onderzoeker na een incident nodig heeft, en het verdwijnt als je de eerste
regel overschrijft.

**Alle vragen staan positief, en het woord "niet" komt er niet in voor.**
"Ja" is altijd goed, "nee" is altijd het probleem. Een lijst waarin één vraag
omgekeerd werkt levert fouten op bij iemand die in de regen op een telefoon
staat te tikken, en zelfs een ontkenning in een bijzin kost een denkstap.
Vandaar "is het werk van anderen om mij heen afgestemd" en niet "kan werk van
anderen mij raken".

Dezelfde reden waarom de vraag over de vergunning niet luidt "is een vergunning
nodig". Daar is "nee" het goede antwoord als er niets nodig is, en dan zou een
veilige situatie het werk stilleggen. Hij vraagt daarom of je de vergunning
hebt die dit werk vraagt; is er geen nodig, dan is het antwoord "niet van
toepassing".

**Acht vragen, niet twintig.** Een LMRA die langer duurt dan een minuut wordt
niet gedaan, of wordt zonder kijken afgevinkt. Dat laatste is erger dan hem
overslaan, want dan staat er een handtekening onder iets dat niemand heeft
gecontroleerd.
"""

from __future__ import annotations

LMRA_VERSIE = "lmra.v1-2026-09"

# Antwoorden. "nvt" bestaat omdat niet elke vraag altijd van toepassing is --
# een werkvergunning is bij een gewone schouw niet aan de orde -- maar het mag
# geen ontsnappingsroute worden voor een vraag waar je geen zin in hebt.
JA, NEE, NVT = "ja", "nee", "nvt"
ANTWOORDEN = (JA, NEE, NVT)

# Vragen waar "niet van toepassing" gewoon niet kan. Je weet altijd wat je gaat
# doen, je draagt altijd iets, en je weet altijd hoe je alarmeert.
NVT_NIET_TOEGESTAAN = frozenset({"LMRA.TAAK", "LMRA.PBM", "LMRA.NOOD"})

VRAGEN: list[dict] = [
    {
        "code": "LMRA.TAAK",
        "vraag": "Weet ik precies wat ik ga doen en hoe?",
        "uitleg": ("Niet: is het besproken. Kun jij nu in één zin zeggen wat de "
                   "eerste handeling is en wat daarna komt?"),
        "norm_ref": "VCA",
    },
    {
        "code": "LMRA.WERKPLEK",
        "vraag": "Is de werkplek veilig, opgeruimd en goed bereikbaar?",
        "uitleg": ("Kijk naar de grond en naar boven. Struikelgevaar, losse "
                   "kabels, hangende delen, gladheid."),
        "norm_ref": "Arbobesluit",
    },
    {
        "code": "LMRA.PBM",
        "vraag": "Draag ik de juiste beschermingsmiddelen voor déze taak?",
        "uitleg": ("Helm, bril, gehoorbescherming, handschoenen, valbeveiliging. "
                   "De taak bepaalt wat je nodig hebt, niet de gewoonte."),
        "norm_ref": "Arbobesluit",
    },
    {
        "code": "LMRA.MIDDELEN",
        "vraag": "Is mijn gereedschap en materieel geschikt, heel en gekeurd?",
        "uitleg": ("Kijk naar de keuringssticker en naar de staat. Geïmproviseerd "
                   "gereedschap is de meest voorkomende oorzaak van letsel."),
        "norm_ref": "Arbobesluit",
    },
    {
        "code": "LMRA.OMGEVING",
        "vraag": "Zijn de risico's uit de omgeving afgedekt?",
        "uitleg": ("Verkeer, hoogte, water, elektra, gas, besloten ruimte, weer. "
                   "Staat de afzetting er zoals afgesproken?"),
        "norm_ref": "CROW 96b",
    },
    {
        "code": "LMRA.DERDEN",
        "vraag": "Is het werk van anderen om mij heen afgestemd?",
        "uitleg": ("Boven of onder je, of vlak naast je. Weten zij dat jij hier "
                   "begint, en weet jij wat zij doen?"),
        "norm_ref": "VCA",
    },
    {
        "code": "LMRA.VERGUNNING",
        "vraag": "Heb ik de werkvergunning of vrijgave die dit werk vraagt?",
        "uitleg": ("Graven, hete werkzaamheden, besloten ruimte, werken aan "
                   "installaties. Is er geen vergunning nodig, kies dan 'niet "
                   "van toepassing'. Bij twijfel: bellen voordat je begint."),
        "norm_ref": "VCA",
    },
    {
        "code": "LMRA.NOOD",
        "vraag": "Weet ik wat ik doe bij een noodgeval en hoe ik alarmeer?",
        "uitleg": ("Waar is de vluchtroute, wie bel je, en kun je uitleggen waar "
                   "je staat? Een adres weten scheelt minuten."),
        "norm_ref": "Arbobesluit",
    },
]

CODES = tuple(v["code"] for v in VRAGEN)
VRAAG_PER_CODE = {v["code"]: v for v in VRAGEN}

VEILIG = "veilig"
GESTOPT = "gestopt"


class OngeldigeLmra(ValueError):
    """De antwoorden vormen samen geen geldige LMRA."""


def checklist() -> dict:
    """De vragenlijst zoals het scherm hem toont."""
    return {
        "versie": LMRA_VERSIE,
        "antwoorden": list(ANTWOORDEN),
        "vragen": [
            {**v, "nvt_toegestaan": v["code"] not in NVT_NIET_TOEGESTAAN}
            for v in VRAGEN
        ],
    }


def _normaliseer(antwoorden: dict) -> dict[str, str]:
    uit: dict[str, str] = {}
    for code, waarde in (antwoorden or {}).items():
        code = str(code).strip().upper()
        waarde = str(waarde or "").strip().lower()
        if code not in VRAAG_PER_CODE:
            raise OngeldigeLmra(f"Onbekende vraag: {code}")
        if waarde not in ANTWOORDEN:
            raise OngeldigeLmra(
                f"Ongeldig antwoord op {code}: {waarde!r}. "
                f"Toegestaan: {', '.join(ANTWOORDEN)}")
        uit[code] = waarde
    return uit


def beoordeel(antwoorden: dict, *, toelichtingen: dict | None = None) -> dict:
    """Wat deze antwoorden betekenen.

    Geeft de uitkomst terug, welke vragen een NEE opleverden en waarom. Deze
    functie beslist; de router slaat alleen op wat hier uit komt. Zo kan de
    regel "met een nee kun je niet doorgaan" niet per ongeluk in twee versies
    bestaan.
    """
    schoon = _normaliseer(antwoorden)

    ontbreekt = [c for c in CODES if c not in schoon]
    if ontbreekt:
        raise OngeldigeLmra(
            "Nog niet alle vragen beantwoord: " + ", ".join(ontbreekt))

    onterecht_nvt = [c for c in NVT_NIET_TOEGESTAAN if schoon.get(c) == NVT]
    if onterecht_nvt:
        raise OngeldigeLmra(
            "Deze vragen kunnen niet 'niet van toepassing' zijn: "
            + ", ".join(sorted(onterecht_nvt)))

    toelichtingen = {str(k).strip().upper(): (v or "").strip()
                     for k, v in (toelichtingen or {}).items()}

    blokkades = []
    for code in CODES:
        if schoon[code] != NEE:
            continue
        toelichting = toelichtingen.get(code, "")
        if not toelichting:
            raise OngeldigeLmra(
                f"{code} is 'nee' zonder toelichting. Wat is er niet in orde?")
        blokkades.append({
            "code": code,
            "vraag": VRAAG_PER_CODE[code]["vraag"],
            "toelichting": toelichting,
        })

    return {
        "versie": LMRA_VERSIE,
        "antwoorden": schoon,
        "uitkomst": GESTOPT if blokkades else VEILIG,
        "aantal_nee": len(blokkades),
        "blokkades": blokkades,
        "mag_beginnen": not blokkades,
    }


def samenvatting(uitkomst: str, aantal_nee: int) -> str:
    """Eén regel voor in een lijst of een melding."""
    if uitkomst == VEILIG:
        return "Veilig om te beginnen"
    if aantal_nee == 1:
        return "Gestopt: één punt niet in orde"
    return f"Gestopt: {aantal_nee} punten niet in orde"
