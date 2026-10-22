"""Projectdagrapport: wat er op een project gebeurde, per dag en per week.

Het werkdagboek is persoonlijk ("wat deed ik vandaag"). Het dagrapport hoort
bij een project en wordt door de hele ploeg bijgehouden, zoals een uitvoerder
dat op papier of in Excel doet:

  Dagboek       per dag het weer, de temperatuur en een logboek in vrije tekst
  Personeel     wie er stond (eigen mensen en inhuur), met functie en uren
  Materieel     welke machines draaiden -- dat zijn de regels uit
                `materieel_inzet`, zodat CO2 op één plek wordt berekend
  Materiaal     wat er werd aan- of afgevoerd, met hoeveelheid en eenheid
  Afwijkingen   meerwerk, stagnatie, verzoeken: wat, waarom, waar, hoe lang,
                wat het kost

Het weekrapport zet dat naast elkaar met een kolom per dag (ma t/m zo), in de
vorm die opdrachtgevers gewend zijn, met bovenaan de CO2 van het materieel:
uitstoot, en de reductie ten opzichte van diesel.

Dit bestand kent de keuzelijsten, de weekopbouw en de export. De endpoints
staan in routers/dagrapport_router.py.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

WEER: dict[str, str] = {
    "droog_zonnig": "Droog, zonnig",
    "droog_bewolkt": "Droog, bewolkt",
    "wisselvallig": "Wisselvallig",
    "motregen": "Motregen",
    "regen": "Regen",
    "zware_regen": "Zware regen",
    "onweer": "Onweer",
    "harde_wind": "Harde wind / storm",
    "mist": "Mist",
    "vorst": "Vorst / gladheid",
    "sneeuw": "Sneeuw",
}

# Stagnatie: lag het werk (deels) stil? "Vertraging" is minder werk dan
# gepland, "stilstand" is niets. Dat onderscheid maakt een opdrachtgever ook.
STAGNATIE: dict[str, str] = {
    "geen": "Geen",
    "vertraging": "Vertraging",
    "stilstand": "Stilstand",
}

AFWIJKING_SOORTEN: dict[str, str] = {
    "geen": "Geen",
    "meerwerk": "Meerwerk",
    "minderwerk": "Minderwerk",
    "regie": "Regiewerk",
}

RICHTINGEN: dict[str, str] = {"aanvoer": "Aanvoer", "afvoer": "Afvoer"}

EENHEDEN: dict[str, str] = {
    "ton": "ton", "m3": "m³", "m2": "m²", "m1": "m¹", "stuk": "stuks",
    "liter": "liter", "kg": "kg", "vracht": "vrachten",
}

# Veelgebruikte functies in de GWW. Een voorstel in het formulier, geen
# verplichte lijst: een "tegelzetter" moet ook kunnen.
FUNCTIES: list[str] = [
    "Uitvoerder", "Voorman", "Machinist", "Vakman GWW", "Grondwerker", "Stratenmaker",
    "Asfalteerder", "Chauffeur", "Verkeersregelaar", "Rioleur", "Bekister",
    "Opzichter", "Werkvoorbereider", "Leerling",
]

DAGEN = ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"]


def week_van(d: date) -> tuple[date, date]:
    """Maandag en zondag van de ISO-week waar `d` in valt."""
    maandag = d - timedelta(days=d.weekday())
    return maandag, maandag + timedelta(days=6)


def weeknummer(d: date) -> str:
    """'2026-38' zoals in de kop van het weekrapport."""
    jaar, week, _ = d.isocalendar()
    return f"{jaar}-{week:02d}"


def _rond(x: float) -> float:
    return round(x + 0.0, 2)


def per_dag(regels: list[dict], sleutel, waarde, maandag: date) -> list[dict]:
    """Groepeer regels op `sleutel(r)` met een kolom per weekdag.

    Geeft per groep {"sleutel": ..., "dagen": [7 getallen], "totaal": x,
    "voorbeeld": eerste regel} in de volgorde waarin de groep voor het eerst
    voorkomt.
    """
    groepen: dict[Any, dict] = {}
    for r in regels:
        k = sleutel(r)
        g = groepen.setdefault(k, {"sleutel": k, "dagen": [0.0] * 7, "totaal": 0.0, "voorbeeld": r})
        dag = (r["_datum"] - maandag).days
        if 0 <= dag < 7:
            w = waarde(r) or 0.0
            g["dagen"][dag] += w
            g["totaal"] += w
    for g in groepen.values():
        g["dagen"] = [_rond(x) for x in g["dagen"]]
        g["totaal"] = _rond(g["totaal"])
    return list(groepen.values())


def weekopbouw(*, maandag: date, dagen: list[dict], personeel: list[dict],
               materieel: list[dict], materiaal: list[dict],
               afwijkingen: list[dict]) -> dict:
    """De week als tabellen, klaar voor het scherm en de export.

    De invoer zijn de al opgemaakte regels (zoals de API ze toont), met een
    extra `_datum` (date) zodat hier niet opnieuw geparsed hoeft te worden.
    """
    pers = per_dag(
        personeel,
        lambda r: (r.get("user_id") or "", (r.get("naam") or "").strip().lower(),
                   (r.get("functie") or "").strip().lower(), (r.get("bedrijf") or "").strip().lower()),
        lambda r: r.get("uren"), maandag)
    mat = per_dag(
        materieel,
        lambda r: (r.get("materieel_id") or r.get("materieel_naam"), r.get("leverancier") or "",
                   r.get("energiedrager") or ""),
        lambda r: r.get("draaiuren"), maandag)
    for g in mat:
        rijen = [r for r in materieel if (r.get("materieel_id") or r.get("materieel_naam"),
                                          r.get("leverancier") or "",
                                          r.get("energiedrager") or "") == g["sleutel"]]
        g["co2_kg"] = _rond(sum(r["co2_kg"] for r in rijen if r.get("co2_kg") is not None))
        g["reductie_kg"] = _rond(sum(r["reductie_kg"] for r in rijen if r.get("reductie_kg")))
        g["zonder_getal"] = sum(1 for r in rijen if r.get("co2_kg") is None)
        g["geschat"] = any(r.get("co2_methode") == "geschat" for r in rijen)
    mtl = per_dag(
        materiaal,
        lambda r: ((r.get("leverancier") or "").strip().lower(), (r.get("materiaal") or "").strip().lower(),
                   r.get("richting"), r.get("eenheid")),
        lambda r: r.get("hoeveelheid"), maandag)

    uitstoot = sum(r["co2_kg"] for r in materieel if r.get("co2_kg") is not None)
    geschat = sum(r["co2_kg"] for r in materieel
                  if r.get("co2_kg") is not None and r.get("co2_methode") == "geschat")
    reductie = sum(r["reductie_kg"] for r in materieel if r.get("reductie_kg"))
    return {
        "maandag": maandag.isoformat(),
        "zondag": (maandag + timedelta(days=6)).isoformat(),
        "week": weeknummer(maandag),
        "dagen": sorted(dagen, key=lambda d: d["datum"]),
        "personeel": pers,
        "materieel": mat,
        "materiaal": mtl,
        "afwijkingen": sorted(afwijkingen, key=lambda a: a["datum"]),
        "co2": {
            "uitstoot_kg": _rond(uitstoot),
            "waarvan_geschat_kg": _rond(geschat),
            "reductie_kg": _rond(reductie),
            "regels_zonder_getal": sum(1 for r in materieel if r.get("co2_kg") is None),
        },
        "uren_personeel": _rond(sum(g["totaal"] for g in pers)),
        "uren_materieel": _rond(sum(g["totaal"] for g in mat)),
        "meerwerk_bedrag": _rond(sum(a.get("bedrag") or 0 for a in afwijkingen
                                     if a.get("soort") in ("meerwerk", "regie"))),
    }


# ── Export ───────────────────────────────────────────────────────────

def _nl(x: Optional[float], dec: int = 2) -> str:
    if x is None:
        return ""
    tekst = f"{x:,.{dec}f}"
    return tekst.replace(",", "_").replace(".", ",").replace("_", ".")


def _temp(d: dict) -> str:
    lo, hi = d.get("temp_min"), d.get("temp_max")
    if lo is None and hi is None:
        return ""
    if lo is not None and hi is not None:
        return f"{lo:g} / {hi:g} °C"
    return f"{(lo if lo is not None else hi):g} °C"


def bladen(week: dict):
    """De week als Blad-en voor PDF en Excel, in de volgorde van het rapport."""
    from export_huisstijl import Blad, Kolom

    dag_kolommen = [Kolom(d, "getal", 2, breedte=11) for d in DAGEN]
    uit = []

    uit.append(Blad("Dagboek", [Kolom("Datum", "datum", breedte=20), Kolom("Weer", breedte=24),
                                Kolom("Temperatuur", breedte=22), Kolom("Log", breedte=120)],
                    [[date.fromisoformat(d["datum"]), d.get("weer_naam") or "", _temp(d), d.get("log") or ""]
                     for d in week["dagen"] if d.get("log") or d.get("weer") or _temp(d)],
                    liggend=True))

    pers = [[(g["voorbeeld"].get("functie") or ""), g["voorbeeld"].get("naam_toon") or "",
             g["voorbeeld"].get("bedrijf") or "", *g["dagen"], g["totaal"]] for g in week["personeel"]]
    uit.append(Blad("Personeel", [Kolom("Functie", breedte=26), Kolom("Naam", breedte=34),
                                  Kolom("Bedrijf", breedte=34), *dag_kolommen,
                                  Kolom("Totaal uur", "getal", 2, breedte=16)],
                    pers, totaal=["Totaal", "", "", *[_rond(sum(g["dagen"][i] for g in week["personeel"]))
                                                   for i in range(7)], week["uren_personeel"]],
                    liggend=True))

    mat = []
    for g in week["materieel"]:
        v = g["voorbeeld"]
        mat.append([v.get("leverancier") or "", v.get("materieel_naam") or "",
                    v.get("energiedrager_naam") or "", v.get("emissieklasse_naam") or "",
                    v.get("vermogen_kw"), *g["dagen"], g["totaal"],
                    g["co2_kg"] if not g["zonder_getal"] or g["co2_kg"] else None])
    uit.append(Blad("Materieel", [Kolom("Leverancier", breedte=28), Kolom("Materieel", breedte=36),
                                  Kolom("Brandstof", breedte=18), Kolom("Stage", breedte=14),
                                  Kolom("kW", "getal", 0, breedte=10), *dag_kolommen,
                                  Kolom("Totaal uur", "getal", 2, breedte=14),
                                  Kolom("CO2 kg", "getal", 2, breedte=14)],
                    mat, totaal=["Totaal", "", "", "", None,
                                 *[_rond(sum(g["dagen"][i] for g in week["materieel"])) for i in range(7)],
                                 week["uren_materieel"], week["co2"]["uitstoot_kg"]],
                    toelichting=[t for t in [
                        "CO2 = liters (gemeten, of draaiuren × verbruik per uur: geschat) × emissiefactor "
                        "uit de landelijke lijst co2emissiefactoren.nl.",
                        (f"Waarvan geschat: {_nl(week['co2']['waarvan_geschat_kg'])} kg."
                         if week["co2"]["waarvan_geschat_kg"] else ""),
                        (f"{week['co2']['regels_zonder_getal']} regel(s) zonder verbruik: niet meegeteld."
                         if week["co2"]["regels_zonder_getal"] else ""),
                    ] if t],
                    liggend=True))

    mtl = [[g["voorbeeld"].get("leverancier") or "", g["voorbeeld"].get("materiaal") or "",
            g["voorbeeld"].get("richting_naam") or "", *g["dagen"], g["totaal"],
            g["voorbeeld"].get("eenheid_naam") or ""] for g in week["materiaal"]]
    uit.append(Blad("Materiaal", [Kolom("Leverancier", breedte=34), Kolom("Materiaal", breedte=40),
                                  Kolom("Aan/afvoer", breedte=18), *dag_kolommen,
                                  Kolom("Totaal", "getal", 2, breedte=14), Kolom("Eenheid", breedte=14)],
                    mtl, liggend=True))

    afw = [[date.fromisoformat(a["datum"]), a.get("omschrijving") or "", a.get("maatregel") or "",
            a.get("adres") or "", a.get("stagnatie_naam") or "", a.get("duur_uren"),
            a.get("soort_naam") or "", a.get("bedrag")] for a in week["afwijkingen"]]
    uit.append(Blad("Afwijkingen", [Kolom("Datum", "datum", breedte=18), Kolom("Omschrijving", breedte=60),
                                    Kolom("Te nemen maatregel", breedte=52), Kolom("Adres", breedte=34),
                                    Kolom("Stagnatie", breedte=18), Kolom("Duur (uur)", "getal", 1, breedte=14),
                                    Kolom("Afwijking", breedte=20), Kolom("Bedrag", "geld", 2, breedte=18)],
                    afw, totaal=(["Totaal", "", "", "", "", None, "", week["meerwerk_bedrag"]]
                                 if week["meerwerk_bedrag"] else None),
                    liggend=True))
    return uit


def pdf(week: dict, *, klant, project_naam: str, opstellers: str) -> bytes:
    from export_huisstijl import FOUT, GOED, HuisstijlPDF

    ondertitel = f"{project_naam} · week {week['week']}"
    doc = HuisstijlPDF(klant, "Weekrapport", ondertitel=ondertitel, liggend=True)
    doc.add_page()
    doc.titelblok(meta="")
    maandag, zondag = date.fromisoformat(week["maandag"]), date.fromisoformat(week["zondag"])
    doc.kv([("Project", project_naam),
            ("Week", f"{week['week']} ({maandag.strftime('%d-%m-%Y')} t/m {zondag.strftime('%d-%m-%Y')})"),
            ("Opsteller", opstellers)])
    co2 = week["co2"]
    doc.kengetallen([
        (f"{_nl(co2['uitstoot_kg'])} kg", "CO2-uitstoot materieel"),
        (f"{_nl(co2['reductie_kg'])} kg", "reductie t.o.v. diesel"),
        (f"{_nl(week['uren_personeel'])} uur", "personeel"),
        (f"{_nl(week['uren_materieel'])} uur", "materieel"),
    ], kleuren=[None, GOED if co2["reductie_kg"] else None, None, None])
    if week["meerwerk_bedrag"]:
        doc.tekst(f"Meerwerk en regie deze week: € {_nl(week['meerwerk_bedrag'])}", grootte=9, kleur=FOUT)
    for b in bladen(week):
        doc.sectie(b.naam)
        if not b.rijen:
            doc.tekst("Niets vastgelegd deze week.", grootte=9)
            continue
        doc.tabel(b.kolommen, b.rijen, totaal=b.totaal, lettergrootte=8)
        for regel in b.toelichting:
            doc.tekst(regel, grootte=7.5, regel=4)
    return doc.uitvoer()
