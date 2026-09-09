"""Bouwt het calculatiewerkboek voor SOK Amsterdam - Asfalt 2026.

Het werkboek is afgeleid van data/sok_amsterdam_asfalt_2026.json. Zodra die
dataset verandert — bijvoorbeeld als de 38 nog niet ingedeelde meldingen een
werksoort krijgen — draai je dit script opnieuw en klopt de calculatie weer:

    python scripts/maak_calculatie_excel.py

Alle bedragen in het werkboek zijn formules die doorrekenen op het blad
Tarieven. Daar staan de enige cellen die je met de hand invult (blauw op geel).
"""
import json
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

WORTEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORTEL))
from werksoorten import WERKSOORTEN  # noqa: E402
from crow_kosten import klasse_to_categorie  # noqa: E402

BRON = WORTEL / "data" / "sok_amsterdam_asfalt_2026.json"
UIT = WORTEL / "data" / "SOK-Amsterdam-Asfalt-2026-calculatie.xlsx"

DATA = json.loads(BRON.read_text(encoding="utf-8"))
MELD = DATA["meldingen"]
WEGEN = {w["naam"]: w for w in DATA["wegen"]}

ARIAL = "Arial"
KOP = Font(name=ARIAL, bold=True, color="FFFFFF", size=10)
KOPVUL = PatternFill("solid", fgColor="1F3864")
INVOER = Font(name=ARIAL, color="0000FF", size=10)          # blauw = handmatige invoer
GEEL = PatternFill("solid", fgColor="FFFF00")
NORM = Font(name=ARIAL, size=10)
VET = Font(name=ARIAL, bold=True, size=10)
TITEL = Font(name=ARIAL, bold=True, size=13)
RAND = Border(*[Side(style="thin", color="BFBFBF")] * 4)
EUR = '€ #,##0;(€ #,##0);-'
GETAL = '#,##0.00;-#,##0.00;-'

wb = Workbook()

