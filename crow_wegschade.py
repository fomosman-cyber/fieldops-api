"""Schadebeeldherkenning voor de camera — wat elk CROW-schadebeeld is, en hoe je
het in beeld herkent.

`crow_kosten` bepaalt WELKE schadebeelden er zijn (asfalt, elementen,
cementbeton) en wat de maatregel per klasse kost. Dit bestand voegt daar het
deel aan toe dat een camera nodig heeft: hoe het eruitziet, waar je het mee
moet verwarren, en hoe licht (L), matig (M) en ernstig (E) er per schadebeeld
uitzien. De schouw geeft deze tekst mee aan de beeldherkenning.

**Eén bron voor de lijst.** De schadebeelden komen uit
`crow_kosten.SCHADEGROEPEN_*`; hier staat alleen de herkenning erbij. Een
schadebeeld dat daar bijkomt zonder herkenning hier, laat
`test_crow_wegschade` falen. Zo blijft "de camera kent alle schadebeelden die
we hebben" waar zonder dat iemand het hoeft te onthouden.

**Dit is geen overname van de CROW-schadecatalogus.** De publicatie met
voorbeeldfoto's per klasse is van CROW; de beschrijvingen hieronder zijn eigen
formuleringen van wat er visueel te zien is. Ze sturen de herkenning; het
oordeel blijft bij de inspecteur, en de foto met het rode vak erbij is het
bewijs waarop hij dat doet.

**Ernst ja, omvang alleen als indicatie.** CROW-klasse is ernst maal omvang
(L1..E3), en omvang gaat over het hele inspectievak. Eén beeld laat een stuk
van dat vak zien. De camera meldt daarom de omvang *in beeld*, en die klasse
heet in het scherm een indicatie — de inspecteur stelt hem vast.
"""

from __future__ import annotations

from typing import Optional

import crow_kosten as ck

WEGSCHADE_VERSIE = "wegschade.v1-2026-09"

# Welke drager van de schouw bij welk verhardingstype hoort. Cementbeton is
# een gesloten verharding; zo tellen schades op beton mee in dezelfde meetlat
# als asfalt.
VERHARDINGEN: dict[str, dict] = {
    "asfalt": {
        "naam": "Asfalt",
        "schouw_drager": "gesloten_verharding",
        "groepen": ck.SCHADEGROEPEN_ASFALT,
    },
    "elementen": {
        "naam": "Elementen (klinkers, tegels)",
        "schouw_drager": "elementenverharding",
        "groepen": ck.SCHADEGROEPEN_ELEMENTEN,
    },
    "beton": {
        "naam": "Cementbeton",
        "schouw_drager": "gesloten_verharding",
        "groepen": ck.SCHADEGROEPEN_BETON,
    },
}

ERNST: dict[str, str] = {
    "L": "licht — beginnend, alleen van dichtbij te zien, geen hinder",
    "M": "matig — duidelijk zichtbaar, breidt zich uit",
    "E": "ernstig — groot, diep of open; hinder of gevaar voor weggebruikers",
}

OMVANG_BEELD: dict[str, str] = {
    "1": "plaatselijk — een klein deel van de zichtbare verharding",
    "2": "over een deel van de zichtbare verharding",
    "3": "over het grootste deel van de zichtbare verharding",
}

# Een schade op de verharding telt in de schouw mee als "heelheid
# verharding". Die meetlat kent niveaus A+ t/m D; de CROW-ernst sluit daar
# rechtstreeks op aan. A blijft voor "geen schade" en komt dus niet uit een
# gevonden schade.
ERNST_NAAR_NIVEAU: dict[str, str] = {"L": "B", "M": "C", "E": "D"}


def _h(naam: str, kenmerken: str, niet: str, L: str, M: str, E: str) -> dict:
    return {"naam": naam, "kenmerken": kenmerken, "niet_verwarren_met": niet,
            "ernst": {"L": L, "M": M, "E": E}}


