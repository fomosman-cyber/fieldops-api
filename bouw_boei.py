"""BOEI — inspectie en onderhoud van gebouwen, op de methodiek van de overheid.

Waar `kunstwerken_taxonomy` over infrastructuur gaat, gaat dit bestand over
**gebouwen**: kantoren, scholen, gemeentehuizen, zorggebouwen, werkplaatsen,
sporthallen en woongebouwen.

BOEI is de conditie- en risico-inspectie die het Rijksvastgoedbedrijf hanteert
en die gemeenten, provincies, woningcorporaties en zorginstellingen hebben
overgenomen. Vier pijlers, in één rondgang opgenomen:

    B  Brandveiligheid      installaties, compartimentering, vluchtwegen
    O  Onderhoud            conditiemeting volgens NEN 2767-1
    E  Energie              energieprestatie en verbruik
    I  Inzicht in wet-      keuringen, certificaten en meldingen die een
       en regelgeving       gebouweigenaar aantoonbaar moet hebben

De O-pijler rekent met dezelfde motor als de infrastructuur-kant: NEN 2767 is
één methodiek voor gebouwen (-1), installaties (-1) en infrastructuur (-4).
Zie `nen2767_scoring.py` — daar hoeft niets voor gedupliceerd te worden. Wat
hier staat is de **elementenstructuur en de vragenlijst**, niet de rekenkern.

Opzet gelijk aan `wpi_checklist.py`: elke vraag heeft een `code`, de `vraag`
zelf, `uitleg` voor wie twijfelt, een `norm_ref` voor de traceerbaarheid en een
`type`. De vraagtekst wordt bij het invullen gesnapshot in het antwoord, zodat
een inspectie van vorig jaar blijft tonen wat er destijds gevraagd is.

Alle ja/nee-vragen zijn positief geformuleerd — "is X in orde?" — met
`attention_when: False`. Een NEE is dus het aandachtspunt. Dat is bewust: een
lijst waarin de ene vraag omgekeerd werkt dan de andere levert fouten op bij
iemand die met een tablet door een ketelhuis loopt.

Over de normverwijzingen geldt dezelfde regel als bij de WPI: hier staat alleen
wat vaststaat. Waar de norm of regeling duidelijk is maar het exacte artikel
niet, staat de regeling zonder artikelnummer. Een verzonnen artikelnummer is
erger dan geen verwijzing — daar rekent een auditor je op af.

Let op de wetswijziging: het Bouwbesluit 2012 is per 1 januari 2024 opgegaan in
het **Besluit bouwwerken leefomgeving (Bbl)** onder de Omgevingswet. Voor
bestaande bouw gelden daar eigen grenswaarden. Verwijzingen hieronder noemen
daarom het Bbl en niet het Bouwbesluit.
"""

from __future__ import annotations

BOEI_VERSION = "boei.v1-2026-09"


# ---------------------------------------------------------------------------
# De vier pijlers
# ---------------------------------------------------------------------------

PIJLERS: dict[str, dict] = {
    "B": {
        "naam": "Brandveiligheid",
        "uitleg": "Installaties, compartimentering en vluchtwegen. De pijler "
                  "waar een gebouweigenaar persoonlijk aansprakelijk op is.",
    },
    "O": {
        "naam": "Onderhoud",
        "uitleg": "Conditiemeting volgens NEN 2767-1. Levert de conditiescore "
                  "per element en daarmee de basis voor het MJOP.",
    },
    "E": {
        "naam": "Energie",
        "uitleg": "Energieprestatie, label en verbruik. Sinds 2023 geldt voor "
                  "kantoren boven 100 m2 een minimumlabel.",
    },
    "I": {
        "naam": "Inzicht wet- en regelgeving",
        "uitleg": "De keuringen, certificaten en meldingen die aantoonbaar "
                  "moeten bestaan. Meestal de pijler met de meeste gaten.",
    },
}


# ---------------------------------------------------------------------------
# Gebouwtypen — bepaalt welke vragen van toepassing zijn
# ---------------------------------------------------------------------------

