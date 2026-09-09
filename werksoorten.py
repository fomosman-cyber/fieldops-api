"""Werksoorten voor asfaltonderhoud — catalogus met kaartkleur.

Eén bron van waarheid voor de drie uitvoeringsvormen die op de kaart uit
elkaar gehouden moeten worden. De werksoort wordt opgeslagen in
`Melding.category`, zodat filteren, bulk-update, CSV-import en export er
zonder extra kolom mee werken.

De kaart (templates/portaal.html) kleurt een marker op werksoort zodra de
categorie hier voorkomt; alle andere categorieën blijven op prioriteit
kleuren. `tests/test_werksoorten.py` bewaakt dat de kleuren in de JS-map
gelijk blijven aan deze lijst.
"""
from __future__ import annotations

# label -> (kleur, emoji, omschrijving)
# Het label is exact wat in Melding.category staat.
WERKSOORTEN: list[dict] = [
    {
        "label": "Hotbox werkzaamheden",
        "kleur": "#ff5252",   # rood
        "emoji": "🔥",
        "omschrijving": "Handmatig herstel met warm asfalt uit de hotbox — "
                        "kleine vakken, gaten en herstelpunten.",
    },
    {
        "label": "Asfalt machinaal",
        "kleur": "#22c55e",   # groen
        "emoji": "🚜",
        "omschrijving": "Asfalt aanbrengen met machine (freesbak/spreidmachine) — "
                        "grotere vlakken en doorgaande stroken.",
    },
    {
        "label": "Scheuren vullen",
        "kleur": "#ffab40",   # oranje
        "emoji": "🩹",
        "omschrijving": "Scheurvulling / voegvulling in langs- en dwarsscheuren.",
    },
    {
        "label": "Asfalt – nog in te delen",
        "kleur": "#94a3b8",   # neutraal grijsblauw
        "emoji": "🛣️",
        "omschrijving": "Geschouwd asfaltgebrek waarvan de uitvoeringsvorm nog "
                        "bepaald moet worden.",
    },
]

# Snelle lookups
WERKSOORT_LABELS: list[str] = [w["label"] for w in WERKSOORTEN]
WERKSOORT_KLEUREN: dict[str, str] = {w["label"]: w["kleur"] for w in WERKSOORTEN}
WERKSOORT_EMOJI: dict[str, str] = {w["label"]: w["emoji"] for w in WERKSOORTEN}

# De categorie die de importer meegeeft zolang de werksoort niet vaststaat.
ONBEPAALD = "Asfalt – nog in te delen"


def kleur_voor(categorie: str | None) -> str | None:
    """Kaartkleur voor een categorie, of None als het geen werksoort is."""
    if not categorie:
        return None
    return WERKSOORT_KLEUREN.get(categorie.strip())
