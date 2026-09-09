"""Keuringen — de vakinhoud achter de module Kwaliteit & keuringen.

Een keuring is het sjabloon: wat moet er gecontroleerd worden, waaraan moet het
voldoen, en welk bewijs hoort erbij. Een registratie is één keer uitvoeren van
dat sjabloon, buiten, op een werkvak. Dat onderscheid is de hele module: wie ze
door elkaar haalt, bouwt of een formulier dat je maar één keer kunt invullen, of
een lijst losse waarnemingen waar geen norm achter zit.

Dit bestand bevat drie dingen:

1. **De veldtypen.** Wat een beheerder in de keuring kan zetten. Bewust een
   gesloten lijst -- een vrij "type"-veld levert over een jaar twaalf varianten
   op van hetzelfde getal en dan is rapporteren onmogelijk.

2. **De standaardkeuringen.** Kant-en-klare sjablonen voor wegen, constructie,
   graafwerk en riool. Een aannemer die begint moet niet eerst een half uur een
   formulier bouwen; hij kiest er een en past hem aan.

3. **Het oordeel.** De regel die van een gemeten waarde een akkoord of een
   afkeur maakt.

Over die laatste, want dat is de plek waar kwaliteitssoftware liegt: **zonder
norm volgt er geen oordeel.** Een keuring die "akkoord" zegt omdat er niets is
ingevuld om aan te toetsen, maakt het dossier erger dan geen dossier -- er staat
dan een goedkeuring in die niemand heeft gegeven. Zo'n veld komt terug als
`niet_beoordeeld` en de uitvoerder ziet dat staan. Dezelfde keuze is eerder
gemaakt in ``crow_schouw.py`` voor de beeldkwaliteitsschouw.

Over de normverwijzingen: hier staat alleen wat vaststaat. Waar de systematiek
duidelijk is maar het exacte artikel niet, staat de regeling zonder
artikelnummer. Een verzonnen artikelnummer is erger dan geen verwijzing. De
getallen in de standaardkeuringen zijn daarom bewust **leeg gelaten waar ze uit
het bestek moeten komen**: verdichtingspercentages, laagdiktes en toleranties
verschillen per werk, en een door ons verzonnen 98% die niemand controleert is
precies hoe een kwaliteitsdossier waardeloos wordt.
"""

from __future__ import annotations

from typing import Optional

KWALITEIT_VERSION = "kwaliteit.v1-2026-09"


# ─────────────────────────────────────────────────────────────────────────────
# Veldtypen
# ─────────────────────────────────────────────────────────────────────────────
# `waarde_kolom` zegt in welke kolom van QualityAnswer het antwoord landt.
# Getypte kolommen in plaats van één JSON-blob, zodat filteren en rapporteren
# werkt op zowel SQLite (tests) als PostgreSQL (productie) -- die twee delen
# geen JSON-operatoren.

VELDTYPES: dict[str, dict] = {
    "tekst_kort":   {"label": "Kort tekstveld",        "waarde_kolom": "answer_text"},
    "tekst_lang":   {"label": "Lang tekstveld",        "waarde_kolom": "answer_text"},
    "getal":        {"label": "Getal",                 "waarde_kolom": "answer_number"},
    "meetwaarde":   {"label": "Meetwaarde met norm",   "waarde_kolom": "answer_number"},
    "ja_nee":       {"label": "Ja / nee",              "waarde_kolom": "answer_bool"},
    "ja_nee_nvt":   {"label": "Ja / nee / n.v.t.",     "waarde_kolom": "answer_text"},
    "akkoord":      {"label": "Akkoord / niet akkoord", "waarde_kolom": "answer_text"},
    "keuring":      {"label": "Goed / afgekeurd",      "waarde_kolom": "answer_text"},
    "keuze":        {"label": "Keuze uit lijst",       "waarde_kolom": "answer_text"},
    "meerkeuze":    {"label": "Meerdere keuzes",       "waarde_kolom": "answer_json"},
    "checklist":    {"label": "Checklist (afvinken)",  "waarde_kolom": "answer_json"},
    "datum":        {"label": "Datum",                 "waarde_kolom": "answer_date"},
    "tijd":         {"label": "Tijd",                  "waarde_kolom": "answer_text"},
    "datumtijd":    {"label": "Datum en tijd",         "waarde_kolom": "answer_date"},
    "foto":         {"label": "Foto",                  "waarde_kolom": "answer_text"},
    "bestand":      {"label": "Bestand (PDF, certificaat)", "waarde_kolom": "answer_text"},
    "handtekening": {"label": "Handtekening",          "waarde_kolom": "answer_text"},
    "locatie":      {"label": "GPS-locatie",           "waarde_kolom": "answer_text"},
}