# ── Toelichting ───────────────────────────────────────────────────────────
tl = wb.active
tl.title = "Toelichting"
regels = [
    ("SOK Amsterdam - Asfalt 2026 — calculatiebasis", TITEL),
    ("", NORM),
    ("Herkomst van de gegevens", VET),
    ("Uitgelezen uit drie rapporten van Gemeente Amsterdam: Asfalt Schouw NOORD "
     "(26-08-2026, 49 pagina's),", NORM),
    ("Notities Asfalt NOORD 2026 en Herstelwerkzaamheden Noord.", NORM),
    ("Coordinaten zijn letterlijk overgenomen uit de GPS-regel van het rapport.", NORM),
    ("", NORM),
    ("Werksoort", VET),
    ("Staat met de hand in de kantlijn van de rapporten: HB = hotbox, AS = asfalt machinaal.", NORM),
    ("Die codes zijn per melding van de scan afgelezen en visueel nagelopen.", NORM),
    ("Bij de herstelpunten van de nutsbedrijven staat geen code; daar noemt de tekst de asfaltsoort.", NORM),
    ("Zonder code en zonder asfaltsoort blijft de melding 'nog in te delen' — er is niets ingevuld", NORM),
    ("wat niet in de bron staat.", NORM),
    ("Kolom 'Waarom deze werksoort' op het blad Locaties zegt per regel waar de indeling vandaan komt.", NORM),
    ("", NORM),
    ("Asfaltsoort — rood of zwart", VET),
    ("Rood of zwart zegt waar het werk in zit, niet hoe het wordt uitgevoerd: zowel rood als zwart", NORM),
    ("asfalt kan met de hotbox of machinaal. Het is dus een aparte kolom, geen werksoort.", NORM),
    ("Het rapport noemt de asfaltsoort alleen bij de herstelpunten van de nutsbedrijven —", NORM),
    ("8 x zwart (rijweg) en 1 x rood (fiets-/voetpad). Bij de overige 147 staat het er niet,", NORM),
    ("en dan blijft de kolom leeg. Zie het blad Per asfaltsoort voor de percentages.", NORM),
    ("", NORM),
    ("Schadebeeld, ernst en conditie", VET),
    ("De foto's laten zien dat het meeste werk geen asfaltschade is maar elementenverharding", NORM),
    ("in het asfalt: klinkerstroken en -vlakken waar een sleuf heeft gelegen, die vervangen", NORM),
    ("moeten worden door asfalt. In CROW-termen is dat vlakheid/verzakking, geen scheurvorming.", NORM),
    ("Daarnaast een minderheid met echte asfaltschade: gaten, rafeling en langsscheuren.", NORM),
    ("Ernst is per melding van de schouwfoto afgelezen, omvang volgt uit de gemeten maat", NORM),
    ("(< 2 m2 = 1, 2-10 m2 = 2, > 10 m2 = 3). Samen geven ze de CROW-klasse en daaruit volgt", NORM),
    ("de NEN 2767-conditie. Het is een schatting van achter een bureau om op te prioriteren —", NORM),
    ("geen inspectie op locatie, en geen basis voor een bestek. De laatste kolom op het blad", NORM),
    ("Locaties zegt per regel waar de indeling vandaan komt.", NORM),
    ("", NORM),
    ("Foto's", VET),
    ("De foto's komen uit het digitale rapport, niet uit de scan. Waar de schouwer meer dan een", NORM),
    ("foto maakte staan ze allemaal in een genummerd blad, in dezelfde volgorde als zijn tekst", NORM),
    ("('foto 1', 'foto 3'). Kolom 'Aantal foto's' zegt hoeveel opnames er per locatie zijn.", NORM),
    ("", NORM),
    ("Maatvoering", VET),
    ("De schouwer noteert vrij: '4.00 x 1.40 + 1.80 x 2.00', '2x 2.20 x 0.45', 'Lengte 3,05 Breedte 1,81'.", NORM),
    ("Alle varianten zijn omgerekend naar vlakken, en daaruit volgt het oppervlak.", NORM),
    ("Gecontroleerd tegen de eigen totalen van het rapport: 5,5 m2 en 4,1 m2 komen exact uit.", NORM),
    ("10 van de 156 meldingen noemen geen enkele maat; die hebben geen oppervlak.", NORM),
    ("", NORM),
    ("Tarieven", VET),
    ("De blauwe cellen op het blad Tarieven zijn invoer — pas ze aan naar je eigen prijspeil.", NORM),
    ("De startwaarden zijn de CROW-kostenkengetallen uit crow_kosten.py en zijn een orde van", NORM),
    ("grootte, geen offerte. Alle bedragen in dit werkboek rekenen daarop door.", NORM),
    ("Met de startwaarden komt het totaal uit tussen € 37.584 en € 89.568.", NORM),
    ("", NORM),
    ("Let op", VET),
    ("Kolom 'Let op' op het blad Locaties markeert elke regel die aandacht vraagt.", NORM),
    ("Baanakkerspad heeft geen GPS in het bronrapport.", NORM),
    ("Stellingweg: het adres in het rapport (Ceramiquelaan 723) ligt 3,4 km van de overige", NORM),
    ("punten op die straat — coordinaat controleren.", NORM),
    ("", NORM),
    ("Bijwerken", VET),
    ("Dit werkboek is gegenereerd uit data/sok_amsterdam_asfalt_2026.json met", NORM),
    ("scripts/maak_calculatie_excel.py. Verandert de dataset, draai dat script opnieuw.", NORM),
]
for i, (tekst, font) in enumerate(regels, 1):
    c = tl.cell(row=i, column=1, value=tekst)
    c.font = font
tl.column_dimensions["A"].width = 105

# ── Tarieven ──────────────────────────────────────────────────────────────
ta = wb.create_sheet("Tarieven")
ta["A1"] = "Tarieven per werksoort — de blauwe cellen zijn invoer"
ta["A1"].font = TITEL
kop = ["Werksoort", "Rekeneenheid", "Tarief min", "Tarief max", "Herkomst startwaarde"]
for k, naam in enumerate(kop, 1):
    c = ta.cell(row=3, column=k, value=naam); c.font = KOP; c.fill = KOPVUL; c.border = RAND

EENHEID = {"Hotbox werkzaamheden": "plek", "Asfalt machinaal": "m2",
           "Scheuren vullen": "m1", "Asfalt – nog in te delen": "m2"}
TARIEF = {"Hotbox werkzaamheden": (150, 400), "Asfalt machinaal": (20, 40),
          "Scheuren vullen": (5, 15), "Asfalt – nog in te delen": (0, 0)}