GEBOUW_TYPES: dict[str, str] = {
    "kantoor": "Kantoorgebouw",
    "school": "Onderwijsgebouw",
    "zorg": "Zorggebouw",
    "woongebouw": "Woongebouw / appartementencomplex",
    "sport": "Sportaccommodatie",
    "werkplaats": "Werkplaats, loods of gemeentewerf",
    "publiek": "Publiek gebouw (gemeentehuis, bibliotheek, museum)",
    "parkeergarage": "Parkeergarage",
    "monument": "Monument of beschermd stadsgezicht",
}

# Waar een vraag alleen voor bepaalde gebouwtypen geldt staat dat op de vraag
# zelf in `alleen_voor`. Ontbreekt dat veld, dan geldt de vraag altijd.


# ---------------------------------------------------------------------------
# Elementenstructuur (O-pijler)
# ---------------------------------------------------------------------------
# Gegroepeerd zoals NEN 2767-1 en de NL/SfB-elementenmethode dat doen: eerst de
# bouwkundige schil van onder naar boven, dan de installaties, dan het terrein.
# Dit is de lijst waarop je een conditiescore vastlegt.

ELEMENTGROEPEN: dict[str, str] = {
    "fundering":        "Fundering en onderbouw",
    "draagconstructie": "Draagconstructie",
    "gevel":            "Gevel en buitenwandopeningen",
    "dak":              "Daken en dakopbouwen",
    "binnen":           "Binnenafbouw en afwerkingen",
    "installatie_w":    "Werktuigbouwkundige installaties",
    "installatie_e":    "Elektrotechnische installaties",
    "transport":        "Transportinstallaties",
    "terrein":          "Terrein en buitenruimte",
}

