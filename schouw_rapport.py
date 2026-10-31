"""Schouwrapport: een schouwronde als PDF, met elke schade en het beeld erbij.

Het rapport is het bewijs van de ronde. Daarom staat elke schade erin die niet
is afgewezen, met de foto waarop hij is gezien en het rode vak op de plek
waar de herkenning hem zag -- hetzelfde vak als in het portaal. Afgewezen
schades staan achteraan in een lijst, zonder foto: ze tellen niet mee, maar
wie het spoor wil volgen, ziet ze wel.

Opbouw:
  1. Gegevens van de ronde en de kengetallen
  2. Overzicht: alle schades in één tabel
  3. Per schade: foto met het rode vak, en de gegevens ernaast
  4. Overige waarnemingen (beeldkwaliteit), met foto als die er is
  5. Afgewezen
"""

from __future__ import annotations

import io
from datetime import timezone
from typing import Callable, Optional

import crow_schouw as cs
import crow_wegschade as cw
import schouw_leren
from export_huisstijl import (
    FOUT, GOED, GRIJS, INKT, LET_OP, LETTER_PDF, LIJN,
    HuisstijlPDF, Kolom, klant_van, naar_nl, rgb,
)
from photo_storage import lees_foto

# Beelden op een maat die scherp genoeg is op papier, zonder dat een rit met
# honderden schades een PDF van honderden megabytes wordt.
FOTO_PX = 1100
FOTO_B_MM = 92.0
FOTO_MAX_H_MM = 70.0

_ERNST = {"L": "licht", "M": "matig", "E": "ernstig"}
_ERNST_KLEUR = {"L": LET_OP, "M": LET_OP, "E": FOUT}


def _datum(dt) -> str:
    if not dt:
        return ""
    # De database bewaart UTC, soms zonder zone erbij.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return naar_nl(dt).strftime("%d-%m-%Y %H:%M")


def _plek(w) -> str:
    delen = []
    if w.straatnaam:
        delen.append(w.straatnaam)
    if w.lat is not None and w.lng is not None:
        delen.append(f"{w.lat:.6f}, {w.lng:.6f}")
    return " · ".join(delen)


def _naam(w) -> str:
    if w.crow_schadebeeld:
        s = cw.zoek(w.crow_verharding, w.crow_schadebeeld)
        return s["naam"] if s else w.crow_schadebeeld
    m = cs.meetlat(w.meetlat) if w.meetlat else None
    if m:
        return m["naam"]
    return (cs.DETECTIEKLASSEN.get(w.detectieklasse, {}).get("naam")
            or w.detectieklasse or "Waarneming")


def _status(w, telt_mee: bool) -> str:
    if w.afgewezen:
        return "afgewezen"
    if w.bevestigd:
        return "bevestigd"
    return "telt mee (automatisch)" if telt_mee else "wacht op oordeel"


def _kader(w) -> Optional[list[float]]:
    import json
    if not w.kader:
        return None
    try:
        k = json.loads(w.kader)
    except (TypeError, ValueError):
        return None
    return k if isinstance(k, list) and len(k) == 4 else None