for r, w in enumerate(WERKSOORTEN, 4):
    lbl = w["label"]
    ta.cell(row=r, column=1, value=lbl).font = NORM
    ta.cell(row=r, column=2, value=EENHEID[lbl]).font = NORM
    for kol, val in ((3, TARIEF[lbl][0]), (4, TARIEF[lbl][1])):
        c = ta.cell(row=r, column=kol, value=val)
        c.font = INVOER; c.fill = GEEL; c.number_format = EUR; c.border = RAND
    ta.cell(row=r, column=5,
            value=(w["kosten_orde"] or "geen tarief — werksoort nog niet bepaald")).font = NORM
for kol, br in zip("ABCDE", (30, 14, 14, 14, 34)):
    ta.column_dimensions[kol].width = br
laatste_tarief = 3 + len(WERKSOORTEN)
ta.cell(row=laatste_tarief + 2, column=1,
        value="Startwaarden: CROW-kostenkengetallen (crow_kosten.py). Orde van grootte, geen offerte.").font = NORM


ASFALT_TEKST = {"zwart": "zwart (rijweg)", "rood": "rood (fiets-/voetpad)"}
BEELD_TEKST = {
    "verzakking": "verzakte elementenverharding (klinkerstrook in het asfalt)",
    "oneffenheden": "oneffen verharding",
    "kuilen": "gaten in het asfalt",
    "rafeling": "rafeling van de deklaag",
    "scheurvorming-langs": "langsscheur",
    "scheurvorming-rand": "randschade / scheur langs de kant",
}
ERNST_TEKST = {"L": "gering", "M": "serieus", "E": "ernstig"}
OMVANG_TEKST = {"1": "beperkt (< 2 m2)", "2": "gemiddeld (2-10 m2)", "3": "groot (> 10 m2)"}
ONDERHOUD_TEKST = {
    "observatie": "observatie — volgende schouw",
    "KO": "klein onderhoud — binnen het jaar",
    "GO": "groot onderhoud — inplannen",
    "acuut": "acuut — veiligheidsmaatregel",
}


def let_op(m):
    """Alles wat de calculator over deze regel moet weten, op één regel."""
    punten = []
    if m.get("lat") is None or m.get("lng") is None:
        punten.append("geen GPS in de bron — locatie handmatig bepalen")
    if m.get("gps_waarschuwing"):
        punten.append(m["gps_waarschuwing"])
    if not m.get("maatvoering"):
        punten.append("geen maatvoering in de bron")
    if (m.get("prioriteit") or "").lower() in ("kritiek", "hoog"):
        punten.append("PRIO — met de hand als voorrang aangemerkt")
    return " · ".join(punten)


# ── Locaties ──────────────────────────────────────────────────────────────
lo = wb.create_sheet("Locaties")
kolommen = ["Referentie", "Werksoort", "Straat", "Buurt", "Wat is er geschouwd",
            "Maatvoering", "Aantal vlakken", "Lengte (m)", "Kleinste breedte (m)",
            "Oppervlak (m2)", "Rekeneenheid", "Hoeveelheid", "Tarief min", "Tarief max",
            "Kosten min", "Kosten max", "Prioriteit", "Schouwdatum", "Week",
            "GPS breedte", "GPS lengte", "Adres bij GPS", "Bron", "Pagina", "Foto",
            "Waarom deze werksoort", "Let op", "Asset-code (weg)",
            "Asfaltsoort", "Aantal foto's",
            "Schadebeeld", "Ernst", "Omvang", "CROW-klasse", "NEN 2767 conditie",
            "Onderhoud", "Waar komt de classificatie vandaan"]
for k, naam in enumerate(kolommen, 1):
    c = lo.cell(row=1, column=k, value=naam); c.font = KOP; c.fill = KOPVUL
    c.border = RAND; c.alignment = Alignment(wrap_text=True, vertical="center")
lo.freeze_panes = "A2"