ELEMENTEN: list[dict] = [
    # ── Fundering en onderbouw ──────────────────────────────────────────
    {"code": "FUN.01", "groep": "fundering", "naam": "Fundering op staal of palen",
     "gebreken": ["scheurvorming", "verzakking", "aantasting beton", "paalrot"]},
    {"code": "FUN.02", "groep": "fundering", "naam": "Kruipruimte",
     "gebreken": ["vocht", "schimmel", "onvoldoende ventilatie", "wateroverlast"]},
    {"code": "FUN.03", "groep": "fundering", "naam": "Kelderwand en keldervloer",
     "gebreken": ["lekkage", "optrekkend vocht", "scheurvorming"]},

    # ── Draagconstructie ────────────────────────────────────────────────
    {"code": "DRA.01", "groep": "draagconstructie", "naam": "Dragende wanden",
     "gebreken": ["scheurvorming", "doorbuiging", "aantasting"]},
    {"code": "DRA.02", "groep": "draagconstructie", "naam": "Kolommen en liggers",
     "gebreken": ["corrosie", "betonrot", "houtrot", "vervorming"]},
    {"code": "DRA.03", "groep": "draagconstructie", "naam": "Vloerconstructie",
     "gebreken": ["doorbuiging", "scheurvorming", "trillingshinder"]},
    {"code": "DRA.04", "groep": "draagconstructie", "naam": "Trappen en hellingbanen",
     "gebreken": ["slijtage", "loszittende treden", "corrosie"]},

    # ── Gevel ───────────────────────────────────────────────────────────
    {"code": "GEV.01", "groep": "gevel", "naam": "Metselwerk buitengevel",
     "gebreken": ["voegwerk uitgesleten", "scheurvorming", "uitbloeiing",
                  "verwering", "spouwankerroest"]},
    {"code": "GEV.02", "groep": "gevel", "naam": "Beton- en natuursteengevel",
     "gebreken": ["betonrot", "wapening zichtbaar", "vervuiling", "scheurvorming"]},
    {"code": "GEV.03", "groep": "gevel", "naam": "Gevelbeplating en gordijngevel",
     "gebreken": ["bevestiging los", "kitvoegen verhard", "corrosie", "lekkage"]},
    {"code": "GEV.04", "groep": "gevel", "naam": "Buitenkozijnen, ramen en deuren",
     "gebreken": ["houtrot", "verflaag verweerd", "beslag defect",
                  "beglazing lek", "tocht"]},
    {"code": "GEV.05", "groep": "gevel", "naam": "Beglazing en zonwering",
     "gebreken": ["lekkende beglazing", "condens tussen ruiten",
                  "zonwering defect"]},
    {"code": "GEV.06", "groep": "gevel", "naam": "Gevelisolatie en spouw",
     "gebreken": ["isolatie ingezakt", "koudebrug", "vochtdoorslag"]},

    # ── Daken ───────────────────────────────────────────────────────────
    {"code": "DAK.01", "groep": "dak", "naam": "Platdakbedekking",
     "gebreken": ["blaasvorming", "scheuren", "naden los", "plasvorming",
                  "lekkage"]},
    {"code": "DAK.02", "groep": "dak", "naam": "Hellend dak en pannen",
     "gebreken": ["gebroken pannen", "verschoven pannen", "mosaanwas",
                  "panlatten verrot"]},
    {"code": "DAK.03", "groep": "dak", "naam": "Dakranden en daktrimmen",
     "gebreken": ["loszittend", "corrosie", "kitwerk verhard"]},
    {"code": "DAK.04", "groep": "dak", "naam": "Hemelwaterafvoer en goten",
     "gebreken": ["verstopping", "lekkage", "onvoldoende afschot", "corrosie"]},
    {"code": "DAK.05", "groep": "dak", "naam": "Dakdoorvoeren en lichtkoepels",
     "gebreken": ["lekkage", "vergeeld", "kitwerk verhard", "niet doorvalveilig"]},
    {"code": "DAK.06", "groep": "dak", "naam": "Dakveiligheidsvoorzieningen",
     "gebreken": ["ankerpunt ontbreekt", "keuring verlopen", "corrosie"]},

    # ── Binnen ──────────────────────────────────────────────────────────
    {"code": "BIN.01", "groep": "binnen", "naam": "Binnenwanden en scheidingen",
     "gebreken": ["scheurvorming", "beschadiging", "doorvoeren niet gedicht"]},
    {"code": "BIN.02", "groep": "binnen", "naam": "Binnenkozijnen en -deuren",
     "gebreken": ["klemmen", "beslag defect", "beschadiging",
                  "zelfsluiting defect"]},
    {"code": "BIN.03", "groep": "binnen", "naam": "Plafonds",
     "gebreken": ["vochtvlekken", "platen los", "verkleuring"]},
    {"code": "BIN.04", "groep": "binnen", "naam": "Vloerafwerking",
     "gebreken": ["slijtage", "loslatende delen", "struikelgevaar"]},
    {"code": "BIN.05", "groep": "binnen", "naam": "Wandafwerking en schilderwerk",
     "gebreken": ["afbladderen", "vochtvlekken", "beschadiging"]},
    {"code": "BIN.06", "groep": "binnen", "naam": "Sanitair",
     "gebreken": ["lekkage", "kalkaanslag", "kitwerk verouderd", "defect"]},

    # ── Werktuigbouwkundig ──────────────────────────────────────────────
    {"code": "WTB.01", "groep": "installatie_w", "naam": "Warmteopwekking (ketel, WP)",
     "gebreken": ["rendementsverlies", "lekkage", "corrosie", "onderhoud verlopen"]},
    {"code": "WTB.02", "groep": "installatie_w", "naam": "Warmteafgifte en leidingwerk",
     "gebreken": ["lekkage", "isolatie ontbreekt", "corrosie", "ontluchting nodig"]},
    {"code": "WTB.03", "groep": "installatie_w", "naam": "Luchtbehandeling en ventilatie",
     "gebreken": ["filters vervuild", "onvoldoende debiet", "geluidhinder",
                  "kanalen vervuild"]},
    {"code": "WTB.04", "groep": "installatie_w", "naam": "Koeling en klimaatinstallatie",
     "gebreken": ["koudemiddellek", "condensafvoer verstopt", "keuring verlopen"]},
    {"code": "WTB.05", "groep": "installatie_w", "naam": "Drinkwaterinstallatie",
     "gebreken": ["dode leiding", "temperatuurafwijking", "lekkage",
                  "terugstroombeveiliging ontbreekt"]},
    {"code": "WTB.06", "groep": "installatie_w", "naam": "Riolering binnen",
     "gebreken": ["verstopping", "stankoverlast", "lekkage", "ontluchting defect"]},
    {"code": "WTB.07", "groep": "installatie_w", "naam": "Gasinstallatie",
     "gebreken": ["lekkage", "corrosie", "ventilatie onvoldoende"]},

    # ── Elektrotechnisch ────────────────────────────────────────────────
    {"code": "ETB.01", "groep": "installatie_e", "naam": "Hoofd- en groepenverdeling",
     "gebreken": ["overbelasting", "warmteontwikkeling", "afscherming ontbreekt",
                  "keuring verlopen"]},
    {"code": "ETB.02", "groep": "installatie_e", "naam": "Verlichting binnen",
     "gebreken": ["armaturen defect", "onvoldoende lichtniveau", "vervuiling"]},
    {"code": "ETB.03", "groep": "installatie_e", "naam": "Noodverlichting en vluchtwegaanduiding",
     "gebreken": ["armatuur defect", "accu leeg", "keuring verlopen",
                  "aanduiding niet zichtbaar"]},
    {"code": "ETB.04", "groep": "installatie_e", "naam": "Brandmeldinstallatie",
     "gebreken": ["melder vervuild", "storingsmelding", "certificaat verlopen",
                  "doormelding defect"]},
    {"code": "ETB.05", "groep": "installatie_e", "naam": "Ontruimingsalarminstallatie",
     "gebreken": ["luidsprekers defect", "onvoldoende geluidsniveau",
                  "keuring verlopen"]},
    {"code": "ETB.06", "groep": "installatie_e", "naam": "Bliksem- en overspanningsbeveiliging",
     "gebreken": ["aardingsweerstand te hoog", "corrosie", "keuring verlopen"]},
    {"code": "ETB.07", "groep": "installatie_e", "naam": "Data- en communicatie-infrastructuur",
     "gebreken": ["beschadiging", "verouderd", "onvoldoende capaciteit"]},

    # ── Transport ───────────────────────────────────────────────────────
    {"code": "TRA.01", "groep": "transport", "naam": "Personen- en goederenlift",
     "gebreken": ["keuring verlopen", "storingen", "slijtage kabels",
                  "noodoproep defect"]},
    {"code": "TRA.02", "groep": "transport", "naam": "Roltrap of rolpad",
     "gebreken": ["keuring verlopen", "slijtage", "noodstop defect"]},

    # ── Terrein ─────────────────────────────────────────────────────────
    {"code": "TER.01", "groep": "terrein", "naam": "Terreinverharding",
     "gebreken": ["verzakking", "scheurvorming", "onkruid", "struikelgevaar"]},
    {"code": "TER.02", "groep": "terrein", "naam": "Terreinriolering en kolken",
     "gebreken": ["verstopping", "verzakking", "wateroverlast"]},
    {"code": "TER.03", "groep": "terrein", "naam": "Terreinverlichting",
     "gebreken": ["armatuur defect", "mast corrosie", "onvoldoende verlichting"]},
    {"code": "TER.04", "groep": "terrein", "naam": "Erfafscheiding en hekwerk",
     "gebreken": ["corrosie", "beschadiging", "poort defect"]},
    {"code": "TER.05", "groep": "terrein", "naam": "Groenvoorziening op eigen terrein",
     "gebreken": ["achterstallig snoeien", "wortelopdruk", "dood hout"]},
]


