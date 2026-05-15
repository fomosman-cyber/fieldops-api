"""Meerjaren Onderhoudsplan (MJOP) — export voor directie + Rekenkamer.

Endpoints:
  GET /api/mjop/preview                  Preview MJOP-data (JSON)
  GET /api/mjop/export.csv               Excel-import vriendelijk CSV
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
from models import User, Asset
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
