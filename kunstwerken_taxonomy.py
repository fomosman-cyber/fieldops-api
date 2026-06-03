"""Element- en gebrek-bibliotheek voor kunstwerken-inspectie.

Per kunstwerk-type (brug/viaduct/tunnel/sluis/duiker/kademuur) staan hier de
standaard-elementen die een inspecteur volgens NEN 2767-2 + CROW 134 zou
moeten beoordelen. De UI gebruikt dit om een nieuwe Inspection automatisch
op te tuigen met de juiste InspectionElement-records.

Per element staan ook veel voorkomende gebreken — die kan de inspecteur in
de defect-picker direct kiezen, of negeren en een vrije gebrek-naam invullen.

Bronnen:
  - NEN 2767-2 (2017): Conditiemeting infrastructuur — methodiek
  - CROW Publicatie 134 (2010): Inspectie van vaste en beweegbare bruggen
  - RWS Areaalrapportage Kunstwerken (KW-elementen-lijst)

Houd deze module data-only — geen DB-IO, geen FastAPI dependencies. Maakt
het testbaar zonder fixtures.
"""
from __future__ import annotations
from typing import Optional

from crow_146 import CROW_146A_SCHADEBEELDEN, CROW_146B_SCHADEBEELDEN


# ─────────────────────────────────────────────────────────────────────────────
# Kunstwerk-types die deze module ondersteunt
# ─────────────────────────────────────────────────────────────────────────────

KUNSTWERK_TYPES = {
    "brug": "Brug",
    "viaduct": "Viaduct",
    "tunnel": "Tunnel",
    "sluis": "Sluis",
    "stuw": "Stuw",
    "duiker": "Duiker",
    "kademuur": "Kademuur",
    "gemaal": "Gemaal",
    "riolering": "Riolering",
    "boom": "Boom (VTA)",
    "speeltoestel": "Speeltoestel (NEN-EN 1176)",
    "verlichting": "Openbare verlichting (NEN 3140)",
    "fontein": "Fontein (NEN 2767-4)",
    "kunstgrasveld": "Kunstgrasveld (NEN 2767-4)",
    "wegmarkering": "Wegmarkering (CROW 145)",
    "wegdek_asfalt": "Wegdek asfalt (CROW 146a)",
    "wegdek_elementen": "Wegdek elementen (CROW 146b)",
}

# Aliassen — input-normalisatie
_TYPE_ALIASES = {
    "bruggen": "brug",
    "viaducten": "viaduct",
    "tunnels": "tunnel",
    "sluizen": "sluis",
    "stuwen": "stuw",
    "duikers": "duiker",
    "kademuren": "kademuur",
    "kade": "kademuur",
    "kaai": "kademuur",
    "gemalen": "gemaal",
    "riool": "riolering",
    "rioolstelsel": "riolering",
    "vrijvervalriool": "riolering",
    "bomen": "boom",
    "laanboom": "boom",
    "monumentale-boom": "boom",
    "monumentaal": "boom",
    "park-boom": "boom",
    "vta": "boom",
    "speeltoestellen": "speeltoestel",
    "speelplek": "speeltoestel",
    "schommel": "speeltoestel",
    "glijbaan": "speeltoestel",
    "klimrek": "speeltoestel",
    "lantaarn": "verlichting",
    "lantaarnpaal": "verlichting",
    "openbare-verlichting": "verlichting",
    "ov": "verlichting",
    "armatuur": "verlichting",
    "nen3140": "verlichting",
    "fonteinen": "fontein",
    "waterelement": "fontein",
    "kunstgras": "kunstgrasveld",
    "sportveld": "kunstgrasveld",
    "markering": "wegmarkering",
    "wegmarkeringen": "wegmarkering",
    "belijning": "wegmarkering",
    "crow145": "wegmarkering",
    # Wegdek / verharding (CROW 146) — sluit aan op IMBOR-wegvak-asset_types.
    "wegdek": "wegdek_asfalt",
    "wegvak_asfalt": "wegdek_asfalt",
    "asfalt": "wegdek_asfalt",
    "asfaltverharding": "wegdek_asfalt",
    "fietspad": "wegdek_asfalt",
    "rijbaan": "wegdek_asfalt",
    "crow146": "wegdek_asfalt",
    "crow146a": "wegdek_asfalt",
    "wegvak_elementen": "wegdek_elementen",
    "elementenverharding": "wegdek_elementen",
    "klinkers": "wegdek_elementen",
    "klinker": "wegdek_elementen",
    "bestrating": "wegdek_elementen",
    "trottoir": "wegdek_elementen",
    "crow146b": "wegdek_elementen",
}


def normalize_type(raw: Optional[str]) -> Optional[str]:
    """Normaliseer een vrije type-naam naar canonieke key.

    >>> normalize_type("Bruggen") == "brug"
    True
    >>> normalize_type("kade") == "kademuur"
    True
    >>> normalize_type(None) is None
    True
    """
    if not raw:
        return None
    key = raw.strip().lower()
    if key in KUNSTWERK_TYPES:
        return key
    return _TYPE_ALIASES.get(key)


# ─────────────────────────────────────────────────────────────────────────────
# Element-bibliotheek
# ─────────────────────────────────────────────────────────────────────────────
#
# Elke entry:
#   code:     stable identifier (kunstwerk_type.element)
#   naam:     display-naam in inspecteurs-UI en PDF
#   groep:    constructief | afwerking | installatie | omgeving
#   gebreken: lijst veel voorkomende gebreken voor dit element
#
# Belangrijk: codes blijven stabiel. Naam mag wijzigen, code niet — er kunnen
# Inspection-records bestaan die naar oude codes refereren.

# Gebreken zijn van het type (code, naam, advies-categorie).
# advies-categorie: observatie | klein onderhoud | groot onderhoud | acuut

_BRUG_ELEMENTEN = [
    {
        "code": "BRUG.ONDERBOUW",
        "naam": "Onderbouw / landhoofden",
        "groep": "constructief",
        "gebreken": [
            {"code": "scheurvorming", "naam": "Scheurvorming in beton"},
            {"code": "afspatting", "naam": "Afspatting / spalling"},
            {"code": "wapeningscorrosie", "naam": "Zichtbare wapeningscorrosie"},
            {"code": "verzakking", "naam": "Verzakking / scheefstand"},
            {"code": "dekkingstekort", "naam": "Dekkingstekort"},
            {"code": "vochtdoorslag", "naam": "Vochtdoorslag / kalkuitbloeiing"},
        ],
    },
    {
        "code": "BRUG.PIJLERS",
        "naam": "Pijlers / kolommen",
        "groep": "constructief",
        "gebreken": [
            {"code": "aanvaringsschade", "naam": "Aanvaringsschade"},
            {"code": "scheurvorming", "naam": "Scheurvorming"},
            {"code": "wapeningscorrosie", "naam": "Wapeningscorrosie"},
            {"code": "afspatting", "naam": "Afspatting beton"},
            {"code": "ontvoeging", "naam": "Ontvoeging metselwerk"},
        ],
    },
    {
        "code": "BRUG.BOVENBOUW",
        "naam": "Bovenbouw / hoofddraagconstructie",
        "groep": "constructief",
        "gebreken": [
            {"code": "scheurvorming", "naam": "Scheurvorming"},
            {"code": "vervorming", "naam": "Vervorming / doorbuiging"},
            {"code": "corrosie_staal", "naam": "Corrosie staalconstructie"},
            {"code": "verlies_voorspanning", "naam": "Verlies voorspanning (vermoedelijk)"},
            {"code": "lasbreuk", "naam": "Lasbreuk / scheurtje las"},
        ],
    },
    {
        "code": "BRUG.BRUGDEK",
        "naam": "Brugdek / rijvloer",
        "groep": "afwerking",
        "gebreken": [
            {"code": "asfalt_scheur", "naam": "Scheurvorming in asfalt"},
            {"code": "asfalt_rafeling", "naam": "Rafeling deklaag"},
            {"code": "spoorvorming", "naam": "Spoorvorming"},
            {"code": "waterstagnatie", "naam": "Waterstagnatie / slechte afwatering"},
            {"code": "voegovergang_lek", "naam": "Lekkage via voegovergang"},
        ],
    },
    {
        "code": "BRUG.VOEGOVERGANGEN",
        "naam": "Voegovergangen",
        "groep": "constructief",
        "gebreken": [
            {"code": "voeg_breuk", "naam": "Breuk in voegprofiel"},
            {"code": "voeg_loszitten", "naam": "Loszittende ankers"},
            {"code": "voeg_lek", "naam": "Lekkage onder voeg"},
            {"code": "voeg_vuil", "naam": "Vuil / blokkering in voeg"},
            {"code": "voeg_geluid", "naam": "Geluid bij overrijden"},
        ],
    },
    {
        "code": "BRUG.OPLEGGINGEN",
        "naam": "Opleggingen",
        "groep": "constructief",
        "gebreken": [
            {"code": "scheefstand", "naam": "Scheefstand / verschuiving"},
            {"code": "rubber_scheur", "naam": "Scheur in elastomeer"},
            {"code": "corrosie", "naam": "Corrosie staalplaten"},
            {"code": "verlies_smering", "naam": "Verlies smering / blokkering"},
        ],
    },
    {
        "code": "BRUG.LEUNINGEN",
        "naam": "Leuningen / geleiderail",
        "groep": "afwerking",
        "gebreken": [
            {"code": "deuk", "naam": "Aanrijdschade / deuk"},
            {"code": "corrosie", "naam": "Corrosie"},
            {"code": "loszitten", "naam": "Loszittende bevestiging"},
            {"code": "ontbrekend_deel", "naam": "Ontbrekend segment"},
        ],
    },
    {
        "code": "BRUG.AFWATERING",
        "naam": "Afwatering / kolken",
        "groep": "installatie",
        "gebreken": [
            {"code": "verstopt", "naam": "Verstopt afvoerputje"},
            {"code": "lek_buis", "naam": "Lek in afvoerleiding"},
            {"code": "ontbrekend_rooster", "naam": "Ontbrekend / kapot rooster"},
        ],
    },
    {
        "code": "BRUG.OEVERS",
        "naam": "Oever- en taludbescherming",
        "groep": "omgeving",
        "gebreken": [
            {"code": "erosie", "naam": "Erosie talud"},
            {"code": "beschoeiing_kapot", "naam": "Beschadigde beschoeiing"},
            {"code": "ondermijning", "naam": "Ondermijning fundering"},
            {"code": "begroeiing", "naam": "Begroeiing met wortelschade"},
        ],
    },
    {
        "code": "BRUG.VERLICHTING",
        "naam": "Openbare verlichting op kunstwerk",
        "groep": "installatie",
        "gebreken": [
            {"code": "armatuur_kapot", "naam": "Armatuur kapot"},
            {"code": "mast_scheef", "naam": "Scheve lichtmast"},
            {"code": "kabel_los", "naam": "Loshangende kabel"},
        ],
    },
]