def beeld_met_vak(ruw: Optional[bytes], kader: Optional[list[float]],
                  label: str = "") -> Optional[tuple[bytes, float]]:
    """JPEG met het rode vak erop, zoals het portaal het tekent: rood op 25%,
    rode rand, de naam erboven. Geeft (bytes, breedte/hoogte) of None."""
    if not ruw:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
        with Image.open(io.BytesIO(ruw)) as bron:
            bron.load()
            beeld = ImageOps.exif_transpose(bron).convert("RGB")
        beeld.thumbnail((FOTO_PX, FOTO_PX))
        if kader:
            w, h = beeld.size
            x0, y0, x1, y1 = (max(0.0, min(1.0, float(v))) for v in kader)
            vak = (round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h))
            if vak[2] > vak[0] and vak[3] > vak[1]:
                laag = Image.new("RGBA", beeld.size, (0, 0, 0, 0))
                teken = ImageDraw.Draw(laag)
                teken.rectangle(vak, fill=(255, 0, 0, 64))
                beeld = Image.alpha_composite(beeld.convert("RGBA"), laag).convert("RGB")
                teken = ImageDraw.Draw(beeld)
                dikte = max(2, round(w / 300))
                teken.rectangle(vak, outline=(255, 0, 0), width=dikte)
                if label:
                    grootte = max(14, round(w / 45))
                    try:
                        letter = ImageFont.load_default(size=grootte)
                    except TypeError:       # Pillow < 10.1: vaste kleine letter
                        letter = ImageFont.load_default()
                    ty = vak[1] - grootte - 6 if vak[1] > grootte + 8 else vak[3] + 4
                    teken.text((vak[0] + 3, ty), label, font=letter, fill=(255, 255, 255),
                               stroke_width=max(2, grootte // 8), stroke_fill=(0, 0, 0))
        uit = io.BytesIO()
        beeld.save(uit, format="JPEG", quality=80, optimize=True)
        return uit.getvalue(), beeld.width / max(beeld.height, 1)
    except Exception:  # noqa: BLE001 -- een kapot beeld mag het rapport niet slopen
        return None


def _blok(pdf: HuisstijlPDF, nr: int, w, telt_mee: bool, foto) -> None:
    """Eén schade: foto links, gegevens rechts."""
    titel = f"{nr}. {_naam(w)}"
    regels: list[tuple[str, str]] = []
    if w.crow_verharding:
        regels.append(("Verharding", cw.VERHARDINGEN.get(w.crow_verharding, {}).get("naam")
                       or w.crow_verharding))
    if w.crow_ernst:
        regels.append(("Ernst", f"{w.crow_ernst} ({_ERNST.get(w.crow_ernst, '')})"))
    if w.crow_omvang:
        regels.append(("Omvang", w.crow_omvang))
    ki = cw.klasse_indicatie(w.crow_ernst, w.crow_omvang)
    if ki:
        regels.append(("Klasse (indicatie)", ki))
    if not w.crow_schadebeeld:
        if w.waarde is not None:
            regels.append(("Waarde", f"{w.waarde:g}"))
        if w.klasse_niveau:
            regels.append(("Niveau", w.klasse_niveau))
    if w.zekerheid is not None:
        regels.append(("Zekerheid", f"{round(w.zekerheid * 100)}%"))
    if (w.keer_gezien or 1) > 1:
        regels.append(("Gezien", f"{w.keer_gezien}x"))
    regels.append(("Status", _status(w, telt_mee)))
    if w.melding_id:
        regels.append(("Melding", "aangemaakt"))
    plek = _plek(w)
    if plek:
        regels.append(("Plek", plek))
    if w.created_at:
        regels.append(("Vastgelegd", _datum(w.created_at)))
    if w.toelichting:
        regels.append(("Toelichting", w.toelichting))

    breed = pdf.w - 2 * pdf.MARGE
    if foto:
        data, verhouding = foto
        fb = min(FOTO_B_MM, FOTO_MAX_H_MM * verhouding)
        fh = fb / verhouding
    else:
        fb, fh = FOTO_B_MM, 14.0
    tekst_x = pdf.MARGE + fb + 5
    tekst_b = breed - fb - 5
    # Schatting van de hoogte van de gegevens, zodat het blok niet over een
    # pagina breekt: foto en gegevens horen bij elkaar.
    pdf.set_font(LETTER_PDF, "", 8.5)
    tekst_h = 7.0 + sum(4.4 * max(1, len(pdf.multi_cell(tekst_b - 26, 4.4, v, dry_run=True,
                                                          output="LINES")))
                        for _, v in regels)
    pdf.ruimte_nodig(max(fh, tekst_h) + 8)

    y = pdf.get_y()
    if foto:
        try:
            pdf.image(io.BytesIO(data), x=pdf.MARGE, y=y, w=fb, h=fh)
            pdf.set_draw_color(*rgb(LIJN))
            pdf.set_line_width(0.2)
            pdf.rect(pdf.MARGE, y, fb, fh)
        except Exception:  # noqa: BLE001
            foto = None
    if not foto:
        pdf.set_draw_color(*rgb(LIJN))
        pdf.set_line_width(0.2)
        pdf.rect(pdf.MARGE, y, fb, fh)
        pdf.set_xy(pdf.MARGE, y + fh / 2 - 2.5)
        pdf.set_font(LETTER_PDF, "I", 8)
        pdf.set_text_color(*rgb(GRIJS))
        pdf.cell(fb, 5, "Geen beeld bewaard", align="C")

    pdf.set_xy(tekst_x, y)
    pdf.set_font(LETTER_PDF, "B", 10.5)
    kleur = _ERNST_KLEUR.get(w.crow_ernst) if w.crow_schadebeeld else None
    pdf.set_text_color(*rgb(kleur or INKT))
    pdf.multi_cell(tekst_b, 5.5, titel, align="L", new_x="LEFT", new_y="NEXT")
    pdf.ln(0.8)
    for label, waarde in regels:
        pdf.set_x(tekst_x)
        ry = pdf.get_y()
        pdf.set_font(LETTER_PDF, "", 7.5)
        pdf.set_text_color(*rgb(GRIJS))
        pdf.cell(26, 4.4, label)
        pdf.set_xy(tekst_x + 26, ry)
        pdf.set_font(LETTER_PDF, "", 8.5)
        kleur = GOED if waarde == "bevestigd" else (FOUT if waarde == "afgewezen" else INKT)
        pdf.set_text_color(*rgb(kleur))
        pdf.multi_cell(tekst_b - 26, 4.4, waarde, align="L", new_x="LEFT", new_y="NEXT")
    pdf.set_text_color(*rgb(INKT))
    onder = max(y + fh, pdf.get_y()) + 4
    pdf.set_draw_color(*rgb(LIJN))
    pdf.set_line_width(0.2)
    pdf.line(pdf.MARGE, onder - 2, pdf.w - pdf.MARGE, onder - 2)
    pdf.set_xy(pdf.MARGE, onder)


def maak_pdf(rit, *, telt_mee: Callable, organization) -> bytes:
    """De PDF van één schouwronde. `telt_mee(w)` zegt of een waarneming in de
    uitslag meetelt (de regel staat in de router)."""
    waarnemingen = sorted(rit.waarnemingen or [],
                          key=lambda w: (w.created_at is None, w.created_at))
    schades = [w for w in waarnemingen if w.crow_schadebeeld and not w.afgewezen]
    overig = [w for w in waarnemingen if not w.crow_schadebeeld and not w.afgewezen]
    afgewezen = [w for w in waarnemingen if w.afgewezen]

    gebied = rit.gebied or rit.naam or "Schouwronde"
    pdf = HuisstijlPDF(klant_van(organization), "Schouwrapport", ondertitel=gebied)
    pdf.add_page()
    pdf.titelblok()

    rijdend = rit.privacy_modus == "rijdend"
    pdf.kv([
        ("Gebied", gebied),
        ("Naam ronde", rit.naam if rit.naam and rit.naam != gebied else None),
        ("Gebiedstype", (cs.GEBIEDSTYPEN.get(rit.gebiedstype or "") or {}).get("naam")),
        ("Ambitieniveau", rit.ambitie or cs.gangbare_ambitie(rit.gebiedstype)),
        ("Werkwijze", "rijdend (opnemen, daarna beoordeeld)" if rijdend
         else "lopend (beeld voor beeld)"),
        ("Inspecteur", rit.inspecteur_naam),
        ("Gestart", _datum(rit.gestart_op)),
        ("Afgerond", _datum(rit.afgerond_op) if rit.afgerond_op else "nog bezig"),
        ("Beelden", f"{rit.frames or 0} beoordeeld"
         + (f", {rit.frames_onbruikbaar} niet bruikbaar" if rit.frames_onbruikbaar else "")),
    ])

    bevestigd = sum(1 for w in schades if w.bevestigd)
    ernstig = sum(1 for w in schades if w.crow_ernst == "E")
    meldingen = sum(1 for w in schades if w.melding_id)
    pdf.kengetallen([
        (rit.beeldkwaliteit or "-", "beeldkwaliteit"),
        (len(schades), "schades"),
        (ernstig, "ernstig"),
        (meldingen, "als melding"),
    ], kleuren=[None, INKT, FOUT if ernstig else INKT, None])
    wacht = sum(1 for w in schades if not w.bevestigd)
    if wacht:
        pdf.tekst(f"{bevestigd} van de {len(schades)} schades zijn door een mens bevestigd; "
                  f"{wacht} zijn alleen door de herkenning gevonden.",
                  grootte=8.5, kleur=GRIJS)
        pdf.ln(2)

    # ── Overzicht ────────────────────────────────────────────────────
    pdf.sectie("Overzicht schades")
    if schades:
        pdf.tabel(
            [Kolom("Nr", soort="heel", breedte=9), Kolom("Schade", breedte=46),
             Kolom("Ernst", breedte=14), Kolom("Klasse", breedte=15),
             Kolom("Plek", breedte=52), Kolom("Status", breedte=30),
             Kolom("Melding", breedte=16)],
            [[i, _naam(w), w.crow_ernst or "", cw.klasse_indicatie(w.crow_ernst, w.crow_omvang) or "",
              _plek(w), _status(w, telt_mee(w)), "ja" if w.melding_id else ""]
             for i, w in enumerate(schades, 1)])
    else:
        pdf.tekst("Geen schades gevonden in deze ronde.", grootte=9.5, kleur=GRIJS)

    # ── Per schade met foto ──────────────────────────────────────────
    if schades:
        # Een eigen pagina: zo blijft de kop niet los onderaan het overzicht.
        pdf.add_page()
        pdf.sectie("Schades met beeld")
        for i, w in enumerate(schades, 1):
            foto = beeld_met_vak(lees_foto(w.photo_url), _kader(w),
                                 f"{_naam(w)}{' · ' + w.crow_ernst if w.crow_ernst else ''}")
            _blok(pdf, i, w, telt_mee(w), foto)

    # ── Overige waarnemingen ─────────────────────────────────────────
    if overig:
        pdf.sectie("Overige waarnemingen")
        for i, w in enumerate(overig, 1):
            foto = beeld_met_vak(lees_foto(w.photo_url), None) if w.photo_url else None
            _blok(pdf, i, w, telt_mee(w), foto)

    # ── Afgewezen ────────────────────────────────────────────────────
    if afgewezen:
        pdf.sectie("Afgewezen (telt niet mee)")
        pdf.tabel(
            [Kolom("Nr", soort="heel", breedte=9), Kolom("Gemeld als", breedte=52),
             Kolom("Reden", breedte=50), Kolom("Plek", breedte=71)],
            [[i, _naam(w), schouw_leren.AFWIJS_REDENEN.get(w.afwijs_reden or "", w.afwijs_reden or ""),
              _plek(w)]
             for i, w in enumerate(afgewezen, 1)])

    return pdf.uitvoer()