# Velden waarvan het antwoord zelf een bewijsstuk is. Bij deze types hoeft de
# uitvoerder niet apart bewijs te uploaden -- het veld ís het bewijs.
BEWIJS_VELDTYPES: frozenset[str] = frozenset({"foto", "bestand", "handtekening"})

# Types met een keuzelijst. Zonder opties is zo'n veld onbruikbaar buiten.
OPTIE_VELDTYPES: frozenset[str] = frozenset({"keuze", "meerkeuze", "checklist"})

# Vaste antwoordwoorden. Vrije tekst hier zou "ok", "OK", "akk" en "akkoord"
# naast elkaar opleveren en dan telt de voortgang niet meer.
AKKOORD_WAARDEN: tuple[str, ...] = ("akkoord", "niet_akkoord", "nvt")
KEURING_WAARDEN: tuple[str, ...] = ("goed", "afgekeurd", "nvt")
JA_NEE_NVT_WAARDEN: tuple[str, ...] = ("ja", "nee", "nvt")


# ─────────────────────────────────────────────────────────────────────────────
# Werksoorten
# ─────────────────────────────────────────────────────────────────────────────

WERKSOORTEN: dict[str, str] = {
    "wegen":       "Wegen en verharding",
    "constructie": "Constructie en kunstwerken",
    "graafwerk":   "Graafwerkzaamheden en grondwerk",
    "riool":       "Riolering en leidingen",
    "algemeen":    "Algemeen",
}

# Hoe vaak een keuring terugkomt. Bepaalt samen met `verwacht_aantal` de
# voortgang: uitgevoerde registraties tegenover verwachte registraties.
FREQUENTIES: dict[str, str] = {
    "eenmalig":     "Eenmalig",
    "dagelijks":    "Dagelijks",
    "wekelijks":    "Wekelijks",
    "per_werkvak":  "Per werkvak",
    "per_100m":     "Per 100 meter",
    "per_object":   "Per object",
    "per_levering": "Per levering",
    "per_ploeg":    "Per ploeg",
    "vrij":         "Vrij (zelf omschrijven)",
}

# Resultaat van een registratie als geheel.
RESULTATEN: dict[str, str] = {
    "akkoord":         "Akkoord",
    "niet_akkoord":    "Niet akkoord",
    "deels_akkoord":   "Deels akkoord",
    "niet_beoordeeld": "Niet beoordeeld",
}

REGISTRATIE_STATUSSEN: tuple[str, ...] = (
    "concept", "in_uitvoering", "ingediend", "in_beoordeling",
    "goedgekeurd", "afgekeurd", "herziening_vereist",
)

BEVINDING_STATUSSEN: tuple[str, ...] = (
    "open", "in_behandeling", "opgelost", "gecontroleerd", "gesloten",
)

ERNST_NIVEAUS: dict[str, str] = {
    "laag":     "Laag",
    "middel":   "Middel",
    "hoog":     "Hoog",
    "kritiek":  "Kritiek",
}


# ─────────────────────────────────────────────────────────────────────────────
# Het oordeel
# ─────────────────────────────────────────────────────────────────────────────

def beoordeel_meetwaarde(
    waarde: Optional[float],
    *,
    norm_min: Optional[float] = None,
    norm_max: Optional[float] = None,
    tolerantie: Optional[float] = None,
) -> str:
    """Toets een gemeten waarde aan de norm. Geeft een sleutel uit RESULTATEN.

    De tolerantie verruimt de grenzen aan beide kanten. Een ondergrens van 98
    met tolerantie 1 keurt 97 nog goed; een bovengrens van 165 met tolerantie 5
    keurt 170 nog goed. Zo staat het in een bestek: de eis met de toegestane
    afwijking eromheen.

    Zonder norm geen oordeel. Dat is de belangrijkste regel van deze module:
    ``niet_beoordeeld`` betekent dat er iemand naar moet kijken, en dat is
    eerlijker dan een groen vinkje dat nergens op gebaseerd is.
    """
    if waarde is None:
        return "niet_beoordeeld"
    if norm_min is None and norm_max is None:
        return "niet_beoordeeld"

    speling = abs(tolerantie) if tolerantie is not None else 0.0

    if norm_min is not None and waarde < (norm_min - speling):
        return "niet_akkoord"
    if norm_max is not None and waarde > (norm_max + speling):
        return "niet_akkoord"
    return "akkoord"


def beoordeel_registratie(oordelen: list[str]) -> str:
    """Rol de veldoordelen op tot één resultaat voor de hele registratie.

    Eén afkeur maakt de registratie niet akkoord -- daar valt niet over te
    middelen. Staat er geen enkel oordeel, dan is de registratie niet
    beoordeeld. Is een deel beoordeeld en de rest niet, dan is het deels
    akkoord: de uitvoerder is nog niet klaar, en dat moet je kunnen zien.
    """
    if any(o == "niet_akkoord" for o in oordelen):
        return "niet_akkoord"
    akkoord = [o for o in oordelen if o == "akkoord"]
    if not akkoord:
        return "niet_beoordeeld"
    if len(akkoord) == len(oordelen):
        return "akkoord"
    return "deels_akkoord"


