"""Meerjaren Onderhoudsplan (MJOP) — export voor directie + Rekenkamer.

Endpoints:
  GET /api/mjop/preview                  Preview MJOP-data (JSON)
  GET /api/mjop/export.csv               Excel-import vriendelijk CSV
  GET /api/mjop/export.pdf               CROW + NEN conform PDF-rapport
  GET /api/mjop/summary                  Aggregaten per jaar + asset-type

Query-params:
  years        Aantal jaren in horizon (default 10, max 25)
  project_id   Filter op project (optioneel)
  asset_type   Filter op asset-type (optioneel)
  include_score_2  Ook score 2 (preventief) meenemen — default False

Bron: NEN 2767-2 + CROW 134 + CROW 145 + RAW-indexen + GWW-kostengids.
Multi-tenant: alle queries gefilterd op organization_id.
RBAC: alle authenticated users mogen lezen (read-only export).

LET OP: Indicatieve kostenranges. Voor exacte ramingen heb je RAW-besteks-
documenten per project nodig. MJOP is een meerjaren-overzicht voor
begrotings-onderbouwing.
"""
from __future__ import annotations
import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from models import User, Asset, Project, Organization
from auth import get_current_user

import mjop_kosten as mjop
import inspection_cycle as cycle

router = APIRouter(prefix="/api/mjop", tags=["MJOP"])


# ─────────────────────────────────────────────────────────────────────────────
# Core builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_mjop_rows(db: Session, *, organization_id: str,
                     years: int = 10,
                     project_id: Optional[str] = None,
                     asset_type: Optional[str] = None,
                     include_score_2: bool = False) -> list[dict]:
    """Maak MJOP-regels voor alle relevante assets.

    Logica:
      1. Pak alle assets met condition_score in deze org
      2. Filter op actionable score (3+, of 2+ als include_score_2)
      3. Bepaal jaar van uitvoering op basis van next_inspection_due of
         expected_lifespan_years
      4. Bereken kosten via mjop_kosten
      5. Sorteer op jaar → asset_type → asset.code
    """
    q = db.query(Asset).filter(
        Asset.organization_id == organization_id,
        Asset.archived_at.is_(None),
        Asset.condition_score.isnot(None),
    )
    if project_id:
        q = q.filter(Asset.project_id == project_id)
    if asset_type:
        q = q.filter(Asset.asset_type == asset_type)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    horizon_end = now + timedelta(days=365 * years)

    rows = []
    threshold = 2 if include_score_2 else 3
    for a in q.all():
        if a.condition_score is None or a.condition_score < threshold:
            continue

        maatregel = mjop.get_maatregel(a.asset_type, a.condition_score)
        if not maatregel:
            continue

        # Bepaal uitvoering-jaar — eerst next_inspection_due, anders projectie
        # uit expected_lifespan_years, anders direct
        if a.next_inspection_due:
            due = a.next_inspection_due
            if due.tzinfo:
                due = due.replace(tzinfo=None)
        else:
            # Fallback: direct uitvoeren in lopende jaar voor score 4+
            due = now if a.condition_score >= 4 else now + timedelta(days=365)

        if due > horizon_end:
            continue  # buiten horizon

        # Bepaal multiplier voor unit-based kosten (per m, per m2)
        multiplier = 1.0
        unit = maatregel.get("unit", "per object")
        if unit == "per m" and a.length_m:
            multiplier = float(a.length_m)
        elif unit == "per m2":
            # We hebben geen aparte oppervlakte-kolom — fallback op length_m × 5m
            # (rijbaan-breedte aanname) als geen specifieke breedte beschikbaar
            multiplier = float(a.length_m or 0) * 5

        total = mjop.estimate_total(maatregel, multiplier=multiplier if multiplier else 1.0)

        rows.append({
            "year": due.year,
            "month": due.month,
            "asset_id": a.id,
            "asset_code": a.code,
            "asset_name": a.name,
            "asset_type": a.asset_type,
            "project_id": a.project_id,
            "condition_score": a.condition_score,
            "norm_reference": cycle.norm_reference(a.asset_type),
            "maatregel": maatregel["maatregel"],
            "unit": unit,
            "multiplier": multiplier,
            "min_eur": maatregel["min_eur"],
            "max_eur": maatregel["max_eur"],
            "min_total": total["min_total"],
            "max_total": total["max_total"],
            "due_date": due.date().isoformat(),
        })

    # Sorteer op (jaar, asset_type, code)
    rows.sort(key=lambda r: (r["year"], r["month"], r["asset_type"], r["asset_code"]))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/preview")
