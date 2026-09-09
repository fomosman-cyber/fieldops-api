"""Werksoorten en asfaltsoorten — kaartkleur, CROW-maatregel en ploeg.

Twee dingen die niet hetzelfde zijn, en die daarom apart staan:

  - **Werksoort** = hoe het werk wordt uitgevoerd: met de hotbox, machinaal, of
    als scheurherstel. Dat bepaalt de ploeg, het materieel en het cluster. De
    werksoort staat in `Melding.category`, zodat filteren, bulk-update,
    CSV-import en export er zonder extra kolom mee werken.

  - **Asfaltsoort** = waar het werk in zit: rood asfalt (fiets- of voetpad) of
    zwart asfalt (rijweg). Dat zegt niets over de uitvoering — je kunt zowel
    rood als zwart asfalt met de hotbox of machinaal herstellen — maar wel over
    het materiaal dat mee moet. Het is dus een eigenschap van de plek, geen
    werksoort. Staat in `norm_data_json["asfaltsoort"]`.

    Deze staat alleen ingevuld waar het bronrapport hem noemt. Uit een
    schouwfoto is de kleur niet betrouwbaar af te leiden, en een gok hier kost
    buiten een rit terug omdat de verkeerde emulsie op de wagen ligt.

Aan de werksoort hangen:

  - **Kaartkleur.** templates/portaal.html kleurt een marker op werksoort zodra
    de categorie hier voorkomt; alle andere categorieën blijven op prioriteit
    kleuren. tests/test_werksoorten.py bewaakt dat de JS-map gelijk blijft aan
    deze lijst.

  - **Clusteren.** De job-orchestratie groepeert op `gw_term` (zie
    orchestration.generate_clusters) en pakt alleen meldingen op die er een
    hebben. Elke werksoort heeft daarom een eigen `gw_term`, gelijk aan de
    werksoort zelf: dan valt de clusterindeling samen met de indeling die de
    uitvoerder op de kaart ziet. Hotbox-werk komt niet in hetzelfde cluster als
    machinaal werk, ook al is de CROW-maatregel verwant.

    "Nog in te delen" heeft bewust geen `gw_term` en clustert dus niet. Zo'n
    melding hoort eerst een werksoort te krijgen — clusteren op een
    uitvoeringsvorm die niemand heeft vastgesteld levert een ploegdag op die
    buiten niet klopt.

  - **Skill.** Per werksoort staat de skill uit crow_kosten.SKILL_CODES die de
    uitvoerder moet hebben. Drie ploegen doen dit werk: hotbox, asfalt
    machinaal en scheuren herstellen. De skill volgt uit de maatregel via
    crow_kosten.MAATREGEL_TO_SKILL; het `skill`-veld hieronder maakt expliciet
    wat daar uit komt, en tests/test_werksoorten.py bewaakt dat de twee gelijk
    blijven.
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
        "maatregel": "Hotbox reparatie",
        "gw_term": "Hotbox werkzaamheden",
        "skill": "HOTBOX",
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
        "gw_term": "Asfalt machinaal",
        "skill": "ASFALT_DEKLAAG",
        "kosten_orde": "€20–40 / m²",
    },
    {
        "label": "Scheuren vullen",
        "kort": "Scheuren",
        "kleur": "#ffab40",   # oranje
        "emoji": "🩹",
        "omschrijving": "Scheurvulling / voegvulling in langs- en dwarsscheuren.",
        "maatregel": "Vullen polymeer",
        "gw_term": "Scheuren herstellen",
        "skill": "VULLEN_POLYMEER",
        "kosten_orde": "€5–15 / m¹",
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
        "skill": None,
        "kosten_orde": None,
    },
]

# ── Asfaltsoort ──────────────────────────────────────────────────────────
# De tweede as: waar zit het werk in. Alleen ingevuld waar het rapport het
# noemt — een niet-ingevulde asfaltsoort is een echte "weten we niet", geen
# stilzwijgende aanname dat het zwart is.
ASFALTSOORTEN: list[dict] = [
    {
        "code": "zwart",
        "label": "Zwart asfalt (rijweg)",
        "kort": "Zwart",
        "kleur": "#334155",   # antraciet
        "emoji": "⬛",
        "omschrijving": "Rijweg in zwart asfalt.",
    },
    {
        "code": "rood",
        "label": "Rood asfalt (fiets-/voetpad)",
        "kort": "Rood",
        "kleur": "#e11d48",   # rozerood
        "emoji": "🟥",
        "omschrijving": "Fiets- of voetpad in rood asfalt.",
    },
]
ASFALTSOORT_LABELS: dict[str, str] = {a["code"]: a["label"] for a in ASFALTSOORTEN}
ASFALTSOORT_KORT: dict[str, str] = {a["code"]: a["kort"] for a in ASFALTSOORTEN}
ASFALTSOORT_KLEUREN: dict[str, str] = {a["code"]: a["kleur"] for a in ASFALTSOORTEN}
ASFALTSOORT_EMOJI: dict[str, str] = {a["code"]: a["emoji"] for a in ASFALTSOORTEN}
ONBEKENDE_ASFALTSOORT = "niet vermeld in het rapport"


def asfaltsoort_label(code: str | None) -> str:
    """Leesbare asfaltsoort, of de tekst die zegt dat we het niet weten."""
    return ASFALTSOORT_LABELS.get((code or "").strip().lower(), ONBEKENDE_ASFALTSOORT)


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
# Welke ploeg het uitvoert. Alleen werksoorten die daadwerkelijk clusteren.
WERKSOORT_SKILL: dict[str, str] = {
    w["label"]: w["skill"] for w in WERKSOORTEN if w["skill"]
}
# De skills waarmee geclusterd wordt — hotbox, machinaal, scheuren.
CLUSTER_SKILLS: list[str] = sorted(set(WERKSOORT_SKILL.values()))

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


def synchroniseer(melding) -> bool:
    """Zet de CROW-velden van een melding gelijk aan zijn werksoort.

    De werksoort (Melding.category) is leidend. Wie in het portaal een melding
    van "nog in te delen" naar "Hotbox werkzaamheden" zet, verwacht dat hij
    daarna in het hotbox-cluster zit — en dat gebeurt alleen als `gw_term`
    meeverandert. Andersom moet een melding die terug op "nog in te delen" gaat
    zijn maatregel kwijtraken, anders blijft hij clusteren op werk dat niemand
    heeft vastgesteld.

    Raakt uitsluitend meldingen waarvan de categorie een werksoort is; alle
    andere categorieen laat deze functie ongemoeid. Geeft True terug als er
    iets is veranderd.
    """
    categorie = (getattr(melding, "category", None) or "").strip()
    if categorie not in WERKSOORT_KLEUREN:
        return False
    doel = WERKSOORT_MAATREGEL.get(categorie, {
        "gw_maatregel": None, "gw_term": None, "gw_kosten_orde": None,
    })
    gewijzigd = False
    for veld, waarde in doel.items():
        if getattr(melding, veld, None) != waarde:
            setattr(melding, veld, waarde)
            gewijzigd = True
    return gewijzigd