def voortgang(uitgevoerd: int, verwacht: Optional[int]) -> Optional[int]:
    """Percentage afgeronde registraties, afgekapt op 100.

    Geen verwacht aantal betekent geen percentage. Een keuring die "zo vaak als
    nodig" wordt gedaan heeft geen noemer, en 0% tonen zou suggereren dat er
    werk open staat dat niet bestaat.
    """
    if not verwacht or verwacht <= 0:
        return None
    return min(100, round(uitgevoerd / verwacht * 100))


# ─────────────────────────────────────────────────────────────────────────────
# Standaardkeuringen
# ─────────────────────────────────────────────────────────────────────────────
# Elk sjabloon is een keuring met velden en eisen. De velden zijn wat je buiten
# invult; de eisen zijn waaraan het moet voldoen en of daar bewijs bij hoort.
#
# `norm_min`, `norm_max` en `tolerantie` staan bewust op None waar het getal uit
# het bestek komt. De beheerder vult ze in bij het aanmaken; tot dat moment
# staat er `niet_beoordeeld` en dat is de bedoeling.

def _v(code, label, veldtype, **kw) -> dict:
    veld = {"code": code, "label": label, "veldtype": veldtype,
            "verplicht": kw.pop("verplicht", False), "eenheid": kw.pop("eenheid", None),
            "norm_min": kw.pop("norm_min", None), "norm_max": kw.pop("norm_max", None),
            "tolerantie": kw.pop("tolerantie", None), "opties": kw.pop("opties", None),
            "toelichting": kw.pop("toelichting", None)}
    veld.update(kw)
    return veld


def _e(nummer, titel, **kw) -> dict:
    return {"eisnummer": nummer, "titel": titel,
            "omschrijving": kw.get("omschrijving"),
            "norm": kw.get("norm"), "bron": kw.get("bron"),
            "meetmethode": kw.get("meetmethode"),
            "tolerantie": kw.get("tolerantie"),
            "bewijs_vereist": kw.get("bewijs_vereist", False)}


