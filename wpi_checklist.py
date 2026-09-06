"""Controlevragen voor de werkplekinspectie (WPI).

Een WPI is de rondgang die een uitvoerder of KAM-functionaris over de werkplek
maakt: loop rond, beantwoord een vaste lijst controlevragen, leg vast wat niet
in orde is en wie het oplost. VCA-gecertificeerde bedrijven moeten er een
aantoonbaar aantal per periode doen.

Opzet gelijk aan kunstwerken_taxonomy: elke vraag heeft een `code`, de
`vraag` zelf, `uitleg` voor wie twijfelt, een `norm_ref` voor de
traceerbaarheid en een `type`. De vraagtekst wordt bij het invullen
gesnapshot in het antwoord, zodat een inspectie van vorig jaar blijft tonen
wat er destijds daadwerkelijk gevraagd is, ook als deze lijst later verandert.

Alle vragen zijn `ja_nee_nvt` en positief geformuleerd -- "is X in orde?" --
met `attention_when: False`: een NEE is het aandachtspunt. Dat is bewust. Een
lijst waarin de ene vraag omgekeerd werkt dan de andere levert fouten op bij
iemand die in de regen op een bouwplaats staat af te vinken.

Over de normverwijzingen: hier staat alleen wat vaststaat. Waar het schema of
de regeling duidelijk is maar het exacte artikel niet, staat de regeling zonder
artikelnummer. Een verzonnen artikelnummer is erger dan geen verwijzing --
daar rekent een auditor je op af.
"""

from __future__ import annotations

WPI_VERSION = "wpi.v1-2026-09"

# Volgorde is de looproute: van algemene indruk naar het specifieke werk.
CATEGORIEEN: dict[str, str] = {
    "algemeen": "Algemeen en orde",
    "pbm": "Persoonlijke beschermingsmiddelen",
    "werkplek": "Werkplek en afzetting",
    "arbeidsmiddelen": "Machines en gereedschap",
    "elektrisch": "Elektrische veiligheid",
    "stoffen": "Gevaarlijke stoffen",
    "hoogte": "Werken op hoogte",
    "graafwerk": "Graafwerk",
    "omgeving": "Omgeving en derden",
}