# Sleutel: (verharding, schadebeeld). Schadebeelden die in twee
# verhardingstypen voorkomen ("oneffenheden") zien er per type anders uit en
# staan daarom apart.
HERKENNING: dict[tuple[str, str], dict] = {
    # ── Asfalt · samenhang ──────────────────────────────────────────
    ("asfalt", "scheurvorming-langs"): _h(
        "Langsscheur",
        "Eén scheur evenwijdig aan de rijrichting, vaak in of naast het "
        "wielspoor of op de naad tussen twee asfaltbanen.",
        "een gesloten werknaad of een markeringsstreep; afbrokkeling aan de "
        "wegrand (dat is randschade)",
        "haarscheur, dicht, alleen van dichtbij te zien",
        "duidelijk open scheur, soms met korte vertakkingen",
        "wijd open, randen brokkelen af of er groeit iets in"),
    ("asfalt", "scheurvorming-dwars"): _h(
        "Dwarsscheur",
        "Scheur haaks op de rijrichting over (een deel van) de rijstrook, "
        "vaak op min of meer regelmatige afstand van elkaar.",
        "een dwarse werknaad, een dwarse markering of de rand van een drempel",
        "haarscheur, dicht",
        "duidelijk open scheur over een groot deel van de strook",
        "wijd open, afbrokkelende randen of hoogteverschil over de scheur"),
    ("asfalt", "scheurvorming-kruis"): _h(
        "Blokscheuren",
        "Langs- en dwarsscheuren die elkaar kruisen en het asfalt in grote "
        "blokken verdelen, van tientallen centimeters tot meters.",
        "craquelé, dat veel kleinere vakjes heeft",
        "enkele kruisende scheuren, nog dicht",
        "duidelijke blokken met open scheuren",
        "blokken liggen los of wiebelen, randen brokkelen"),
    ("asfalt", "scheurvorming-rand"): _h(
        "Randschade",
        "Scheuren, afbrokkeling of afgebroken stukken langs de rand van de "
        "verharding, bij de berm, de goot of de opsluiting.",
        "een langsscheur midden in de rijbaan",
        "enkele scheuren langs de rand, rand nog heel",
        "rand brokkelt over een stuk af",
        "rand is weggebroken of verzakt, stukken ontbreken"),
    ("asfalt", "scheurvorming-groep"): _h(
        "Craquelé",
        "Fijnmazig netwerk van scheuren, als een olifantshuid: vakjes van "
        "enkele centimeters tot een paar decimeter, vaak in het wielspoor.",
        "blokscheuren, met veel grotere vakken",
        "fijn netwerk, scheuren dicht, kleine plek",
        "duidelijk netwerk met open scheuren",
        "stukjes komen los, er ontstaan gaten"),
    # ── Asfalt · textuur ────────────────────────────────────────────
    ("asfalt", "rafeling"): _h(
        "Rafeling",
        "Steentjes laten los uit de toplaag: een ruw, open oppervlak met losse "
        "steentjes en kleine putjes, vaak eerst in de wielsporen.",
        "spaltverlies (alleen bij een oppervlakbehandeling); zand of vuil op "
        "het wegdek",
        "oppervlak ruw, hier en daar een steentje weg",
        "duidelijk open textuur met losse steentjes",
        "toplaag grotendeels weg, er ontstaan kuiltjes"),
    ("asfalt", "spaltverlies"): _h(
        "Spaltverlies",
        "Bij een oppervlakbehandeling (steenslag ingestrooid op bitumen): de "
        "steentjes zijn weg en het donkere, gladde bindmiddel ligt bloot, in "
        "plekken of banen.",
        "bitumen dat door het asfalt omhoogkomt; rafeling van dicht asfalt",
        "enkele kale plekken",
        "kale banen, bindmiddel duidelijk zichtbaar",
        "grote kale vlakken, glad bij nat weer"),
    # ── Asfalt · vlakheid ───────────────────────────────────────────
    ("asfalt", "spoorvorming"): _h(
        "Spoorvorming",
        "Twee langgerekte verdiepingen in de wielsporen, evenwijdig aan de "
        "rijrichting; vaak te zien aan plassen of aan schaduw bij laag licht.",
        "verkleuring of markering in het wielspoor zonder diepte",
        "nauwelijks te zien, alleen bij strijklicht",
        "duidelijke sporen, water blijft erin staan",
        "diepe sporen met opgestuwde randen ernaast"),
    ("asfalt", "oneffenheden"): _h(
        "Oneffenheden (asfalt)",
        "Golven, bulten of dalen in het asfalt die niet specifiek in de "
        "wielsporen liggen, bijvoorbeeld boven een leiding of een oude sleuf.",
        "een drempel of plateau dat bewust is aangelegd",
        "licht golvend",
        "duidelijk hobbelig, merkbaar bij het rijden",
        "sterke hoogteverschillen, gevaarlijk voor fietsers"),
    ("asfalt", "kuilen"): _h(
        "Kuil",
        "Gat in het wegdek waar materiaal ontbreekt, met scherpe randen, vaak "
        "met losse brokken of water erin.",
        "een putdeksel of kolk; een reparatieplek die vlak ligt",
        "klein en ondiep gat",
        "duidelijk gat van enkele decimeters",
        "groot of diep gat, gevaar voor het verkeer"),
    ("asfalt", "deformatie"): _h(
        "Deformatie",
        "Het asfalt is vervormd of verschoven: ribbels of golven dwars op de "
        "rijrichting, opgestuwde randen, vooral waar verkeer remt of optrekt.",
        "spoorvorming, die in de lengterichting in de wielsporen ligt",
        "lichte ribbels",
        "duidelijke ribbels of verschuiving",
        "sterk vervormd met opgestuwde bulten"),
    # ── Asfalt · watergevoeligheid ──────────────────────────────────
    ("asfalt", "bitumen-uittreden"): _h(
        "Bitumen uittreden",
        "Glimmende, zwarte, vette plekken waar bitumen naar boven is gekomen; "
        "de steentjes zijn er niet meer te zien en het oppervlak is glad.",
        "een natte plek, een olievlek of een verse reparatie",
        "kleine glimmende plekken",
        "grotere vette banen, vaak in de wielsporen",
        "grote gladde vlakken, slipgevaar"),

    # ── Elementen · vlakheid ────────────────────────────────────────
    ("elementen", "verzakking"): _h(
        "Verzakking",
        "Stenen of tegels liggen over een oppervlak lager dan de omgeving: een "
        "kom waarin water blijft staan.",
        "een goot die bewust lager ligt",
        "lichte kom",
        "duidelijke kom, plas na regen",
        "diepe verzakking, struikelgevaar aan de rand"),
    ("elementen", "oneffenheden"): _h(
        "Oneffenheden (straatwerk)",
        "Golvend straatwerk waarin stenen onderling op verschillende hoogtes "
        "liggen, zonder één duidelijke kom.",
        "een verzakking (één kom) of een opdrukking (één bult)",
        "licht golvend",
        "duidelijke hoogteverschillen tussen stenen",
        "sterke hoogteverschillen, struikelgevaar"),
    ("elementen", "opdrukking"): _h(
        "Opdrukking",
        "Stenen of tegels worden omhooggedrukt, meestal door boomwortels: een "
        "bult of scheve tegels rond een boom of langs een bomenrij.",
        "een drempel of een bewust verhoogd vlak",
        "licht opgedrukt",
        "duidelijke bult, tegels staan scheef",
        "tegels steken uit, struikelgevaar"),
    # ── Elementen · voegen ──────────────────────────────────────────
    ("elementen", "voegwijdte"): _h(
        "Te wijde voegen",
        "Voegen tussen de stenen zijn te breed: stenen liggen uit verband of "
        "zijn uit elkaar geschoven.",
        "lege voegen bij stenen die nog goed in verband liggen",
        "voegen iets te breed",
        "duidelijk wijde voegen, stenen verschoven",
        "stenen kunnen kantelen of losraken"),
    ("elementen", "voegvulling-gebrek"): _h(
        "Voegvulling ontbreekt",
        "Voegen zijn leeg of half leeg: het voegzand is weg, donkere open "
        "voegen, vaak met onkruid erin.",
        "te wijde voegen, waarbij de stenen zelf verschoven zijn",
        "plaatselijk lege voegen",
        "lege voegen over een groter vlak",
        "stenen liggen los doordat de voegvulling ontbreekt"),
    # ── Elementen · stenen ──────────────────────────────────────────
    ("elementen", "gebroken-stenen"): _h(
        "Gebroken stenen",
        "Stenen of tegels met barsten of in stukken gebroken, of met "
        "afgebroken hoeken.",
        "een bewust gezaagde passtuk-tegel",
        "enkele barst",
        "meerdere gebroken elementen",
        "brokstukken liggen los, gat in het vlak"),
    ("elementen", "ontbrekende-stenen"): _h(
        "Ontbrekende stenen",
        "Een of meer stenen of tegels ontbreken: een gat in het patroon, vaak "
        "opgevuld met zand of een asfaltplek.",
        "een boomspiegel, kolk of putdeksel in het straatwerk",
        "één element weg, gat opgevuld",
        "meerdere elementen weg of een open gat",
        "groot gat, struikel- of valgevaar"),
    ("elementen", "los-liggend"): _h(
        "Losliggende stenen",
        "Stenen of tegels die los liggen of scheef staan (wipstenen), met een "
        "hoogteverschil ten opzichte van de buren.",
        "opdrukking door wortels, waarbij een hele groep omhoog staat",
        "licht scheef",
        "duidelijk los, wipt bij belasting",
        "steekt uit, struikelgevaar"),

    # ── Cementbeton · vlakheid ──────────────────────────────────────
    ("beton", "voegovergangen"): _h(
        "Hoogteverschil bij voeg",
        "Hoogteverschil tussen twee betonplaten bij een voeg: een trapje.",
        "een bewust aangelegde overgang naar een andere verharding",
        "nauwelijks te zien",
        "duidelijk trapje",
        "groot hoogteverschil, gevaar voor fietsers"),
    ("beton", "oneffenheid"): _h(
        "Oneffenheid (beton)",
        "Golvend of verzakt oppervlak binnen een betonplaat.",
        "een hoogteverschil op de voeg tussen twee platen",
        "licht golvend",
        "duidelijk verzakt of golvend",
        "sterk verzakt, water blijft staan"),
    # ── Cementbeton · voegen ────────────────────────────────────────
    ("beton", "voeg-degradatie"): _h(
        "Beschadigde voegkanten",
        "De randen van de voeg tussen de platen zijn afgebrokkeld.",
        "ontbrekende of losse voegkit bij voegkanten die nog heel zijn",
        "kleine afbrokkeling",
        "voegkant over een stuk afgebrokkeld",
        "brede, diepe afbrokkeling langs de voeg"),
    ("beton", "kit-gebreken"): _h(
        "Voegkit ontbreekt of is kapot",
        "De voegkit ontbreekt, is uitgedroogd, gescheurd of zit los van de "
        "betonrand.",
        "afgebrokkelde voegkanten",
        "kit plaatselijk gescheurd",
        "kit over grotere lengte los of weg",
        "voeg ligt open, vuil en water dringen in"),
    # ── Cementbeton · samenhang ─────────────────────────────────────
    ("beton", "scheurvorming"): _h(
        "Scheur in betonplaat",
        "Scheur door een betonplaat, in de lengte, dwars of diagonaal.",
        "een gezaagde voeg (strak en recht)",
        "haarscheur",
        "duidelijk open scheur",
        "wijd open, de delen verschuiven ten opzichte van elkaar"),
    ("beton", "breuk"): _h(
        "Gebroken plaat",
        "Een betonplaat is in meerdere stukken gebroken, of er is een hoek "
        "afgebroken.",
        "een enkele scheur door de plaat",
        "hoek gebroken, deel ligt nog op zijn plek",
        "plaat in meerdere stukken",
        "stukken los of verzakt, gat in de verharding"),
    ("beton", "betondegradatie"): _h(
        "Aangetast beton",
        "Het oppervlak brokkelt af: afschilfering, blootliggend grind, "
        "roestvlekken of zichtbare wapening.",
        "vuil of mos op het oppervlak",
        "oppervlak licht afgeschilferd",
        "grind ligt duidelijk bloot over een vlak",
        "diepe aantasting of zichtbare wapening"),
}