tar = f"Tarieven!$A$4:$E${laatste_tarief}"
for i, m in enumerate(MELD, 2):
    rij = [m["ref"], m["categorie"], m["straat"], m["buurt"] or "", m["bron_tekst"],
           m["maatvoering"] or "", m["aantal_vlakken"] or 0, m["lengte_m"],
           m["kleinste_breedte_m"], m["oppervlakte_m2"]]
    for k, v in enumerate(rij, 1):
        c = lo.cell(row=i, column=k, value=v); c.font = NORM
        if k in (8, 9, 10):
            c.number_format = GETAL
    lo.cell(row=i, column=11, value=f'=INDEX({tar},MATCH($B{i},Tarieven!$A$4:$A${laatste_tarief},0),2)').font = NORM
    lo.cell(row=i, column=12,
            value=f'=IF($K{i}="plek",$G{i},IF($K{i}="m1",$H{i},$J{i}))').font = NORM
    lo.cell(row=i, column=12).number_format = GETAL
    for kol, bron in ((13, 3), (14, 4)):
        c = lo.cell(row=i, column=kol,
                    value=f'=INDEX({tar},MATCH($B{i},Tarieven!$A$4:$A${laatste_tarief},0),{bron})')
        c.font = NORM; c.number_format = EUR
    for kol, tkol in ((15, "M"), (16, "N")):
        c = lo.cell(row=i, column=kol, value=f'=IFERROR($L{i}*${tkol}{i},0)')
        c.font = NORM; c.number_format = EUR
    staart = [m["prioriteit"], m["schouwdatum"], m["week"], m["lat"], m["lng"],
              m["bron_adres"], m["bron"], m["pagina"], m["foto"],
              m.get("werksoort_herkomst") or "", let_op(m), m.get("asset_code") or "",
              ASFALT_TEKST.get(m.get("asfaltsoort"), ""), m.get("aantal_fotos") or 0,
              BEELD_TEKST.get(m.get("crow_schadebeeld"), m.get("crow_schadebeeld") or ""),
              ERNST_TEKST.get(m.get("crow_ernst"), ""),
              OMVANG_TEKST.get(m.get("crow_omvang"), ""),
              m.get("crow_klasse") or "", m.get("nen_2767_conditie") or "",
              ONDERHOUD_TEKST.get(klasse_to_categorie(m["crow_klasse"]) if m.get("crow_klasse") else "", ""),
              m.get("crow_herkomst") or ""]
    for k, v in enumerate(staart, 17):
        c = lo.cell(row=i, column=k, value=v); c.font = NORM
        if k in (26, 27):
            c.alignment = Alignment(wrap_text=True, vertical="top")
n = len(MELD) + 1
tot = n + 2
lo.cell(row=tot, column=1, value="Totaal").font = VET
for kol in ("G", "H", "J", "L", "O", "P"):
    c = lo[f"{kol}{tot}"]
    c.value = f"=SUM({kol}2:{kol}{n})"
    c.font = VET
    c.number_format = EUR if kol in ("O", "P") else GETAL
for kol, br in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                   (17, 26, 20, 22, 52, 34, 9, 11, 13, 13, 12, 12, 11, 11, 12, 12,
                    10, 12, 7, 11, 11, 30, 34, 9, 24, 44)):
    lo.column_dimensions[kol].width = br
lo.column_dimensions["AA"].width = 52
lo.column_dimensions["AB"].width = 26
for kol, br in (("AC", 22), ("AD", 12), ("AE", 42), ("AF", 12), ("AG", 20),
                ("AH", 13), ("AI", 17), ("AJ", 26), ("AK", 54)):
    lo.column_dimensions[kol].width = br

# ── Per werksoort ─────────────────────────────────────────────────────────
pw = wb.create_sheet("Per werksoort")
pw["A1"] = "Samenvatting per werksoort"; pw["A1"].font = TITEL
for k, naam in enumerate(["Werksoort", "Aantal locaties", "Vlakken", "Lengte (m)",
                          "Oppervlak (m2)", "Kosten min", "Kosten max"], 1):
    c = pw.cell(row=3, column=k, value=naam); c.font = KOP; c.fill = KOPVUL; c.border = RAND
for r, w in enumerate(WERKSOORTEN, 4):
    pw.cell(row=r, column=1, value=w["label"]).font = NORM
    pw.cell(row=r, column=2, value=f'=COUNTIF(Locaties!$B$2:$B${n},$A{r})').font = NORM
    for kol, brk in ((3, "G"), (4, "H"), (5, "J"), (6, "O"), (7, "P")):
        c = pw.cell(row=r, column=kol,
                    value=f'=SUMIF(Locaties!$B$2:$B${n},$A{r},Locaties!${brk}$2:${brk}${n})')
        c.font = NORM; c.number_format = EUR if kol in (6, 7) else GETAL