VRAGEN: list[dict] = [
    # ── Algemeen en orde ────────────────────────────────────────────
    {
        "code": "ALG.VGPLAN",
        "categorie": "algemeen",
        "vraag": "Is het V&G-plan of de werkinstructie op de werkplek aanwezig en bekend?",
        "uitleg": "Vraag een medewerker wat er in staat. Aanwezig zijn is niet hetzelfde als bekend zijn.",
        "norm_ref": "VCA",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "ALG.TOOLBOX",
        "categorie": "algemeen",
        "vraag": "Is de laatste toolbox gehouden en getekend door de aanwezigen?",
        "uitleg": "In FieldOps terug te zien onder Veiligheid > Toolbox, inclusief presentielijst.",
        "norm_ref": "VCA",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "ALG.ORDE",
        "categorie": "algemeen",
        "vraag": "Zijn orde en netheid op de werkplek in orde?",
        "uitleg": "Looppaden vrij, geen materiaal in de weg, afval verzameld. Struikelen is de meest voorkomende oorzaak van verzuim.",
        "norm_ref": "Arbobesluit",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "ALG.EHBO",
        "categorie": "algemeen",
        "vraag": "Is er een gevulde verbandtrommel aanwezig en weet men waar die ligt?",
        "uitleg": "Controleer ook de houdbaarheidsdatum van het steriele materiaal.",
        "norm_ref": "Arbobesluit",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "ALG.ALARM",
        "categorie": "algemeen",
        "vraag": "Weet iedereen wie er bij een ongeval gebeld moet worden?",
        "uitleg": "Vraag het na bij iemand die er die dag voor het eerst is. Dat is de test.",
        "norm_ref": "VCA",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },

    # ── Persoonlijke beschermingsmiddelen ───────────────────────────
    {
        "code": "PBM.DRAGEN",
        "categorie": "pbm",
        "vraag": "Draagt iedereen de voorgeschreven PBM?",
        "uitleg": "Veiligheidsschoenen, hesje, en waar nodig helm, bril, gehoorbescherming of handschoenen.",
        "norm_ref": "Arbobesluit hoofdstuk 8",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "PBM.STAAT",
        "categorie": "pbm",
        "vraag": "Zijn de PBM in goede staat en van de juiste klasse?",
        "uitleg": "Versleten hesje, gescheurde handschoen of een helm ouder dan de gebruikstermijn van de fabrikant.",
        "norm_ref": "Arbobesluit hoofdstuk 8",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "PBM.ZICHT",
        "categorie": "pbm",
        "vraag": "Draagt iedereen langs de rijbaan zichtbaarheidskleding van de juiste klasse?",
        "uitleg": "Langs wegen met hogere snelheden is klasse 3 vereist. Bij duisternis en slecht zicht extra letten op vervuiling van het retroreflecterende materiaal.",
        "norm_ref": "CROW 96b",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },

    # ── Werkplek en afzetting ───────────────────────────────────────
    {
        "code": "WPL.AFZET",
        "categorie": "werkplek",
        "vraag": "Staat de afzetting volgens de gekozen figuur en is die compleet?",
        "uitleg": "Vergelijk met de figuur uit de werkinstructie. Let op ontbrekende of omgereden bakens.",
        "norm_ref": "CROW 96b",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "WPL.AFZET.STAAT",
        "categorie": "werkplek",
        "vraag": "Zijn de afzetmaterialen schoon, heel en goed zichtbaar?",
        "uitleg": "Vervuilde of verbleekte borden zijn 's nachts en bij regen nauwelijks te zien.",
        "norm_ref": "CROW 96b",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "WPL.VLUCHT",
        "categorie": "werkplek",
        "vraag": "Is er een veilige vluchtweg uit de werkstrook?",
        "uitleg": "Kun je weg zonder de rijbaan op te moeten? Bij een smalle strook is dit het eerste dat misgaat.",
        "norm_ref": "CROW 96b",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "WPL.VERLICHT",
        "categorie": "werkplek",
        "vraag": "Is de verlichting toereikend voor het werk en het tijdstip?",
        "uitleg": "Ook overdag relevant in tunnels, kelders en putten.",
        "norm_ref": "Arbobesluit",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "WPL.SLEUF",
        "categorie": "werkplek",
        "vraag": "Zijn open sleuven, putten en gaten afgedekt of afgezet?",
        "uitleg": "Ook buiten werktijd. Een open put zonder afzetting is het klassieke incident met derden.",
        "norm_ref": "Arbobesluit",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },

    # ── Machines en gereedschap ─────────────────────────────────────
    {
        "code": "ARB.KEURING",
        "categorie": "arbeidsmiddelen",
        "vraag": "Zijn machines en hijsmiddelen aantoonbaar gekeurd en is de keuring geldig?",
        "uitleg": "Controleer het keuringssticker of -certificaat, niet alleen of er iets op zit geplakt.",
        "norm_ref": "Arbobesluit hoofdstuk 7",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "ARB.AFSCHERM",
        "categorie": "arbeidsmiddelen",
        "vraag": "Zijn alle afschermingen en beveiligingen aanwezig en werkend?",
        "uitleg": "Weggehaalde beschermkappen op slijptollen en cirkelzagen zijn een terugkerend beeld.",
        "norm_ref": "Arbobesluit hoofdstuk 7",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "ARB.BEDIENING",
        "categorie": "arbeidsmiddelen",
        "vraag": "Is de bediener aantoonbaar bevoegd voor deze machine?",
        "uitleg": "Machinistenpas, heftruckcertificaat of vergelijkbaar. Vraag ernaar in plaats van het aan te nemen.",
        "norm_ref": "Arbobesluit hoofdstuk 7",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "ARB.ZICHT",
        "categorie": "arbeidsmiddelen",
        "vraag": "Is er goed zicht en oogcontact tussen machinist en grondpersoneel?",
        "uitleg": "Spiegels en camera's schoon, en afspraken over wie waar mag lopen.",
        "norm_ref": "Arbobesluit hoofdstuk 7",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },

    # ── Elektrische veiligheid ──────────────────────────────────────
    {
        "code": "ELE.KEURING",
        "categorie": "elektrisch",
        "vraag": "Zijn elektrische arbeidsmiddelen en verdeelinrichtingen periodiek gekeurd?",
        "uitleg": "Handgereedschap, haspels en bouwkasten. Let op de keuringsdatum, niet alleen op het sticker.",
        "norm_ref": "NEN 3140",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "ELE.SNOER",
        "categorie": "elektrisch",
        "vraag": "Zijn snoeren, stekkers en haspels onbeschadigd en droog opgesteld?",
        "uitleg": "Haspels helemaal afrollen bij belasting, anders raken ze oververhit.",
        "norm_ref": "NEN 3140",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "ELE.AARDLEK",
        "categorie": "elektrisch",
        "vraag": "Is er een werkende aardlekschakelaar op de bouwstroomvoorziening?",
        "uitleg": "Test hem ter plekke met de testknop; aanwezig zijn is niet hetzelfde als werken.",
        "norm_ref": "NEN 3140",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },

    # ── Gevaarlijke stoffen ─────────────────────────────────────────
    {
        "code": "STF.VIB",
        "categorie": "stoffen",
        "vraag": "Zijn de veiligheidsinformatiebladen van de gebruikte stoffen beschikbaar?",
        "uitleg": "Digitaal mag ook, mits ter plaatse te openen zonder bereik-problemen.",
        "norm_ref": "Arbobesluit",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "STF.OPSLAG",
        "categorie": "stoffen",
        "vraag": "Worden gevaarlijke stoffen juist opgeslagen en geëtiketteerd?",
        "uitleg": "Originele verpakking met etiket, lekbak waar nodig, en niet naast voedsel of drinken.",
        "norm_ref": "Arbobesluit",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "STF.BODEM",
        "categorie": "stoffen",
        "vraag": "Is bij werken in mogelijk verontreinigde bodem de juiste veiligheidsklasse bepaald?",
        "uitleg": "Vooraf vastgesteld op basis van het bodemonderzoek, met de bijbehorende maatregelen.",
        "norm_ref": "CROW 400",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },

    # ── Werken op hoogte ────────────────────────────────────────────
    {
        "code": "HGT.VALGEVAAR",
        "categorie": "hoogte",
        "vraag": "Is bij valgevaar een collectieve voorziening aanwezig, of anders valbeveiliging?",
        "uitleg": "Eerst leuning, hekwerk of net. Een harnas is de laatste stap, niet de eerste.",
        "norm_ref": "Arbobesluit art. 3.16",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "HGT.STEIGER",
        "categorie": "hoogte",
        "vraag": "Is de steiger voorzien van een geldige steigerkaart en compleet opgebouwd?",
        "uitleg": "Leuningen, kantplanken, complete vloeren en een deugdelijke opstap.",
        "norm_ref": "Arbobesluit hoofdstuk 7",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "HGT.LADDER",
        "categorie": "hoogte",
        "vraag": "Worden ladders en trappen alleen als toegang gebruikt en staan ze stabiel?",
        "uitleg": "Werken vanaf een ladder is alleen toegestaan bij kortdurend licht werk.",
        "norm_ref": "Arbobesluit",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },

    # ── Graafwerk ───────────────────────────────────────────────────
    {
        "code": "GRF.KLIC",
        "categorie": "graafwerk",
        "vraag": "Is er een geldige KLIC-melding en liggen de tekeningen op de werkplek?",
        "uitleg": "Een graafmelding is beperkt geldig; controleer de datum. Graven zonder is een overtreding.",
        "norm_ref": "WION",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "GRF.PROEFSLEUF",
        "categorie": "graafwerk",
        "vraag": "Is de ligging van kabels en leidingen gelokaliseerd voordat er machinaal is gegraven?",
        "uitleg": "Proefsleuven of detectie. De meeste graafschade ontstaat doordat deze stap is overgeslagen.",
        "norm_ref": "WION",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "GRF.TALUD",
        "categorie": "graafwerk",
        "vraag": "Is de ontgraving voldoende afgeschuind of gestempeld tegen instorten?",
        "uitleg": "Let op de grondsoort en op regen; een sleuf die gisteren hield kan vandaag inkalven.",
        "norm_ref": "Arbobesluit",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },

    # ── Omgeving en derden ──────────────────────────────────────────
    {
        "code": "OMG.DERDEN",
        "categorie": "omgeving",
        "vraag": "Zijn voetgangers en fietsers veilig langs of om het werk geleid?",
        "uitleg": "Let op rolstoel- en kinderwagenbreedte en op een sluitende geleiding, niet alleen een bord.",
        "norm_ref": "CROW 96b",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "OMG.BEREIKBAAR",
        "categorie": "omgeving",
        "vraag": "Blijven inritten en nooddiensten-routes bereikbaar?",
        "uitleg": "Vraag na of hulpdiensten en aanwonenden geïnformeerd zijn bij een langdurige afsluiting.",
        "norm_ref": "CROW 96b",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
    {
        "code": "OMG.MILIEU",
        "categorie": "omgeving",
        "vraag": "Worden bodem, water en omgeving beschermd tegen morsen en verspreiding?",
        "uitleg": "Lekbakken onder aggregaten, geen spoelwater in het straatkolk, stofbeperking bij zagen.",
        "norm_ref": "Arbobesluit",
        "type": "ja_nee_nvt",
        "attention_when": False,
    },
]


def vragen_voor(categorie: str | None = None) -> list[dict]:
    """Alle vragen, of die van één categorie, in vaste volgorde."""
    if categorie is None:
        return list(VRAGEN)
    return [v for v in VRAGEN if v["categorie"] == categorie]


def vraag(code: str) -> dict | None:
    for v in VRAGEN:
        if v["code"] == code:
            return v
    return None


def bereken_score(antwoorden: list[dict]) -> dict:
    """Percentage in orde, over de vragen die daadwerkelijk beoordeeld zijn.

    N.v.t. telt niet mee -- anders zakt de score van een klein werk waar de
    helft niet van toepassing is, en dan gaan mensen n.v.t. vermijden en
    lukraak 'in orde' invullen. Dat is precies het gedrag dat je niet wilt.

    Onbeantwoorde vragen tellen ook niet mee; een halve inspectie hoort geen
    score te krijgen alsof hij af is. `beantwoord` en `totaal` maken zichtbaar
    hoe compleet de rondgang was.
    """
    in_orde = sum(1 for a in antwoorden if a.get("antwoord") == "ja")
    niet_in_orde = sum(1 for a in antwoorden if a.get("antwoord") == "nee")
    nvt = sum(1 for a in antwoorden if a.get("antwoord") == "nvt")
    beoordeeld = in_orde + niet_in_orde

    return {
        "in_orde": in_orde,
        "niet_in_orde": niet_in_orde,
        "nvt": nvt,
        "beoordeeld": beoordeeld,
        "beantwoord": beoordeeld + nvt,
        "totaal": len(VRAGEN),
        "score_pct": round(100 * in_orde / beoordeeld) if beoordeeld else None,
    }
