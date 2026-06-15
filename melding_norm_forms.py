"""Dynamische norm-specifieke invulvelden per asset-type (bug #16).

Bij het aanmaken van een melding wil je per asset-type/categorie de juiste
NEN/CROW-invulvelden zien — niet alleen de CROW 146-velden (wegverharding).
Deze module beschrijft, per genormaliseerd asset-type, welke norm van
toepassing is en welke extra velden de melding-modal moet tonen.

De waarden worden los opgeslagen in Melding.norm_data_json (vrije JSON-string),
zodat we geen kolom-per-norm aan het meldingen-schema hoeven toe te voegen.

Verharding (wegdek_asfalt / wegdek_elementen, CROW 146) heeft al een eigen,
rijke classificatie-sectie in de modal (crow_schadegroep/-schadebeeld/-ernst/
-omvang). Voor die types geven we hier bewust géén extra velden terug.

De velddefinitie is opzettelijk simpel/declaratief zodat de frontend ze
generiek kan renderen en het document (#12) ze generiek kan tonen:
  {key, label, type, opties?, min?, max?, step?, suffix?, help?}
  type ∈ {"select", "number", "text"}
"""
from typing import Optional

from kunstwerken_taxonomy import normalize_type, KUNSTWERK_TYPES


# Norm-label per genormaliseerd type (voor de sectie-titel in de modal).
_NORM_LABEL = {
    "brug": "NEN 2767-2 / CROW 134 — conditiemeting kunstwerk",
    "viaduct": "NEN 2767-2 / CROW 134 — conditiemeting kunstwerk",
    "tunnel": "NEN 2767-2 / CROW 134 — conditiemeting kunstwerk",
    "sluis": "NEN 2767-2 / CROW 134 — conditiemeting kunstwerk",
    "stuw": "NEN 2767-2 / CROW 134 — conditiemeting kunstwerk",
    "duiker": "NEN 2767-2 / CROW 134 — conditiemeting kunstwerk",
    "kademuur": "NEN 2767-2 / CROW 134 — conditiemeting kunstwerk",
    "gemaal": "NEN 2767-2 / CROW 134 — conditiemeting kunstwerk",
    "fontein": "NEN 2767-4 — conditiemeting",
    "kunstgrasveld": "NEN 2767-4 — conditiemeting",
    "boom": "VTA (Visual Tree Assessment) — Mattheck",
    "speeltoestel": "NEN-EN 1176 — speeltoestellen",
    "verlichting": "NEN 3140 — elektrische veiligheid",
    "wegmarkering": "CROW 145 — wegmarkering retroreflectie",
    "riolering": "NEN 3399 / NEN-EN 13508 — riolering",
}

# ── Veld-sets per norm ──────────────────────────────────────────────────────

_CONDITIE_2767 = [
    {"key": "nen2767_conditiescore", "label": "Conditiescore (1=uitstekend … 6=zeer slecht)",
     "type": "select", "opties": ["1", "2", "3", "4", "5", "6"]},
    {"key": "nen2767_ernst", "label": "Ernst", "type": "select",
     "opties": ["gering", "serieus", "ernstig"]},
    {"key": "nen2767_intensiteit", "label": "Intensiteit", "type": "select",
     "opties": ["beginstadium", "gevorderd", "eindstadium"]},
    {"key": "nen2767_omvang", "label": "Omvang", "type": "select",
     "opties": ["<2%", "2-10%", "10-30%", "30-70%", ">70%"]},
]

_VTA_BOOM = [
    {"key": "vta_risicoklasse", "label": "VTA-risicoklasse (1=laag … 5=acuut)",
     "type": "select", "opties": ["1", "2", "3", "4", "5"]},
    {"key": "vta_holte_pct", "label": "Holte in stam", "type": "number",
     "min": 0, "max": 100, "step": 1, "suffix": "%"},
    {"key": "vta_t_r_ratio", "label": "t/R-ratio (restwanddikte ÷ straal)",
     "type": "number", "min": 0, "max": 1, "step": 0.01,
     "help": "Waarschuwing onder ~0,30"},
]