# ---------------------------------------------------------------------------
# De vragenlijst
# ---------------------------------------------------------------------------
# `type` is "ja_nee_nvt" tenzij anders vermeld. `attention_when: False`
# betekent: een NEE is het aandachtspunt.
# `bewijs` geeft aan dat er een document of certificaat bij hoort — dat is bij
# de I-pijler het halve werk.

VRAGEN: list[dict] = [
    # ══ B — Brandveiligheid ═════════════════════════════════════════════
    {"code": "B.01", "pijler": "B", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Zijn alle vluchtwegen vrij van obstakels en over de volle breedte begaanbaar?",
     "uitleg": "Loop de route die iemand bij ontruiming aflegt, niet alleen de deur. "
               "Opgeslagen materiaal in een gang is de meest voorkomende bevinding.",
     "norm_ref": "Bbl — bruikbaarheid vluchtroutes"},
    {"code": "B.02", "pijler": "B", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Zijn vluchtdeuren zonder sleutel of hulpmiddel te openen in vluchtrichting?",
     "uitleg": "Test het. Een deur met een cilinder aan de binnenzijde is een bevinding, "
               "ook als de sleutel in een kastje naast de deur hangt.",
     "norm_ref": "Bbl — vluchtroutes"},
    {"code": "B.03", "pijler": "B", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is de brandcompartimentering intact, inclusief doorvoeren van kabels en leidingen?",
     "uitleg": "Kijk boven het verlaagd plafond. Een niet-gedichte doorvoer maakt de "
               "hele scheiding waardeloos en is bij verbouwingen het eerste dat sneuvelt.",
     "norm_ref": "NEN 6068 — bepaling WBDBO"},
    {"code": "B.04", "pijler": "B", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Sluiten zelfsluitende branddeuren volledig en grijpt het slot aan?",
     "uitleg": "Laat de deur uit elke stand los. Een deur die op een wig staat telt als niet-sluitend.",
     "norm_ref": "Bbl — zelfsluitende constructieonderdelen"},
    {"code": "B.05", "pijler": "B", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is de brandmeldinstallatie storingsvrij en het onderhoudscertificaat geldig?",
     "bewijs": "Onderhoudscertificaat brandmeldinstallatie",
     "uitleg": "Lees het paneel af én controleer de datum op het certificaat. "
               "Een storingsmelding die 'al maanden staat' is een bevinding.",
     "norm_ref": "NEN 2535 — brandmeldinstallaties"},
    {"code": "B.06", "pijler": "B", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is de ontruimingsalarminstallatie beproefd en het certificaat geldig?",
     "bewijs": "Onderhoudscertificaat ontruimingsalarminstallatie",
     "norm_ref": "NEN 2575 — ontruimingsalarminstallaties"},
    {"code": "B.07", "pijler": "B", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Werkt de noodverlichting en is de autonomietijd aantoonbaar getest?",
     "bewijs": "Testrapport noodverlichting",
     "uitleg": "Een brandende armatuur zegt niets over de accu. Vraag naar het testrapport.",
     "norm_ref": "NEN-EN 1838 en NEN-EN 50172 — noodverlichting"},
    {"code": "B.08", "pijler": "B", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Zijn blusmiddelen aanwezig, bereikbaar en binnen de keurtermijn?",
     "bewijs": "Keuringssticker of onderhoudsrapport blusmiddelen",
     "norm_ref": "NEN 2559 en NEN-EN 671 — draagbare blustoestellen en brandslanghaspels"},
    {"code": "B.09", "pijler": "B", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is de vluchtwegaanduiding vanaf elke plek in de ruimte zichtbaar?",
     "uitleg": "Ga staan waar iemand werkt, niet in de deuropening.",
     "norm_ref": "NEN 3011 — veiligheidskleuren en -tekens"},
    {"code": "B.10", "pijler": "B", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is er een actueel ontruimingsplan en zijn de plattegronden bij de uitgangen actueel?",
     "bewijs": "Ontruimingsplan",
     "uitleg": "Actueel betekent: komt overeen met de huidige indeling. Een plan van "
               "voor de laatste verbouwing is een bevinding.",
     "norm_ref": "NEN 8112 — ontruimingsplan"},
    {"code": "B.11", "pijler": "B", "type": "ja_nee_nvt", "attention_when": False,
     "alleen_voor": ["parkeergarage", "publiek", "zorg", "woongebouw"],
     "vraag": "Is de rook- en warmteafvoerinstallatie beproefd?",
     "bewijs": "Beproevingsrapport RWA",
     "norm_ref": "NEN 6093 — rook- en warmteafvoerinstallaties"},

    # ══ O — Onderhoud ═══════════════════════════════════════════════════
    {"code": "O.01", "pijler": "O", "type": "conditiescore",
     "vraag": "Conditiescore per bouwkundig element vastleggen",
     "uitleg": "Per element ernst, intensiteit en omvang vastleggen. De score volgt "
               "daaruit; niet zelf een cijfer kiezen.",
     "norm_ref": "NEN 2767-1 — conditiemeting gebouwen en installaties"},
    {"code": "O.02", "pijler": "O", "type": "conditiescore",
     "vraag": "Conditiescore per installatie-element vastleggen",
     "uitleg": "Installaties vallen onder dezelfde methodiek als de bouwkundige "
               "elementen, met een eigen gebrekenlijst.",
     "norm_ref": "NEN 2767-1 — conditiemeting gebouwen en installaties"},
    {"code": "O.03", "pijler": "O", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is het dak vrij van plasvorming en zijn de hemelwaterafvoeren doorstromend?",
     "uitleg": "Plasvorming die na twee dagen nog staat is een gebrek, ook zonder lekkage. "
               "Het is de voorbode.",
     "norm_ref": "NEN 2767-1"},
    {"code": "O.04", "pijler": "O", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is het buitenschilderwerk intact op de houten delen?",
     "uitleg": "Let op onderdorpels en aansluitingen; daar begint houtrot.",
     "norm_ref": "NEN 2767-1"},
    {"code": "O.05", "pijler": "O", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is het voegwerk van het metselwerk intact?",
     "norm_ref": "NEN 2767-1"},
    {"code": "O.06", "pijler": "O", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Zijn er sinds de vorige inspectie nieuwe scheuren in gevel of constructie?",
     "uitleg": "Nieuw of groeiend is de vraag, niet of er scheuren zijn. Fotografeer met "
               "een maatlat, dan is de volgende ronde vergelijkbaar.",
     "norm_ref": "NEN 2767-1"},
    {"code": "O.07", "pijler": "O", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is het onderhoud aan de klimaatinstallatie uitgevoerd volgens contract?",
     "bewijs": "Onderhoudsrapportage installateur",
     "norm_ref": "NEN 2767-1"},
    {"code": "O.08", "pijler": "O", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is er een actueel meerjarenonderhoudsplan dat aansluit op deze conditiemeting?",
     "bewijs": "MJOP",
     "uitleg": "Een MJOP ouder dan de laatste inspectie is een plan op verouderde gegevens.",
     "norm_ref": "NEN 2767-1 in samenhang met NEN 2699 — exploitatiekosten"},

    # ══ E — Energie ═════════════════════════════════════════════════════
    {"code": "E.01", "pijler": "E", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is er een geldig energielabel voor het gebouw?",
     "bewijs": "Energielabel",
     "uitleg": "Een label is tien jaar geldig. Controleer de datum, niet alleen het bestaan.",
     "norm_ref": "NTA 8800 — bepalingsmethode energieprestatie"},
    {"code": "E.02", "pijler": "E", "type": "ja_nee_nvt", "attention_when": False,
     "alleen_voor": ["kantoor"],
     "vraag": "Voldoet dit kantoor aan de verplichting van minimaal label C?",
     "uitleg": "Geldt sinds 1 januari 2023 voor kantoren vanaf 100 m2, met uitzonderingen "
               "voor monumenten en gebouwen die grotendeels een andere functie hebben.",
     "norm_ref": "Bbl — energielabelplicht kantoren"},
    {"code": "E.03", "pijler": "E", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Zijn de erkende energiebesparende maatregelen met terugverdientijd tot vijf jaar uitgevoerd?",
     "bewijs": "Informatieplicht energiebesparing (eLoket RVO)",
     "uitleg": "Geldt voor locaties vanaf 50.000 kWh of 25.000 m3 gas per jaar. "
               "De informatieplicht is een aparte verplichting naast het uitvoeren zelf.",
     "norm_ref": "Besluit activiteiten leefomgeving — energiebesparingsplicht"},
    {"code": "E.04", "pijler": "E", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is de gebouwschil vrij van zichtbare koudebruggen en isolatiegebreken?",
     "uitleg": "Zichtbaar aan vochtplekken, schimmel in hoeken en afwijkende sneeuwsmelt op het dak.",
     "norm_ref": "NTA 8800"},
    {"code": "E.05", "pijler": "E", "type": "getal",
     "vraag": "Gasverbruik afgelopen twaalf maanden (m3)",
     "uitleg": "Zonder verbruikscijfer is de E-pijler een mening. Vraag de meterstanden op.",
     "norm_ref": "NTA 8800"},
    {"code": "E.06", "pijler": "E", "type": "getal",
     "vraag": "Elektriciteitsverbruik afgelopen twaalf maanden (kWh)",
     "norm_ref": "NTA 8800"},
    {"code": "E.07", "pijler": "E", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is de installatie ingeregeld en zijn de stooklijnen actueel?",
     "uitleg": "De goedkoopste besparing die er is, en vrijwel nooit gedaan na een verbouwing.",
     "norm_ref": "NTA 8800"},

    # ══ I — Inzicht wet- en regelgeving ═════════════════════════════════
    {"code": "I.01", "pijler": "I", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is de elektrische installatie periodiek geïnspecteerd en het rapport beschikbaar?",
     "bewijs": "Inspectierapport elektrische installatie",
     "uitleg": "NEN 3140 gaat over de bedrijfsvoering en periodieke inspectie; NEN 1010 "
               "over de aanleg. Voor een bestaand gebouw is 3140 de relevante.",
     "norm_ref": "NEN 3140 — bedrijfsvoering van elektrische installaties"},
    {"code": "I.02", "pijler": "I", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Zijn de elektrische arbeidsmiddelen gekeurd en voorzien van een geldige sticker?",
     "bewijs": "Keuringsrapport arbeidsmiddelen",
     "norm_ref": "NEN 3140 in samenhang met het Arbobesluit — keuring arbeidsmiddelen"},
    {"code": "I.03", "pijler": "I", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is er een actuele legionella-risicoanalyse en beheersplan voor de drinkwaterinstallatie?",
     "bewijs": "Risicoanalyse en beheersplan legionella",
     "uitleg": "Verplicht voor prioritaire instellingen: zorg, onderwijs met douches, "
               "sport, hotels en publieke gebouwen met douchevoorzieningen.",
     "norm_ref": "Drinkwaterbesluit en ISSO 55.1 — legionellapreventie"},
    {"code": "I.04", "pijler": "I", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Zijn de temperatuurmetingen en spoelrondes van de drinkwaterinstallatie bijgehouden?",
     "bewijs": "Logboek legionellabeheer",
     "norm_ref": "Drinkwaterbesluit en ISSO 55.1"},
    {"code": "I.05", "pijler": "I", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is de liftinstallatie gekeurd en het keuringscertificaat geldig?",
     "bewijs": "Keuringscertificaat lift",
     "uitleg": "Een lift wordt periodiek gekeurd door een aangewezen keuringsinstantie. "
               "Controleer de datum in de liftkooi.",
     "norm_ref": "Warenwetbesluit liften"},
    {"code": "I.06", "pijler": "I", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is er een asbestinventarisatie voor dit gebouw als het van vóór 1994 is?",
     "bewijs": "Asbestinventarisatierapport",
     "uitleg": "Bouwjaar vóór 1994 betekent verdacht. Zonder inventarisatie mag er niet "
               "gesloopt of geboord worden en ligt het werk stil bij de eerste vondst.",
     "norm_ref": "Arbobesluit en certificatieschema asbestinventarisatie"},
    {"code": "I.07", "pijler": "I", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Zijn de valbeveiligingsvoorzieningen op het dak jaarlijks gekeurd?",
     "bewijs": "Keuringsrapport valbeveiliging",
     "uitleg": "Ankerpunten en lijnsystemen zijn persoonlijke beschermingsmiddelen en "
               "vallen daarmee onder de jaarlijkse keurplicht.",
     "norm_ref": "NEN-EN 795 en het Arbobesluit — persoonlijke valbeveiliging"},
    {"code": "I.08", "pijler": "I", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is de gebruiksmelding of omgevingsvergunning brandveilig gebruik aanwezig en actueel?",
     "bewijs": "Gebruiksmelding of vergunning",
     "uitleg": "Actueel betekent: past bij het huidige aantal personen en de huidige indeling.",
     "norm_ref": "Bbl — gebruiksmelding brandveilig gebruik"},
    {"code": "I.09", "pijler": "I", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Is er een actueel logboek waarin keuringen, storingen en wijzigingen worden bijgehouden?",
     "bewijs": "Gebouwlogboek",
     "uitleg": "Bij een controle is het logboek het eerste dat gevraagd wordt. Geen logboek "
               "betekent in de praktijk: niets aantoonbaar.",
     "norm_ref": "Bbl — logboekverplichting brandveiligheidsinstallaties"},
    {"code": "I.10", "pijler": "I", "type": "ja_nee_nvt", "attention_when": False,
     "alleen_voor": ["monument"],
     "vraag": "Zijn de vergunningplichtige werkzaamheden aan het monument afgestemd met de bevoegde instantie?",
     "bewijs": "Omgevingsvergunning monument",
     "norm_ref": "Erfgoedwet en Omgevingswet"},
    {"code": "I.11", "pijler": "I", "type": "ja_nee_nvt", "attention_when": False,
     "vraag": "Zijn er verplichte keuringen die binnen zes maanden verlopen?",
     "uitleg": "Vooruitkijken hoort bij de inspectie. Een certificaat dat over drie maanden "
               "verloopt is nu te plannen en straks een spoedklus.",
     "norm_ref": "BOEI-methodiek — pijler I"},
]


