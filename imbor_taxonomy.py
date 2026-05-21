"""IMBOR-taxonomie voor FieldOps asset-types.

IMBOR (Informatiemodel Beheer Openbare Ruimte) is de Nederlandse standaard
van CROW (publicatie 1011/IMBOR 2024) voor classificatie van objecten in
de openbare ruimte. Gemeenten gebruiken dit voor uniforme registratie
tussen systemen (GBI World, iAsset, Antea, BGT, etc.).

Deze module is een MVP-implementatie van de meest gebruikte IMBOR-types
(±100 asset-types over 11 hoofdgroepen). Volledige taxonomie heeft ~400
types — uitbreidbaar op basis van klant-vraag.

Structuur per asset-type:
  - code: stabiel IMBOR-conform code (gebruikt in DB)
  - naam: NL display-naam
  - hoofdgroep: bovenliggende categorie
  - default_inspection_norm: NEN-EN 1176 / NEN 3140 / CROW 134 / CROW 146 / VTA
  - default_inspection_frequency_months: typische cycle
  - typical_properties: lijst van extra velden die voor dit type relevant zijn
  - bgt_class: koppeling naar BGT-classificatie (optioneel)

Gebruik:
    from imbor_taxonomy import TAXONOMY, get_type_info, list_types
    info = get_type_info("lichtmast")
    all_wegen = [t for t in list_types() if t["hoofdgroep"] == "wegen"]
"""
from __future__ import annotations
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Hoofdgroepen — top-level IMBOR-categorisatie
# ─────────────────────────────────────────────────────────────────────────────
HOOFDGROEPEN = [
    {"code": "wegen",           "naam": "Wegen + verhardingen", "icon": "🛣️"},
    {"code": "kunstwerken",     "naam": "Civiele kunstwerken", "icon": "🌉"},
    {"code": "riolering",       "naam": "Riolering + ondergronds", "icon": "🚰"},
    {"code": "verlichting",     "naam": "Verlichting + VRI", "icon": "💡"},
    {"code": "groen",           "naam": "Groenvoorzieningen", "icon": "🌳"},
    {"code": "speel_sport",     "naam": "Speel + sport", "icon": "🎠"},
    {"code": "meubilair",       "naam": "Straatmeubilair", "icon": "🪑"},
    {"code": "bewegwijzering",  "naam": "Bewegwijzering + markering", "icon": "🚧"},
    {"code": "water",           "naam": "Watergangen + oevers", "icon": "💧"},
    {"code": "gebouwen",        "naam": "Gebouwen + opstallen", "icon": "🏛️"},
    {"code": "overig",          "naam": "Overige / Specifiek", "icon": "📌"},
]