def preview_mjop(
    years: int = Query(10, ge=1, le=25),
    project_id: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    include_score_2: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Preview MJOP-data als JSON (max 200 regels)."""
    rows = _build_mjop_rows(
        db,
        organization_id=current_user.organization_id,
        years=years, project_id=project_id, asset_type=asset_type,
        include_score_2=include_score_2,
    )
    return {
        "count": len(rows),
        "items": rows[:500],   # cap voor preview
        "horizon_years": years,
        "kosten_version": mjop.KOSTEN_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Indicatieve kostenranges — geen RAW-bestek. Voor onderbouwing aanbesteding gebruik je een eigen kosten-raming per project.",
    }


@router.get("/summary")
def mjop_summary(
    years: int = Query(10, ge=1, le=25),
    project_id: Optional[str] = Query(None),
    include_score_2: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aggregaten per jaar + per asset-type — voor directie-grafiek.

    Returns:
      summary_by_year[year]            = {min_total, max_total, count}
      summary_by_type[asset_type]      = {min_total, max_total, count}
      grand_total                      = {min, max}
    """
    rows = _build_mjop_rows(
        db,
        organization_id=current_user.organization_id,
        years=years, project_id=project_id,
        include_score_2=include_score_2,
    )

    by_year: dict = {}
    by_type: dict = {}
    grand_min = 0
    grand_max = 0

    for r in rows:
        y = r["year"]
        t = r["asset_type"]
        by_year.setdefault(y, {"year": y, "min_total": 0, "max_total": 0,
                                "count": 0, "by_type": {}})
        by_type.setdefault(t, {"asset_type": t, "min_total": 0, "max_total": 0,
                                "count": 0, "norm_reference": r["norm_reference"]})

        by_year[y]["min_total"] += r["min_total"]
        by_year[y]["max_total"] += r["max_total"]
        by_year[y]["count"] += 1
        by_year[y]["by_type"].setdefault(t, 0)
        by_year[y]["by_type"][t] += r["min_total"]

        by_type[t]["min_total"] += r["min_total"]
        by_type[t]["max_total"] += r["max_total"]
        by_type[t]["count"] += 1

        grand_min += r["min_total"]
        grand_max += r["max_total"]

    return {
        "horizon_years": years,
        "by_year": sorted(by_year.values(), key=lambda x: x["year"]),
        "by_type": sorted(by_type.values(), key=lambda x: -x["max_total"]),
        "grand_total": {"min": grand_min, "max": grand_max},
        "total_assets": len(rows),
        "kosten_version": mjop.KOSTEN_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/export.csv")
def export_mjop_csv(
    years: int = Query(10, ge=1, le=25),
    project_id: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    include_score_2: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Excel-import vriendelijk CSV met alle MJOP-regels.

    Gebruikt `;` als delimiter (NL Excel-default) en BOM voor UTF-8.
    """
    rows = _build_mjop_rows(
        db,
        organization_id=current_user.organization_id,
        years=years, project_id=project_id, asset_type=asset_type,
        include_score_2=include_score_2,
    )

    buf = io.StringIO()
    buf.write("﻿")  # BOM voor Excel UTF-8 herkenning
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "Jaar", "Maand", "Asset code", "Asset naam", "Type", "Norm",
        "Conditie", "Maatregel", "Eenheid", "Hoeveelheid",
        "Min € per eenheid", "Max € per eenheid",
        "Min € totaal", "Max € totaal", "Geplande datum",
    ])
    for r in rows:
        writer.writerow([
            r["year"], r["month"], r["asset_code"], r["asset_name"] or "",
            r["asset_type"], r["norm_reference"],
            r["condition_score"], r["maatregel"],
            r["unit"], r["multiplier"],
            r["min_eur"], r["max_eur"],
            r["min_total"], r["max_total"], r["due_date"],
        ])
    # Voettekst — meta
    writer.writerow([])
    writer.writerow([f"MJOP gegenereerd op {datetime.now(timezone.utc).date().isoformat()}"])
    writer.writerow([f"Versie kosten-katalogus: {mjop.KOSTEN_VERSION}"])
    writer.writerow(["Bronnen: NEN 2767-2 + CROW 134 + CROW 145 + GWW-kostengids 2024"])
    writer.writerow(["LET OP: indicatieve kostenranges, geen RAW-bestek"])

    buf.seek(0)
    today = datetime.now(timezone.utc).date().isoformat()
    filename = f"mjop-{today}.csv"
    return StreamingResponse(
        iter([buf.read()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# PDF export — CROW + NEN conform rapport voor directie / Rekenkamer
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_eur(n: float) -> str:
    """NL-stijl euro-formatting zonder symbool (fpdf2 default font is latin-1)."""
    return f"{n:,.0f}".replace(",", ".")


@router.get("/export.pdf")
def export_mjop_pdf(
    years: int = Query(10, ge=1, le=25),
    project_id: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    include_score_2: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """PDF-rapport (A4) — CROW 134 + 145 + NEN 2767-2 conform.

    Bevat cover, projectinfo, totalen-per-jaar tabel, detail-regels, bronnen +
    disclaimer. Voor directie- en begrotings-doeleinden.
    """
    try:
        from fpdf import FPDF
    except ImportError:
        return StreamingResponse(
            iter([b"PDF-generator niet geinstalleerd: pip install fpdf2"]),
            status_code=500, media_type="text/plain",
        )

    rows = _build_mjop_rows(
        db, organization_id=current_user.organization_id,
        years=years, project_id=project_id, asset_type=asset_type,
        include_score_2=include_score_2,
    )

    project_name = None
    if project_id:
        p = db.query(Project).filter(
            Project.id == project_id,
            Project.organization_id == current_user.organization_id,
        ).first()
        if p:
            project_name = p.name
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    org_name = org.name if org else "—"

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # ── Cover ──
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 12, "MEERJAREN ONDERHOUDSPLAN", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 14)
    pdf.cell(0, 8, "(MJOP)", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(12)

    def _info_row(label: str, value: str) -> None:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(50, 7, label)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 7, value, new_x="LMARGIN", new_y="NEXT")

    _info_row("Organisatie:", org_name)
    _info_row("Project:", project_name or "Alle projecten (organisatie-breed)")
    _info_row("Horizon:", f"{years} jaar")
    _info_row("Inclusief score 2:", "ja (preventief)" if include_score_2 else "nee (alleen actionable)")
    _info_row("Gegenereerd:", datetime.now(timezone.utc).strftime("%d-%m-%Y %H:%M UTC"))
    _info_row("Kosten-versie:", mjop.KOSTEN_VERSION)

    pdf.ln(10)

    # ── Samenvatting ──
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "Samenvatting", new_x="LMARGIN", new_y="NEXT", border="B")
    pdf.ln(3)

    total_min = sum(r["min_total"] for r in rows)
    total_max = sum(r["max_total"] for r in rows)
    assets_count = len({r["asset_id"] for r in rows})

    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"Aantal regels in plan: {len(rows)}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Aantal unieke assets met maatregel: {assets_count}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, f"Totaal indicatieve kosten: EUR {_fmt_eur(total_min)}  -  EUR {_fmt_eur(total_max)}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # ── Per-jaar tabel ──
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "Totalen per jaar", new_x="LMARGIN", new_y="NEXT", border="B")
    pdf.ln(2)

    by_year: dict[int, dict] = {}
    for r in rows:
        d = by_year.setdefault(r["year"], {"count": 0, "min": 0.0, "max": 0.0})
        d["count"] += 1
        d["min"] += r["min_total"]
        d["max"] += r["max_total"]

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(25, 7, "Jaar", border=1, fill=True)
    pdf.cell(30, 7, "Regels", border=1, fill=True, align="R")
    pdf.cell(60, 7, "Min totaal (EUR)", border=1, fill=True, align="R")
    pdf.cell(60, 7, "Max totaal (EUR)", border=1, fill=True, align="R")
    pdf.ln()

    pdf.set_font("Helvetica", "", 10)
    if by_year:
        for y in sorted(by_year):
            d = by_year[y]
            pdf.cell(25, 6, str(y), border=1)
            pdf.cell(30, 6, str(d["count"]), border=1, align="R")
            pdf.cell(60, 6, _fmt_eur(d["min"]), border=1, align="R")
            pdf.cell(60, 6, _fmt_eur(d["max"]), border=1, align="R")
            pdf.ln()
    else:
        pdf.cell(0, 6, "Geen MJOP-regels gevonden voor deze filter-combinatie.",
                 new_x="LMARGIN", new_y="NEXT")

    # ── Detail-regels (nieuwe pagina) ──
    if rows:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 8, "Detail-regels (per asset, per jaar)",
                 new_x="LMARGIN", new_y="NEXT", border="B")
        pdf.ln(3)

        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(230, 230, 230)
        pdf.cell(15, 6, "Jaar", border=1, fill=True)
        pdf.cell(28, 6, "Asset-code", border=1, fill=True)
        pdf.cell(28, 6, "Type", border=1, fill=True)
        pdf.cell(13, 6, "Cond.", border=1, fill=True, align="C")
        pdf.cell(58, 6, "Maatregel", border=1, fill=True)
        pdf.cell(25, 6, "Min EUR", border=1, fill=True, align="R")
        pdf.cell(25, 6, "Max EUR", border=1, fill=True, align="R")
        pdf.ln()

        pdf.set_font("Helvetica", "", 7)
        for r in rows:
            pdf.cell(15, 5, str(r["year"]), border=1)
            pdf.cell(28, 5, str(r["asset_code"] or "")[:16], border=1)
            pdf.cell(28, 5, str(r["asset_type"] or "")[:16], border=1)
            pdf.cell(13, 5, str(r["condition_score"]), border=1, align="C")
            pdf.cell(58, 5, str(r["maatregel"])[:38], border=1)
            pdf.cell(25, 5, _fmt_eur(r["min_total"]), border=1, align="R")
            pdf.cell(25, 5, _fmt_eur(r["max_total"]), border=1, align="R")
            pdf.ln()

    # ── Disclaimers + bronnen ──
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(0, 4,
        "Bronnen: NEN 2767-2 (conditiemeting infrastructuur), CROW 134 "
        "(inspectie van bruggen en viaducten), CROW 145 (wegmarkering), "
        "GWW-kostengids 2024.\n\n"
        "Maatregel-bibliotheek versie: " + str(mjop.KOSTEN_VERSION) + "\n\n"
        "LET OP: Indicatieve kostenranges. Voor onderbouwing van aanbestedingen "
        "is een eigen RAW-besteksraming per project vereist. Dit MJOP-rapport "
        "is bedoeld voor begrotings- en directie-doeleinden, niet als "
        "contractdocument."
    )

    pdf_bytes = bytes(pdf.output())
    today = datetime.now(timezone.utc).date().isoformat()
    slug = (project_name or "all").replace(" ", "-").lower()
    # Houd filename veilig: alleen alfanum + dash
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in slug)[:30].strip("-") or "all"
    filename = f"mjop-{slug}-{today}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
