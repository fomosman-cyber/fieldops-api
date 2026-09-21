"""Eén huisstijl voor alles wat een klant exporteert: PDF en Excel.

De kleuren en letters zijn van FieldOps, zodat elk rapport herkenbaar uit
hetzelfde portaal komt. Het logo en de bedrijfsgegevens zijn van de klant:
het is hún rapport, dat zij doorsturen naar hun opdrachtgever.

PDF en Excel krijgen dezelfde opbouw, zodat twee exports van hetzelfde
overzicht naast elkaar hetzelfde lezen:

    briefhoofd   logo van de klant links, naam en gegevens rechts
    blauwe lijn
    titel        groot, met ondertitel en exportdatum eronder
    tabel        blauwe kopregel met witte letters, rijen om en om gestreept
    voet         titel links, "Pagina x van n" rechts, "Opgesteld met FieldOps"

Een export beschrijft zijn inhoud één keer als `Blad` (kolommen + rijen) en
laat die door `pdf_van()` en `excel_van()` tekenen. Rapporten met eigen
hoofdstukken (MJOP, inspecties) gebruiken `HuisstijlPDF` direct en geven
hun tabellen als `Blad` aan Excel mee.
"""
from __future__ import annotations

import base64
import binascii
import io
import math
import re
import textwrap
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

from fastapi import Response
from fpdf import FPDF
from fpdf.fonts import FontFace

# ── Kleuren: dezelfde als het portaal (--accent, --text, --text-muted, --border)
BLAUW = "0284C7"
BLAUW_DONKER = "0369A1"
INKT = "1E293B"
GRIJS = "64748B"
LICHTGRIJS = "94A3B8"
LIJN = "E2E8F0"
STREEP = "F1F5F9"     # om-en-om-rij in tabellen
VLAK = "F8FAFC"       # kengetallen, totaalregel
WIT = "FFFFFF"
GOED = "16A34A"
LET_OP = "EA580C"
FOUT = "DC2626"

# Helvetica (PDF) en Arial (Office) zijn dezelfde letter onder twee namen.
LETTER_PDF = "Helvetica"
LETTER_EXCEL = "Arial"

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Grootste logo dat we inbedden; groter maakt alleen het bestand zwaarder.
_LOGO_MAX_PX = (900, 300)


def rgb(hexkleur: str) -> tuple[int, int, int]:
    h = hexkleur.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _laatste_zondag(jaar: int, maand: int) -> int:
    d = date(jaar, maand, 31)
    return 31 - (d.weekday() + 1) % 7


def naar_nl(moment: datetime) -> datetime:
    """UTC (of een andere zone) naar Nederlandse tijd, zonder tijdzonedatabase:
    zomertijd loopt van de laatste zondag van maart tot die van oktober, om
    01:00 UTC. Zo rekent de server hetzelfde als een laptop zonder tzdata."""
    if moment.tzinfo is None:
        return moment
    utc = moment.astimezone(timezone.utc).replace(tzinfo=None)
    begin = datetime(utc.year, 3, _laatste_zondag(utc.year, 3), 1)
    eind = datetime(utc.year, 10, _laatste_zondag(utc.year, 10), 1)
    return utc + timedelta(hours=2 if begin <= utc < eind else 1)


def nu_lokaal() -> datetime:
    """Nederlandse tijd voor 'geëxporteerd op'."""
    return naar_nl(datetime.now(timezone.utc))


# ═══════════════════════════════════════════════════════════════════════════
# De klant: naam, logo en gegevens
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Logo:
    png: Optional[bytes]        # gerasterd, voor Excel en PDF
    svg: Optional[bytes]        # alleen PDF kan SVG tekenen
    breedte_px: int = 0
    hoogte_px: int = 0

    @property
    def verhouding(self) -> float:
        if self.breedte_px and self.hoogte_px:
            return self.breedte_px / self.hoogte_px
        return 3.0


def logo_uit_data_url(waarde: Optional[str]) -> Optional[Logo]:
    """Het logo zoals de organisatie het heeft ingesteld, klaar om in te
    bedden. Een kapot of onleesbaar logo geeft None: dan staat de naam er,
    en de export gaat gewoon door."""
    if not waarde or not isinstance(waarde, str) or not waarde.startswith("data:image/"):
        return None
    try:
        kop, data = waarde.split(",", 1)
        ruw = base64.b64decode(data) if ";base64" in kop else data.encode("utf-8")
    except (ValueError, binascii.Error):
        return None
    if "svg" in kop:
        return Logo(png=None, svg=ruw)
    try:
        from PIL import Image
        with Image.open(io.BytesIO(ruw)) as beeld:
            beeld.load()
            beeld = beeld.convert("RGBA")
            beeld.thumbnail(_LOGO_MAX_PX)
            uit = io.BytesIO()
            beeld.save(uit, format="PNG", optimize=True)
            return Logo(png=uit.getvalue(), svg=None,
                        breedte_px=beeld.width, hoogte_px=beeld.height)
    except Exception:
        return None


@dataclass(frozen=True)
class Klant:
    naam: str
    logo: Optional[Logo] = None
    adres: str = ""
    kvk: str = ""
    btw: str = ""
    email: str = ""
    telefoon: str = ""

    def gegevens(self) -> list[str]:
        """De regels onder de naam in het briefhoofd. Wat leeg is, valt weg."""
        regels = []
        if self.adres:
            regels.append(self.adres)
        ids = " · ".join(x for x in (f"KvK {self.kvk}" if self.kvk else "",
                                     f"BTW {self.btw}" if self.btw else "") if x)
        if ids:
            regels.append(ids)
        contact = " · ".join(x for x in (self.email, self.telefoon) if x)
        if contact:
            regels.append(contact)
        return regels