def schadebeelden() -> list[dict]:
    """Alle schadebeelden die de camera kent, in de volgorde van crow_kosten."""
    uit: list[dict] = []
    for vcode, v in VERHARDINGEN.items():
        for groep, beelden in v["groepen"].items():
            for code in beelden:
                h = HERKENNING.get((vcode, code))
                if not h:
                    continue
                uit.append({
                    "verharding": vcode,
                    "verharding_naam": v["naam"],
                    "schadegroep": groep,
                    "schadebeeld": code,
                    "naam": h["naam"],
                    "kenmerken": h["kenmerken"],
                    "niet_verwarren_met": h["niet_verwarren_met"],
                    "ernst": h["ernst"],
                })
    return uit


def zoek(verharding: Optional[str], schadebeeld: Optional[str]) -> Optional[dict]:
    """Het schadebeeld bij dit verhardingstype, of None als die combinatie niet
    bestaat. "Rafeling" op klinkers bestaat niet; dat is dan geen schade die
    we kunnen vastleggen, maar een vergissing van de herkenning."""
    for s in schadebeelden():
        if s["verharding"] == verharding and s["schadebeeld"] == schadebeeld:
            return s
    return None


def klasse_indicatie(ernst: Optional[str], omvang: Optional[str]) -> Optional[str]:
    """L/M/E plus 1/2/3 -> bv. "M2". Alleen als beide bekend zijn."""
    if ernst in ERNST and omvang in OMVANG_BEELD:
        return f"{ernst}{omvang}"
    return None


def prompt_tekst() -> str:
    """De catalogus zoals de beeldherkenning hem krijgt.

    Statisch: dezelfde tekst bij elk beeld, zodat hij in de promptcache blijft
    staan. Een wisselende volgorde of datum hierin zou elk beeld de volle prijs
    laten betalen.
    """
    regels: list[str] = []
    for vcode, v in VERHARDINGEN.items():
        regels.append(f"\n{v['naam'].upper()} (verharding: \"{vcode}\")")
        for s in schadebeelden():
            if s["verharding"] != vcode:
                continue
            e = s["ernst"]
            regels.append(
                f"- {s['schadebeeld']} [{s['schadegroep']}] — {s['naam']}. "
                f"{s['kenmerken']} Niet verwarren met: {s['niet_verwarren_met']}. "
                f"L: {e['L']}. M: {e['M']}. E: {e['E']}.")
    return "\n".join(regels)
