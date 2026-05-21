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
from models import User, Asset, Project, Organization, Melding
from auth import get_current_user

import mjop_kosten as mjop
import inspection_cycle as cycle
from collections import Counter

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

    # ─── Extra data voor super-rapportage ───
    # Asset-overzicht binnen organisatie (en project-filter indien gezet)
    asset_q = db.query(Asset).filter(
        Asset.organization_id == current_user.organization_id,
        Asset.archived_at.is_(None),
    )
    if project_id:
        asset_q = asset_q.filter(Asset.project_id == project_id)
    if asset_type:
        asset_q = asset_q.filter(Asset.asset_type == asset_type)
    all_assets = asset_q.all()
    asset_type_counts = Counter(a.asset_type for a in all_assets if a.asset_type)
    cond_counts = Counter(a.condition_score for a in all_assets if a.condition_score is not None)
    assets_zonder_score = sum(1 for a in all_assets if a.condition_score is None)

    # Open meldingen voor context
    meld_q = db.query(Melding).filter(
        Melding.organization_id == current_user.organization_id,
        Melding.status.in_(("open", "in_behandeling", "in_uitvoering", "nieuw")),
    )
    if project_id:
        meld_q = meld_q.filter(Melding.project_id == project_id)
    open_meldingen = meld_q.all()
    meld_per_type = Counter(m.category for m in open_meldingen if m.category)
    meld_per_asset = Counter(m.asset_id for m in open_meldingen if m.asset_id)

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=18)

    def _section_title(title: str) -> None:
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(2, 132, 199)
        pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT", border="B")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(3)

    def _info_row(label: str, value: str) -> None:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(55, 7, label)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 7, value, new_x="LMARGIN", new_y="NEXT")

    # ═════════════════════════════════════════════════════════════════
    # PAGINA 1 — COVER + KERNCIJFERS
    # ═════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.set_fill_color(2, 132, 199)
    pdf.rect(0, 0, 210, 50, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_xy(18, 16)
    pdf.cell(0, 10, "MEERJAREN ONDERHOUDSPLAN", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_x(18)
    pdf.cell(0, 7, f"{org_name} - " + (project_name or "Organisatie-breed"))
    pdf.set_text_color(0, 0, 0)
    pdf.ln(40)

    _info_row("Organisatie:", org_name)
    _info_row("Project:", project_name or "Alle projecten (organisatie-breed)")
    _info_row("Horizon:", f"{years} jaar")
    _info_row("Inclusief score 2:", "ja (preventief)" if include_score_2 else "nee (alleen actionable)")
    _info_row("Gegenereerd:", datetime.now(timezone.utc).strftime("%d-%m-%Y %H:%M UTC"))
    _info_row("Kosten-versie:", mjop.KOSTEN_VERSION)
    pdf.ln(8)

    # Kerncijfers-grid (3 grote getallen)
    total_max = sum(r["max_total"] for r in rows)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Kerncijfers", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    kpi_w = 58
    kpi_h = 26
    kpi_gap = 4
    kpi_y = pdf.get_y()
    kpis = [
        (f"{len(all_assets)}", "assets in scope", 2, 132, 199),
        (f"{len(rows)}", "MJOP-regels", 22, 163, 74),
        (f"EUR {_fmt_eur(total_max)}", "max kosten", 234, 88, 12),
    ]
    for i, (val, lbl, r_c, g_c, b_c) in enumerate(kpis):
        x = 18 + i * (kpi_w + kpi_gap)
        pdf.set_fill_color(r_c, g_c, b_c)
        pdf.rect(x, kpi_y, kpi_w, kpi_h, "F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_xy(x, kpi_y + 4)
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(kpi_w, 8, val, align="C")
        pdf.set_xy(x, kpi_y + 16)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(kpi_w, 5, lbl, align="C")
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(kpi_y + kpi_h + 6)

    # ═════════════════════════════════════════════════════════════════
    # PAGINA 2 — METHODOLOGIE (FORMULE + NORMEN)
    # ═════════════════════════════════════════════════════════════════
    pdf.add_page()
    _section_title("1. Methodologie - hoe is de begroting opgebouwd?")

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Bron-normen", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        "- NEN 2767-2 (2017): Conditiemeting infrastructuur. Bepaalt de \n"
        "  6-puntsschaal voor de assets (1 = uitstekend, 6 = zeer slecht).\n"
        "- CROW 134: Inspectie van vaste/beweegbare bruggen + viaducten.\n"
        "- CROW 145: Inspectie wegmarkering (retroreflectie-meting).\n"
        "- NEN 3140: Periodieke inspectie elektrische installaties (verlichting).\n"
        "- NEN-EN 1176: Veiligheid speeltoestellen (Warenwetbesluit).\n"
        "- NEN 3399: Visuele inspectie van vrijvervalriool (camera).\n"
        "- VTA (Mattheck): Visual Tree Assessment voor bomen.\n"
        "- GWW-kostengids 2024: prijsbasis per maatregel."
    )
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Berekening per regel", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        "Voor elk asset met een conditie-score wordt een MJOP-regel berekend:\n\n"
        "    eindbedrag = eenheidsprijs * hoeveelheid * cyclus-factor\n\n"
        "Waarbij:\n"
        "  - eenheidsprijs: GWW-kostengids basis per maatregel-categorie\n"
        "    (per m, per m2 of per stuk - afhankelijk van asset-type)\n"
        "  - hoeveelheid: length_m van asset, of standaard breedte voor wegvakken\n"
        "  - cyclus-factor: hoe vaak in de horizon (years) terugkomt op basis van\n"
        "    de norm-conforme inspectie-cyclus per asset-type:\n"
        "       - Brug/viaduct: 6 jaar (CROW 134)\n"
        "       - Speeltoestel: 1 jaar (NEN-EN 1176, wettelijk)\n"
        "       - Verlichting: 5 jaar (NEN 3140)\n"
        "       - Wegmarkering: 2 jaar (CROW 145)\n"
        "       - Boom: 1-3 jaar afhankelijk van VTA-risicoklasse\n"
        "\n"
        "De maatregel zelf wordt afgeleid uit de conditie-score:\n"
        "  - score 3: regulier onderhoud (kleinere ingreep)\n"
        "  - score 4: groot onderhoud\n"
        "  - score 5: vervanging (capex)\n"
        "  - score 6: vervanging (acuut)\n"
        + ("  - score 2 (preventief): meegenomen vanwege include_score_2=true\n"
           if include_score_2 else "")
    )
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Min/max-bandbreedte", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        "Per maatregel publiceert de GWW-kostengids 2024 een ondergrens en bovengrens "
        "(bijvoorbeeld EUR 5-10/m voor pleksgewijze reparatie). Wij rapporteren beide:\n"
        "  - min_total = ondergrens * hoeveelheid * cyclus\n"
        "  - max_total = bovengrens * hoeveelheid * cyclus\n\n"
        "Het verschil tussen min en max weerspiegelt onzekerheid in marktprijs, "
        "uitvoeringsomstandigheden en bijkomende posten. Voor aanbesteding-onderbouwing "
        "is een eigen RAW-besteksraming per project alsnog vereist."
    )

    # ═════════════════════════════════════════════════════════════════
    # PAGINA 3 — ASSET-OVERZICHT
    # ═════════════════════════════════════════════════════════════════
    pdf.add_page()
    _section_title("2. Asset-overzicht")

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Totaal aantal assets in scope: {len(all_assets)}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Met conditie-score: {len(all_assets) - assets_zonder_score}    "
                   f"Zonder score (geen MJOP-regel): {assets_zonder_score}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Verdeling per asset-type
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Verdeling per asset-type", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(70, 6, "Type", border=1, fill=True)
    pdf.cell(30, 6, "Aantal", border=1, fill=True, align="R")
    pdf.cell(40, 6, "% van totaal", border=1, fill=True, align="R")
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    total_typed = sum(asset_type_counts.values()) or 1
    for atype, cnt in asset_type_counts.most_common(15):
        pct = (cnt / total_typed) * 100
        pdf.cell(70, 5, str(atype)[:32], border=1)
        pdf.cell(30, 5, str(cnt), border=1, align="R")
        pdf.cell(40, 5, f"{pct:.1f}%", border=1, align="R")
        pdf.ln()
    pdf.ln(4)

    # Conditie-verdeling
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Conditie-verdeling (NEN 2767-2)", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    cond_labels = {1: "1 - Uitstekend", 2: "2 - Goed", 3: "3 - Redelijk",
                   4: "4 - Matig", 5: "5 - Slecht", 6: "6 - Zeer slecht"}
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(70, 6, "Conditie-score", border=1, fill=True)
    pdf.cell(30, 6, "Aantal", border=1, fill=True, align="R")
    pdf.cell(40, 6, "Maatregel", border=1, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    maatregel_per_score = {
        1: "Geen actie", 2: "Preventief", 3: "Klein onderhoud",
        4: "Groot onderhoud", 5: "Vervanging", 6: "Acute vervanging",
    }
    for score in sorted(cond_counts):
        pdf.cell(70, 5, cond_labels.get(score, f"Score {score}"), border=1)
        pdf.cell(30, 5, str(cond_counts[score]), border=1, align="R")
        pdf.cell(40, 5, maatregel_per_score.get(score, "-"), border=1)
        pdf.ln()
    if assets_zonder_score:
        pdf.cell(70, 5, "Geen conditie-score", border=1)
        pdf.cell(30, 5, str(assets_zonder_score), border=1, align="R")
        pdf.cell(40, 5, "Vraagt inspectie", border=1)
        pdf.ln()

    # ═════════════════════════════════════════════════════════════════
    # PAGINA 4 — MELDINGEN-CONTEXT
    # ═════════════════════════════════════════════════════════════════
    pdf.add_page()
    _section_title("3. Meldingen-context")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        "Open meldingen geven aanvullend signaal: assets met veel meldingen "
        "degraderen vaak sneller dan de cyclus-prognose. Bij voortdurende meldingen "
        "op een asset is het verstandig om de eerstvolgende inspectie te vervroegen."
    )
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, f"Totaal aantal open/actieve meldingen: {len(open_meldingen)}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Meldingen per categorie
    if meld_per_type:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, "Top categorieen met openstaande meldingen", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(230, 230, 230)
        pdf.cell(90, 6, "Categorie", border=1, fill=True)
        pdf.cell(30, 6, "Aantal", border=1, fill=True, align="R")
        pdf.ln()
        pdf.set_font("Helvetica", "", 9)
        for cat, cnt in meld_per_type.most_common(10):
            pdf.cell(90, 5, str(cat)[:42], border=1)
            pdf.cell(30, 5, str(cnt), border=1, align="R")
            pdf.ln()
        pdf.ln(4)

    # Top-assets met meldingen
    if meld_per_asset:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, "Top assets met meeste open meldingen", new_x="LMARGIN", new_y="NEXT")
        asset_lookup = {a.id: a for a in all_assets}
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(230, 230, 230)
        pdf.cell(50, 6, "Asset-code", border=1, fill=True)
        pdf.cell(40, 6, "Type", border=1, fill=True)
        pdf.cell(40, 6, "Aantal meld.", border=1, fill=True, align="R")
        pdf.cell(30, 6, "Conditie", border=1, fill=True, align="C")
        pdf.ln()
        pdf.set_font("Helvetica", "", 9)
        for aid, cnt in meld_per_asset.most_common(15):
            a = asset_lookup.get(aid)
            if not a:
                continue
            pdf.cell(50, 5, str(a.code or "-")[:22], border=1)
            pdf.cell(40, 5, str(a.asset_type or "-")[:18], border=1)
            pdf.cell(40, 5, str(cnt), border=1, align="R")
            pdf.cell(30, 5, str(a.condition_score) if a.condition_score else "-",
                     border=1, align="C")
            pdf.ln()

    # ═════════════════════════════════════════════════════════════════
    # PAGINA 5 — KOSTEN PER JAAR (was 'Totalen per jaar')
    # ═════════════════════════════════════════════════════════════════
    pdf.add_page()
    _section_title("4. Kosten per jaar")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Horizon: {years} jaar - alleen actionable scores ({3}+){' incl. preventief (score 2)' if include_score_2 else ''}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

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

    # ═════════════════════════════════════════════════════════════════
    # PAGINA 6 — DETAIL-REGELS (volledige MJOP-tabel)
    # ═════════════════════════════════════════════════════════════════
    if rows:
        pdf.add_page()
        _section_title("5. Volledige MJOP-tabel (per asset, per jaar)")

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

    # ═════════════════════════════════════════════════════════════════
    # PAGINA 7 — VOORBEELD-BEREKENING (concreet uitgewerkt)
    # ═════════════════════════════════════════════════════════════════
    if rows:
        pdf.add_page()
        _section_title("6. Voorbeeld-berekening")
        # Pak een willekeurig representatief rij (eerste hoge-min regel)
        sorted_rows = sorted(rows, key=lambda r: r["max_total"], reverse=True)
        sample = sorted_rows[0] if sorted_rows else rows[0]

        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5,
            "Hieronder de uitgewerkte berekening voor de duurste MJOP-regel in "
            "dit rapport, zodat je kunt zien hoe min/max tot stand komt.\n"
        )
        pdf.ln(2)

        _info_row("Asset-code:", str(sample["asset_code"] or "-"))
        _info_row("Asset-type:", str(sample["asset_type"] or "-"))
        _info_row("Conditie-score:", f"{sample['condition_score']} (NEN 2767-2)")
        _info_row("Norm-bron:", str(sample.get("norm_reference") or "-"))
        _info_row("Maatregel:", str(sample["maatregel"]))
        _info_row("Eenheid:", str(sample["unit"]))
        _info_row("Hoeveelheid:", f"{sample['multiplier']}")
        _info_row("Eenheidsprijs min:", f"EUR {_fmt_eur(sample['min_eur'])}")
        _info_row("Eenheidsprijs max:", f"EUR {_fmt_eur(sample['max_eur'])}")
        _info_row("Geplande datum:", str(sample.get("due_date") or "-"))

        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, "Berekening", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Courier", "", 9)
        pdf.multi_cell(0, 5,
            f"  min_total = {sample['min_eur']} EUR * {sample['multiplier']} = "
            f"EUR {_fmt_eur(sample['min_total'])}\n"
            f"  max_total = {sample['max_eur']} EUR * {sample['multiplier']} = "
            f"EUR {_fmt_eur(sample['max_total'])}\n"
            f"  bandbreedte = EUR {_fmt_eur(sample['max_total'] - sample['min_total'])} "
            f"({((sample['max_total']/max(sample['min_total'],1) - 1) * 100):.0f}% verschil)"
        )

    # ═════════════════════════════════════════════════════════════════
    # SLOT — Bronnen + disclaimer
    # ═════════════════════════════════════════════════════════════════
    pdf.add_page()
    _section_title("7. Bronnen en disclaimers")
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Geraadpleegde normen en richtlijnen", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        "- NEN 2767-2 (2017): Conditiemeting infrastructuur - 6-puntsschaal\n"
        "- NEN 2767-4: Terreinen en buitenruimten (fontein, kunstgrasveld)\n"
        "- CROW 134 (2010): Inspectie vaste/beweegbare bruggen\n"
        "- CROW 145: Inspectie en onderhoud wegmarkering\n"
        "- CROW 146a/b: Visuele inspectie verhardingen (asfalt/elementen)\n"
        "- NEN 3140: Bedrijfsvoering elektrische installaties\n"
        "- NEN-EN 1176: Veiligheid speeltoestellen\n"
        "- NEN 3399: Visuele inspectie vrijvervalriool\n"
        "- VTA (Mattheck): Visual Tree Assessment voor bomen\n"
        "- GWW-kostengids 2024 (CROW): prijsbasis maatregelen\n"
        "- RWS Areaalrapportage Kunstwerken (KW-elementenlijst)\n"
    )
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Disclaimer", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(0, 5,
        "Dit MJOP-rapport bevat indicatieve kostenramingen op basis van "
        "norm-conforme cyclus-prognoses en gepubliceerde eenheidsprijzen. "
        "Het is bedoeld voor begrotings- en directie-doeleinden (BBV-conform "
        "voor gemeenten, Rekenkamer-onderbouwing voor publieke organisaties). "
        "\n\n"
        "Voor onderbouwing van aanbestedingen, RAW-bestek of contractuele "
        "verplichtingen is een aparte project-specifieke kostenraming vereist "
        "die rekening houdt met locatie-specifieke uitvoeringsomstandigheden, "
        "marktprijzen op aanbestedingsdatum en eventuele bijkomende werken.\n"
        "\n"
        f"Maatregel-bibliotheek versie: {mjop.KOSTEN_VERSION}"
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