# Viaduct = brug-variant zonder waterpassage; hergebruik brug-elementen
# zonder OEVERS + AFWATERING-specifiek voor watergangen.
_VIADUCT_ELEMENTEN = [
    e for e in _BRUG_ELEMENTEN if e["code"] not in ("BRUG.OEVERS",)
]

_TUNNEL_ELEMENTEN = [
    {
        "code": "TUNNEL.TUNNELDAK",
        "naam": "Tunneldak / dakplaten",
        "groep": "constructief",
        "gebreken": [
            {"code": "lek", "naam": "Vochtdoorslag / lekkage"},
            {"code": "scheurvorming", "naam": "Scheurvorming"},
            {"code": "afspatting", "naam": "Afspatting"},
            {"code": "kalkuitbloei", "naam": "Kalkuitbloeiing"},
        ],
    },
    {
        "code": "TUNNEL.TUNNELWAND",
        "naam": "Tunnelwanden",
        "groep": "constructief",
        "gebreken": [
            {"code": "scheurvorming", "naam": "Scheurvorming"},
            {"code": "vocht", "naam": "Vochtplekken"},
            {"code": "afgebroken", "naam": "Afgebroken stukken beton"},
            {"code": "graffiti", "naam": "Graffiti / vandalisme"},
        ],
    },
    {
        "code": "TUNNEL.RIJVLOER",
        "naam": "Tunnel-rijvloer",
        "groep": "afwerking",
        "gebreken": [
            {"code": "asfalt_scheur", "naam": "Scheurvorming asfalt"},
            {"code": "spoorvorming", "naam": "Spoorvorming"},
            {"code": "rafeling", "naam": "Rafeling"},
            {"code": "waterplas", "naam": "Plasvorming"},
        ],
    },
    {
        "code": "TUNNEL.VERLICHTING",
        "naam": "Tunnelverlichting",
        "groep": "installatie",
        "gebreken": [
            {"code": "armatuur_uit", "naam": "Uitval armatuur"},
            {"code": "luxe_te_laag", "naam": "Verlichtingssterkte onder norm"},
            {"code": "kabel_beschadigd", "naam": "Beschadigde bekabeling"},
        ],
    },
    {
        "code": "TUNNEL.VENTILATIE",
        "naam": "Ventilatie-installatie",
        "groep": "installatie",
        "gebreken": [
            {"code": "ventilator_defect", "naam": "Defecte ventilator"},
            {"code": "filter_vuil", "naam": "Vervuilde filters"},
            {"code": "geluidoverlast", "naam": "Geluidoverlast / trillingen"},
        ],
    },
    {
        "code": "TUNNEL.BRANDVEILIGHEID",
        "naam": "Brand- & veiligheidsinstallaties",
        "groep": "installatie",
        "gebreken": [
            {"code": "blusser_ontbreekt", "naam": "Ontbrekende blusser"},
            {"code": "vluchtdeur_klemt", "naam": "Klemmende vluchtdeur"},
            {"code": "bewegwijzering_kapot", "naam": "Kapotte bewegwijzering"},
            {"code": "noodverlichting_uit", "naam": "Noodverlichting uit"},
        ],
    },
    {
        "code": "TUNNEL.AFWATERING",
        "naam": "Tunnel-afwatering",
        "groep": "installatie",
        "gebreken": [
            {"code": "pompput_vol", "naam": "Volle pompput"},
            {"code": "pomp_defect", "naam": "Defecte hemelwaterpomp"},
            {"code": "lek_riool", "naam": "Lek in riool / persleiding"},
        ],
    },
    {
        "code": "TUNNEL.WEGAANSLUITING",
        "naam": "Wegaansluiting / toerit",
        "groep": "afwerking",
        "gebreken": [
            {"code": "verzakking", "naam": "Verzakking toerit"},
            {"code": "voeg_lek", "naam": "Lek bij dilatatievoeg"},
        ],
    },
]

_SLUIS_ELEMENTEN = [
    {
        "code": "SLUIS.KOLK",
        "naam": "Sluiskolk (vloer + wanden)",
        "groep": "constructief",
        "gebreken": [
            {"code": "scheurvorming", "naam": "Scheurvorming in beton"},
            {"code": "afspatting", "naam": "Afspatting"},
            {"code": "wapeningscorrosie", "naam": "Wapeningscorrosie"},
            {"code": "aanvaringsschade", "naam": "Aanvaringsschade"},
        ],
    },
    {
        "code": "SLUIS.DEUREN",
        "naam": "Sluisdeuren",
        "groep": "constructief",
        "gebreken": [
            {"code": "lek_deur", "naam": "Lekkage tussen deurhelften"},
            {"code": "corrosie_staal", "naam": "Corrosie staalbeplating"},
            {"code": "rubber_versleten", "naam": "Versleten afdichtrubber"},
            {"code": "deur_klemt", "naam": "Klemmen / stroef bewegen"},
            {"code": "scheef", "naam": "Scheefstand deur"},
        ],
    },
    {
        "code": "SLUIS.BEWEGINGSWERK",
        "naam": "Bewegingswerk / aandrijving",
        "groep": "installatie",
        "gebreken": [
            {"code": "lek_olie", "naam": "Lekkage hydrauliek"},
            {"code": "motor_geluid", "naam": "Afwijkend motorgeluid"},
            {"code": "tandwiel_versleten", "naam": "Versleten tandwerk / tandheugel"},
            {"code": "smering_droog", "naam": "Onvoldoende smering"},
            {"code": "sensor_storing", "naam": "Storing eindschakelaars / sensoren"},
        ],
    },
    {
        "code": "SLUIS.HEFTOREN",
        "naam": "Heftoren / draaipunten",
        "groep": "constructief",
        "gebreken": [
            {"code": "scheurvorming", "naam": "Scheurvorming"},
            {"code": "corrosie", "naam": "Corrosie staalconstructie"},
            {"code": "deformatie", "naam": "Deformatie"},
            {"code": "lager_versleten", "naam": "Versleten lager / draaipunt"},
        ],
    },
    {
        "code": "SLUIS.REMMINGWERK",
        "naam": "Remming- en geleidewerk",
        "groep": "afwerking",
        "gebreken": [
            {"code": "deuken", "naam": "Aanvaringsdeuken"},
            {"code": "fender_los", "naam": "Loszittende fender"},
            {"code": "corrosie", "naam": "Corrosie"},
            {"code": "ontbrekend", "naam": "Ontbrekend segment"},
        ],
    },
    {
        "code": "SLUIS.WACHTPLAATS",
        "naam": "Wachtplaats / steiger",
        "groep": "afwerking",
        "gebreken": [
            {"code": "houtrot", "naam": "Houtrot"},
            {"code": "loszittend_dek", "naam": "Loszittende plank"},
            {"code": "bolder_los", "naam": "Loszittende bolder"},
            {"code": "verlichting_kapot", "naam": "Defecte verlichting"},
        ],
    },
    {
        "code": "SLUIS.BEDIENING",
        "naam": "Bedieningssysteem / nautische installaties",
        "groep": "installatie",
        "gebreken": [
            {"code": "scada_storing", "naam": "SCADA / PLC-storing"},
            {"code": "communicatie", "naam": "Communicatiestoring"},
            {"code": "seinen_uit", "naam": "Uitval scheepvaartseinen"},
        ],
    },
]

# Stuw = vergelijkbaar met sluis voor inspectie-doeleinden
_STUW_ELEMENTEN = _SLUIS_ELEMENTEN

_DUIKER_ELEMENTEN = [
    {
        "code": "DUIKER.LICHAAM",
        "naam": "Duiker-lichaam (buis/koker)",
        "groep": "constructief",
        "gebreken": [
            {"code": "scheurvorming", "naam": "Scheurvorming"},
            {"code": "vervorming", "naam": "Vervorming / ovaal worden"},
            {"code": "corrosie", "naam": "Corrosie (stalen duiker)"},
            {"code": "voeg_open", "naam": "Open voeg tussen segmenten"},
            {"code": "wapeningscorrosie", "naam": "Wapeningscorrosie"},
        ],
    },
    {
        "code": "DUIKER.INSTROOM",
        "naam": "In- en uitstroom-opening",
        "groep": "constructief",
        "gebreken": [
            {"code": "verzakking", "naam": "Verzakking inlooprand"},
            {"code": "verstopt", "naam": "Verstopt door slib / takken"},
            {"code": "erosie", "naam": "Erosie achter inlaat"},
            {"code": "ontbrekend_rooster", "naam": "Ontbrekend / kapot rooster"},
        ],
    },
    {
        "code": "DUIKER.BODEM",
        "naam": "Bodem / stroomprofiel",
        "groep": "constructief",
        "gebreken": [
            {"code": "uitspoeling", "naam": "Uitspoeling onder duiker"},
            {"code": "slibafzetting", "naam": "Slibafzetting"},
            {"code": "obstakel", "naam": "Obstakels / puin"},
        ],
    },
    {
        "code": "DUIKER.OEVERAANSLUITING",
        "naam": "Oever-aansluiting",
        "groep": "omgeving",
        "gebreken": [
            {"code": "talud_erosie", "naam": "Talud-erosie"},
            {"code": "begroeiing", "naam": "Doorgroei begroeiing"},
            {"code": "lek_omloop", "naam": "Lek / omloop water"},
        ],
    },
]