er = 4 + len(WERKSOORTEN)
pw.cell(row=er, column=1, value="Totaal").font = VET
for kol in range(2, 8):
    L = get_column_letter(kol)
    c = pw.cell(row=er, column=kol, value=f"=SUM({L}4:{L}{er-1})")
    c.font = VET; c.number_format = EUR if kol in (6, 7) else GETAL
for kol, br in zip("ABCDEFG", (30, 15, 11, 13, 15, 14, 14)):
    pw.column_dimensions[kol].width = br

# ── Per asfaltsoort ───────────────────────────────────────────────────────
# Tweede as naast de werksoort: waar zit het werk in. De percentages rekenen
# over alle 156 locaties, zodat "niet vermeld" zichtbaar meetelt in plaats van
# stilzwijgend bij zwart te worden opgeteld.
pa = wb.create_sheet("Per asfaltsoort")
pa["A1"] = "Rood asfalt tegenover zwart asfalt"; pa["A1"].font = TITEL
pa["A2"] = ("Het bronrapport noemt de asfaltsoort alleen bij de herstelpunten van de "
            "nutsbedrijven. Bij de rest staat het er niet.")
pa["A2"].font = NORM
for k, naam in enumerate(["Asfaltsoort", "Aantal locaties", "Percentage",
                          "Oppervlak (m2)", "Kosten min", "Kosten max"], 1):
    c = pa.cell(row=4, column=k, value=naam); c.font = KOP; c.fill = KOPVUL; c.border = RAND
SOORTRIJEN = [("zwart (rijweg)", "zwart"), ("rood (fiets-/voetpad)", "rood"),
              ("niet vermeld in het rapport", None)]
ar = 5 + len(SOORTRIJEN)                     # de totaalregel
# De regel "niet vermeld" telt niet zelf, maar is het verschil met het totaal.
# Een lege cel tellen hangt af van hoe het rekenblad leeg opvat; aftrekken van
# het totaal doet dat niet en komt altijd op 156 uit.
for r, (naam, code) in enumerate(SOORTRIJEN, 5):
    pa.cell(row=r, column=1, value=naam).font = NORM
    pa.cell(row=r, column=2,
            value=(f'=COUNTIF(Locaties!$AC$2:$AC${n},$A{r})' if code
                   else f'=$B${ar}-SUM(B5:B6)')).font = NORM
    c = pa.cell(row=r, column=3, value=f'=IFERROR($B{r}/$B${ar},0)')
    c.font = NORM; c.number_format = "0.0%"
    for kol, brk in ((4, "J"), (5, "O"), (6, "P")):
        L = get_column_letter(kol)
        c = pa.cell(row=r, column=kol,
                    value=(f'=SUMIF(Locaties!$AC$2:$AC${n},$A{r},Locaties!${brk}$2:${brk}${n})'
                           if code else
                           f'=SUM(Locaties!${brk}$2:${brk}${n})-SUM({L}5:{L}6)'))
        c.font = NORM; c.number_format = EUR if kol in (5, 6) else GETAL
pa.cell(row=ar, column=1, value="Totaal (alle locaties)").font = VET
c = pa.cell(row=ar, column=2, value=f'=COUNTA(Locaties!$A$2:$A${n})')
c.font = VET
for kol, brk in ((4, "J"), (5, "O"), (6, "P")):
    c = pa.cell(row=ar, column=kol, value=f'=SUM(Locaties!${brk}$2:${brk}${n})')
    c.font = VET; c.number_format = EUR if kol in (5, 6) else GETAL
c = pa.cell(row=ar, column=3, value=f"=SUM(C5:C{ar-1})")
c.font = VET; c.number_format = "0.0%"
for kol, br in zip("ABCDEF", (30, 15, 12, 15, 14, 14)):
    pa.column_dimensions[kol].width = br