# ─────────────────────────────────────────────────────────────────────────────
# Asset-types — IMBOR-conform classificatie met defaults
# ─────────────────────────────────────────────────────────────────────────────
TAXONOMY: list[dict] = [
    # ── WEGEN ──
    {"code": "wegvak_asfalt",       "naam": "Wegvak (asfaltverharding)", "hoofdgroep": "wegen",
     "default_inspection_norm": "CROW 146a", "default_inspection_frequency_months": 24,
     "typical_properties": ["lengte_m", "breedte_m", "verhardingsbreedte_m", "deklaagtype", "bouwjaar"],
     "bgt_class": "wegdeel"},
    {"code": "wegvak_elementen",    "naam": "Wegvak (elementen-verharding)", "hoofdgroep": "wegen",
     "default_inspection_norm": "CROW 146b", "default_inspection_frequency_months": 24,
     "typical_properties": ["lengte_m", "breedte_m", "elementtype", "voegmateriaal", "bouwjaar"],
     "bgt_class": "wegdeel"},
    {"code": "fietspad",            "naam": "Fietspad", "hoofdgroep": "wegen",
     "default_inspection_norm": "CROW 146a", "default_inspection_frequency_months": 24,
     "typical_properties": ["lengte_m", "breedte_m", "deklaagtype"]},
    {"code": "trottoir",            "naam": "Trottoir / voetpad", "hoofdgroep": "wegen",
     "default_inspection_norm": "CROW 146b", "default_inspection_frequency_months": 36,
     "typical_properties": ["lengte_m", "breedte_m", "elementtype"]},
    {"code": "parkeervlak",         "naam": "Parkeervlak", "hoofdgroep": "wegen",
     "default_inspection_norm": "CROW 146", "default_inspection_frequency_months": 36,
     "typical_properties": ["aantal_plaatsen", "oppervlak_m2", "verhardingstype"]},
    {"code": "rotonde",             "naam": "Rotonde", "hoofdgroep": "wegen",
     "default_inspection_norm": "CROW 146a", "default_inspection_frequency_months": 24,
     "typical_properties": ["diameter_m", "aantal_takken"]},
    {"code": "verkeersdrempel",     "naam": "Verkeersdrempel / plateau", "hoofdgroep": "wegen",
     "default_inspection_norm": "CROW 146", "default_inspection_frequency_months": 12,
     "typical_properties": ["type", "lengte_m", "hoogte_cm"]},

    # ── KUNSTWERKEN ──
    {"code": "brug_vast",           "naam": "Vaste brug", "hoofdgroep": "kunstwerken",
     "default_inspection_norm": "NEN 2767-2 + CROW 134", "default_inspection_frequency_months": 36,
     "typical_properties": ["lengte_m", "breedte_m", "draagvermogen_ton", "constructietype", "bouwjaar"]},
    {"code": "brug_beweegbaar",     "naam": "Beweegbare brug (basculebrug/draaibrug)", "hoofdgroep": "kunstwerken",
     "default_inspection_norm": "NEN 2767-2", "default_inspection_frequency_months": 24,
     "typical_properties": ["type_bediening", "vrije_doorvaarthoogte_m", "bedrijfsuren"]},
    {"code": "viaduct",             "naam": "Viaduct", "hoofdgroep": "kunstwerken",
     "default_inspection_norm": "NEN 2767-2", "default_inspection_frequency_months": 36,
     "typical_properties": ["lengte_m", "doorrijhoogte_m", "constructietype"]},
    {"code": "tunnel",              "naam": "Tunnel", "hoofdgroep": "kunstwerken",
     "default_inspection_norm": "NEN 2767-2", "default_inspection_frequency_months": 24,
     "typical_properties": ["lengte_m", "rijstroken", "ventilatie_type"]},
    {"code": "sluis",               "naam": "Sluis / stuw", "hoofdgroep": "kunstwerken",
     "default_inspection_norm": "NEN 2767-2", "default_inspection_frequency_months": 24,
     "typical_properties": ["doorvaarthoogte_m", "verval_m", "schut_capaciteit"]},
    {"code": "kademuur",            "naam": "Kademuur", "hoofdgroep": "kunstwerken",
     "default_inspection_norm": "NEN 2767-2", "default_inspection_frequency_months": 60,
     "typical_properties": ["lengte_m", "hoogte_m", "materiaal"]},
    {"code": "duiker",              "naam": "Duiker", "hoofdgroep": "kunstwerken",
     "default_inspection_norm": "NEN 2767-2 + NEN 3399", "default_inspection_frequency_months": 60,
     "typical_properties": ["lengte_m", "diameter_mm", "materiaal"]},
    {"code": "geluidscherm",        "naam": "Geluidscherm", "hoofdgroep": "kunstwerken",
     "default_inspection_norm": "NEN 2767-2", "default_inspection_frequency_months": 60,
     "typical_properties": ["lengte_m", "hoogte_m", "materiaal"]},

    # ── RIOLERING ──
    {"code": "riool_vrijverval",    "naam": "Vrijverval-riool (DWA/RWA)", "hoofdgroep": "riolering",
     "default_inspection_norm": "NEN 3399", "default_inspection_frequency_months": 120,
     "typical_properties": ["diameter_mm", "lengte_m", "materiaal", "verhang", "bouwjaar"]},
    {"code": "persleiding",         "naam": "Persleiding", "hoofdgroep": "riolering",
     "default_inspection_norm": "NEN 3399", "default_inspection_frequency_months": 120,
     "typical_properties": ["diameter_mm", "lengte_m", "materiaal", "druk_bar"]},
    {"code": "put_riool",           "naam": "Rioolput / inspectieput", "hoofdgroep": "riolering",
     "default_inspection_norm": "NEN 3399", "default_inspection_frequency_months": 60,
     "typical_properties": ["diepte_m", "diameter_mm", "deksel_type"]},
    {"code": "kolk",                "naam": "Straatkolk", "hoofdgroep": "riolering",
     "default_inspection_norm": "NEN 3399", "default_inspection_frequency_months": 24,
     "typical_properties": ["aansluitwijze", "diameter_aansluiting_mm"]},
    {"code": "drainage",            "naam": "Drainage-leiding", "hoofdgroep": "riolering",
     "default_inspection_norm": "NEN 3399", "default_inspection_frequency_months": 60,
     "typical_properties": ["lengte_m", "diameter_mm", "filter_type"]},
    {"code": "gemaal_riool",        "naam": "Rioolgemaal / opvoerbron", "hoofdgroep": "riolering",
     "default_inspection_norm": "NEN 3399", "default_inspection_frequency_months": 12,
     "typical_properties": ["pompcapaciteit_l_s", "aantal_pompen", "vermogen_kw"]},

    # ── VERLICHTING ──
    {"code": "lichtmast",           "naam": "Lichtmast (openbare verlichting)", "hoofdgroep": "verlichting",
     "default_inspection_norm": "NEN 3140", "default_inspection_frequency_months": 12,
     "typical_properties": ["masthoogte_m", "materiaal", "armatuur_type", "lamp_type", "vermogen_w"]},
    {"code": "armatuur",            "naam": "Armatuur (los van mast)", "hoofdgroep": "verlichting",
     "default_inspection_norm": "NEN 3140", "default_inspection_frequency_months": 12,
     "typical_properties": ["type", "lamp_type", "vermogen_w", "leeftijd_jaar"]},
    {"code": "verkeerslicht",       "naam": "Verkeerslicht (VRI)", "hoofdgroep": "verlichting",
     "default_inspection_norm": "NEN 3140 + CROW 161", "default_inspection_frequency_months": 6,
     "typical_properties": ["aantal_richtingen", "type_lampen", "regelaar_type"]},
    {"code": "kast_elektra",        "naam": "Elektrakast / verdeelinrichting", "hoofdgroep": "verlichting",
     "default_inspection_norm": "NEN 3140 + NEN 1010", "default_inspection_frequency_months": 60,
     "typical_properties": ["nominale_stroom_a", "ip_klasse", "aantal_groepen"]},

    # ── GROEN ──
    {"code": "boom_laan",           "naam": "Laanboom", "hoofdgroep": "groen",
     "default_inspection_norm": "VTA (Mattheck)", "default_inspection_frequency_months": 12,
     "typical_properties": ["soort_latijn", "stamomtrek_cm", "kroonbreedte_m", "plantjaar", "standplaats_type"]},
    {"code": "boom_park",           "naam": "Parkboom / solitair", "hoofdgroep": "groen",
     "default_inspection_norm": "VTA (Mattheck)", "default_inspection_frequency_months": 24,
     "typical_properties": ["soort_latijn", "stamomtrek_cm", "kroonbreedte_m", "monumentaal"]},
    {"code": "haag",                "naam": "Haag", "hoofdgroep": "groen",
     "default_inspection_norm": "CROW 145", "default_inspection_frequency_months": 12,
     "typical_properties": ["lengte_m", "hoogte_m", "soort"]},
    {"code": "plantsoen",           "naam": "Plantsoen / sierplantvak", "hoofdgroep": "groen",
     "default_inspection_norm": "CROW 145", "default_inspection_frequency_months": 6,
     "typical_properties": ["oppervlak_m2", "type_beplanting"]},
    {"code": "grasveld",            "naam": "Grasveld / gazon", "hoofdgroep": "groen",
     "default_inspection_norm": "CROW 145", "default_inspection_frequency_months": 6,
     "typical_properties": ["oppervlak_m2", "type_gras", "intensiteit"]},
    {"code": "bermsloot",           "naam": "Berm + sloot", "hoofdgroep": "groen",
     "default_inspection_norm": "CROW 145", "default_inspection_frequency_months": 12,
     "typical_properties": ["lengte_m", "type_berm"]},

    # ── SPEEL + SPORT ──
    {"code": "speeltoestel_schommel", "naam": "Schommel", "hoofdgroep": "speel_sport",
     "default_inspection_norm": "NEN-EN 1176", "default_inspection_frequency_months": 12,
     "typical_properties": ["leeftijdsdoelgroep", "aantal_schommels", "valhoogte_cm", "ondergrond_type"]},
    {"code": "speeltoestel_glijbaan", "naam": "Glijbaan", "hoofdgroep": "speel_sport",
     "default_inspection_norm": "NEN-EN 1176", "default_inspection_frequency_months": 12,
     "typical_properties": ["leeftijdsdoelgroep", "hoogte_m", "materiaal", "valhoogte_cm"]},
    {"code": "speeltoestel_klimrek",  "naam": "Klimtoestel / klimrek", "hoofdgroep": "speel_sport",
     "default_inspection_norm": "NEN-EN 1176", "default_inspection_frequency_months": 12,
     "typical_properties": ["leeftijdsdoelgroep", "hoogte_m", "materiaal", "valhoogte_cm"]},
    {"code": "speeltoestel_wip",      "naam": "Wip / draaitoestel", "hoofdgroep": "speel_sport",
     "default_inspection_norm": "NEN-EN 1176", "default_inspection_frequency_months": 12,
     "typical_properties": ["leeftijdsdoelgroep", "type"]},
    {"code": "speelondergrond",       "naam": "Speelondergrond (rubber/zand)", "hoofdgroep": "speel_sport",
     "default_inspection_norm": "NEN-EN 1177", "default_inspection_frequency_months": 12,
     "typical_properties": ["oppervlak_m2", "type_ondergrond", "dikte_cm"]},
    {"code": "sportveld",             "naam": "Sportveld", "hoofdgroep": "speel_sport",
     "default_inspection_norm": "NEN 2767-4", "default_inspection_frequency_months": 24,
     "typical_properties": ["oppervlak_m2", "type_veld", "drainage"]},
    {"code": "sport_kunstgras",       "naam": "Kunstgras-sportveld", "hoofdgroep": "speel_sport",
     "default_inspection_norm": "NEN 2767-4 + NOC*NSF", "default_inspection_frequency_months": 12,
     "typical_properties": ["oppervlak_m2", "type_infill", "leeftijd_jaar"]},

    # ── STRAATMEUBILAIR ──
    {"code": "bank",                "naam": "Bank / zitbank", "hoofdgroep": "meubilair",
     "default_inspection_norm": "Visueel", "default_inspection_frequency_months": 12,
     "typical_properties": ["materiaal", "type", "lengte_cm"]},
    {"code": "prullenbak",          "naam": "Prullenbak / afvalbak", "hoofdgroep": "meubilair",
     "default_inspection_norm": "Visueel", "default_inspection_frequency_months": 6,
     "typical_properties": ["volume_l", "type_lediging", "materiaal"]},
    {"code": "fietsenstalling",     "naam": "Fietsenstalling / -rek", "hoofdgroep": "meubilair",
     "default_inspection_norm": "Visueel", "default_inspection_frequency_months": 12,
     "typical_properties": ["capaciteit", "type"]},
    {"code": "fontein",             "naam": "Fontein / waterornament", "hoofdgroep": "meubilair",
     "default_inspection_norm": "Visueel + NEN 3140", "default_inspection_frequency_months": 12,
     "typical_properties": ["watercapaciteit_l", "pomp_type"]},
    {"code": "ondergrondse_container", "naam": "Ondergrondse afvalcontainer", "hoofdgroep": "meubilair",
     "default_inspection_norm": "Visueel + technisch", "default_inspection_frequency_months": 6,
     "typical_properties": ["volume_m3", "afvalstroom", "leverancier"]},
    {"code": "informatiebord",      "naam": "Informatiebord", "hoofdgroep": "meubilair",
     "default_inspection_norm": "Visueel", "default_inspection_frequency_months": 12,
     "typical_properties": ["afmetingen_cm"]},

    # ── BEWEGWIJZERING + MARKERING ──
    {"code": "verkeersbord",        "naam": "Verkeersbord", "hoofdgroep": "bewegwijzering",
     "default_inspection_norm": "CROW 96", "default_inspection_frequency_months": 24,
     "typical_properties": ["bord_code", "afmetingen_cm", "reflectie_klasse", "drager_type"]},
    {"code": "wegmarkering",        "naam": "Wegmarkering (strepen/symbolen)", "hoofdgroep": "bewegwijzering",
     "default_inspection_norm": "CROW 145", "default_inspection_frequency_months": 12,
     "typical_properties": ["type_streep", "lengte_m", "kleur"]},
    {"code": "bewegwijzeringsbord", "naam": "Bewegwijzeringsbord (anwb-stijl)", "hoofdgroep": "bewegwijzering",
     "default_inspection_norm": "CROW 96", "default_inspection_frequency_months": 24,
     "typical_properties": ["afmetingen_cm", "drager_type", "bestemmingen"]},
    {"code": "wegafzetting",        "naam": "Wegafzetting (vast)", "hoofdgroep": "bewegwijzering",
     "default_inspection_norm": "Visueel", "default_inspection_frequency_months": 12,
     "typical_properties": ["type", "lengte_m"]},
    {"code": "paal_anti_parkeer",   "naam": "Anti-parkeerpaal / -pollen", "hoofdgroep": "bewegwijzering",
     "default_inspection_norm": "Visueel", "default_inspection_frequency_months": 24,
     "typical_properties": ["type", "materiaal", "verzinkbaar"]},

    # ── WATERGANGEN + OEVERS ──
    {"code": "watergang",           "naam": "Watergang (sloot/wetering/vijver)", "hoofdgroep": "water",
     "default_inspection_norm": "NEN 2767", "default_inspection_frequency_months": 24,
     "typical_properties": ["lengte_m", "breedte_m", "diepte_m", "functie"]},
    {"code": "beschoeiing",         "naam": "Beschoeiing (oeverbescherming)", "hoofdgroep": "water",
     "default_inspection_norm": "NEN 2767-2", "default_inspection_frequency_months": 36,
     "typical_properties": ["lengte_m", "materiaal", "hoogte_cm"]},
    {"code": "vlonder_steiger",     "naam": "Vlonder / steiger", "hoofdgroep": "water",
     "default_inspection_norm": "NEN 2767-2", "default_inspection_frequency_months": 24,
     "typical_properties": ["oppervlak_m2", "materiaal"]},

    # ── GEBOUWEN ──
    {"code": "gebouw_publiek",      "naam": "Publiek gebouw (sporthal/buurthuis)", "hoofdgroep": "gebouwen",
     "default_inspection_norm": "NEN 2767-1", "default_inspection_frequency_months": 36,
     "typical_properties": ["bouwjaar", "oppervlak_m2", "energielabel"]},
    {"code": "gemaal_gebouw",       "naam": "Gemaal-gebouw", "hoofdgroep": "gebouwen",
     "default_inspection_norm": "NEN 2767-2", "default_inspection_frequency_months": 24,
     "typical_properties": ["bouwjaar", "functie"]},
    {"code": "abri",                "naam": "Abri / bushokje", "hoofdgroep": "gebouwen",
     "default_inspection_norm": "Visueel", "default_inspection_frequency_months": 12,
     "typical_properties": ["materiaal", "afmetingen_m"]},
    {"code": "schuilstal",          "naam": "Schuilplaats / schuur", "hoofdgroep": "gebouwen",
     "default_inspection_norm": "Visueel", "default_inspection_frequency_months": 24,
     "typical_properties": ["materiaal", "oppervlak_m2"]},
]


def list_hoofdgroepen() -> list[dict]:
    """Return alle IMBOR-hoofdgroepen + asset-count per groep."""
    counts: dict[str, int] = {}
    for t in TAXONOMY:
        counts[t["hoofdgroep"]] = counts.get(t["hoofdgroep"], 0) + 1
    return [
        {**hg, "asset_count": counts.get(hg["code"], 0)}
        for hg in HOOFDGROEPEN
    ]


def list_types(hoofdgroep: Optional[str] = None) -> list[dict]:
    """Return alle asset-types, optioneel gefilterd op hoofdgroep."""
    if hoofdgroep:
        return [t for t in TAXONOMY if t["hoofdgroep"] == hoofdgroep]
    return list(TAXONOMY)


def get_type_info(code: str) -> Optional[dict]:
    """Lookup een asset-type op code."""
    for t in TAXONOMY:
        if t["code"] == code:
            return t
    return None


def get_tree() -> dict:
    """Hierarchical tree: hoofdgroep -> [types]. Voor cascading dropdown."""
    tree = {}
    for hg in HOOFDGROEPEN:
        tree[hg["code"]] = {
            **hg,
            "asset_types": [t for t in TAXONOMY if t["hoofdgroep"] == hg["code"]],
        }
    return tree