_KADEMUUR_ELEMENTEN = [
    {
        "code": "KADE.MUUR",
        "naam": "Kademuur / damwand",
        "groep": "constructief",
        "gebreken": [
            {"code": "scheurvorming", "naam": "Scheurvorming"},
            {"code": "wapeningscorrosie", "naam": "Wapeningscorrosie"},
            {"code": "damwand_perforatie", "naam": "Perforatie damwand"},
            {"code": "deformatie", "naam": "Deformatie / uitbuiging"},
            {"code": "scheefstand", "naam": "Scheefstand / vooroverhellen"},
            {"code": "ankering_los", "naam": "Loszittend trekanker"},
        ],
    },
    {
        "code": "KADE.KESPEN",
        "naam": "Kespen / dekbalk",
        "groep": "constructief",
        "gebreken": [
            {"code": "houtrot", "naam": "Houtrot (houten kespen)"},
            {"code": "betonschade", "naam": "Betonschade"},
            {"code": "scheurvorming", "naam": "Scheurvorming"},
        ],
    },
    {
        "code": "KADE.BOLDERS",
        "naam": "Bolders / kademeubilair",
        "groep": "afwerking",
        "gebreken": [
            {"code": "los", "naam": "Loszittend"},
            {"code": "corrosie", "naam": "Corrosie"},
            {"code": "ontbrekend", "naam": "Ontbrekend"},
            {"code": "deuk", "naam": "Aanvaringsschade"},
        ],
    },
    {
        "code": "KADE.OEVERBESCHERMING",
        "naam": "Oeverbescherming / talud",
        "groep": "omgeving",
        "gebreken": [
            {"code": "erosie", "naam": "Erosie"},
            {"code": "ondermijning", "naam": "Ondermijning"},
            {"code": "begroeiing", "naam": "Wortelgroei door bescherming"},
        ],
    },
    {
        "code": "KADE.VERHARDING",
        "naam": "Bovenverharding kade",
        "groep": "afwerking",
        "gebreken": [
            {"code": "verzakking", "naam": "Verzakking"},
            {"code": "scheurvorming", "naam": "Scheurvorming"},
            {"code": "voegwijdte", "naam": "Te grote voegen"},
            {"code": "ontbrekende_stenen", "naam": "Ontbrekende stenen"},
        ],
    },
]

_GEMAAL_ELEMENTEN = [
    {
        "code": "GEMAAL.GEBOUW",
        "naam": "Gemaalgebouw / bouwkundig",
        "groep": "constructief",
        "gebreken": [
            {"code": "scheurvorming", "naam": "Scheurvorming"},
            {"code": "lek", "naam": "Vochtdoorslag"},
            {"code": "deur_versleten", "naam": "Versleten deuren / kozijnen"},
        ],
    },
    {
        "code": "GEMAAL.POMPEN",
        "naam": "Pompen",
        "groep": "installatie",
        "gebreken": [
            {"code": "lekkage", "naam": "Lekkage pomphuis"},
            {"code": "trilling", "naam": "Afwijkende trilling"},
            {"code": "rendement_laag", "naam": "Verminderd rendement"},
            {"code": "lager_versleten", "naam": "Versleten lager"},
        ],
    },
    {
        "code": "GEMAAL.LEIDINGEN",
        "naam": "Pers- en zuigleidingen",
        "groep": "installatie",
        "gebreken": [
            {"code": "lek", "naam": "Lekkage flens / koppeling"},
            {"code": "corrosie", "naam": "Corrosie"},
            {"code": "afzetting", "naam": "Afzetting / vernauwing"},
        ],
    },
    {
        "code": "GEMAAL.ELEKTRO",
        "naam": "Elektro- & besturing",
        "groep": "installatie",
        "gebreken": [
            {"code": "isolatie", "naam": "Isolatiefout"},
            {"code": "scada", "naam": "SCADA-storing"},
            {"code": "noodstroom", "naam": "Defecte noodstroom"},
        ],
    },
    {
        "code": "GEMAAL.ROOSTER",
        "naam": "Inlaatrooster / vuilrooster",
        "groep": "installatie",
        "gebreken": [
            {"code": "verstopt", "naam": "Verstopt door drijfvuil"},
            {"code": "harkmachine_kapot", "naam": "Defecte vuilharkmachine"},
            {"code": "rooster_kapot", "naam": "Beschadigd rooster"},
        ],
    },
]