def klant_van(org: Any) -> Klant:
    """De gegevens van de organisatie die exporteert."""
    if org is None:
        return Klant(naam="FieldOps")
    adres = getattr(org, "billing_address", None) or ""
    adres = ", ".join(r.strip().rstrip(",") for r in re.split(r"[\r\n]+", adres) if r.strip())
    return Klant(
        naam=(getattr(org, "name", None) or "").strip() or "FieldOps",
        logo=logo_uit_data_url(getattr(org, "logo_data_url", None)),
        adres=adres,
        kvk=(getattr(org, "kvk_number", None) or "").strip(),
        btw=(getattr(org, "btw_number", None) or "").strip(),
        email=(getattr(org, "contact_email", None) or "").strip(),
        telefoon=(getattr(org, "contact_phone", None) or "").strip(),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Wat er in een tabel staat: kolommen en hoe hun waarden eruitzien
# ═══════════════════════════════════════════════════════════════════════════

SOORTEN = ("tekst", "getal", "heel", "geld", "procent", "datum", "datumtijd", "jaar")

_EXCEL_FORMAAT = {
    "heel": "#,##0",
    "jaar": "0",                  # 2026, niet 2.026
    "procent": "0%",
    "datum": "dd-mm-yyyy",
    "datumtijd": "dd-mm-yyyy hh:mm",
}


@dataclass(frozen=True)
class Kolom:
    naam: str
    soort: str = "tekst"
    decimalen: int = 2          # voor "getal" en "geld"
    breedte: Optional[float] = None   # relatief gewicht; leeg = naar inhoud

    def __post_init__(self):
        if self.soort not in SOORTEN:
            raise ValueError(f"Onbekende kolomsoort: {self.soort}")

    @property
    def rechts(self) -> bool:
        return self.soort in ("getal", "heel", "geld", "procent")

    @property
    def vast(self) -> bool:
        """Breekt nooit af: getallen, bedragen, datums, jaartallen."""
        return self.soort != "tekst"

    @property
    def excel_formaat(self) -> Optional[str]:
        decimalen = ("." + "0" * self.decimalen) if self.decimalen else ""
        if self.soort == "getal":
            return "#,##0" + decimalen
        if self.soort == "geld":
            return '"€" #,##0' + decimalen
        return _EXCEL_FORMAAT.get(self.soort)


@dataclass
class Blad:
    """Eén overzicht: in de PDF een tabel, in Excel een tabblad."""
    naam: str                                   # tabbladnaam en sectiekop
    kolommen: Sequence[Kolom]
    rijen: Iterable[Sequence[Any]]
    totaal: Optional[Sequence[Any]] = None      # vetgedrukte slotregel
    toelichting: Sequence[str] = field(default_factory=list)
    liggend: Optional[bool] = None              # leeg = liggend bij > 7 kolommen

    def __post_init__(self):
        self.rijen = [list(r) for r in self.rijen]
        for r in self.rijen:
            if len(r) != len(self.kolommen):
                raise ValueError(f"Rij met {len(r)} waarden in blad '{self.naam}' "
                                 f"met {len(self.kolommen)} kolommen")

    @property
    def is_liggend(self) -> bool:
        return self.liggend if self.liggend is not None else len(self.kolommen) > 7


def _als_getal(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(v, str) and v.strip():
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _als_datum(v: Any, met_tijd: bool) -> Optional[datetime | date]:
    if isinstance(v, datetime):
        d = naar_nl(v)
        return d if met_tijd else d.date()
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day) if met_tijd else v
    if isinstance(v, str) and v.strip():
        s = v.strip().replace("Z", "+00:00")
        try:
            return _als_datum(datetime.fromisoformat(s), met_tijd)
        except ValueError:
            return None
    return None


def nl_getal(v: float, decimalen: int = 2) -> str:
    """1234.5 -> '1.234,50' (Nederlandse notatie)."""
    s = f"{v:,.{decimalen}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def als_tekst(kolom: Kolom, v: Any) -> str:
    """Zoals de waarde in de PDF staat. Excel krijgt de echte waarde met een
    celopmaak, zodat hij er ook mee kan rekenen."""
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "ja" if v else "nee"
    if kolom.soort in ("datum", "datumtijd"):
        d = _als_datum(v, kolom.soort == "datumtijd")
        if d is None:
            return str(v)
        return d.strftime("%d-%m-%Y %H:%M") if kolom.soort == "datumtijd" else d.strftime("%d-%m-%Y")
    getal = _als_getal(v) if (kolom.rechts or kolom.soort == "jaar") else None
    if getal is None:
        return str(v)
    if kolom.soort == "jaar":
        return str(int(round(getal)))
    if kolom.soort == "geld":
        return "€ " + nl_getal(getal, kolom.decimalen)
    if kolom.soort == "heel":
        return nl_getal(round(getal), 0)
    if kolom.soort == "procent":
        return nl_getal(getal * 100, 0) + "%"
    return nl_getal(getal, kolom.decimalen)


# ═══════════════════════════════════════════════════════════════════════════
# PDF
# ═══════════════════════════════════════════════════════════════════════════

# Tekens die niet in de PDF-standaardletter (Windows-1252) zitten.
_VERVANG_PDF = {
    "→": "->", "←": "<-", "↔": "<->", "⇒": "=>", "≥": ">=", "≤": "<=", "≠": "!=",
    "✓": "v", "✔": "v", "✗": "x", "✘": "x", "−": "-", "‑": "-", "‐": "-",
    " ": " ", " ": " ", " ": " ", "≈": "~", "∅": "0", "Δ": "delta",
    "⚠": "!", "★": "*", "●": "-", "○": "-", "▪": "-", "■": "-",
}


def pdf_tekst(tekst: Any) -> str:
    """Tekst die de standaardletter kan tekenen. €, – en “ ” kunnen gewoon;
    pijlen en vinkjes worden leesbare tekens, emoji verdwijnen."""
    s = "" if tekst is None else str(tekst)
    for k, v in _VERVANG_PDF.items():
        if k in s:
            s = s.replace(k, v)
    try:
        s.encode("cp1252")
        return s
    except UnicodeEncodeError:
        pass
    uit = []
    for teken in s:
        try:
            teken.encode("cp1252")
            uit.append(teken)
        except UnicodeEncodeError:
            # é-achtige tekens terug naar hun basisletter, de rest weg
            basis = unicodedata.normalize("NFKD", teken).encode("cp1252", "ignore").decode("cp1252")
            uit.append(basis)
    return "".join(uit)


class HuisstijlPDF(FPDF):
    """A4 met briefhoofd en voet in de FieldOps-huisstijl, logo en gegevens
    van de klant. Rapporten tekenen hun inhoud tussen kop en voet met de
    hulpjes hieronder (titelblok, sectie, kv, kengetallen, tabel)."""

    MARGE = 16.0
    KOP_ONDER = 27.0          # y van de blauwe lijn
    INHOUD_START = 33.0

    def __init__(self, klant: Klant, titel: str, *, ondertitel: str = "",
                 liggend: bool = False, gemaakt_op: Optional[datetime] = None):
        super().__init__(orientation="L" if liggend else "P", unit="mm", format="A4")
        self.core_fonts_encoding = "windows-1252"
        self.klant = klant
        self.titel = titel
        self.ondertitel = ondertitel
        self.gemaakt_op = gemaakt_op or nu_lokaal()
        self.set_margins(self.MARGE, self.INHOUD_START, self.MARGE)
        self.set_auto_page_break(True, margin=22)
        self.alias_nb_pages()
        self.set_title(pdf_tekst(titel))
        self.set_author(pdf_tekst(klant.naam))
        self.set_creator("FieldOps")

    def normalize_text(self, text: str) -> str:
        return super().normalize_text(pdf_tekst(text))

    # ── kop en voet ──────────────────────────────────────────────────────
    def _logo_tekenen(self, x: float, y: float, max_b: float, max_h: float) -> bool:
        logo = self.klant.logo
        if logo is None:
            return False
        try:
            if logo.png:
                b = min(max_b, max_h * logo.verhouding)
                h = b / logo.verhouding
                self.image(io.BytesIO(logo.png), x=x, y=y + (max_h - h) / 2, w=b, h=h)
            else:
                self.image(io.BytesIO(logo.svg), x=x, y=y, h=max_h)
            return True
        except Exception:
            return False

    def header(self):
        rechts = self.w - self.MARGE
        y0 = 9.0
        met_logo = self._logo_tekenen(self.MARGE, y0, 48, 14)
        regels = self.klant.gegevens()
        if not met_logo:
            self.set_xy(self.MARGE, y0 + 2)
            self.set_font(LETTER_PDF, "B", 14)
            self.set_text_color(*rgb(INKT))
            self.cell(110, 7, self.klant.naam)
        y = y0 + (0 if met_logo else 1)
        if met_logo:
            self.set_font(LETTER_PDF, "B", 9)
            self.set_text_color(*rgb(INKT))
            self.set_xy(self.MARGE + 50, y)
            self.cell(rechts - self.MARGE - 50, 4.2, self.klant.naam, align="R")
            y += 4.6
        self.set_font(LETTER_PDF, "", 7.5)
        self.set_text_color(*rgb(GRIJS))
        for regel in regels[:3]:
            self.set_xy(self.MARGE + 50, y)
            self.cell(rechts - self.MARGE - 50, 3.6, regel, align="R")
            y += 3.7
        self.set_fill_color(*rgb(BLAUW))
        self.rect(self.MARGE, self.KOP_ONDER, self.w - 2 * self.MARGE, 0.9, "F")
        self.set_text_color(*rgb(INKT))
        self.set_xy(self.MARGE, self.INHOUD_START)

    def footer(self):
        self.set_y(-15)
        y = self.get_y()
        self.set_draw_color(*rgb(LIJN))
        self.set_line_width(0.25)
        self.line(self.MARGE, y, self.w - self.MARGE, y)
        self.set_xy(self.MARGE, y + 1.5)
        self.set_font(LETTER_PDF, "", 7.5)
        self.set_text_color(*rgb(GRIJS))
        loop = self.titel + (f" · {self.ondertitel}" if self.ondertitel else "")
        breed = self.w - 2 * self.MARGE - 32
        while loop and self.get_string_width(loop) > breed:
            loop = loop[:-2]
        self.cell(breed, 4, loop)
        self.cell(32, 4, f"Pagina {self.page_no()} van {{nb}}", align="R")
        self.set_xy(self.MARGE, y + 5.5)
        self.set_font(LETTER_PDF, "", 6.5)
        self.set_text_color(*rgb(LICHTGRIJS))
        self.cell(0, 3.5, f"Opgesteld met FieldOps · {self.gemaakt_op.strftime('%d-%m-%Y %H:%M')}")
        self.set_text_color(*rgb(INKT))

    # ── bouwstenen voor de inhoud ────────────────────────────────────────
    def ruimte_nodig(self, mm: float) -> None:
        """Nieuwe pagina als er minder dan `mm` ruimte over is, zodat een kop
        niet los onderaan een pagina blijft hangen."""
        if self.get_y() + mm > self.h - self.b_margin:
            self.add_page(same=True)

    def titelblok(self, titel: Optional[str] = None, ondertitel: Optional[str] = None,
                  meta: Optional[str] = None) -> None:
        self.set_x(self.MARGE)
        self.set_font(LETTER_PDF, "B", 19)
        self.set_text_color(*rgb(INKT))
        self.multi_cell(0, 8.5, titel or self.titel, new_x="LMARGIN", new_y="NEXT")
        sub = self.ondertitel if ondertitel is None else ondertitel
        if sub:
            self.set_font(LETTER_PDF, "", 11)
            self.set_text_color(*rgb(GRIJS))
            self.multi_cell(0, 5.8, sub, new_x="LMARGIN", new_y="NEXT")
        if meta is None:
            meta = f"Geëxporteerd op {self.gemaakt_op.strftime('%d-%m-%Y')} om {self.gemaakt_op.strftime('%H:%M')}"
        if meta:
            self.set_font(LETTER_PDF, "", 8.5)
            self.set_text_color(*rgb(LICHTGRIJS))
            self.multi_cell(0, 4.6, meta, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*rgb(INKT))
        self.ln(5)

    def sectie(self, titel: str) -> None:
        # Kop, de kopregel van een tabel en een paar regels passen samen;
        # anders begint de kop op de volgende pagina.
        self.ruimte_nodig(36)
        self.ln(2)
        y = self.get_y()
        self.set_fill_color(*rgb(BLAUW))
        self.rect(self.MARGE, y + 0.8, 1.3, 5.4, "F")
        self.set_xy(self.MARGE + 3.5, y)
        self.set_font(LETTER_PDF, "B", 12.5)
        self.set_text_color(*rgb(INKT))
        self.multi_cell(0, 7, titel, new_x="LMARGIN", new_y="NEXT")
        self.ln(1.5)

    def subsectie(self, titel: str) -> None:
        self.ruimte_nodig(30)
        self.set_font(LETTER_PDF, "B", 10.5)
        self.set_text_color(*rgb(BLAUW_DONKER))
        self.multi_cell(0, 6, titel, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*rgb(INKT))
        self.ln(0.5)

    def tekst(self, tekst: str, *, grootte: float = 10, kleur: str = INKT,
              stijl: str = "", regel: float = 5.0) -> None:
        if not tekst:
            return
        self.set_x(self.MARGE)
        self.set_font(LETTER_PDF, stijl, grootte)
        self.set_text_color(*rgb(kleur))
        self.multi_cell(0, regel, tekst, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*rgb(INKT))

    def kv(self, paren: Sequence[tuple[str, Any]], *, labelbreedte: float = 48) -> None:
        """Label-waarde-regels (projectgegevens, inspecteur, datum, ...)."""
        for label, waarde in paren:
            if waarde is None or waarde == "":
                continue
            self.ruimte_nodig(6)
            self.set_x(self.MARGE)
            self.set_font(LETTER_PDF, "", 8.5)
            self.set_text_color(*rgb(GRIJS))
            y = self.get_y()
            self.cell(labelbreedte, 5.4, str(label))
            self.set_font(LETTER_PDF, "", 10)
            self.set_text_color(*rgb(INKT))
            self.set_xy(self.MARGE + labelbreedte, y)
            self.multi_cell(0, 5.4, str(waarde), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def kengetallen(self, items: Sequence[tuple[Any, str]], *, kleuren: Optional[Sequence[str]] = None) -> None:
        """Blokjes met één groot getal en een label eronder."""
        if not items:
            return
        self.ruimte_nodig(24)
        tussen = 3.0
        per_rij = min(len(items), 4)
        b = (self.w - 2 * self.MARGE - tussen * (per_rij - 1)) / per_rij
        h = 19.0
        y = self.get_y()
        for i, (waarde, label) in enumerate(items):
            kolom = i % per_rij
            if kolom == 0 and i:
                self.set_y(y + h + tussen)
                self.ruimte_nodig(h + 2)
                y = self.get_y()
            x = self.MARGE + kolom * (b + tussen)
            self.set_fill_color(*rgb(VLAK))
            self.set_draw_color(*rgb(LIJN))
            self.set_line_width(0.25)
            self.rect(x, y, b, h, "DF")
            self.set_xy(x + 3.5, y + 3)
            self.set_font(LETTER_PDF, "B", 15)
            kleur = (kleuren[i] if kleuren and i < len(kleuren) and kleuren[i] else INKT)
            self.set_text_color(*rgb(kleur))
            self.cell(b - 7, 7.5, str(waarde))
            self.set_xy(x + 3.5, y + 11)
            self.set_font(LETTER_PDF, "", 7.5)
            self.set_text_color(*rgb(GRIJS))
            self.cell(b - 7, 4.5, str(label))
        self.set_text_color(*rgb(INKT))
        self.set_xy(self.MARGE, y + h + 5)

    def tabel(self, kolommen: Sequence[Kolom], rijen: Sequence[Sequence[Any]], *,
              totaal: Optional[Sequence[Any]] = None, lettergrootte: float = 8.5) -> None:
        """Tabel met blauwe kop die op elke nieuwe pagina terugkomt.

        Breedtes: hebben alle kolommen een `breedte`, dan zijn dat de
        verhoudingen. Anders rekent de tabel ze uit: getallen, bedragen,
        datums en jaartallen breken nooit af, koppen mogen over twee of drie
        regels, en tekstkolommen verdelen de rest naar hun inhoud. Past dat
        niet, dan een halve punt kleiner."""
        rijen = list(rijen)
        self.ruimte_nodig(16)
        if all(k.breedte for k in kolommen):
            breedtes, grootte = [float(k.breedte) for k in kolommen], lettergrootte
        else:
            breedtes, grootte = kolombreedtes(self, kolommen, rijen, totaal, lettergrootte,
                                              enkel=len(rijen) > SNEL_VANAF)
        self.set_x(self.MARGE)
        self.set_font(LETTER_PDF, "", grootte)
        self.set_text_color(*rgb(INKT))
        self.set_draw_color(*rgb(LIJN))
        self.set_fill_color(*rgb(WIT))
        self.set_line_width(0.2)

        def cel(k: Kolom, v: Any) -> str:
            tekst = als_tekst(k, v)
            return afbreekbaar(tekst) if k.soort == "tekst" else tekst

        if len(rijen) > SNEL_VANAF:
            self._tabel_snel(kolommen, [[als_tekst(k, v) for k, v in zip(kolommen, r)] for r in rijen],
                             [als_tekst(k, v) for k, v in zip(kolommen, totaal)] if totaal is not None else None,
                             breedtes, grootte)
            return
        kop = FontFace(emphasis="BOLD", color=rgb(WIT), fill_color=rgb(BLAUW))
        uitlijning = ["RIGHT" if k.rechts else "LEFT" for k in kolommen]
        with self.table(width=self.w - 2 * self.MARGE, col_widths=breedtes,
                        headings_style=kop, cell_fill_color=rgb(STREEP), cell_fill_mode="ROWS",
                        borders_layout="HORIZONTAL_LINES", line_height=grootte * 0.55,
                        text_align=uitlijning, padding=(1.1, 1.5), align="LEFT",
                        first_row_as_headings=True, repeat_headings=1) as t:
            kopregel = t.row()
            for k in kolommen:
                kopregel.cell(k.naam)
            for rij in rijen:
                r = t.row()
                for k, v in zip(kolommen, rij):
                    r.cell(cel(k, v))
            if totaal is not None:
                r = t.row(style=FontFace(emphasis="BOLD", fill_color=rgb(LIJN)))
                for k, v in zip(kolommen, totaal):
                    r.cell(cel(k, v))
        self.ln(3)

    # fpdf2's tabel meet elke cel op meerdere regels: ~10 ms per rij. Bij een
    # MJOP van 2000 regels is dat 20 seconden. Daarboven tekent de tabel zelf,
    # één regel per rij; wat niet past wordt ingekort (de Excel heeft alles).
    def _regels_passend(self, tekst: str, breedte: float) -> list[str]:
        regels, regel = [], ""
        for woord in tekst.split():
            kandidaat = (regel + " " + woord).strip()
            if regel and self.get_string_width(kandidaat) > breedte:
                regels.append(regel)
                regel = woord
            else:
                regel = kandidaat
        return regels + [regel] if regel else regels or [""]

    def _inkorten(self, tekst: str, breedte: float) -> tuple[str, bool]:
        if self.get_string_width(tekst) <= breedte:
            return tekst, False
        laag, hoog = 0, len(tekst)
        while laag < hoog:
            midden = (laag + hoog + 1) // 2
            if self.get_string_width(tekst[:midden].rstrip() + "…") <= breedte:
                laag = midden
            else:
                hoog = midden - 1
        return tekst[:laag].rstrip() + "…", True

    def _tabel_snel(self, kolommen, teksten, totaal, breedtes, grootte) -> None:
        schaal = (self.w - 2 * self.MARGE) / sum(breedtes)
        breedtes = [b * schaal for b in breedtes]
        regel, pad_v, pad_h = grootte * 0.55, 1.1, 1.5
        rij_h = regel + 2 * pad_v
        ingekort = False

        def kopregel():
            self.set_font(LETTER_PDF, "B", grootte)
            per_kolom = [self._regels_passend(k.naam, b - 2 * pad_h) for k, b in zip(kolommen, breedtes)]
            h = max(len(r) for r in per_kolom) * regel + 2 * pad_v
            self.ruimte_nodig(h + rij_h)
            y, x = self.get_y(), self.MARGE
            self.set_fill_color(*rgb(BLAUW))
            self.rect(self.MARGE, y, sum(breedtes), h, "F")
            self.set_text_color(*rgb(WIT))
            for k, b, regels in zip(kolommen, breedtes, per_kolom):
                for j, r in enumerate(regels):
                    self.set_xy(x + pad_h, y + pad_v + j * regel)
                    self.cell(b - 2 * pad_h, regel, r, align="R" if k.rechts else "L")
                x += b
            self.set_xy(self.MARGE, y + h)

        def teken(waarden, vet: bool, vulling: Optional[str]):
            nonlocal ingekort
            y, x = self.get_y(), self.MARGE
            if vulling:
                self.set_fill_color(*rgb(vulling))
                self.rect(self.MARGE, y, sum(breedtes), rij_h, "F")
            self.set_font(LETTER_PDF, "B" if vet else "", grootte)
            self.set_text_color(*rgb(INKT))
            for k, b, tekst in zip(kolommen, breedtes, waarden):
                tekst, kort = self._inkorten(tekst.replace("\n", " "), b - 2 * pad_h)
                ingekort = ingekort or kort
                self.set_xy(x + pad_h, y + pad_v)
                self.cell(b - 2 * pad_h, regel, tekst, align="R" if k.rechts else "L")
                x += b
            self.set_draw_color(*rgb(LIJN))
            self.set_line_width(0.2)
            self.line(self.MARGE, y + rij_h, self.MARGE + sum(breedtes), y + rij_h)
            self.set_xy(self.MARGE, y + rij_h)

        kopregel()
        for i, waarden in enumerate(teksten):
            if self.get_y() + rij_h > self.h - self.b_margin:
                self.add_page(same=True)
                kopregel()
            teken(waarden, False, STREEP if i % 2 == 0 else None)
        if totaal is not None:
            if self.get_y() + rij_h > self.h - self.b_margin:
                self.add_page(same=True)
                kopregel()
            teken(totaal, True, LIJN)
        self.ln(3)
        if ingekort:
            self.tekst("Lange teksten staan hier op één regel ingekort; de Excel-export bevat ze volledig.",
                       grootte=7.5, kleur=GRIJS, regel=4)

    def blad(self, blad: Blad, *, met_kop: bool = True) -> None:
        if met_kop:
            self.sectie(blad.naam)
        if blad.rijen:
            self.tabel(blad.kolommen, blad.rijen, totaal=blad.totaal)
        else:
            self.tekst("Geen gegevens voor deze selectie.", grootte=9.5, kleur=GRIJS)
        for regel in blad.toelichting:
            self.tekst(regel, grootte=8, kleur=GRIJS, regel=4.2)

    def uitvoer(self) -> bytes:
        return bytes(self.output())


def _tekstbreedte(kolom: Kolom, waarden: Sequence[Any]) -> int:
    """Breedte naar inhoud in tekens: het 85e percentiel, zodat een enkele
    uitschieter de tabel niet scheef trekt. De kop telt mee per woord, want
    die mag over twee regels."""
    kop = max((len(w) for w in kolom.naam.split()), default=4)
    lengtes = sorted(n for n in (len(als_tekst(kolom, v)) for v in list(waarden)[:400]) if n)
    if not lengtes:
        return max(kop, 6)
    return max(kop, lengtes[int((len(lengtes) - 1) * 0.85)], 4)


SNEL_VANAF = 400


def _kopbreedte(pdf: FPDF, naam: str, regels: int) -> float:
    """Kleinste breedte waarin een kolomkop in `regels` regels past, zonder
    woorden te breken."""
    woorden = naam.split() or [""]
    breed = pdf.get_string_width
    kandidaten = sorted({breed(" ".join(woorden[a:b]))
                         for a in range(len(woorden)) for b in range(a + 1, len(woorden) + 1)})
    langste = max(breed(w) for w in woorden)
    for kandidaat in kandidaten:
        if kandidaat < langste:
            continue
        n, regel = 1, woorden[0]
        for w in woorden[1:]:
            if breed(regel + " " + w) <= kandidaat:
                regel += " " + w
            else:
                n, regel = n + 1, w
        if n <= regels:
            return kandidaat
    return kandidaten[-1]


def _metingen(pdf: FPDF, kolommen, rijen, totaal, grootte: float) -> list[dict]:
    """Per kolom de maten (mm, bij `grootte` punt) die de breedte bepalen.
    Eén keer meten is genoeg: tekstbreedte schaalt recht met de letter."""
    uit = []
    for i, k in enumerate(kolommen):
        teksten = [als_tekst(k, rij[i]) for rij in rijen]
        pdf.set_font(LETTER_PDF, "", grootte)
        # Types, normen en maatregelen herhalen zich: elke tekst één keer meten.
        maat = {t: pdf.get_string_width(t) for t in set(teksten)}
        breed = sorted(maat[t] for t in teksten) or [0.0]
        woord = 0.0 if k.vast else max(
            [0.0] + [pdf.get_string_width(w) for w in {w for t in maat for w in t.split()}])
        pdf.set_font(LETTER_PDF, "B", grootte)      # kop en totaalregel zijn vet
        uit.append({
            "vast": k.vast,
            "mm": float(k.breedte) if k.breedte else None,
            "breedste": breed[-1],
            "p85": breed[int((len(breed) - 1) * 0.85)],
            "woord": woord,
            "totaal": pdf.get_string_width(als_tekst(k, totaal[i])) if totaal is not None else 0.0,
            "kopwoord": max(pdf.get_string_width(w) for w in (k.naam.split() or [""])),
            "kop": {n: _kopbreedte(pdf, k.naam, n) for n in (2, 3)},
        })
    return uit


def _probeer_breedtes(beschikbaar: float, metingen, schaal: float, kopregels: int,
                      enkel: bool = False):
    """Breedtes (mm) bij deze letterschaal, of None als het niet past. Met
    `enkel` staat elke cel op één regel: korte teksten (tot 25 mm) moeten dan
    helemaal passen, alleen lange worden ingekort."""
    marge = 3.3                      # celpadding links + rechts (3 mm), plus wat lucht
    vast: dict[int, float] = {}
    wens: dict[int, float] = {}
    minimaal: dict[int, float] = {}
    for i, m in enumerate(metingen):
        if m["mm"]:
            vast[i] = m["mm"]        # door de export zelf vastgelegd
        elif m["vast"]:
            vast[i] = max(m["breedste"], m["totaal"], m["kop"][kopregels]) * schaal + marge
        else:
            # Een kopwoord breekt nooit; een extreem lang woord in de inhoud mag.
            inhoud = min(m["breedste"], 25) if enkel else min(max(m["woord"], m["totaal"]), 13)
            minimaal[i] = max(m["kopwoord"] * schaal, inhoud * schaal) + marge
            wens[i] = max(minimaal[i], min((m["breedste"] if enkel else m["p85"]) * schaal, 60) + marge)
    rest = beschikbaar - sum(vast.values())
    if rest < sum(minimaal.values()) - 0.01:
        return None
    breedte = dict(vast)
    if not wens:
        factor = beschikbaar / sum(vast.values())
        return {i: w * factor for i, w in vast.items()}
    if rest >= sum(wens.values()):
        factor = rest / sum(wens.values())
        breedte.update({i: w * factor for i, w in wens.items()})
    else:
        extra = rest - sum(minimaal.values())
        speling = sum(wens[i] - minimaal[i] for i in wens) or 1
        breedte.update({i: minimaal[i] + extra * (wens[i] - minimaal[i]) / speling for i in wens})
    return breedte


def kolombreedtes(pdf: FPDF, kolommen: Sequence[Kolom], rijen: Sequence[Sequence[Any]],
                  totaal: Optional[Sequence[Any]], grootte: float,
                  enkel: bool = False) -> tuple[list[float], float]:
    """Breedte per kolom in mm, plus de lettergrootte waarbij dat past."""
    beschikbaar = pdf.w - 2 * HuisstijlPDF.MARGE
    metingen = _metingen(pdf, kolommen, list(rijen[:3000]), totaal, grootte)
    for g in (grootte, grootte - 0.5, grootte - 1.0):
        for kopregels in (2, 3):
            breedte = _probeer_breedtes(beschikbaar, metingen, g / grootte, kopregels, enkel)
            if breedte is not None:
                return [breedte[i] for i in range(len(kolommen))], g
    # Past echt niet: verdeel naar inhoud en laat afbreken.
    return [float(min(36, _tekstbreedte(k, [r[i] for r in rijen])))
            for i, k in enumerate(kolommen)], grootte - 1.0


# Lange samenstellingen (ontruimingsalarminstallatie) passen niet in een smalle
# kolom; fpdf2 breekt ze dan midden in het woord af zonder streepje. Met zachte
# afbreekstreepjes op lettergreepgrenzen breekt hij netjes, met een streepje.
# Eenvoudige regel: tussen twee klinkers gaat de laatste medeklinker (of een
# beginklank als bl, st, sch) mee naar de volgende lettergreep, en een
# tussen-s blijft bij het eerste deel (ontruimings-alarm). Alleen voor de PDF.
_KLINKERS = set("aeiouyáéíóúàèëïöüâêîôû")
_BEGINKLANKEN = {"bl", "br", "ch", "dr", "dw", "fl", "fr", "gl", "gr", "kl", "kn", "kr",
                 "pl", "pr", "sch", "schr", "sj", "sl", "sm", "sn", "sp", "spr", "st",
                 "str", "tj", "tr", "tw", "vl", "vr", "wr", "zw"}
_LANG_WOORD = re.compile(r"[^\W\d_]{15,}")


def _klinker(w: str, i: int) -> bool:
    c = w[i].lower()
    return c in _KLINKERS or (c == "j" and i > 0 and w[i - 1].lower() == "i")


def _lettergrepen(m: re.Match) -> str:
    w, grenzen, i = m.group(0), [], 0
    while i < len(w):
        if not _klinker(w, i):
            i += 1
            continue
        j = i + 1
        while j < len(w) and not _klinker(w, j):
            j += 1
        if i + 1 < j < len(w):
            cluster = w[i + 1:j].lower()
            k = len(cluster) - 1
            for lengte in (4, 3, 2):
                if cluster[-lengte:] in _BEGINKLANKEN and len(cluster) >= lengte:
                    k = len(cluster) - lengte
                    break
            if cluster[k:] == "s" and len(cluster) >= 2:
                k = len(cluster)
            if 3 <= i + 1 + k <= len(w) - 3:
                grenzen.append(i + 1 + k)
        i = j
    for pos in reversed(grenzen):
        w = w[:pos] + "\u00ad" + w[pos:]
    return w


def afbreekbaar(tekst: str) -> str:
    """Lange woorden krijgen zachte afbreekstreepjes (alleen voor de PDF)."""
    return _LANG_WOORD.sub(_lettergrepen, tekst) if tekst else tekst


def pdf_van(klant: Klant, titel: str, bladen: Sequence[Blad], *, ondertitel: str = "",
            meta: Optional[str] = None, liggend: Optional[bool] = None) -> bytes:
    """Een PDF met één of meer overzichten, zoals `excel_van` ze als tabbladen
    maakt."""
    if liggend is None:
        liggend = any(b.is_liggend for b in bladen)
    pdf = HuisstijlPDF(klant, titel, ondertitel=ondertitel, liggend=liggend)
    pdf.add_page()
    pdf.titelblok(meta=meta)
    for b in bladen:
        pdf.blad(b, met_kop=len(bladen) > 1)
    return pdf.uitvoer()


# ═══════════════════════════════════════════════════════════════════════════
# Excel
# ═══════════════════════════════════════════════════════════════════════════

KOP_RIJ_EXCEL = 11

_ONGELDIG_BLAD = re.compile(r"[\[\]:*?/\\]")


def _bladnaam(naam: str, gebruikt: set[str]) -> str:
    basis = _ONGELDIG_BLAD.sub("-", naam).strip("'") or "Blad"
    basis = basis[:31]
    kandidaat, n = basis, 2
    while kandidaat.lower() in gebruikt:
        achter = f" ({n})"
        kandidaat = basis[:31 - len(achter)] + achter
        n += 1
    gebruikt.add(kandidaat.lower())
    return kandidaat


def _excel_waarde(kolom: Kolom, v: Any) -> Any:
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return "ja" if v else "nee"
    if kolom.soort in ("datum", "datumtijd"):
        d = _als_datum(v, kolom.soort == "datumtijd")
        return d if d is not None else ILLEGAL_CHARACTERS_RE.sub("", str(v))
    if kolom.rechts or kolom.soort == "jaar":
        getal = _als_getal(v)
        if getal is not None:
            return int(round(getal)) if kolom.soort in ("heel", "jaar") else getal
    return ILLEGAL_CHARACTERS_RE.sub("", str(v))


def excel_van(klant: Klant, titel: str, bladen: Sequence[Blad], *, ondertitel: str = "",
              meta: Optional[str] = None) -> bytes:
    """Een werkboek met per overzicht een tabblad, opgemaakt als de PDF."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    wb.properties.title = titel
    wb.properties.creator = klant.naam
    wb.properties.lastModifiedBy = "FieldOps"
    gemaakt = nu_lokaal()
    if meta is None:
        meta = f"Geëxporteerd op {gemaakt.strftime('%d-%m-%Y')} om {gemaakt.strftime('%H:%M')}"
    gebruikt: set[str] = set()
    for blad in bladen:
        ws = wb.create_sheet(_bladnaam(blad.naam, gebruikt))
        _teken_blad(ws, klant, blad, titel=titel if len(bladen) == 1 else f"{titel} — {blad.naam}",
                    ondertitel=ondertitel, meta=meta)
    uit = io.BytesIO()
    wb.save(uit)
    return uit.getvalue()


def _teken_blad(ws, klant: Klant, blad: Blad, *, titel: str, ondertitel: str, meta: str) -> None:
    from openpyxl.drawing.image import Image as XlImage
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    n = max(len(blad.kolommen), 1)
    laatste = get_column_letter(max(n, 2))
    f = lambda **kw: Font(name=LETTER_EXCEL, **kw)  # noqa: E731
    vul = lambda kleur: PatternFill("solid", start_color=kleur, end_color=kleur)  # noqa: E731
    lijn = Side(style="thin", color=LIJN)
    ws.sheet_view.showGridLines = False

    # ── briefhoofd: rij 1-4, blauwe lijn op rij 5
    for r in range(1, 5):
        ws.row_dimensions[r].height = 15
    rechts = Alignment(horizontal="right", vertical="center")
    met_logo = False
    if klant.logo and klant.logo.png:
        try:
            img = XlImage(io.BytesIO(klant.logo.png))
            max_b, max_h = 190, 56
            b = min(max_b, max_h * klant.logo.verhouding)
            img.width, img.height = int(b), int(b / klant.logo.verhouding)
            ws.add_image(img, "A1")
            met_logo = True
        except Exception:
            met_logo = False
    regels = klant.gegevens()
    rij = 1
    if met_logo:
        c = ws[f"{laatste}1"]
        c.value, c.font, c.alignment = klant.naam, f(bold=True, size=10, color=INKT), rechts
        rij = 2
    else:
        c = ws["A1"]
        c.value, c.font = klant.naam, f(bold=True, size=14, color=INKT)
        ws.row_dimensions[1].height = 20
        rij = 2
    for regel in regels[:3]:
        c = ws[f"{laatste}{rij}"]
        c.value, c.font, c.alignment = regel, f(size=8, color=GRIJS), rechts
        rij += 1
    ws.row_dimensions[5].height = 3.75
    for kol in range(1, max(n, 2) + 1):
        ws.cell(row=5, column=kol).fill = vul(BLAUW)

    # ── titel
    ws.row_dimensions[6].height = 9
    ws["A7"].value, ws["A7"].font = titel, f(bold=True, size=16, color=INKT)
    ws.row_dimensions[7].height = 24
    # Vaste plekken, ook als ze leeg zijn: de kopregel staat altijd op rij 11.
    if ondertitel:
        ws["A8"].value, ws["A8"].font = ondertitel, f(size=10.5, color=GRIJS)
    if meta:
        ws["A9"].value, ws["A9"].font = meta, f(size=8.5, color=LICHTGRIJS)
    kop_rij = KOP_RIJ_EXCEL

    # ── tabel
    kop_font = f(bold=True, size=9, color=WIT)
    for i, k in enumerate(blad.kolommen, start=1):
        c = ws.cell(row=kop_rij, column=i, value=k.naam)
        c.font, c.fill = kop_font, vul(BLAUW)
        c.alignment = Alignment(horizontal="right" if k.rechts else "left",
                                vertical="center", wrap_text=True)
        c.border = Border(bottom=lijn)
    breedtes, afbreken = _excel_kolommen(blad)
    # Logo links en naam met gegevens rechts hebben samen ~58 breed nodig;
    # op een smal tabblad schuiven ze anders over elkaar.
    if sum(breedtes) < 58:
        breedtes[-1] += 58 - sum(breedtes)
    kopregels = max(len(textwrap.wrap(k.naam, max(1, int(w * _TEKENS_PER_EENHEID) - 1)) or [""])
                    for k, w in zip(blad.kolommen, breedtes))
    ws.row_dimensions[kop_rij].height = max(30, kopregels * _REGELHOOGTE_PT + 6)
    celfont = f(size=9, color=INKT)
    rand = Border(bottom=lijn)
    for j, rij_waarden in enumerate(blad.rijen):
        rr = kop_rij + 1 + j
        streep = j % 2 == 0          # zoals de PDF: de eerste regel gestreept
        hoogte = _excel_rijhoogte(rij_waarden, blad.kolommen, breedtes, afbreken)
        if hoogte:
            ws.row_dimensions[rr].height = hoogte
        for i, (k, v) in enumerate(zip(blad.kolommen, rij_waarden), start=1):
            c = ws.cell(row=rr, column=i)
            waarde = _excel_waarde(k, v)
            c.value = waarde
            if isinstance(waarde, str) and waarde.startswith("="):
                c.data_type = "s"          # tekst, geen formule
            c.font, c.border = celfont, rand
            if k.excel_formaat and not isinstance(waarde, str):
                c.number_format = k.excel_formaat
            c.alignment = Alignment(horizontal="right" if k.rechts else "left",
                                    vertical="center", wrap_text=afbreken[i - 1])
            if streep:
                c.fill = vul(STREEP)
    laatste_data = kop_rij + len(blad.rijen)
    if not blad.rijen:
        ws.cell(row=kop_rij + 1, column=1, value="Geen gegevens voor deze selectie.").font = f(
            size=9, italic=True, color=GRIJS)
        laatste_data = kop_rij + 1

    if blad.totaal is not None:
        rr = laatste_data + 1
        boven = Border(top=Side(style="medium", color=INKT), bottom=lijn)
        for i, (k, v) in enumerate(zip(blad.kolommen, blad.totaal), start=1):
            c = ws.cell(row=rr, column=i)
            waarde = _excel_waarde(k, v)
            c.value = waarde
            c.font, c.fill, c.border = f(bold=True, size=9, color=INKT), vul(LIJN), boven
            if k.excel_formaat and not isinstance(waarde, str):
                c.number_format = k.excel_formaat
            c.alignment = Alignment(horizontal="right" if k.rechts else "left", vertical="center")
        laatste_data = rr

    # Toelichting over de volle tabelbreedte, teruglopend: één lange regel
    # zou anders de afdruk van de hele tabel verkleinen.
    rr = laatste_data + 2
    totaal_breed = sum(breedtes)
    for regel in blad.toelichting:
        c = ws.cell(row=rr, column=1, value=regel)
        c.font = f(size=8, italic=True, color=GRIJS)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if len(breedtes) > 1:
            ws.merge_cells(start_row=rr, start_column=1, end_row=rr, end_column=len(breedtes))
        n = len(textwrap.wrap(regel, max(1, int(totaal_breed * _TEKENS_PER_EENHEID * 1.1) - 1)) or [""])
        ws.row_dimensions[rr].height = n * 11 + 3
        rr += 1

    # ── breedtes, bevriezen, filter, afdrukken
    for i, w in enumerate(breedtes, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(row=kop_rij + 1, column=1)
    if blad.rijen:
        ws.auto_filter.ref = f"A{kop_rij}:{get_column_letter(n)}{kop_rij + len(blad.rijen)}"

    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "landscape" if blad.is_liggend else "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{kop_rij}:{kop_rij}"
    ws.page_margins.left = ws.page_margins.right = 0.6
    ws.page_margins.top, ws.page_margins.bottom = 0.6, 0.7
    for deel, tekst in ((ws.oddFooter.left, titel), (ws.oddFooter.center, "Opgesteld met FieldOps"),
                        (ws.oddFooter.right, "Pagina &P van &N")):
        deel.text, deel.size, deel.font, deel.color = tekst, 8, f"{LETTER_EXCEL},Regular", GRIJS


# Arial 9 in een Excel-kolom: ongeveer 1,15 teken per breedte-eenheid, en een
# regel is ~12,6 punt hoog (op papier gemeten). Liever te ruim dan afgekapt.
_TEKENS_PER_EENHEID = 1.15
_REGELHOOGTE_PT = 12.6


def _excel_kolommen(blad: Blad) -> tuple[list[float], list[bool]]:
    """Breedte per kolom, en of de tekst erin mag afbreken. Getallen en codes
    zonder spaties (BRUG.ONDERBOUW, KW-0012) breken niet af en krijgen de
    breedte van de langste waarde; tekst breekt nooit midden in een woord."""
    breedtes, afbreken = [], []
    for i, k in enumerate(blad.kolommen):
        waarden = [rij[i] for rij in blad.rijen]
        if blad.totaal is not None:
            waarden = waarden + [blad.totaal[i]]
        teksten = [als_tekst(k, v) for v in waarden[:3000]]
        gevuld = [t for t in teksten if t]
        kop = max((len(w) for w in k.naam.split()), default=6)
        if k.soort == "datum":
            breedtes.append(max(12, kop + 2))
            afbreken.append(False)
        elif k.soort == "datumtijd":
            breedtes.append(max(17, kop + 2))
            afbreken.append(False)
        elif k.vast:
            # Getallen: de langste opgemaakte waarde, anders toont Excel ####.
            langste = max((len(t) for t in gevuld), default=4)
            breedtes.append(max(8, kop + 2, min(langste + 3, 24)))
            afbreken.append(False)
        elif gevuld and not any(" " in t or "\n" in t for t in gevuld):
            # Codes zijn vaak hoofdletters, die zijn breder dan gemiddeld.
            breedtes.append(max(8, kop + 2, min(round(max(len(t) for t in gevuld) * 1.2) + 2, 45)))
            afbreken.append(False)
        else:
            inhoud = _tekstbreedte(k, waarden)
            woord = max((len(w) for t in gevuld for w in t.split()), default=0)
            breedtes.append(max(8, min(inhoud + 2, 60), min(woord + 2, 30)))
            afbreken.append(True)
    return breedtes, afbreken


def _excel_rijhoogte(rij: Sequence[Any], kolommen: Sequence[Kolom], breedtes: Sequence[float],
                     afbreken: Sequence[bool]) -> Optional[float]:
    """Hoogte die een regel nodig heeft als zijn tekst afbreekt. Excel rekent
    dat zelf niet uit bij het openen of afdrukken, dan valt tekst weg."""
    regels = 1
    for k, v, w, breekt in zip(kolommen, rij, breedtes, afbreken):
        if not breekt:
            continue
        tekst = als_tekst(k, v)
        if not tekst:
            continue
        per_regel = max(1, int(w * _TEKENS_PER_EENHEID) - 1)
        n = sum(max(1, len(textwrap.wrap(deel, per_regel, break_long_words=True)))
                for deel in tekst.split("\n"))
        regels = max(regels, n)
    # Eén regel laat Excel zelf; alleen afgebroken tekst krijgt een vaste hoogte.
    return round(regels * _REGELHOOGTE_PT + 5, 1) if regels > 1 else None


# ═══════════════════════════════════════════════════════════════════════════
# Antwoorden
# ═══════════════════════════════════════════════════════════════════════════

def bestandsnaam(*delen: Any, ext: str, met_datum: bool = True) -> str:
    """'MJOP', 'Gemeente Één' -> 'MJOP-Gemeente-Een-2026-09-21.pdf'. Zonder
    datum als de delen zelf al een periode noemen."""
    stukken = []
    for d in delen:
        if not d:
            continue
        s = unicodedata.normalize("NFKD", str(d)).encode("ascii", "ignore").decode("ascii")
        s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
        if s:
            stukken.append(s[:40])
    if met_datum:
        stukken.append(nu_lokaal().strftime("%Y-%m-%d"))
    return "-".join(stukken) + "." + ext.lstrip(".")


def pdf_antwoord(inhoud: bytes, naam: str) -> Response:
    return Response(content=inhoud, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{naam}"'})


def excel_antwoord(inhoud: bytes, naam: str) -> Response:
    return Response(content=inhoud, media_type=XLSX_MIME,
                    headers={"Content-Disposition": f'attachment; filename="{naam}"'})
