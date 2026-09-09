"""Werksoorten voor asfaltonderhoud — kaartkleur en CROW-maatregel.

Eén bron van waarheid voor de uitvoeringsvormen die op de kaart uit elkaar
gehouden moeten worden. De werksoort wordt opgeslagen in `Melding.category`,
zodat filteren, bulk-update, CSV-import en export er zonder extra kolom mee
werken.

Twee dingen hangen eraan:

  - **Kaartkleur.** templates/portaal.html kleurt een marker op werksoort zodra
    de categorie hier voorkomt; alle andere categorieën blijven op prioriteit
    kleuren. tests/test_werksoorten.py bewaakt dat de JS-map gelijk blijft aan
    deze lijst.

  - **Clusteren.** De job-orchestratie pakt alleen meldingen op met `gw_term`
    gezet (zie orchestration.generate_clusters). Zonder maatregel valt een
    melding buiten elk cluster. Daarom draagt elke werksoort de CROW-maatregel
    die erbij hoort; de namen komen uit crow_kosten.MAATREGEL_TO_SKILL.
"""
from __future__ import annotations

# label -> alles wat eraan hangt. Het label is exact wat in Melding.category staat.
WERKSOORTEN: list[dict] = [
    {
        "label": "Hotbox werkzaamheden",
        "kort": "Hotbox",
        "kleur": "#ff5252",   # rood
        "emoji": "🔥",
        "omschrijving": "Handmatig herstel met warm asfalt uit de hotbox — "
                        "kleine vakken, gaten en herstelpunten.",
        "maatregel": "Pleksgewijze reparatie",
        "gw_term": "Pleksgewijze reparatie frees-vul",
        "kosten_orde": "€150–400 / plek",
    },
    {
        "label": "Asfalt machinaal",
        "kort": "Machinaal",
        "kleur": "#22c55e",   # groen
        "emoji": "🚜",
        "omschrijving": "Asfalt aanbrengen met machine — grotere vlakken en "
                        "doorgaande stroken.",
        "maatregel": "Deklaag vervangen",
        "gw_term": "Aanbrengen dicht asfaltbeton (deklaag)",
        "kosten_orde": "€20–40 / m²",
    },
    {
        "label": "Scheuren vullen",
        "kort": "Scheuren",
        "kleur": "#ffab40",   # oranje
        "emoji": "🩹",
        "omschrijving": "Scheurvulling / voegvulling in langs- en dwarsscheuren.",
        "maatregel": "Vullen polymeer",
        "gw_term": "Vullen polymeer (cold-pour)",
        "kosten_orde": "€5–15 / m¹",
    },
    {
        "label": "Asfalt zwart (rijweg)",
        "kort": "Zwart asfalt",
        "kleur": "#334155",   # antraciet
        "emoji": "⬛",
        "omschrijving": "Herstelpunt in zwart asfalt op de rijweg — meestal na "
                        "een sleuf van een nutsbedrijf.",
        "maatregel": "Pleksgewijze reparatie",
        "gw_term": "Pleksgewijze reparatie frees-vul",
        "kosten_orde": "€150–400 / plek",
    },
    {
        "label": "Asfalt rood (fiets-/voetpad)",
        "kort": "Rood asfalt",
        "kleur": "#e11d48",   # rozerood
        "emoji": "🟥",
        "omschrijving": "Herstelpunt in rood asfalt op een fiets- of voetpad — "
                        "meestal na een sleuf van een nutsbedrijf.",
        "maatregel": "Pleksgewijze reparatie",
        "gw_term": "Pleksgewijze reparatie frees-vul",
        "kosten_orde": "€150–400 / plek",
    },
    {
        "label": "Asfalt – nog in te delen",
        "kort": "Nog in te delen",
        "kleur": "#94a3b8",   # neutraal grijsblauw
        "emoji": "🛣️",
        "omschrijving": "Geschouwd asfaltgebrek waarvan de uitvoeringsvorm nog "
                        "bepaald moet worden.",
        "maatregel": None,
        "gw_term": None,
        "kosten_orde": None,
    },
]

# Snelle lookups
WERKSOORT_LABELS: list[str] = [w["label"] for w in WERKSOORTEN]
WERKSOORT_KLEUREN: dict[str, str] = {w["label"]: w["kleur"] for w in WERKSOORTEN}
WERKSOORT_EMOJI: dict[str, str] = {w["label"]: w["emoji"] for w in WERKSOORTEN}
# Korte naam voor in een meldingtitel — het volledige label leest daar te lang.
WERKSOORT_KORT: dict[str, str] = {w["label"]: w["kort"] for w in WERKSOORTEN}
WERKSOORT_MAATREGEL: dict[str, dict] = {
    w["label"]: {"gw_maatregel": w["maatregel"], "gw_term": w["gw_term"],
                 "gw_kosten_orde": w["kosten_orde"]}
    for w in WERKSOORTEN if w["maatregel"]
}

# De categorie die de importer meegeeft zolang de werksoort niet vaststaat.
ONBEPAALD = "Asfalt – nog in te delen"


def kleur_voor(categorie: str | None) -> str | None:
    """Kaartkleur voor een categorie, of None als het geen werksoort is."""
    if not categorie:
        return None
    return WERKSOORT_KLEUREN.get(categorie.strip())


def maatregel_voor(categorie: str | None) -> dict:
    """CROW-maatregelvelden voor een werksoort.

    Zonder deze velden valt een melding buiten het clusteren — de
    job-orchestratie filtert op `gw_term`.
    """
    if not categorie:
        return {}
    return WERKSOORT_MAATREGEL.get(categorie.strip(), {})