_RIOLERING_ELEMENTEN = [
    {
        "code": "RIO.INSPECTIEPUT",
        "naam": "Inspectieput",
        "groep": "constructief",
        "gebreken": [
            {"code": "verzakking", "naam": "Verzakking inspectieput"},
            {"code": "scheurvorming", "naam": "Scheurvorming wand"},
            {"code": "deksel_los", "naam": "Loszittend / verzakt putdeksel"},
            {"code": "deksel_kapot", "naam": "Beschadigd putdeksel"},
            {"code": "lekkage_voeg", "naam": "Lekkage tussen ringen"},
            {"code": "wortelingroei", "naam": "Wortelingroei door voegen"},
            {"code": "vervorming", "naam": "Vervorming van put"},
        ],
    },
    {
        "code": "RIO.VRIJVERVAL_LEIDING",
        "naam": "Vrijverval-rioolleiding",
        "groep": "constructief",
        "gebreken": [
            {"code": "scheur_langs", "naam": "Langsscheur (NEN 3399 BAA)"},
            {"code": "scheur_dwars", "naam": "Dwarsscheur (BAB)"},
            {"code": "scherf", "naam": "Ontbrekend stuk / scherf (BAC)"},
            {"code": "deformatie", "naam": "Vervorming buis (BAJ)"},
            {"code": "verzakking", "naam": "Verzakking leiding (BAL)"},
            {"code": "infiltratie", "naam": "Infiltratie / lekkage (BBF)"},
            {"code": "wortelingroei", "naam": "Wortelingroei (BBA)"},
            {"code": "afzetting", "naam": "Afzetting / slib (BBC)"},
            {"code": "obstakel", "naam": "Obstakel / blokkade (BBE)"},
            {"code": "corrosie_chemisch", "naam": "Chemische aantasting (BAF)"},
        ],
    },
    {
        "code": "RIO.AANSLUITING",
        "naam": "Aansluiting / huisaansluiting",
        "groep": "constructief",
        "gebreken": [
            {"code": "uitstekend", "naam": "Uitstekende aansluiting (BAG)"},
            {"code": "ontbrekend", "naam": "Ontbrekende aansluiting (BAH)"},
            {"code": "lekkage", "naam": "Lekkage bij aansluiting"},
            {"code": "wortelingroei", "naam": "Wortelingroei via aansluiting"},
        ],
    },
    {
        "code": "RIO.PERSLEIDING",
        "naam": "Persleiding",
        "groep": "constructief",
        "gebreken": [
            {"code": "corrosie", "naam": "Externe corrosie"},
            {"code": "lekkage", "naam": "Lekkage flens / koppeling"},
            {"code": "afzetting", "naam": "Afzetting / vernauwing"},
            {"code": "kathodisch", "naam": "Falende kathodische bescherming"},
        ],
    },
    {
        "code": "RIO.OVERSTORT",
        "naam": "Overstortput / overstortdrempel",
        "groep": "constructief",
        "gebreken": [
            {"code": "verstopt_rooster", "naam": "Verstopt vuilrooster"},
            {"code": "rooster_kapot", "naam": "Beschadigd rooster"},
            {"code": "drempel_beton", "naam": "Beschadigde drempel (beton)"},
            {"code": "hoogwater_marker", "naam": "Onleesbare/ontbrekende hoogwater-marker"},
        ],
    },
    {
        "code": "RIO.GEMAAL",
        "naam": "Pompput / rioolgemaal",
        "groep": "installatie",
        "gebreken": [
            {"code": "pomp_lekt", "naam": "Lekkage pomp"},
            {"code": "niveausensor", "naam": "Storing niveau-sensor"},
            {"code": "ontluchting", "naam": "Falende ontluchting"},
            {"code": "slibniveau", "naam": "Te hoog slibniveau"},
            {"code": "scada_storing", "naam": "SCADA / PLC-storing"},
            {"code": "geluid", "naam": "Afwijkend geluid pomp"},
            {"code": "noodstroom", "naam": "Defecte noodstroom-voorziening"},
        ],
    },
    {
        "code": "RIO.IBA",
        "naam": "IBA (individuele behandelinstallatie)",
        "groep": "installatie",
        "gebreken": [
            {"code": "slib_vol", "naam": "Slibcompartiment vol"},
            {"code": "beluchting", "naam": "Defecte beluchter"},
            {"code": "effluent", "naam": "Effluentkwaliteit onder norm"},
            {"code": "geur", "naam": "Geuremissie"},
        ],
    },
    {
        "code": "RIO.KOLK",
        "naam": "Straat- / trottoirkolk",
        "groep": "constructief",
        "gebreken": [
            {"code": "verstopt", "naam": "Verstopt door blad/zand"},
            {"code": "rooster_kapot", "naam": "Beschadigd of ontbrekend rooster"},
            {"code": "verzakking", "naam": "Verzakking rond kolk"},
            {"code": "geur", "naam": "Geur uit kolk"},
            {"code": "uitspoeling", "naam": "Uitspoeling stoeptegels"},
        ],
    },
    {
        "code": "RIO.OEVERAFVOER",
        "naam": "Bermsloot- / oeverafvoer",
        "groep": "omgeving",
        "gebreken": [
            {"code": "verstopt", "naam": "Verstopt door begroeiing"},
            {"code": "erosie", "naam": "Erosie talud bij uitstroom"},
            {"code": "ondermijning", "naam": "Ondermijning uitstroombak"},
        ],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Bomen (VTA — Visual Tree Assessment)
# Methodiek: Mattheck VTA, NTS (Nederlandse Taxateurs Standaard),
# CROW publicatie 200 (handboek beheer openbare ruimte voor bomen),
# en de BBA-bomenkeuring. De boom-elementen-bibliotheek volgt de
# 5 standaard inspectie-zones die elke VTA-inspecteur doorloopt.
# ─────────────────────────────────────────────────────────────────────────────

_BOOM_ELEMENTEN = [
    {
        "code": "BOOM.STAM",
        "naam": "Stam",
        "groep": "vegetatief",
        "gebreken": [
            {"code": "langsscheur", "naam": "Langsscheur in stam"},
            {"code": "dwarsscheur", "naam": "Dwarsscheur / barst"},
            {"code": "holte", "naam": "Stam-holte / rotting"},
            {"code": "zwam", "naam": "Houtzwam / parasitaire schimmel"},
            {"code": "kanker", "naam": "Bastkanker / tumor"},
            {"code": "bastbeschadiging", "naam": "Bastbeschadiging (mechanisch)"},
            {"code": "bliksem", "naam": "Bliksemschade"},
            {"code": "opslag_basaal", "naam": "Opslag aan stamvoet (stress-indicator)"},
        ],
    },
    {
        "code": "BOOM.WORTELAANLOOP",
        "naam": "Wortelaanloop / wortelopdruk",
        "groep": "vegetatief",
        "gebreken": [
            {"code": "wortelopdruk", "naam": "Wortelopdruk (verhardingsschade)"},
            {"code": "wortelverstikking", "naam": "Wortel-verstikking (verharding/bouw)"},
            {"code": "uitholling", "naam": "Uitholling rond stamvoet"},
            {"code": "wortelafwijking", "naam": "Scheve wortelaanloop / instabiel"},
            {"code": "rotting_wortel", "naam": "Wortelrotting zichtbaar"},
            {"code": "verzakking", "naam": "Verzakking van stamvoet"},
        ],
    },
    {
        "code": "BOOM.HOOFDTAKKEN",
        "naam": "Hoofdtakken / gesteltakken",
        "groep": "vegetatief",
        "gebreken": [
            {"code": "plakoksel", "naam": "Plakoksel (ingegroeide bast)"},
            {"code": "scheurvorming_tak", "naam": "Scheurvorming in oksel"},
            {"code": "dode_tak", "naam": "Dode hoofdtak"},
            {"code": "afgebroken_tak", "naam": "Afgebroken takstomp"},
            {"code": "ophanging", "naam": "Loszittende / hangende tak"},
            {"code": "stam_vork", "naam": "Risicovolle stamvork"},
        ],
    },
    {
        "code": "BOOM.KROON",
        "naam": "Kroon / bladvolume",
        "groep": "afwerking",
        "gebreken": [
            {"code": "dood_hout", "naam": "Dood hout in kroon"},
            {"code": "scheef", "naam": "Scheefstand kroon"},
            {"code": "vitaliteitsverlies", "naam": "Vitaliteitsverlies / spaarzaam blad"},
            {"code": "uitval_top", "naam": "Top-uitval (top-sterven)"},
            {"code": "snoeischade", "naam": "Snoeischade / open wonden"},
            {"code": "plaagdruk", "naam": "Plaagdruk (rups, kever, mineerder)"},
        ],
    },
    {
        "code": "BOOM.STANDPLAATS",
        "naam": "Standplaats / groei-omstandigheden",
        "groep": "omgeving",
        "gebreken": [
            {"code": "verdichting", "naam": "Bodem-verdichting"},
            {"code": "droogtestress", "naam": "Droogtestress / waterstress"},
            {"code": "zoutschade", "naam": "Zoutschade (strooizout)"},
            {"code": "te_klein", "naam": "Te krappe groeiplaats"},
            {"code": "infectiedruk", "naam": "Hoge infectiedruk omgeving"},
            {"code": "aanrijdschade", "naam": "Aanrijdschade door verkeer"},
        ],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Speeltoestellen (NEN-EN 1176/1177)
# Wettelijke routine-inspectie 1× per jaar (Warenwetbesluit attractie- en
# speeltoestellen). Elke speeltuin doorloopt visueel-functioneel onderzoek
# per toestel + valdempende ondergrond.
# ─────────────────────────────────────────────────────────────────────────────

_SPEELTOESTEL_ELEMENTEN = [
    {
        "code": "SPEEL.CONSTRUCTIE",
        "naam": "Constructie / dragend frame",
        "groep": "speel_constructief",
        "gebreken": [
            {"code": "rot_hout", "naam": "Hout-rot / aantasting"},
            {"code": "corrosie", "naam": "Corrosie staal"},
            {"code": "loszittend_bout", "naam": "Loszittende bouten / klemmen"},
            {"code": "scheur", "naam": "Scheurvorming in dragend deel"},
            {"code": "vervorming", "naam": "Vervorming / verbuiging"},
        ],
    },
    {
        "code": "SPEEL.BEWEGENDE_DELEN",
        "naam": "Bewegende delen (kettingen, lagers, scharnieren)",
        "groep": "speel_constructief",
        "gebreken": [
            {"code": "ketting_versleten", "naam": "Versleten ketting / kabel"},
            {"code": "lager_droog", "naam": "Droog/piepend lager"},
            {"code": "speling", "naam": "Te veel speling"},
            {"code": "kapot_scharnier", "naam": "Kapot scharnier"},
        ],
    },
    {
        "code": "SPEEL.KLIMDELEN",
        "naam": "Klim-/grijpdelen",
        "groep": "afwerking",
        "gebreken": [
            {"code": "splinters", "naam": "Splinters / scherpe randen"},
            {"code": "ontbreekt", "naam": "Ontbrekend onderdeel"},
            {"code": "gladheid", "naam": "Gladde / versleten grip"},
            {"code": "vandalisme", "naam": "Vandalismeschade"},
        ],
    },
    {
        "code": "SPEEL.VALDEMPING",
        "naam": "Valdempende ondergrond (NEN-EN 1177)",
        "groep": "omgeving",
        "gebreken": [
            {"code": "ondergrond_te_dun", "naam": "Te dunne laag (< 30cm zand/schors)"},
            {"code": "verzakking", "naam": "Verzakking / kuilen in ondergrond"},
            {"code": "verharding", "naam": "Verharding ondergrond"},
            {"code": "te_klein_oppervlak", "naam": "Valoppervlak te klein voor valhoogte"},
            {"code": "rubber_versleten", "naam": "Versleten rubbertegels"},
        ],
    },
    {
        "code": "SPEEL.VEILIGHEID",
        "naam": "Veiligheidsafstanden + knelpunten",
        "groep": "speel_veiligheid",
        "gebreken": [
            {"code": "knelpunt", "naam": "Knelpunt voor hoofd / vinger"},
            {"code": "snoer_risico", "naam": "Risico op verstrikking koord"},
            {"code": "uitsteeksel", "naam": "Scherp uitsteeksel"},
            {"code": "valhoogte_te_groot", "naam": "Valhoogte > toegestaan voor leeftijdsgroep"},
        ],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Openbare verlichting (NEN 3140 + NEN 1010)
# 5-jaarlijkse elektrische keuring + visueel onderhoud
# ─────────────────────────────────────────────────────────────────────────────

_VERLICHTING_ELEMENTEN = [
    {
        "code": "OV.MAST",
        "naam": "Lichtmast",
        "groep": "constructief",
        "gebreken": [
            {"code": "corrosie_voet", "naam": "Corrosie aan mastvoet (waterlijn)"},
            {"code": "aanrijdschade", "naam": "Aanrijdschade"},
            {"code": "scheef", "naam": "Scheefstand mast"},
            {"code": "fundering_los", "naam": "Loszittende fundering / verzakking"},
            {"code": "verfschade", "naam": "Verfschade / bescherming weg"},
        ],
    },
    {
        "code": "OV.ARMATUUR",
        "naam": "Armatuur",
        "groep": "ov_elektrisch",
        "gebreken": [
            {"code": "led_kapot", "naam": "Kapotte LED / lamp"},
            {"code": "dichting_lek", "naam": "Lekkende dichting (water in armatuur)"},
            {"code": "kapje_kapot", "naam": "Kapot of vermist polycarbonaat-kapje"},
            {"code": "armatuur_los", "naam": "Loszittende armatuur"},
            {"code": "verkeer_verlichting", "naam": "Onjuiste lichtuitstoot / lichthinder"},
        ],
    },
    {
        "code": "OV.AANSLUITKAST",
        "naam": "Aansluitkast / zekering",
        "groep": "ov_elektrisch",
        "gebreken": [
            {"code": "kast_open", "naam": "Aansluitkast niet afgesloten"},
            {"code": "water_in_kast", "naam": "Water/vocht in aansluitkast"},
            {"code": "zekering_doorgebrand", "naam": "Doorgebrande zekering"},
            {"code": "kabel_los", "naam": "Loszittende kabel / klem"},
            {"code": "isolatie", "naam": "Verslechterde isolatie"},
        ],
    },
    {
        "code": "OV.AARDING",
        "naam": "Aarding / aardlek (NEN 3140)",
        "groep": "ov_elektrisch",
        "gebreken": [
            {"code": "aarding_los", "naam": "Loszittende aardingsdraad"},
            {"code": "aarding_corrosie", "naam": "Corrosie aardingspunt"},
            {"code": "aarding_te_hoog", "naam": "Te hoge aardingsweerstand"},
            {"code": "aardlek_defect", "naam": "Defecte aardlekschakelaar"},
        ],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# NEN 2767-4 installaties (fontein, kunstgrasveld)
# Deel 4 voor gebouw-installaties + buiten-installaties.
# ─────────────────────────────────────────────────────────────────────────────

_FONTEIN_ELEMENTEN = [
    {"code": "FONT.POMP", "naam": "Pomp + filter", "groep": "installatie",
     "gebreken": [
         {"code": "pomp_lek", "naam": "Lekkage pomp"},
         {"code": "filter_vuil", "naam": "Vervuild filter"},
         {"code": "geluid", "naam": "Afwijkend geluid"},
         {"code": "rendement", "naam": "Verlaagd doorvoer-rendement"},
     ]},
    {"code": "FONT.WATERBAK", "naam": "Waterbak / bassin", "groep": "constructief",
     "gebreken": [
         {"code": "lekkage_bak", "naam": "Lekkage bak"},
         {"code": "scheur", "naam": "Scheurvorming"},
         {"code": "kalkaanslag", "naam": "Kalkaanslag / aanslag"},
         {"code": "tegels_los", "naam": "Loszittende tegels"},
     ]},
    {"code": "FONT.SPROEIERS", "naam": "Sproeiers / nozzles", "groep": "installatie",
     "gebreken": [
         {"code": "verstopt", "naam": "Verstopt"},
         {"code": "scheef_patroon", "naam": "Onregelmatig spuit-patroon"},
         {"code": "vermist", "naam": "Ontbrekende nozzle"},
     ]},
    {"code": "FONT.VERLICHTING", "naam": "Onderwater-verlichting", "groep": "ov_elektrisch",
     "gebreken": [
         {"code": "led_kapot", "naam": "Kapotte LED-armatuur"},
         {"code": "waterdicht", "naam": "Waterdichtheid armatuur"},
         {"code": "kabel", "naam": "Beschadigde kabel"},
     ]},
]

_KUNSTGRAS_ELEMENTEN = [
    {"code": "KGRAS.MAT", "naam": "Grasmat", "groep": "afwerking",
     "gebreken": [
         {"code": "rafel", "naam": "Rafelvorming"},
         {"code": "naden_los", "naam": "Loszittende naden"},
         {"code": "kleur", "naam": "Verkleuring / verbleking"},
         {"code": "infill_uitval", "naam": "Infill-uitspoeling"},
     ]},
    {"code": "KGRAS.ONDERBOUW", "naam": "Onderbouw / shockpad", "groep": "constructief",
     "gebreken": [
         {"code": "verzakking", "naam": "Lokale verzakking"},
         {"code": "demping", "naam": "Verminderde valdemping"},
         {"code": "drainage", "naam": "Slechte drainage"},
     ]},
    {"code": "KGRAS.OMRANDING", "naam": "Omranding + lijnen", "groep": "afwerking",
     "gebreken": [
         {"code": "lijn_versleten", "naam": "Versleten belijning"},
         {"code": "rand_los", "naam": "Loszittende rand-/keermuur"},
     ]},
    {"code": "KGRAS.MEUBILAIR", "naam": "Doelen / tribunes / netten", "groep": "installatie",
     "gebreken": [
         {"code": "doel_kapot", "naam": "Beschadigde doel-constructie"},
         {"code": "net_kapot", "naam": "Kapot net"},
         {"code": "stabiel", "naam": "Onstabiele tribune-onderdelen"},
     ]},
]

# ─────────────────────────────────────────────────────────────────────────────
# CROW 145 wegmarkering
# Beoordeelt zichtbaarheid (nat/droog), slijtage, contrast.
# ─────────────────────────────────────────────────────────────────────────────

_WEGMARKERING_ELEMENTEN = [
    {"code": "MARK.LANGS", "naam": "Langs-markering (rij-strepen)", "groep": "afwerking",
     "gebreken": [
         {"code": "slijtage", "naam": "Slijtage / dunne lijn"},
         {"code": "contrast_laag", "naam": "Verlaagd contrast (oud)"},
         {"code": "onderbreking", "naam": "Onderbroken patroon"},
         {"code": "nacht_zichtbaarheid", "naam": "Slechte nacht-zichtbaarheid"},
     ]},
    {"code": "MARK.DWARS", "naam": "Dwars-markering (zebra/stop)", "groep": "afwerking",
     "gebreken": [
         {"code": "slijtage", "naam": "Slijtage"},
         {"code": "ontbrekend", "naam": "Ontbrekend deel"},
     ]},
    {"code": "MARK.SYMBOLEN", "naam": "Symbolen + pijlen", "groep": "afwerking",
     "gebreken": [
         {"code": "vervaagd", "naam": "Vervaagd"},
         {"code": "onleesbaar", "naam": "Onleesbaar"},
     ]},
    {"code": "MARK.KATTENOGEN", "naam": "Kattenogen / reflectoren", "groep": "installatie",
     "gebreken": [
         {"code": "kapot", "naam": "Kapot reflectorelement"},
         {"code": "vermist", "naam": "Vermist reflector"},
         {"code": "vies", "naam": "Vervuild (geen reflectie)"},
     ]},
]


# ── Wegdek / verharding (CROW 146a asfalt, 146b elementen) ──────────────────
# De CROW 146-schadebeelden vormen direct de gebrek-picker per deklaag-element.
def _crow146_gebreken(schadebeelden: list[dict]) -> list[dict]:
    return [{"code": s["code"], "naam": s["naam"]} for s in schadebeelden]


_WEGDEK_ASFALT_ELEMENTEN = [
    {
        "code": "WEGDEK.DEKLAAG",
        "naam": "Asfaltdeklaag (toplaag)",
        "groep": "afwerking",
        "gebreken": _crow146_gebreken(CROW_146A_SCHADEBEELDEN),
    },
    {
        "code": "WEGDEK.VLAKHEID",
        "naam": "Vlakheid / spoorvorming",
        "groep": "constructief",
        "gebreken": [
            {"code": "spoorvorming", "naam": "Spoorvorming (langsonvlakheid wielspoor)"},
            {"code": "dwarsonvlakheid", "naam": "Dwarsonvlakheid / golfvorming"},
            {"code": "zetting", "naam": "Zetting / verzakking ondergrond"},
        ],
    },
    {
        "code": "WEGDEK.AFWATERING",
        "naam": "Afwatering / goten / kolken",
        "groep": "installatie",
        "gebreken": [
            {"code": "waterstagnatie", "naam": "Waterstagnatie / plasvorming"},
            {"code": "kolk_verstopt", "naam": "Verstopte/verzakte straatkolk"},
            {"code": "goot_defect", "naam": "Beschadigde goot / molgoot"},
        ],
    },
    {
        "code": "WEGDEK.KANTOPSLUITING",
        "naam": "Kantopsluiting / banden",
        "groep": "constructief",
        "gebreken": [
            {"code": "band_verzakt", "naam": "Verzakte/losse band"},
            {"code": "band_beschadigd", "naam": "Beschadigde band"},
            {"code": "voeg_open", "naam": "Open/uitgespoelde voeg"},
        ],
    },
]

_WEGDEK_ELEMENTEN_ELEMENTEN = [
    {
        "code": "WEGDEK.ELEMENTENVERHARDING",
        "naam": "Elementenverharding (klinkers/tegels)",
        "groep": "afwerking",
        "gebreken": _crow146_gebreken(CROW_146B_SCHADEBEELDEN),
    },
    {
        "code": "WEGDEK.VLAKHEID",
        "naam": "Vlakheid / verzakking",
        "groep": "constructief",
        "gebreken": [
            {"code": "oneffenheid", "naam": "Oneffenheid / rateleffect"},
            {"code": "zetting", "naam": "Zetting / verzakking"},
            {"code": "wortelopdruk", "naam": "Wortelopdruk"},
        ],
    },
    {
        "code": "WEGDEK.AFWATERING",
        "naam": "Afwatering / goten / kolken",
        "groep": "installatie",
        "gebreken": [
            {"code": "waterstagnatie", "naam": "Waterstagnatie / plasvorming"},
            {"code": "kolk_verstopt", "naam": "Verstopte/verzakte straatkolk"},
        ],
    },
    {
        "code": "WEGDEK.KANTOPSLUITING",
        "naam": "Kantopsluiting / banden",
        "groep": "constructief",
        "gebreken": [
            {"code": "band_verzakt", "naam": "Verzakte/losse band"},
            {"code": "voeg_open", "naam": "Open/uitgespoelde voeg"},
        ],
    },
]


_ELEMENTEN_PER_TYPE = {
    "brug": _BRUG_ELEMENTEN,
    "viaduct": _VIADUCT_ELEMENTEN,
    "tunnel": _TUNNEL_ELEMENTEN,
    "sluis": _SLUIS_ELEMENTEN,
    "stuw": _STUW_ELEMENTEN,
    "duiker": _DUIKER_ELEMENTEN,
    "kademuur": _KADEMUUR_ELEMENTEN,
    "gemaal": _GEMAAL_ELEMENTEN,
    "riolering": _RIOLERING_ELEMENTEN,
    "boom": _BOOM_ELEMENTEN,
    "speeltoestel": _SPEELTOESTEL_ELEMENTEN,
    "verlichting": _VERLICHTING_ELEMENTEN,
    "fontein": _FONTEIN_ELEMENTEN,
    "kunstgrasveld": _KUNSTGRAS_ELEMENTEN,
    "wegmarkering": _WEGMARKERING_ELEMENTEN,
    "wegdek_asfalt": _WEGDEK_ASFALT_ELEMENTEN,
    "wegdek_elementen": _WEGDEK_ELEMENTEN_ELEMENTEN,
}


def elementen_voor(kunstwerk_type: Optional[str]) -> list[dict]:
    """Returnt de element-bibliotheek voor één kunstwerk-type.

    Aanroep met onbekend type retourneert een lege lijst — de UI valt dan
    terug op een vrije element-invoer.
    """
    key = normalize_type(kunstwerk_type)
    if not key:
        return []
    return list(_ELEMENTEN_PER_TYPE.get(key, []))


def gebreken_voor(kunstwerk_type: Optional[str], element_code: str) -> list[dict]:
    """Lijst van veel voorkomende gebreken voor één element."""
    for e in elementen_voor(kunstwerk_type):
        if e["code"] == element_code:
            return list(e.get("gebreken") or [])
    return []


def alle_element_codes() -> set[str]:
    """Alle bekende element-codes — voor validatie."""
    codes = set()
    for items in _ELEMENTEN_PER_TYPE.values():
        for e in items:
            codes.add(e["code"])
    return codes


def type_beschikbaar(kunstwerk_type: Optional[str]) -> bool:
    return normalize_type(kunstwerk_type) is not None


# ─────────────────────────────────────────────────────────────────────────────
# Vragenlijst per element — NEN 2767-2 + CROW 134 conform
# ─────────────────────────────────────────────────────────────────────────────
#
# Elk element krijgt automatisch:
#   - de 4 generieke vragen (algemene staat, veiligheid, inspecteerbaarheid, urgentie)
#   - groep-specifieke vragen op basis van element_groep
#   - eventueel element-specifieke vragen (alleen waar de norm dat eist)
#
# Antwoord-types:
#   score_1_6      — NEN 2767-2 conditie-schaal
#   ja_nee         — ja/nee met optionele toelichting
#   ja_nee_nvt     — ja/nee/n.v.t.
#   keuze          — multi-choice, opties als list
#   meting         — numerieke meting met eenheid
#   tekst          — vrij tekstveld
#
# Versie wordt meegelogd in InspectionAnswer.question_version voor audit-trail.

QUESTIONS_VERSION = "kw-vragen.v1.1-2026-05-bomen"

GENERIEKE_VRAGEN = [
    {
        "code": "GEN.STAAT",
        "vraag": "Algemene visuele staat van dit element",
        "uitleg": ("Beoordeel de algehele conditie op basis van zichtbare gebreken. "
                   "Volgens NEN 2767-2 hangt de conditie af van ernst × intensiteit × omvang "
                   "van de aangetroffen gebreken."),
        "norm_ref": "NEN 2767-2 § 5",
        "type": "score_1_6",
        "verplicht": True,
    },
    {
        "code": "GEN.VEILIG",
        "vraag": "Veiligheidsrisico geconstateerd?",
        "uitleg": ("Een veiligheidsrisico betekent direct gevaar voor gebruikers of "
                   "constructie-integriteit. Bij 'ja': toelichting verplicht en aanbeveling "
                   "voor acute maatregel."),
        "norm_ref": "CROW 134 § 3.4",
        "type": "ja_nee",
        "verplicht": True,
        "attention_when": True,  # 'ja' = aandacht
    },
    {
        "code": "GEN.INSPECTEERBAARHEID",
        "vraag": "Was inspectie van dit element volledig mogelijk?",
        "uitleg": ("Volledig = alle relevante oppervlakken zichtbaar/toegankelijk. "
                   "Gedeeltelijk = deel niet toegankelijk (vermeld reden). "
                   "Niet inspecteerbaar = geen visuele beoordeling mogelijk."),
        "norm_ref": "NEN 2767-2 § 6.1",
        "type": "keuze",
        "opties": ["volledig", "gedeeltelijk", "niet"],
        "verplicht": True,
    },
    {
        "code": "GEN.URGENTIE",
        "vraag": "Onderhouds-urgentie advies",
        "uitleg": ("Geen = standaard her-inspectie. Regulier = klein/groot onderhoud "
                   "binnen normale termijn. Verkort = her-inspectie binnen 6-12 maanden. "
                   "Acuut = directe maatregel."),
        "norm_ref": "CROW 134 maatregelmatrix",
        "type": "keuze",
        "opties": ["geen", "regulier", "verkort", "acuut"],
        "verplicht": False,
    },
]

VRAGEN_PER_GROEP = {
    "constructief": [
        {
            "code": "CONSTR.SCHEUR",
            "vraag": "Scheuren > 0,2 mm zichtbaar in het hoofdmateriaal?",
            "uitleg": ("Scheuren > 0,2 mm in beton zijn een mogelijke indicator voor "
                       "constructieve overbelasting of wapeningscorrosie. Meet met "
                       "scheurlineaal of vergrootglas."),
            "norm_ref": "NEN 2767-2 § 6.2.1 · CROW 134 tabel 4.2",
            "type": "ja_nee",
            "attention_when": True,
        },
        {
            "code": "CONSTR.WAPENING",
            "vraag": "Wapeningscorrosie of dekkingstekort zichtbaar?",
            "uitleg": ("Bruinrode strepen, opbol-vorming of afgespatte beton met zichtbare "
                       "wapening. Cruciaal voor restlevensduur."),
            "norm_ref": "NEN 2767-2 § 6.2.3",
            "type": "ja_nee",
            "attention_when": True,
        },
        {
            "code": "CONSTR.VERVORM",
            "vraag": "Afwijkende vervorming of scheefstand?",
            "uitleg": ("Doorbuiging, scheefstand of zetting > 1% van element-afmeting. "
                       "Vergelijk met as-built tekeningen indien beschikbaar."),
            "norm_ref": "CROW 134 § 4.3",
            "type": "ja_nee",
            "attention_when": True,
        },
    ],
    "afwerking": [
        {
            "code": "AFW.SLIJT",
            "vraag": "Geschatte slijtage (% van element-oppervlak)",
            "uitleg": ("Zichtbare slijtage zoals rafeling, kleurverlies, geslepen randen. "
                       "Schat het percentage van het totale oppervlak."),
            "norm_ref": "NEN 2767-2 § 6.3",
            "type": "meting",
            "eenheid": "%",
        },
        {
            "code": "AFW.BESCHADIG",
            "vraag": "Beschadigingen door aanvaring/vandalisme?",
            "uitleg": ("Deuken, bramen, graffiti of doelbewuste beschadiging. Meld "
                       "criminaliteit ook bij gemeente."),
            "norm_ref": "CROW 134 § 4.5",
            "type": "ja_nee",
        },
    ],
    "installatie": [
        {
            "code": "INST.FUNC",
            "vraag": "Werkt de installatie naar behoren tijdens inspectie?",
            "uitleg": ("Test de functie ter plekke indien mogelijk en veilig: pomp "
                       "draait, verlichting brandt, mechanisme beweegt soepel."),
            "norm_ref": "CROW 134 § 5",
            "type": "ja_nee_nvt",
            "attention_when": False,  # 'nee' = aandacht
        },
        {
            "code": "INST.STORING",
            "vraag": "Storingsmeldingen of foutcodes zichtbaar?",
            "uitleg": ("Controleer indicatielampjes, SCADA-display of foutenlog. "
                       "Vermeld foutcode in toelichting."),
            "norm_ref": "CROW 134 § 5.2",
            "type": "ja_nee",
            "attention_when": True,
        },
        {
            "code": "INST.ONDERHOUD",
            "vraag": "Datum laatste preventieve onderhoudsbeurt bekend?",
            "uitleg": ("Vraag uit serviceregister, plakker op apparatuur of "
                       "beheerder. Vermeld datum in toelichting (yyyy-mm)."),
            "norm_ref": "CROW 134 § 5.4",
            "type": "tekst",
        },
    ],
    "omgeving": [
        {
            "code": "OMG.EROSIE",
            "vraag": "Erosie of ondermijning rond element zichtbaar?",
            "uitleg": ("Uitgespoelde grond, holtes onder fundering, blootgelegde "
                       "wapening. Risico voor stabiliteit."),
            "norm_ref": "NEN 2767-2 § 6.4",
            "type": "ja_nee",
            "attention_when": True,
        },
        {
            "code": "OMG.BEGROEIING",
            "vraag": "Schadelijke begroeiing met wortelschade?",
            "uitleg": ("Wortels in voegen, klimop op constructie, invasieve exoten "
                       "(Japanse duizendknoop, reuzenberenklauw)."),
            "norm_ref": "CROW 134 § 4.7",
            "type": "ja_nee",
        },
    ],
    "speel_constructief": [
        # Voor speeltoestellen — focus op draagkracht + bewegende delen
        {
            "code": "SPEEL.STABIEL",
            "vraag": "Toestel mechanisch stabiel onder belasting?",
            "uitleg": ("Test door duwen/schudden: geen onverwachte beweging, "
                       "geen kraakgeluiden bij belasting. Volgens NEN-EN 1176 § 7."),
            "norm_ref": "NEN-EN 1176-1 § 7",
            "type": "ja_nee",
            "attention_when": False,  # 'nee' = aandacht
        },
        {
            "code": "SPEEL.BOUTAANTREKKEN",
            "vraag": "Alle zichtbare bouten/klemmen vast aangedraaid?",
            "uitleg": ("Test met sleutel: geen losse bouten of klemmen. "
                       "Vaste verbindingen zijn cruciaal voor structurele integriteit."),
            "norm_ref": "NEN-EN 1176-7",
            "type": "ja_nee",
            "attention_when": True,
        },
    ],
    "speel_veiligheid": [
        # Specifiek voor knelpunten + valhoogte
        {
            "code": "SPEEL.KNELPUNT_GETEST",
            "vraag": "Knelpunt-test (hoofd Ø 8.6cm, vinger Ø 8mm) uitgevoerd?",
            "uitleg": ("NEN-EN 1176 vereist test met meetringen: hoofd-knelpunten "
                       "8.6 cm, vinger-/teen-knelpunten 8 mm. Bij doorlopen = aandacht."),
            "norm_ref": "NEN-EN 1176-1 § 4.2.7",
            "type": "ja_nee",
            "attention_when": False,
        },
        {
            "code": "SPEEL.VALHOOGTE",
            "vraag": "Vrije valhoogte (m)",
            "uitleg": ("Vrije valhoogte van hoogste klimpositie tot ondergrond. "
                       "Volgens NEN-EN 1177 bepaalt dit minimaal ondergrondstype + dikte."),
            "norm_ref": "NEN-EN 1177",
            "type": "meting",
            "eenheid": "m",
        },
    ],
    "ov_elektrisch": [
        # Voor openbare verlichting — NEN 3140 + NEN 1010
        {
            "code": "OV.AARDING_OK",
            "vraag": "Aardingsweerstand binnen norm (< 100Ω)?",
            "uitleg": ("Gemeten aardingsweerstand moet onder 100 Ω blijven volgens "
                       "NEN 1010. Boven 100Ω = veiligheidsrisico bij kortsluiting."),
            "norm_ref": "NEN 1010 + NEN 3140",
            "type": "ja_nee",
            "attention_when": False,  # 'nee' = aandacht
        },
        {
            "code": "OV.ISOLATIE_OK",
            "vraag": "Isolatie-weerstand boven 0.5 MΩ?",
            "uitleg": ("Isolatiemeting met 500V tester. Onder 0.5 MΩ = vochtindringing "
                       "of beschadiging — risico kortsluiting en doorslag."),
            "norm_ref": "NEN 3140 § 6.4",
            "type": "ja_nee",
            "attention_when": False,
        },
        {
            "code": "OV.AARDLEK_TEST",
            "vraag": "Aardlek-schakelaar werkt (test-knop)?",
            "uitleg": ("Functietest aardlek: drukken op test-knop moet circuit "
                       "binnen 30ms uitschakelen. Werkt niet = direct vervangen."),
            "norm_ref": "NEN 3140",
            "type": "ja_nee",
            "attention_when": False,
        },
    ],
    "vegetatief": [
        # Voor bomen — vervangt 'constructief' om beton-specifieke vragen
        # (wapeningscorrosie etc.) te vermijden. VTA-conform.
        {
            "code": "VEG.SCHEUR",
            "vraag": "Zichtbare scheurvorming in het houtweefsel?",
            "uitleg": ("Langs- of dwarsscheuren in stam, takken of wortelaanloop. "
                       "Bij twijfel: meet breedte met scheurliniaal. Scheuren > 5 mm "
                       "of door de bast heen verdienen aandacht."),
            "norm_ref": "VTA + NTS",
            "type": "ja_nee",
            "attention_when": True,
        },
        {
            "code": "VEG.SCHEEF",
            "vraag": "Afwijkende scheefstand of kanteling?",
            "uitleg": ("Boomstam wijkt > 5° af van verticaal, of recent toegenomen "
                       "scheefstand. Vergelijk met eerdere inspectie-fotos indien "
                       "beschikbaar. Windworp-risico."),
            "norm_ref": "Mattheck statica",
            "type": "ja_nee",
            "attention_when": True,
        },
        {
            "code": "VEG.MECHANISCH",
            "vraag": "Recente mechanische beschadiging of breuk?",
            "uitleg": ("Verse schade door storm, bouwverkeer of vandalisme. Bij ja: "
                       "infectie- en breuk-risico verhoogd, ook al lijkt boom OK."),
            "norm_ref": "VTA § 4.4",
            "type": "ja_nee",
            "attention_when": False,
        },
    ],
}

# Element-specifieke vragen waar de norm extra detail vraagt.
# Niet voor elk element — alleen waar nuttig.
VRAGEN_PER_ELEMENT = {
    "BRUG.VOEGOVERGANGEN": [
        {
            "code": "VOEG.LEK",
            "vraag": "Lekkage onder de voeg zichtbaar?",
            "uitleg": ("Druppels, vochtplekken of kalkstrepen onder de voegovergang. "
                       "Indicator voor falende afdichting."),
            "norm_ref": "CROW 134 § 4.3.2",
            "type": "ja_nee",
            "attention_when": True,
        },
        {
            "code": "VOEG.GELUID",
            "vraag": "Afwijkend geluid bij overrijden?",
            "uitleg": ("Bonk, ratel of klap-geluid indicatief voor loszittende "
                       "ankers of versleten profiel."),
            "norm_ref": "CROW 134 § 4.3.3",
            "type": "ja_nee",
        },
    ],
    "BRUG.OPLEGGINGEN": [
        {
            "code": "OPL.STAND",
            "vraag": "Oplegging in correcte stand (binnen toleranties)?",
            "uitleg": ("Visuele controle op kantelen, scheef staan of overmatige "
                       "horizontale verschuiving."),
            "norm_ref": "CROW 134 § 4.4.1",
            "type": "ja_nee_nvt",
            "attention_when": False,
        },
    ],
    "TUNNEL.VENTILATIE": [
        {
            "code": "VENT.METING",
            "vraag": "Luchtsnelheid binnen norm (m/s)?",
            "uitleg": ("Meet bij tunnel-uitstroom met anemometer. Norm-waarde "
                       "afhankelijk van tunnel-lengte en -type."),
            "norm_ref": "RWS richtlijn tunnelveiligheid",
            "type": "meting",
            "eenheid": "m/s",
        },
    ],
    "TUNNEL.BRANDVEILIGHEID": [
        {
            "code": "BRAND.BLUSSER",
            "vraag": "Alle blussers aanwezig en niet verlopen?",
            "uitleg": ("Tel blussers vs. plattegrond. Check sticker met "
                       "keuringsdatum (geldig 1 jaar)."),
            "norm_ref": "NEN 4001 + Bouwbesluit",
            "type": "ja_nee",
            "attention_when": False,
        },
        {
            "code": "BRAND.VLUCHT",
            "vraag": "Vluchtdeuren werken probleemloos?",
            "uitleg": ("Open elke deur 1 keer. Controleer panic-bar, sluiter en "
                       "afdichting."),
            "norm_ref": "Bouwbesluit + ARBO",
            "type": "ja_nee_nvt",
            "attention_when": False,
        },
    ],
    "SLUIS.DEUREN": [
        {
            "code": "SLUIS.LEK",
            "vraag": "Lekkage tussen deurhelften? (l/min)",
            "uitleg": ("Meet bij gesloten deuren tijdens hoogwater. > 5 l/min = "
                       "aandacht (versleten rubber)."),
            "norm_ref": "RWS sluis-richtlijn",
            "type": "meting",
            "eenheid": "l/min",
        },
    ],
    "SLUIS.BEWEGINGSWERK": [
        {
            "code": "MECH.GELUID",
            "vraag": "Afwijkend mechanisch geluid tijdens bewegen?",
            "uitleg": ("Knarsen, piepen, dreunen — indicatie versleten lager of "
                       "tandwerk."),
            "norm_ref": "CROW 134 § 5.1",
            "type": "ja_nee",
            "attention_when": True,
        },
    ],
    "DUIKER.LICHAAM": [
        {
            "code": "DUIK.DOORSTROOM",
            "vraag": "Doorstroom gehinderd (slib, takken, obstakels)?",
            "uitleg": ("Visueel ingang+uitgang, voel stroming. Bij > 30% blokkering "
                       "ontstaat opstuwing benedenstrooms."),
            "norm_ref": "CROW 134 § 4.8",
            "type": "ja_nee",
            "attention_when": True,
        },
    ],
    "KADE.MUUR": [
        {
            "code": "KADE.UITBUIG",
            "vraag": "Horizontale uitbuiging > 50 mm?",
            "uitleg": ("Meet ten opzichte van as-built lijn of buurliggend deel. "
                       "> 50 mm op 10m = constructief risico."),
            "norm_ref": "NEN 9997 + CROW 134",
            "type": "meting",
            "eenheid": "mm",
        },
    ],
    "RIO.VRIJVERVAL_LEIDING": [
        {
            "code": "RIO.METHODE",
            "vraag": "Welke inspectiemethode is toegepast?",
            "uitleg": ("Visueel = uit-de-put bekeken. Camera = TV-inspectie volgens "
                       "NEN-EN 13508-2. Drone = robot-camera bij grotere diameter (DN 800+)."),
            "norm_ref": "NEN-EN 13508-2 + NEN 3399",
            "type": "keuze",
            "opties": ["visueel", "camera", "drone"],
            "verplicht": True,
        },
        {
            "code": "RIO.STREFKLASSE",
            "vraag": "Eindklasse strengtoestand (1-5)",
            "uitleg": ("NEN 3399 strengtoestand-klasse: 1 = uitstekend, "
                       "5 = vervangen op korte termijn. Volgt uit ernstigste "
                       "schade × intensiteit × omvang in de streng."),
            "norm_ref": "NEN 3399 § 5",
            "type": "score_1_6",
        },
        {
            "code": "RIO.VULGRAAD",
            "vraag": "Geschatte vulgraad / slib-afzetting (%)",
            "uitleg": ("Percentage van diameter dat door slib of afzetting "
                       "is verminderd. > 30% = reinigingsadvies."),
            "norm_ref": "NEN 3399 § 6.3",
            "type": "meting",
            "eenheid": "%",
        },
    ],
    "RIO.INSPECTIEPUT": [
        {
            "code": "RIO.PUT_BEREIK",
            "vraag": "Inspectieput bereikbaar volgens richtlijn?",
            "uitleg": ("Toegankelijkheid volgens ARBO / Veilig werken in besloten "
                       "ruimte. Bij 'nee': toelichting waarom (te diep, gevaarlijke "
                       "gassen, geblokkeerd deksel)."),
            "norm_ref": "NEN 3399 § 4 + ARBO",
            "type": "ja_nee",
            "attention_when": False,  # 'nee' = aandacht
        },
    ],
    "RIO.GEMAAL": [
        {
            "code": "RIO.POMP_RENDEMENT",
            "vraag": "Pomp-rendement binnen specificatie?",
            "uitleg": ("Gemeten doorvoer en stroom-opname vergelijken met "
                       "originele specificatie. Afwijking > 15% = aandacht."),
            "norm_ref": "Fabrikanten-spec + NEN 3399",
            "type": "ja_nee_nvt",
            "attention_when": False,
        },
        {
            "code": "RIO.NOODOVERSTORT",
            "vraag": "Noodoverstort getest binnen laatste 12 maanden?",
            "uitleg": ("Wettelijke functietest noodoverstort bij wateroverlast. "
                       "Niet getest = aandacht (vooral voor regenwater-gemalen)."),
            "norm_ref": "Waterwet + NEN 3399",
            "type": "ja_nee",
            "attention_when": False,
        },
    ],
    "RIO.OVERSTORT": [
        {
            "code": "RIO.OVERSTORT_FREQ",
            "vraag": "Aantal overstort-gebeurtenissen afgelopen 12 mnd",
            "uitleg": ("Telling van overstortingen op basis van logging of "
                       "hoogwater-markeerder. > 7 = aandacht (omgevings­impact)."),
            "norm_ref": "Waterwet + KRW",
            "type": "meting",
            "eenheid": "x/jaar",
        },
    ],
    "RIO.KOLK": [
        {
            "code": "RIO.KOLK_REINIGING",
            "vraag": "Datum laatste reiniging bekend?",
            "uitleg": ("Reiniging-frequentie standaard 1× per jaar. "
                       "Onbekend of > 18 mnd geleden = aandacht."),
            "norm_ref": "Gemeentelijk reinigingsplan",
            "type": "tekst",
        },
    ],
    # ─────────────── VTA Bomeninspectie ───────────────
    # VTA-classificatie volgens Mattheck + CROW 200. De inspecteur beoordeelt
    # per element op faalkans en schadepotentieel. Risicoklasse 1-5 wordt
    # via de NEN-schaal opgeslagen als score 1-6 (vta1=1, vta2=2, vta3=3,
    # vta4=5, vta5=6 — geen score 4 voor bomen, springt van regulier naar
    # acuut omdat boomdegradatie niet-lineair is).
    "BOOM.STAM": [
        {
            "code": "VTA.STAM_HOLTE_PCT",
            "vraag": "Geschat percentage holte/rotting in stamdoorsnede",
            "uitleg": ("VTA-vuistregel: bij > 70% holte resterend wandhout < t/r=0,3 "
                       "(Mattheck) — verhoogd breukrisico. Meet of schat percentage "
                       "van de doorsnede dat verloren is."),
            "norm_ref": "Mattheck VTA — t/r criterium",
            "type": "meting",
            "eenheid": "%",
        },
        {
            "code": "VTA.STAM_ZWAM",
            "vraag": "Houtaantastende zwam aanwezig?",
            "uitleg": ("Vooral hardhout-zwammen (honingzwam, reuzenzwam, tonderzwam) "
                       "verzwakken het houtweefsel. Soort + hoeveelheid noteren in "
                       "toelichting voor specialist."),
            "norm_ref": "VTA + NTS soortlijst",
            "type": "ja_nee",
            "attention_when": True,
        },
        {
            "code": "VTA.STAM_KLOPTEST",
            "vraag": "Klop-test: holle klank zonder visuele holte?",
            "uitleg": ("Verborgen holte indicator. Bij ja: aanvullend onderzoek "
                       "(boorweerstand of geluidstomografie) plannen."),
            "norm_ref": "VTA § 4.2",
            "type": "ja_nee",
            "attention_when": True,
        },
    ],
    "BOOM.WORTELAANLOOP": [
        {
            "code": "VTA.WORTEL_VERSTIKKING",
            "vraag": "Verstikkende verharding/bouwwerk binnen 1m van stam?",
            "uitleg": ("Asfalt, beton of fundering binnen 1m van de stam beperkt "
                       "wortelgroei en is hoog-risico standplaats."),
            "norm_ref": "CROW 200 § 3.4",
            "type": "ja_nee",
            "attention_when": True,
        },
        {
            "code": "VTA.WORTEL_OPSLAG",
            "vraag": "Schimmel-vruchtlichaam op of bij stamvoet?",
            "uitleg": ("Indicator van wortelrotting. Bij paddenstoel-cluster of "
                       "ophoping fruitlichamen: directe specialist-keuring nodig."),
            "norm_ref": "VTA + NTS",
            "type": "ja_nee",
            "attention_when": True,
        },
        {
            "code": "VTA.WORTEL_AFWIJKING",
            "vraag": "Wortelaanloop asymmetrisch / kantelt boom?",
            "uitleg": ("Symptomen: ene zijde uitgehold, andere zijde verheven, of "
                       "kantelhoek > 5°. Hoog windworp-risico."),
            "norm_ref": "Mattheck statica",
            "type": "ja_nee",
            "attention_when": True,
        },
    ],
    "BOOM.HOOFDTAKKEN": [
        {
            "code": "VTA.PLAKOKSEL",
            "vraag": "Plakoksel met ingegroeide bast aanwezig?",
            "uitleg": ("Twee gesteltakken die samenwassen met bast tussen — "
                       "verzwakt aanhechtingspunt. Bij diameter > 100mm tak: "
                       "preventief snoeien of stutten."),
            "norm_ref": "VTA — Mattheck",
            "type": "ja_nee",
            "attention_when": True,
        },
        {
            "code": "VTA.DODE_TAK_BOVEN_DOEL",
            "vraag": "Dode tak (Ø > 50mm) boven verkeer/wandelroute?",
            "uitleg": ("Combinatie van dode tak + onder-doel = directe veiligheid. "
                       "Tak ≥ 50mm met val van ≥ 3m kan dodelijk letsel veroorzaken."),
            "norm_ref": "Zorgplicht artikel 6:174 BW",
            "type": "ja_nee",
            "attention_when": True,
        },
        {
            "code": "VTA.HANGENDE_TAK",
            "vraag": "Loszittende of hangende tak boven publiek gebied?",
            "uitleg": ("Bij ja: directe afzetting + spoedreparatie. Veiligheids-"
                       "voorrang boven plan-onderhoud."),
            "norm_ref": "Zorgplicht artikel 6:174 BW",
            "type": "ja_nee",
            "attention_when": True,
        },
    ],
    "BOOM.KROON": [
        {
            "code": "VTA.DOOD_HOUT_PCT",
            "vraag": "Percentage dood hout in kroon",
            "uitleg": ("Visuele schatting kroonvolume. <10% = normaal, 10-25% = "
                       "aandacht, >25% = vitaliteitsprobleem (mogelijk wortel-issue)."),
            "norm_ref": "NTS kroonklasse",
            "type": "meting",
            "eenheid": "%",
        },
        {
            "code": "VTA.VITALITEIT",
            "vraag": "Vitaliteitsklasse volgens NTS",
            "uitleg": ("Klasse A = goed (sterke jaarscheuten), B = redelijk, "
                       "C = afnemend, D = slecht (kandidaat-rooien)."),
            "norm_ref": "NTS § 2.3",
            "type": "keuze",
            "opties": ["A", "B", "C", "D"],
        },
        {
            "code": "VTA.TOP_UITVAL",
            "vraag": "Top-uitval / topsterven zichtbaar?",
            "uitleg": ("Sterven van de bovenste meter(s) — vaak vroege indicator "
                       "van wortelschade of langdurige droogtestress."),
            "norm_ref": "NTS § 3.1",
            "type": "ja_nee",
            "attention_when": True,
        },
    ],
    "MARK.LANGS": [
        {
            "code": "MARK.RETROREFLECTIE",
            "vraag": "Retroreflectie-meting in mcd/m²/lx (droog)",
            "uitleg": ("Conform CROW 145: nieuwe markering ≥ 150 mcd, "
                       "vervangen bij < 80 mcd."),
            "norm_ref": "CROW 145 § 3.2",
            "type": "meting",
            "eenheid": "mcd",
        },
        {
            "code": "MARK.NACHT_ZICHTBAAR",
            "vraag": "Zichtbaarheid bij dimlicht acceptabel?",
            "uitleg": ("Visuele check 's nachts of bij regen. Bij 'nee': vervangen plannen."),
            "norm_ref": "CROW 145",
            "type": "ja_nee",
            "attention_when": False,
        },
    ],
    "FONT.POMP": [
        {
            "code": "FONT.DRUK",
            "vraag": "Pomp-werkdruk binnen specificatie?",
            "uitleg": "Vergelijk met fabrikants-spec. Afwijking > 15% = aandacht.",
            "norm_ref": "NEN 2767-4 + fabrikant",
            "type": "ja_nee_nvt",
        },
    ],
    "KGRAS.MAT": [
        {
            "code": "KGRAS.SPORTKEUR",
            "vraag": "Voldoet aan NOC*NSF sportveldnorm?",
            "uitleg": "Voor competitievelden verplicht. Periodieke sportkeuring nodig.",
            "norm_ref": "NOC*NSF + NEN 2767-4",
            "type": "ja_nee_nvt",
        },
    ],
    "BOOM.STANDPLAATS": [
        {
            "code": "VTA.STANDPLAATS_TYPE",
            "vraag": "Type standplaats / doel-risico",
            "uitleg": ("Bepaalt het schadepotentieel bij falen. Drukke verkeerszone "
                       "of fietspad = hoog. Park = midden. Wei = laag."),
            "norm_ref": "CROW 200 risicoclassificatie",
            "type": "keuze",
            "opties": ["verkeer", "fietspad", "voetganger", "park", "wei", "afgelegen"],
            "verplicht": True,
        },
        {
            "code": "VTA.AANRIJD",
            "vraag": "Aanrijdschade / mechanische beschadiging recent?",
            "uitleg": ("Verse stam-beschadiging door bouwverkeer/auto's. Bij ja: "
                       "infectie-risico verhoogd, ook al lijkt boom OK."),
            "norm_ref": "VTA",
            "type": "ja_nee",
            "attention_when": False,
        },
        {
            "code": "VTA.DBH",
            "vraag": "Stamdiameter op 1.30 m (DBH in cm)",
            "uitleg": ("Stamdiameter op borsthoogte — standaard inventarisatie-"
                       "parameter. Nodig voor risico-berekening en herinventarisatie."),
            "norm_ref": "NTS / NEN",
            "type": "meting",
            "eenheid": "cm",
        },
    ],
}


def vragen_voor(kunstwerk_type: Optional[str], element_code: str,
                element_groep: Optional[str] = None) -> list[dict]:
    """Bouw de volledige vragenlijst voor één element.

    Combineert generieke vragen + groep-vragen + element-specifieke vragen.
    Als `element_groep` ontbreekt wordt 't opgezocht in de bibliotheek.

    >>> qs = vragen_voor("brug", "BRUG.ONDERBOUW")
    >>> any(q["code"] == "GEN.STAAT" for q in qs)
    True
    >>> any(q["code"] == "CONSTR.SCHEUR" for q in qs)
    True
    """
    # Vind element_groep als die niet meegegeven is
    if not element_groep and kunstwerk_type:
        for e in elementen_voor(kunstwerk_type):
            if e["code"] == element_code:
                element_groep = e.get("groep")
                break
    out = list(GENERIEKE_VRAGEN)
    if element_groep and element_groep in VRAGEN_PER_GROEP:
        out += VRAGEN_PER_GROEP[element_groep]
    if element_code in VRAGEN_PER_ELEMENT:
        out += VRAGEN_PER_ELEMENT[element_code]
    return out


def alle_question_codes() -> set[str]:
    codes = set()
    for q in GENERIEKE_VRAGEN: codes.add(q["code"])
    for qs in VRAGEN_PER_GROEP.values():
        for q in qs: codes.add(q["code"])
    for qs in VRAGEN_PER_ELEMENT.values():
        for q in qs: codes.add(q["code"])
    return codes