_NEN1176_SPEEL = [
    {"key": "en1176_categorie", "label": "Risicocategorie", "type": "select",
     "opties": ["A", "B", "C", "D"],
     "help": "C/D = direct gevaar → toestel afsluiten"},
    {"key": "nen1176_inspectie_kind", "label": "Inspectiesoort", "type": "select",
     "opties": ["routine", "operationeel", "hoofd"]},
]

_NEN3140_VERLICHTING = [
    {"key": "nen3140_isolatie_megaohm", "label": "Isolatieweerstand", "type": "number",
     "min": 0, "step": 0.1, "suffix": "MΩ", "help": "Eis ≥ 1,0 MΩ"},
    {"key": "nen3140_aardingsweerstand_ohm", "label": "Aardingsweerstand", "type": "number",
     "min": 0, "step": 0.1, "suffix": "Ω", "help": "Eis ≤ 100 Ω"},
    {"key": "nen3140_aardlek_ms", "label": "Aardlek-uitschakeltijd", "type": "number",
     "min": 0, "step": 1, "suffix": "ms", "help": "Eis ≤ 200 ms"},
    {"key": "nen3140_aardlek_ma", "label": "Aardlekstroom", "type": "number",
     "min": 0, "step": 1, "suffix": "mA", "help": "Eis ≤ 30 mA"},
]

_CROW145_MARKERING = [
    {"key": "crow145_rl_droog_mcd", "label": "Retroreflectie droog (RL)", "type": "number",
     "min": 0, "step": 1, "suffix": "mcd/m²/lx", "help": "Eis ≥ 100 (nieuw), ≥ 80 (onderhoud)"},
    {"key": "crow145_rl_nat_mcd", "label": "Retroreflectie nat (RL)", "type": "number",
     "min": 0, "step": 1, "suffix": "mcd/m²/lx", "help": "Eis ≥ 35 bij nat-wegdek-markering"},
]

_NEN3399_RIOOL = [
    {"key": "nen3399_code", "label": "Schadecode (NEN-EN 13508, bv. BAA/BAH)",
     "type": "text", "help": "3 tekens, begint met BA"},
    {"key": "nen3399_klasse", "label": "Eindklasse (1=licht … 5=zeer ernstig)",
     "type": "select", "opties": ["1", "2", "3", "4", "5"]},
]

_VELDEN_PER_TYPE = {
    "brug": _CONDITIE_2767, "viaduct": _CONDITIE_2767, "tunnel": _CONDITIE_2767,
    "sluis": _CONDITIE_2767, "stuw": _CONDITIE_2767, "duiker": _CONDITIE_2767,
    "kademuur": _CONDITIE_2767, "gemaal": _CONDITIE_2767,
    "fontein": _CONDITIE_2767, "kunstgrasveld": _CONDITIE_2767,
    "boom": _VTA_BOOM,
    "speeltoestel": _NEN1176_SPEEL,
    "verlichting": _NEN3140_VERLICHTING,
    "wegmarkering": _CROW145_MARKERING,
    "riolering": _NEN3399_RIOOL,
}


def norm_form_voor(asset_type: Optional[str]) -> dict:
    """Geef de norm-specifieke velddefinitie voor een (vrij) asset-type.

    Returnt altijd een dict met:
      {asset_type, canonical_type, norm_label, velden: [...]}
    velden is leeg als er geen norm-specifieke velden zijn (bv. wegverharding,
    dat de bestaande CROW 146-sectie gebruikt, of een onbekend type).
    """
    canonical = normalize_type(asset_type)
    velden = _VELDEN_PER_TYPE.get(canonical, [])
    return {
        "asset_type": asset_type,
        "canonical_type": canonical,
        "norm_label": _NORM_LABEL.get(canonical) if velden else None,
        "velden": velden,
    }


def alle_norm_velden_keys() -> set:
    """Alle bekende norm-veld-keys — voor validatie/filtering van norm_data."""
    keys = set()
    for velden in _VELDEN_PER_TYPE.values():
        for v in velden:
            keys.add(v["key"])
    return keys