TEMPLATES: list[dict] = [
    # ── Wegen ────────────────────────────────────────────────────────────────
    {
        "code": "weg.asfalt",
        "werksoort": "wegen",
        "naam": "Aanbrengen asfaltverharding",
        "omschrijving": "Controle van ondergrond tot en met profilering bij het "
                        "aanbrengen van een asfaltlaag.",
        "frequentie": "per_werkvak",
        "velden": [
            _v("ondergrond_schoon", "Ondergrond schoon en vrij van losse delen", "ja_nee",
               verplicht=True),
            _v("ondergrond_vlak", "Ondergrond vlak en op hoogte", "ja_nee", verplicht=True),
            _v("kleeflaag", "Kleeflaag aangebracht en afgebonden", "ja_nee_nvt"),
            _v("mengsel", "Toegepast mengsel", "tekst_kort", verplicht=True,
               toelichting="Zoals opgegeven in het bestek of op de weegbon."),
            _v("weegbon", "Weegbon / leveringsbon", "bestand",
               toelichting="Bewijs van het geleverde mengsel en de hoeveelheid."),
            _v("temp_aanvoer", "Temperatuur bij aanvoer", "meetwaarde", eenheid="°C",
               verplicht=True,
               toelichting="Grenzen uit het bestek; vul ze in voordat je gaat keuren."),
            _v("temp_verwerking", "Temperatuur bij verwerking", "meetwaarde", eenheid="°C"),
            _v("laagdikte", "Laagdikte", "meetwaarde", eenheid="mm", verplicht=True),
            _v("verdichting", "Verdichtingsgraad", "meetwaarde", eenheid="%"),
            _v("vlakheid", "Vlakheid gecontroleerd met rei", "ja_nee"),
            _v("aansluitingen", "Aansluitingen op putten en banden", "akkoord"),
            _v("naden", "Naden en kanten afgewerkt", "akkoord"),
            _v("foto_werkvak", "Foto van het werkvak", "foto", verplicht=True),
            _v("opmerking", "Bijzonderheden", "tekst_lang"),
        ],
        "eisen": [
            _e("01", "Ondergrond gereed voor asfaltering",
               omschrijving="Schoon, vlak, droog genoeg en op de juiste hoogte.",
               bron="Standaard RAW Bepalingen", meetmethode="Visueel en waterpas",
               bewijs_vereist=True),
            _e("02", "Mengsel conform bestek",
               omschrijving="Het geleverde mengsel komt overeen met wat is voorgeschreven.",
               norm="NEN-EN 13108-serie (asfaltmengsels)", bron="Bestek en weegbon",
               meetmethode="Weegbon vergelijken met bestek", bewijs_vereist=True),
            _e("03", "Verwerkingstemperatuur binnen de grenzen",
               omschrijving="Te koud verwerken kost verdichting, te heet beschadigt het bitumen.",
               bron="Standaard RAW Bepalingen en leveranciersopgave",
               meetmethode="Steekthermometer in de laag", bewijs_vereist=True),
            _e("04", "Laagdikte volgens bestek",
               bron="Standaard RAW Bepalingen",
               meetmethode="Meting of boorkern", bewijs_vereist=True),
            _e("05", "Vlakheid en profilering",
               omschrijving="Geen plasvorming, afschot volgens tekening.",
               bron="Standaard RAW Bepalingen", meetmethode="Rei en waterpas"),
        ],
    },
    {
        "code": "weg.fundering",
        "werksoort": "wegen",
        "naam": "Fundering en verdichting",
        "omschrijving": "Controle van de funderingslaag voordat de verharding erop gaat.",
        "frequentie": "per_100m",
        "velden": [
            _v("materiaal", "Toegepast funderingsmateriaal", "tekst_kort", verplicht=True),
            _v("certificaat", "Certificaat / kwaliteitsverklaring", "bestand"),
            _v("laagdikte", "Laagdikte fundering", "meetwaarde", eenheid="mm", verplicht=True),
            _v("verdichting", "Verdichtingsgraad", "meetwaarde", eenheid="%", verplicht=True,
               toelichting="Ondergrens uit het bestek invullen voordat je keurt."),
            _v("meetrapport", "Meetrapport verdichting", "bestand"),
            _v("hoogte", "Hoogteligging gecontroleerd", "ja_nee", verplicht=True),
            _v("draagkracht", "Draagkracht beproefd", "ja_nee_nvt"),
            _v("foto", "Foto van de laag", "foto", verplicht=True),
            _v("opmerking", "Bijzonderheden", "tekst_lang"),
        ],
        "eisen": [
            _e("01", "Materiaal conform bestek",
               bron="Standaard RAW Bepalingen", bewijs_vereist=True),
            _e("02", "Verdichting voldoet aan de gestelde eis",
               omschrijving="De minimale verdichtingsgraad staat in het bestek.",
               bron="Standaard RAW Bepalingen",
               meetmethode="Proctor / plaatbelastingsproef", bewijs_vereist=True),
            _e("03", "Laagdikte en hoogteligging volgens tekening",
               bron="Bestek en revisietekening", meetmethode="Waterpas"),
        ],
    },
    {
        "code": "weg.elementen",
        "werksoort": "wegen",
        "naam": "Elementenverharding (straatwerk)",
        "omschrijving": "Controle van straatwerk: bed, verband, hoogte en afwerking.",
        "frequentie": "per_werkvak",
        "velden": [
            _v("straatlaag", "Straatlaag op dikte en vlak", "ja_nee", verplicht=True),
            _v("verband", "Verband", "keuze",
               opties=["Halfsteens", "Keperverband", "Blokverband", "Elleboogverband", "Anders"]),
            _v("materiaal", "Toegepast element", "tekst_kort", verplicht=True),
            _v("hoogte_afwijking", "Afwijking hoogteligging", "meetwaarde", eenheid="mm"),
            _v("afschot", "Afschot volgens tekening", "ja_nee", verplicht=True),
            _v("aansluiting_kolken", "Aansluiting op kolken en putten", "akkoord"),
            _v("voegvulling", "Voegen gevuld en ingeveegd", "ja_nee"),
            _v("banden", "Banden op lijn en hoogte", "akkoord"),
            _v("foto", "Foto van het werkvak", "foto", verplicht=True),
            _v("opmerking", "Bijzonderheden", "tekst_lang"),
        ],
        "eisen": [
            _e("01", "Straatlaag gereed",
               bron="Standaard RAW Bepalingen", meetmethode="Visueel en waterpas"),
            _e("02", "Hoogteligging en afschot volgens tekening",
               omschrijving="Water moet weg kunnen; geen plasvorming na regen.",
               bron="Bestek en tekening", meetmethode="Waterpas", bewijs_vereist=True),
            _e("03", "Aansluitingen sluitend en zonder rammelaars",
               bron="Standaard RAW Bepalingen", meetmethode="Visueel"),
        ],
    },
    {
        "code": "weg.markering",
        "werksoort": "wegen",
        "naam": "Markering aanbrengen",
        "omschrijving": "Controle van wegmarkering: ondergrond, uitvoering en maatvoering.",
        "frequentie": "per_werkvak",
        "velden": [
            _v("ondergrond_droog", "Ondergrond droog en schoon", "ja_nee", verplicht=True),
            _v("type_markering", "Type markering", "keuze",
               opties=["Verf", "Thermoplast", "Koudplast", "Folie", "Anders"]),
            _v("breedte", "Breedte van de streep", "meetwaarde", eenheid="mm"),
            _v("maatvoering", "Maatvoering volgens tekening", "ja_nee", verplicht=True),
            _v("retroreflectie", "Retroreflectie gemeten", "meetwaarde", eenheid="mcd/lx/m²"),
            _v("foto", "Foto van de markering", "foto", verplicht=True),
            _v("opmerking", "Bijzonderheden", "tekst_lang"),
        ],
        "eisen": [
            _e("01", "Ondergrond geschikt voor aanbrengen",
               omschrijving="Droog en vrij van stof; anders hecht de markering niet.",
               bron="Leveranciersvoorschrift", bewijs_vereist=True),
            _e("02", "Maatvoering volgens tekening",
               bron="Bestek en tekening", meetmethode="Meetlint"),
        ],
    },
    {
        "code": "weg.afzetting",
        "werksoort": "wegen",
        "naam": "Verkeersmaatregelen en afzetting",
        "omschrijving": "Controle of de afzetting staat zoals afgesproken, voordat het "
                        "werk begint en tijdens de uitvoering.",
        "frequentie": "dagelijks",
        "velden": [
            _v("volgens_plan", "Afzetting volgens goedgekeurd plan", "ja_nee", verplicht=True),
            _v("figuur", "Toegepaste figuur / maatregel", "tekst_kort"),
            _v("borden", "Borden compleet en leesbaar", "akkoord", verplicht=True),
            _v("verlichting", "Verlichting en bebakening in orde", "ja_nee_nvt"),
            _v("doorgang_voetgangers", "Doorgang voetgangers en fietsers geborgd", "ja_nee",
               verplicht=True),
            _v("nooddiensten", "Doorgang nood- en hulpdiensten geborgd", "ja_nee",
               verplicht=True),
            _v("foto", "Foto van de afzetting", "foto", verplicht=True),
            _v("opmerking", "Bijzonderheden", "tekst_lang"),
        ],
        "eisen": [
            _e("01", "Afzetting conform het goedgekeurde verkeersplan",
               norm="CROW 96b (werk in uitvoering, niet-autosnelwegen)",
               bron="Verkeersplan en vergunning", bewijs_vereist=True),
            _e("02", "Doorgang voor voetgangers, fietsers en hulpdiensten",
               omschrijving="Een afzetting die de stoep dichtzet is niet akkoord.",
               bron="Verkeersplan en vergunning", meetmethode="Visueel ter plaatse"),
        ],
    },

    # ── Constructie ──────────────────────────────────────────────────────────
    {
        "code": "con.beton",
        "werksoort": "constructie",
        "naam": "Betonwerk storten",
        "omschrijving": "Controle van bekisting, wapening en stort tot en met nabehandeling.",
        "frequentie": "per_object",
        "velden": [
            _v("bekisting", "Bekisting maatvast, schoon en gesteld", "ja_nee", verplicht=True),
            _v("wapening_volgens_tekening", "Wapening volgens tekening", "ja_nee",
               verplicht=True),
            _v("dekking", "Betondekking", "meetwaarde", eenheid="mm", verplicht=True),
            _v("wapening_vrijgave", "Wapening vrijgegeven voor stort", "akkoord",
               verplicht=True),
            _v("foto_wapening", "Foto wapening vóór stort", "foto", verplicht=True,
               toelichting="Na het storten is dit niet meer te controleren."),
            _v("betonspecie", "Betonspecificatie", "tekst_kort", verplicht=True),
            _v("bon", "Begeleidingsbon betonmortel", "bestand", verplicht=True),
            _v("consistentie", "Consistentie (zetmaat)", "meetwaarde", eenheid="mm"),
            _v("temperatuur", "Temperatuur betonspecie", "meetwaarde", eenheid="°C"),
            _v("kubussen", "Proefkubussen genomen", "ja_nee_nvt"),
            _v("verdichten", "Verdicht en nabehandeld", "ja_nee", verplicht=True),
            _v("foto_resultaat", "Foto na het storten", "foto"),
            _v("opmerking", "Bijzonderheden", "tekst_lang"),
        ],
        "eisen": [
            _e("01", "Wapening volgens tekening en vrijgegeven",
               omschrijving="Storten zonder vrijgave is onomkeerbaar; hier hoort een "
                            "foto en een handtekening bij.",
               bron="Constructietekening en bestek",
               meetmethode="Visuele controle en maatcontrole", bewijs_vereist=True),
            _e("02", "Betondekking voldoende",
               omschrijving="Te weinig dekking is de meest voorkomende oorzaak van "
                            "wapeningscorrosie.",
               norm="NEN-EN 1992-serie (Eurocode 2)", bron="Constructieberekening",
               meetmethode="Afstandhouders en steekmaat", bewijs_vereist=True),
            _e("03", "Betonspecie conform specificatie",
               norm="NEN-EN 206 (beton — specificatie en conformiteit)",
               bron="Begeleidingsbon", bewijs_vereist=True),
            _e("04", "Verdicht en nabehandeld",
               omschrijving="Nabehandeling bepaalt de uiteindelijke sterkte en scheurvorming.",
               bron="Standaard RAW Bepalingen", meetmethode="Visueel"),
        ],
    },
    {
        "code": "con.staal",
        "werksoort": "constructie",
        "naam": "Staalconstructie en verbindingen",
        "omschrijving": "Controle van montage, verbindingen en conservering.",
        "frequentie": "per_object",
        "velden": [
            _v("certificaten", "Certificaten materiaal aanwezig", "ja_nee", verplicht=True),
            _v("certificaat_bestand", "Certificaat", "bestand"),
            _v("maatvoering", "Maatvoering gecontroleerd", "ja_nee", verplicht=True),
            _v("bouten_type", "Type bouten", "tekst_kort"),
            _v("aanhaalmoment", "Aanhaalmoment", "meetwaarde", eenheid="Nm"),
            _v("lassen", "Lassen visueel gecontroleerd", "akkoord"),
            _v("laskeuring", "Laskeuringsrapport", "bestand"),
            _v("conservering", "Conservering onbeschadigd", "ja_nee"),
            _v("laagdikte_coating", "Laagdikte conservering", "meetwaarde", eenheid="µm"),
            _v("foto", "Foto van de verbinding", "foto", verplicht=True),
            _v("opmerking", "Bijzonderheden", "tekst_lang"),
        ],
        "eisen": [
            _e("01", "Materiaalcertificaten aanwezig en kloppend",
               norm="NEN-EN 1090-serie (uitvoering staalconstructies)",
               bron="Leveranciersdocumentatie", bewijs_vereist=True),
            _e("02", "Verbindingen op moment aangehaald",
               bron="Constructietekening en bestek",
               meetmethode="Momentsleutel", bewijs_vereist=True),
            _e("03", "Conservering intact",
               omschrijving="Beschadigingen bij montage moeten hersteld worden.",
               bron="Bestek", meetmethode="Laagdiktemeter"),
        ],
    },

    # ── Graafwerk ────────────────────────────────────────────────────────────
    {
        "code": "graaf.ontgraving",
        "werksoort": "graafwerk",
        "naam": "Graafwerkzaamheden — start en uitvoering",
        "omschrijving": "Controle vóór en tijdens graven: klic-melding, proefsleuven, "
                        "kabels en leidingen, en de sleuf zelf.",
        "frequentie": "per_werkvak",
        "velden": [
            _v("klic_aanwezig", "KLIC-melding aanwezig en actueel", "ja_nee", verplicht=True,
               toelichting="Een KLIC-melding is beperkt geldig; controleer de datum."),
            _v("klic_nummer", "KLIC-meldnummer", "tekst_kort"),
            _v("klic_bestand", "KLIC-gegevens", "bestand"),
            _v("tekening_op_locatie", "Kabel- en leidingtekening op de locatie aanwezig",
               "ja_nee", verplicht=True),
            _v("proefsleuven", "Proefsleuven gegraven waar voorgeschreven", "ja_nee_nvt",
               verplicht=True),
            _v("liggingsafwijking", "Grootste afwijking t.o.v. tekening", "meetwaarde",
               eenheid="m"),
            _v("afwijking_gemeld", "Afwijkende ligging gemeld aan de netbeheerder",
               "ja_nee_nvt"),
            _v("graafploeg_geinstrueerd", "Graafploeg geïnstrueerd over de ligging", "ja_nee",
               verplicht=True),
            _v("sleufbreedte", "Sleufbreedte", "meetwaarde", eenheid="m"),
            _v("sleufdiepte", "Sleufdiepte", "meetwaarde", eenheid="m"),
            _v("talud_bemaling", "Talud of grondkering aangebracht", "ja_nee_nvt",
               verplicht=True),
            _v("bodemkwaliteit", "Bodemkwaliteit bekend / partijkeuring", "ja_nee_nvt"),
            _v("grondscheiding", "Grondsoorten gescheiden ontgraven en opgeslagen", "ja_nee"),
            _v("foto_sleuf", "Foto van de open sleuf", "foto", verplicht=True),
            _v("opmerking", "Bijzonderheden", "tekst_lang"),
        ],
        "eisen": [
            _e("01", "Zorgvuldig graven — gegevens aanwezig en gedeeld",
               omschrijving="Graven mag pas als de ligging bekend is en de ploeg die kent.",
               norm="CROW 500 (schade door graafwerkzaamheden voorkomen)",
               bron="WIBON en KLIC-melding",
               meetmethode="Documentcontrole op de locatie", bewijs_vereist=True),
            _e("02", "Proefsleuven waar de ligging onzeker is",
               omschrijving="Afwijkingen worden gemeld aan de netbeheerder.",
               norm="CROW 500", bron="Graafmeldingsprocedure", bewijs_vereist=True),
            _e("03", "Sleuf veilig — talud of grondkering",
               omschrijving="Een instortende sleuf is het grootste risico van dit werk.",
               bron="Arbeidsomstandighedenwetgeving en V&G-plan",
               meetmethode="Visueel ter plaatse", bewijs_vereist=True),
            _e("04", "Omgang met verontreinigde grond",
               omschrijving="Alleen van toepassing als de bodemkwaliteit daartoe aanleiding geeft.",
               norm="CROW 400 (werken in en met verontreinigde bodem)",
               bron="Bodemonderzoek en V&G-plan"),
        ],
    },
    {
        "code": "graaf.aanvulling",
        "werksoort": "graafwerk",
        "naam": "Aanvullen en verdichten sleuf",
        "omschrijving": "Controle van het dichtmaken: materiaal, laagdikte en verdichting.",
        "frequentie": "per_werkvak",
        "velden": [
            _v("sleuf_leeg", "Sleuf vrij van puin, hout en gereedschap", "ja_nee",
               verplicht=True),
            _v("leidingen_gecontroleerd", "Leidingen en kabels onbeschadigd", "ja_nee",
               verplicht=True),
            _v("foto_voor_aanvullen", "Foto vóór het aanvullen", "foto", verplicht=True,
               toelichting="Daarna is er niets meer te zien."),
            _v("aanvulmateriaal", "Aanvulmateriaal", "tekst_kort", verplicht=True),
            _v("laagdikte_aanvulling", "Laagdikte per aanvullaag", "meetwaarde", eenheid="mm"),
            _v("verdichting", "Verdichtingsgraad", "meetwaarde", eenheid="%"),
            _v("meetrapport", "Meetrapport verdichting", "bestand"),
            _v("waarschuwingslint", "Waarschuwingslint of afdekking aangebracht",
               "ja_nee_nvt"),
            _v("maaiveld_hersteld", "Maaiveld op hoogte hersteld", "ja_nee", verplicht=True),
            _v("foto_na", "Foto na herstel", "foto"),
            _v("opmerking", "Bijzonderheden", "tekst_lang"),
        ],
        "eisen": [
            _e("01", "Leidingen onbeschadigd bij dichtmaken",
               omschrijving="Schade die onder de grond verdwijnt komt jaren later terug.",
               norm="CROW 500", bron="Netbeheerdersvoorschrift", bewijs_vereist=True),
            _e("02", "Aanvulling in lagen verdicht",
               omschrijving="Te dikke lagen leveren nazakking en dan verzakt de verharding.",
               bron="Standaard RAW Bepalingen",
               meetmethode="Laagdikte en verdichtingsmeting", bewijs_vereist=True),
            _e("03", "Maaiveld hersteld op hoogte",
               bron="Bestek", meetmethode="Waterpas"),
        ],
    },

    # ── Riool ────────────────────────────────────────────────────────────────
    {
        "code": "riool.leiding",
        "werksoort": "riool",
        "naam": "Rioolleiding leggen",
        "omschrijving": "Controle van bed, ligging, verhang en verbindingen.",
        "frequentie": "per_100m",
        "velden": [
            _v("materiaal", "Buismateriaal en diameter", "tekst_kort", verplicht=True),
            _v("certificaat", "Kwaliteitsverklaring buismateriaal", "bestand"),
            _v("bed", "Leidingbed op dikte en vlak", "ja_nee", verplicht=True),
            _v("bob_begin", "BOB begin", "meetwaarde", eenheid="m NAP", verplicht=True),
            _v("bob_eind", "BOB eind", "meetwaarde", eenheid="m NAP", verplicht=True),
            _v("verhang", "Verhang volgens tekening", "ja_nee", verplicht=True),
            _v("verbindingen", "Verbindingen gemaakt volgens voorschrift", "akkoord",
               verplicht=True),
            _v("afdichting", "Afdichtingsringen gecontroleerd", "ja_nee"),
            _v("aansluitingen", "Huisaansluitingen aangebracht en ingemeten", "ja_nee_nvt"),
            _v("omhulling", "Omhulling en aanvulling rond de buis", "ja_nee", verplicht=True),
            _v("foto", "Foto van de gelegde leiding", "foto", verplicht=True),
            _v("opmerking", "Bijzonderheden", "tekst_lang"),
        ],
        "eisen": [
            _e("01", "Leidingbed vlak en op hoogte",
               omschrijving="Een buis op een ongelijk bed zakt door en gaat lekken.",
               norm="NEN-EN 1610 (buitenriolering — leggen en beproeven)",
               bron="Bestek", meetmethode="Waterpas", bewijs_vereist=True),
            _e("02", "Ligging en verhang volgens tekening",
               omschrijving="Onvoldoende verhang geeft stankoverlast en aanslag.",
               norm="NEN-EN 1610", bron="Rioleringstekening",
               meetmethode="Waterpas, BOB-meting", bewijs_vereist=True),
            _e("03", "Verbindingen volgens voorschrift",
               norm="NEN-EN 1610", bron="Leveranciersvoorschrift", bewijs_vereist=True),
        ],
    },
    {
        "code": "riool.beproeving",
        "werksoort": "riool",
        "naam": "Beproeving en inspectie riool",
        "omschrijving": "Dichtheidsbeproeving en camera-inspectie na aanleg.",
        "frequentie": "per_object",
        "velden": [
            _v("beproevingsmethode", "Beproevingsmethode", "keuze", verplicht=True,
               opties=["Lucht", "Water", "Beide"]),
            _v("proefdruk", "Proefdruk", "meetwaarde", eenheid="mbar"),
            _v("proefduur", "Proefduur", "meetwaarde", eenheid="min"),
            _v("drukverlies", "Drukverlies", "meetwaarde", eenheid="mbar"),
            _v("resultaat_beproeving", "Beproeving geslaagd", "keuring", verplicht=True),
            _v("beproevingsrapport", "Beproevingsrapport", "bestand", verplicht=True),
            _v("camera_inspectie", "Camera-inspectie uitgevoerd", "ja_nee_nvt"),
            _v("inspectierapport", "Inspectierapport", "bestand"),
            _v("putten_schoon", "Putten schoon opgeleverd", "ja_nee"),
            _v("revisie", "Revisiegegevens ingemeten", "ja_nee", verplicht=True),
            _v("foto", "Foto van de beproeving", "foto"),
            _v("opmerking", "Bijzonderheden", "tekst_lang"),
        ],
        "eisen": [
            _e("01", "Dichtheid aangetoond",
               omschrijving="Zonder geslaagde beproeving is het riool niet opgeleverd.",
               norm="NEN-EN 1610 (buitenriolering — leggen en beproeven)",
               bron="Bestek", meetmethode="Lucht- of waterbeproeving",
               bewijs_vereist=True),
            _e("02", "Camera-inspectie zonder afkeurcriteria",
               norm="NEN-EN 13508-serie (toestandsbeoordeling riolering)",
               bron="Bestek", bewijs_vereist=True),
            _e("03", "Revisiegegevens compleet",
               omschrijving="Wat niet is ingemeten, is over tien jaar kwijt.",
               bron="Bestek", bewijs_vereist=True),
        ],
    },

    # ── Algemeen ─────────────────────────────────────────────────────────────
    {
        "code": "alg.materiaal",
        "werksoort": "algemeen",
        "naam": "Materiaalcontrole bij levering",
        "omschrijving": "Controle van een levering voordat het materiaal wordt verwerkt.",
        "frequentie": "per_levering",
        "velden": [
            _v("leverancier", "Leverancier", "tekst_kort", verplicht=True),
            _v("product", "Product", "tekst_kort", verplicht=True),
            _v("hoeveelheid", "Geleverde hoeveelheid", "getal"),
            _v("bon", "Leveringsbon", "bestand", verplicht=True),
            _v("certificaat", "Certificaat / kwaliteitsverklaring", "bestand"),
            _v("conform_bestek", "Conform bestek", "akkoord", verplicht=True),
            _v("schade", "Transportschade geconstateerd", "ja_nee"),
            _v("opslag", "Correct opgeslagen", "ja_nee"),
            _v("foto", "Foto van de levering", "foto", verplicht=True),
            _v("opmerking", "Bijzonderheden", "tekst_lang"),
        ],
        "eisen": [
            _e("01", "Geleverd materiaal conform bestek",
               bron="Bestek en leveringsbon",
               meetmethode="Bon vergelijken met bestek", bewijs_vereist=True),
            _e("02", "Certificaat aanwezig waar voorgeschreven",
               bron="Bestek", bewijs_vereist=True),
        ],
    },
]


TEMPLATES_OP_CODE: dict[str, dict] = {t["code"]: t for t in TEMPLATES}


def templates_voor(werksoort: Optional[str] = None) -> list[dict]:
    """Sjablonen, eventueel gefilterd op werksoort."""
    if not werksoort:
        return list(TEMPLATES)
    return [t for t in TEMPLATES if t["werksoort"] == werksoort]


def template(code: str) -> Optional[dict]:
    return TEMPLATES_OP_CODE.get(code)