# ── Per CROW-klasse ───────────────────────────────────────────────────────
# De klasse is een schatting van de foto, geen inspectie op locatie. Hij staat
# er om te kunnen prioriteren, niet om een bestek op te baseren.
pk = wb.create_sheet("Per klasse")
pk["A1"] = "Ernst en omvang — CROW-klasse"; pk["A1"].font = TITEL
pk["A2"] = ("Ernst is afgelezen van de schouwfoto, omvang volgt uit de gemeten maat. "
            "Een schatting om op te prioriteren — controleren op locatie.")
pk["A2"].font = NORM
for k, naam in enumerate(["CROW-klasse", "Betekenis", "Aantal locaties",
                          "Oppervlak (m2)", "Kosten min", "Kosten max"], 1):
    c = pk.cell(row=4, column=k, value=naam); c.font = KOP; c.fill = KOPVUL; c.border = RAND
KLASSEN = [(f"{e}{o}", f"{ERNST_TEKST[e]}, {OMVANG_TEKST[o]}")
           for e in ("L", "M", "E") for o in ("1", "2", "3")]
for r, (klasse, betekenis) in enumerate(KLASSEN, 5):
    pk.cell(row=r, column=1, value=klasse).font = NORM
    pk.cell(row=r, column=2,
            value=f"{betekenis} — {ONDERHOUD_TEKST[klasse_to_categorie(klasse)]}").font = NORM
    pk.cell(row=r, column=3, value=f'=COUNTIF(Locaties!$AH$2:$AH${n},$A{r})').font = NORM
    for kol, brk in ((4, "J"), (5, "O"), (6, "P")):
        c = pk.cell(row=r, column=kol,
                    value=f'=SUMIF(Locaties!$AH$2:$AH${n},$A{r},Locaties!${brk}$2:${brk}${n})')
        c.font = NORM; c.number_format = EUR if kol in (5, 6) else GETAL
kr = 5 + len(KLASSEN)
pk.cell(row=kr, column=1, value="Totaal").font = VET
for kol in (3, 4, 5, 6):
    L = get_column_letter(kol)
    c = pk.cell(row=kr, column=kol, value=f"=SUM({L}5:{L}{kr-1})")
    c.font = VET; c.number_format = EUR if kol in (5, 6) else GETAL
for kol, br in zip("ABCDEF", (14, 56, 15, 15, 14, 14)):
    pk.column_dimensions[kol].width = br

# ── Per weg ───────────────────────────────────────────────────────────────
pg = wb.create_sheet("Per weg")
pg["A1"] = "Samenvatting per weg"; pg["A1"].font = TITEL
for k, naam in enumerate(["Weg", "Buurt", "Aantal locaties", "Oppervlak (m2)",
                          "Kosten min", "Kosten max", "Conditie (afgeleid)"], 1):
    c = pg.cell(row=3, column=k, value=naam); c.font = KOP; c.fill = KOPVUL; c.border = RAND
namen = sorted(WEGEN)
for r, naam in enumerate(namen, 4):
    w = WEGEN[naam]
    pg.cell(row=r, column=1, value=naam).font = NORM
    pg.cell(row=r, column=2, value=w["locatie_omschrijving"]).font = NORM
    pg.cell(row=r, column=3, value=f'=COUNTIF(Locaties!$C$2:$C${n},$A{r})').font = NORM
    for kol, brk in ((4, "J"), (5, "O"), (6, "P")):
        c = pg.cell(row=r, column=kol,
                    value=f'=SUMIF(Locaties!$C$2:$C${n},$A{r},Locaties!${brk}$2:${brk}${n})')
        c.font = NORM; c.number_format = EUR if kol in (5, 6) else GETAL
    pg.cell(row=r, column=7, value=w["conditie_score"]).font = NORM
wr = 4 + len(namen)
pg.cell(row=wr, column=1, value="Totaal").font = VET
for kol in range(3, 7):
    L = get_column_letter(kol)
    c = pg.cell(row=wr, column=kol, value=f"=SUM({L}4:{L}{wr-1})")
    c.font = VET; c.number_format = EUR if kol in (5, 6) else GETAL
pg.cell(row=wr + 2, column=1,
        value="Conditie is afgeleid uit het geschouwde schadeoppervlak per weg, niet uit een "
              "NEN 2767-inspectie. Zie het blad Toelichting.").font = NORM
for kol, br in zip("ABCDEFG", (26, 40, 15, 15, 14, 14, 18)):
    pg.column_dimensions[kol].width = br

wb.save(UIT)
print(UIT)
