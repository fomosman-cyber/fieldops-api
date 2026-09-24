"""Materieel en de CO2 die het uitstoot.

Dit bestand kent drie dingen: welke soorten materieel er zijn, welke
energiedragers er bestaan, en hoe je van een dag draaien naar kilogrammen CO2
komt. De registratie zelf (welke kraan, welke leverancier, hoeveel uur) staat
in de modellen; het rapport staat in de router.

**Zonder ingevuld getal geen uitstoot.** Er zijn drie uitkomsten, niet twee:

  gemeten    Er staan liters of kWh in. Hoeveelheid maal factor. Dit is het
             getal waar een auditor van de CO2-Prestatieladder op rekent.
  geschat    Er staan alleen draaiuren, en het materieelstuk heeft een
             ingevuld verbruik per uur. Uren maal verbruik maal factor, en
             overal waar dit getal verschijnt staat erbij dat het geschat is.
  onbekend   Geen van beide. Dan is er geen getal. Niet nul -- nul betekent
             "deze machine heeft niet uitgestoten" en dat is een bewering die
             we niet kunnen doen over een dag waarop niemand iets invulde.

**De factoren komen uit de landelijke lijst**, niet uit ons hoofd. Ze staan in
``data/co2_factoren.json`` met bron en versiedatum erbij, en elke berekening
draagt die bron mee, zodat in het rapport te zien is waarmee gerekend is. Een
organisatie die van haar opdrachtgever of auditor andere factoren moet
aanhouden, zet ze over in de instellingen; dan wint die van de lijst.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

FACTOREN_BESTAND = Path(__file__).parent / "data" / "co2_factoren.json"


# ── Wat voor materieel ───────────────────────────────────────────────
# Bewust grof. De soort bepaalt niets in de berekening; hij groepeert het
# rapport en maakt de keuzelijst doorzoekbaar. Wie fijner wil indelen gebruikt
# het vrije veld `omschrijving`.

SOORTEN: dict[str, str] = {
    "graafmachine":   "Graafmachine / kraan",
    "shovel":         "Shovel / wiellader",
    "dumper":         "Dumper / kipper",
    "vrachtwagen":    "Vrachtwagen",
    "bedrijfsbus":    "Bedrijfsbus / pickup",
    "personenauto":   "Personenauto",
    "walsen":         "Wals / verdichter",
    "asfaltmachine":  "Asfaltspreidmachine",
    "freesmachine":   "Freesmachine",
    "trilplaat":      "Trilplaat / stamper",
    "aggregaat":      "Aggregaat / generator",
    "pomp":           "Pomp",
    "hoogwerker":     "Hoogwerker",
    "verreiker":      "Verreiker / heftruck",
    "veegmachine":    "Veegmachine",
    "maaimachine":    "Maaimachine",
    "handgereedschap": "Handgereedschap (motor)",
    "aanhanger":      "Aanhanger / oplegger",
    "keet":           "Keet / container",
    "afzetting":      "Afzetmateriaal",
    "overig":         "Overig",
}

# Soorten die uit zichzelf niets verbruiken. Ze mogen wel op een dag staan --
# een afzetting huur je en kost geld -- maar de CO2-kolom blijft leeg in
# plaats van nul, en het formulier vraagt niet om liters.
SOORTEN_ZONDER_ENERGIE = {"aanhanger", "keet", "afzetting"}


# ── Waar het op loopt ────────────────────────────────────────────────
# `factor_code` wijst naar een rij in data/co2_factoren.json. `eenheid` is
# waarin je tankt of laadt: dat is wat de gebruiker invult.

ENERGIEDRAGERS: dict[str, dict[str, Any]] = {
    "diesel":      {"naam": "Diesel (B7)",            "eenheid": "liter", "factor_code": "diesel"},
    "hvo100":      {"naam": "HVO100",                  "eenheid": "liter", "factor_code": "hvo100"},
    "gtl":         {"naam": "GTL",                     "eenheid": "liter", "factor_code": "gtl"},
    "benzine":     {"naam": "Benzine (E10)",           "eenheid": "liter", "factor_code": "benzine"},
    "lpg":         {"naam": "LPG",                     "eenheid": "liter", "factor_code": "lpg"},
    "cng":         {"naam": "CNG / aardgas",           "eenheid": "kg",    "factor_code": "cng"},
    "elektrisch":  {"naam": "Elektrisch (netstroom)",  "eenheid": "kWh",   "factor_code": "elektrisch_net"},
    "elektrisch_groen": {"naam": "Elektrisch (groene stroom)", "eenheid": "kWh", "factor_code": "elektrisch_groen"},
    "geen":        {"naam": "Geen (niet aangedreven)", "eenheid": "",      "factor_code": None},
}

# Hybride materieel bestaat, maar een hybride kraan die je een dag draait
# tankt diesel en laadt stroom. Twee regels op dezelfde dag is eerlijker dan
# een halve factor verzinnen, dus 'hybride' is geen energiedrager.


# ── Hoe schoon de motor is ───────────────────────────────────────────
# Dit rekent nergens in mee. Het staat er omdat opdrachtgevers erop gunnen en
# erop handhaven: Stage-eisen in het bestek, milieuzones, het Schone Lucht
# Akkoord. Wie het niet weet vult 'onbekend' in, en dat is beter dan een
# klasse aankruisen die niet op het typeplaatje staat.

EMISSIEKLASSEN: dict[str, str] = {
    "stage_i":    "Stage I",
    "stage_ii":   "Stage II",
    "stage_iiia": "Stage IIIA",
    "stage_iiib": "Stage IIIB",
    "stage_iv":   "Stage IV",
    "stage_v":    "Stage V",
    "euro_4":     "Euro 4 (weg)",
    "euro_5":     "Euro 5 (weg)",
    "euro_6":     "Euro 6 (weg)",
    "elektrisch": "Emissieloos",
    "onbekend":   "Onbekend",
}

EIGENDOM: dict[str, str] = {
    "eigen":         "Eigen materieel",
    "huur":          "Gehuurd",
    "onderaannemer": "Van onderaannemer",
    "opdrachtgever": "Van opdrachtgever",
}


# ── De factoren ──────────────────────────────────────────────────────

_lijst_cache: Optional[dict] = None


def factorenlijst() -> dict:
    """De landelijke lijst, eenmalig ingelezen.

    Ontbreekt of is het bestand stuk, dan komt er een lege lijst terug en
    levert elke berekening 'onbekend' op. Dat is vervelend maar eerlijk: een
    kapot bestand mag geen stille nullen produceren.
    """
    global _lijst_cache
    if _lijst_cache is None:
        try:
            _lijst_cache = json.loads(FACTOREN_BESTAND.read_text(encoding="utf-8"))
        except Exception:
            _lijst_cache = {"lijst": "", "versie": "", "bron": "", "factoren": []}
    return _lijst_cache


def factoren_per_code() -> dict[str, dict]:
    return {f["code"]: f for f in factorenlijst().get("factoren", [])}


@dataclass(frozen=True)
class Factor:
    """Eén emissiefactor, met waar hij vandaan komt."""
    code: str
    naam: str
    eenheid: str            # liter | kg | kWh
    kg_co2_per_eenheid: float
    bron: str
    versie: str
    eigen: bool = False     # True als de organisatie hem zelf heeft ingesteld

    @property
    def herkomst(self) -> str:
        if self.eigen:
            return "Eigen factor van de organisatie"
        return f"{self.bron} (versie {self.versie})" if self.bron else "onbekende bron"


def factor_voor(energiedrager: str, eigen_factoren: Optional[dict] = None) -> Optional[Factor]:
    """De factor voor deze energiedrager, of None als er niets mee te rekenen valt.

    ``eigen_factoren`` is de instelling van de organisatie: een map van
    energiedrager naar kg CO2 per eenheid. Staat de energiedrager daarin, dan
    wint dat getal van de landelijke lijst -- de organisatie weet beter dan
    wij met welke factor haar auditor rekent.
    """
    drager = ENERGIEDRAGERS.get(energiedrager)
    if not drager or not drager["factor_code"]:
        return None

    if eigen_factoren:
        eigen = eigen_factoren.get(energiedrager)
        if isinstance(eigen, (int, float)) and not isinstance(eigen, bool) and eigen >= 0:
            return Factor(code=drager["factor_code"], naam=drager["naam"],
                          eenheid=drager["eenheid"], kg_co2_per_eenheid=float(eigen),
                          bron="", versie="", eigen=True)

    rij = factoren_per_code().get(drager["factor_code"])
    if not rij or rij.get("wtw") is None:
        return None
    lijst = factorenlijst()
    return Factor(code=rij["code"], naam=rij["naam"], eenheid=rij["eenheid"],
                  kg_co2_per_eenheid=float(rij["wtw"]),
                  bron=rij.get("bron") or lijst.get("bron", ""),
                  versie=lijst.get("versie", ""))


# ── De berekening ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Berekening:
    """Wat er van één inzet-regel te zeggen valt over CO2."""
    kg: Optional[float]         # None = niet te berekenen
    methode: str                # gemeten | geschat | onbekend
    hoeveelheid: Optional[float] = None   # liters, kg of kWh waarmee gerekend is
    eenheid: str = ""
    factor: Optional[Factor] = None
    reden: str = ""             # waarom er niets berekend kon worden

    @property
    def is_schatting(self) -> bool:
        return self.methode == "geschat"

    def uitleg(self) -> str:
        """Eén regel die in het rapport onder de kolom past."""
        if self.methode == "onbekend":
            return self.reden or "Niet te berekenen"
        hoe = "gemeten" if self.methode == "gemeten" else "geschat uit draaiuren"
        f = self.factor
        stuk = f"{self.hoeveelheid:g} {self.eenheid} ({hoe})"
        if f:
            stuk += f" × {f.kg_co2_per_eenheid:g} kg/{f.eenheid} — {f.herkomst}"
        return stuk


def bereken(
    *,
    energiedrager: str,
    brandstof_hoeveelheid: Optional[float] = None,
    draaiuren: Optional[float] = None,
    verbruik_per_uur: Optional[float] = None,
    eigen_factoren: Optional[dict] = None,
) -> Berekening:
    """Van een dag inzet naar kilogrammen CO2.

    Gemeten gaat voor geschat: staan er liters in, dan tellen die, ook als er
    ook draaiuren en een standaardverbruik zijn. De tank liegt niet, het
    kengetal wel eens.
    """
    if energiedrager == "geen" or not energiedrager:
        return Berekening(kg=None, methode="onbekend",
                          reden="Dit materieel heeft geen eigen aandrijving")

    factor = factor_voor(energiedrager, eigen_factoren)
    if factor is None:
        return Berekening(kg=None, methode="onbekend",
                          reden=f"Geen emissiefactor bekend voor {energiedrager}")

    if brandstof_hoeveelheid is not None and brandstof_hoeveelheid >= 0:
        kg = round(float(brandstof_hoeveelheid) * factor.kg_co2_per_eenheid, 3)
        return Berekening(kg=kg, methode="gemeten",
                          hoeveelheid=float(brandstof_hoeveelheid),
                          eenheid=factor.eenheid, factor=factor)

    if draaiuren and verbruik_per_uur:
        hoeveelheid = float(draaiuren) * float(verbruik_per_uur)
        kg = round(hoeveelheid * factor.kg_co2_per_eenheid, 3)
        return Berekening(kg=kg, methode="geschat", hoeveelheid=round(hoeveelheid, 3),
                          eenheid=factor.eenheid, factor=factor)

    if draaiuren:
        return Berekening(kg=None, methode="onbekend", factor=factor,
                          reden="Wel draaiuren, geen verbruik per uur bij dit materieel")
    return Berekening(kg=None, methode="onbekend", factor=factor,
                      reden="Geen getankte hoeveelheid en geen draaiuren ingevuld")


def totaal(berekeningen: list[Berekening]) -> dict[str, Any]:
    """Tel een reeks regels op, met de onzekerheid erbij.

    Het totaal is geen enkel getal maar drie: wat gemeten is, wat geschat is,
    en hoeveel regels helemaal niets opleverden. Wie alleen de som laat zien
    verbergt precies datgene waar een auditor naar vraagt.
    """
    gemeten = sum(b.kg for b in berekeningen if b.methode == "gemeten" and b.kg is not None)
    geschat = sum(b.kg for b in berekeningen if b.methode == "geschat" and b.kg is not None)
    onbekend = sum(1 for b in berekeningen if b.methode == "onbekend")
    return {
        "kg_gemeten": round(gemeten, 2),
        "kg_geschat": round(geschat, 2),
        "kg_totaal": round(gemeten + geschat, 2),
        "regels_zonder_getal": onbekend,
        "volledig": onbekend == 0,
    }


# ── Materieellijst inlezen ───────────────────────────────────────────
# Een klant heeft zijn materieel al ergens staan, meestal in Excel. Overtypen
# is de snelste manier om hem af te laten haken, dus lezen we zijn lijst in.

CSV_KOLOMMEN: dict[str, tuple[str, ...]] = {
    # veld            aanvaarde kopteksten (kleine letters, zonder spaties)
    "naam":           ("naam", "materieel", "omschrijving", "machine"),
    "soort":          ("soort", "categorie", "type"),
    "intern_nummer":  ("internnummer", "nummer", "wagenparknummer", "objectnummer"),
    "kenteken":       ("kenteken",),
    "eigendom":       ("eigendom", "eigenofhuur"),
    "leverancier":    ("leverancier", "verhuurder", "verhuurbedrijf"),
    "energiedrager":  ("energiedrager", "brandstof"),
    "emissieklasse":  ("emissieklasse", "stage", "euroklasse"),
    "bouwjaar":       ("bouwjaar", "jaar"),
    "verbruik_per_uur": ("verbruikperuur", "verbruik", "literperuur", "lu"),
    "opmerking":      ("opmerking", "notitie", "toelichting"),
}


def _sleutel(kop: str) -> str:
    return "".join(ch for ch in kop.lower() if ch.isalnum())


def _getal(waarde: str) -> Optional[float]:
    """Lees een getal zoals een Nederlander het typt: 12,5 en 12.5 zijn gelijk."""
    tekst = (waarde or "").strip().replace(" ", "")
    if not tekst:
        return None
    tekst = tekst.replace(".", "") if tekst.count(",") == 1 and tekst.count(".") >= 1 else tekst
    tekst = tekst.replace(",", ".")
    try:
        return float(tekst)
    except ValueError:
        return None


def lees_csv(inhoud: str) -> tuple[list[dict], list[str]]:
    """Zet een geplakte materieellijst om in rijen, met wat er misging.

    Geeft (rijen, waarschuwingen). Een rij zonder naam wordt overgeslagen --
    dat is een lege regel onderaan de Excel. Een onbekende soort of
    energiedrager wordt niet geraden maar gemeld, en die rij komt wel binnen
    met een lege waarde: de gebruiker vult hem daarna in het scherm aan.
    """
    monster = inhoud[:2000]
    try:
        dialect = csv.Sniffer().sniff(monster, delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if monster.count(";") > monster.count(",") else ","

    lezer = csv.DictReader(io.StringIO(inhoud), dialect=dialect)
    if not lezer.fieldnames:
        return [], ["Het bestand heeft geen kopregel."]

    kop_naar_veld: dict[str, str] = {}
    for kop in lezer.fieldnames:
        s = _sleutel(kop or "")
        for veld, varianten in CSV_KOLOMMEN.items():
            if s in varianten:
                kop_naar_veld[kop] = veld
                break

    if "naam" not in kop_naar_veld.values():
        return [], ["Geen kolom met de naam van het materieel gevonden. "
                    "Noem die kolom 'naam' of 'materieel'."]

    soort_op_naam = {v.lower(): k for k, v in SOORTEN.items()}
    drager_op_naam = {v["naam"].lower(): k for k, v in ENERGIEDRAGERS.items()}

    rijen: list[dict] = []
    waarschuwingen: list[str] = []
    for nummer, ruw in enumerate(lezer, start=2):
        rij: dict[str, Any] = {}
        for kop, veld in kop_naar_veld.items():
            rij[veld] = (ruw.get(kop) or "").strip()
        if not rij.get("naam"):
            continue

        soort = (rij.get("soort") or "").lower()
        if soort:
            rij["soort"] = soort if soort in SOORTEN else soort_op_naam.get(soort, "")
            if not rij["soort"]:
                waarschuwingen.append(f"Regel {nummer}: soort '{soort}' ken ik niet.")
        drager = (rij.get("energiedrager") or "").lower()
        if drager:
            rij["energiedrager"] = drager if drager in ENERGIEDRAGERS else drager_op_naam.get(drager, "")
            if not rij["energiedrager"]:
                waarschuwingen.append(f"Regel {nummer}: brandstof '{drager}' ken ik niet.")
        eigendom = (rij.get("eigendom") or "").lower()
        if eigendom and eigendom not in EIGENDOM:
            rij["eigendom"] = "huur" if "huur" in eigendom else ("eigen" if "eigen" in eigendom else "")
        klasse = _sleutel(rij.get("emissieklasse") or "").replace("stage", "stage_")
        rij["emissieklasse"] = klasse if klasse in EMISSIEKLASSEN else ""

        rij["bouwjaar"] = int(rij["bouwjaar"]) if (rij.get("bouwjaar") or "").isdigit() else None
        rij["verbruik_per_uur"] = _getal(rij.get("verbruik_per_uur") or "")
        rijen.append(rij)

    return rijen, waarschuwingen