# ---------------------------------------------------------------------------
# Opzoekfuncties
# ---------------------------------------------------------------------------

def pijlers() -> list[dict]:
    """De vier pijlers, in vaste volgorde."""
    return [{"code": code, **waarden} for code, waarden in PIJLERS.items()]


def vragen_voor(pijler: str | None = None,
                gebouw_type: str | None = None) -> list[dict]:
    """Vragen filteren op pijler en gebouwtype.

    Een vraag zonder ``alleen_voor`` geldt voor elk gebouwtype. Wordt er geen
    gebouwtype meegegeven, dan komt alles terug — beter te veel tonen dan een
    inspecteur stilletjes een verplichte vraag onthouden.
    """
    uit = VRAGEN
    if pijler:
        uit = [v for v in uit if v["pijler"] == pijler.upper()]
    if gebouw_type:
        uit = [v for v in uit
               if "alleen_voor" not in v or gebouw_type in v["alleen_voor"]]
    return uit


def vraag(code: str) -> dict | None:
    for v in VRAGEN:
        if v["code"] == code:
            return v
    return None


def elementen_voor(groep: str | None = None) -> list[dict]:
    if groep is None:
        return ELEMENTEN
    return [e for e in ELEMENTEN if e["groep"] == groep]


def element(code: str) -> dict | None:
    for e in ELEMENTEN:
        if e["code"] == code:
            return e
    return None


def bewijsstukken() -> list[dict]:
    """Alle documenten die de vragenlijst opvraagt.

    Handig als losse lijst: dit is wat een gebouwbeheerder moet kunnen
    overleggen, en meestal het eerste dat bij een controle gevraagd wordt.
    """
    gezien: set[str] = set()
    uit: list[dict] = []
    for v in VRAGEN:
        naam = v.get("bewijs")
        if naam and naam not in gezien:
            gezien.add(naam)
            uit.append({"bewijs": naam, "code": v["code"],
                        "pijler": v["pijler"], "norm_ref": v["norm_ref"]})
    return uit


def bereken_score(antwoorden: list[dict]) -> dict:
    """Samenvatting per pijler over een ingevulde lijst.

    Telt alleen ja/nee-vragen; conditiescores en getallen horen niet in een
    percentage thuis. ``nvt`` telt niet mee in de noemer — een gebouw zonder
    lift hoort niet lager te scoren omdat de liftvraag niet van toepassing is.

    Verwacht per antwoord ``{"code": ..., "antwoord": "ja"|"nee"|"nvt"}``.
    """
    per_pijler: dict[str, dict] = {
        code: {"naam": p["naam"], "ja": 0, "nee": 0, "nvt": 0, "aandachtspunten": []}
        for code, p in PIJLERS.items()
    }

    for a in antwoorden:
        v = vraag(a.get("code", ""))
        if v is None or v.get("type") != "ja_nee_nvt":
            continue
        blok = per_pijler.get(v["pijler"])
        if blok is None:
            continue
        gegeven = (a.get("antwoord") or "").lower()
        if gegeven not in ("ja", "nee", "nvt"):
            continue
        blok[gegeven] += 1
        if gegeven == "nee":
            blok["aandachtspunten"].append({"code": v["code"], "vraag": v["vraag"]})

    totaal_ja = totaal_nee = 0
    for blok in per_pijler.values():
        beoordeeld = blok["ja"] + blok["nee"]
        blok["beoordeeld"] = beoordeeld
        blok["score_pct"] = round(100 * blok["ja"] / beoordeeld, 1) if beoordeeld else None
        totaal_ja += blok["ja"]
        totaal_nee += blok["nee"]

    beoordeeld = totaal_ja + totaal_nee
    return {
        "versie": BOEI_VERSION,
        "per_pijler": per_pijler,
        "beoordeeld": beoordeeld,
        "aandachtspunten": totaal_nee,
        "score_pct": round(100 * totaal_ja / beoordeeld, 1) if beoordeeld else None,
    }
