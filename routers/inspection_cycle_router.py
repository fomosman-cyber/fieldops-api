"""Inspection-cycle endpoints — compliance-tracking voor alle asset-types.

Endpoints:
  GET  /api/inspection-cycle/asset/{asset_id}        Cyclus-status voor 1 asset
  POST /api/inspection-cycle/recompute/{asset_id}    Handmatig hercomputeren
  GET  /api/inspection-cycle/overdue                 Lijst verlopen inspecties
  GET  /api/inspection-cycle/upcoming                Lijst komende inspecties
  GET  /api/inspection-cycle/compliance-report       Samenvatting per asset-type
                                                     — voor Rekenkamer / directie

Compliance-statussen:
  compliant   : volgende > 30 dagen weg
  due-soon    : volgende binnen 30 dagen
  overdue     : volgende in het verleden
  unscheduled : geen volgende-datum bekend (asset nog niet geïnspecteerd)

Multi-tenant: alle queries gefilterd op organization_id.
RBAC: alle authenticated users mogen lezen. Alleen admin/manager mogen
manueel hercomputeren.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session

from database import get_db
from models import User, Asset
from auth import get_current_user
from audit import log_action

import inspection_cycle as cycle

router = APIRouter(prefix="/api/inspection-cycle", tags=["Inspection-cycle"])


# ─────────────────────────────────────────────────────────────────────────────
# Serialisatie
# ─────────────────────────────────────────────────────────────────────────────

def _asset_cycle_dict(a: Asset, *, today: Optional[datetime] = None) -> dict:
    """Serialiseer Asset-cyclus voor UI/API."""
    now = today or datetime.now(timezone.utc)
    days = cycle.days_until_due(a.next_inspection_due, now)
    status = cycle.compliance_status(a.next_inspection_due, now)
    return {
        "asset_id": a.id,
        "asset_code": a.code,
        "asset_name": a.name,
        "asset_type": a.asset_type,
        "norm_reference": cycle.norm_reference(a.asset_type),
        "condition_score": a.condition_score,
        "last_inspection_at": (a.last_inspection_at.isoformat()
                                if a.last_inspection_at else None),
        "last_inspection_id": a.last_inspection_id,
        "inspection_cycle_months": a.inspection_cycle_months,
        "next_inspection_due": (a.next_inspection_due.isoformat()
                                 if a.next_inspection_due else None),
        "days_until_due": days,
        "compliance_status": status,
        "is_overdue": status == "overdue",
        "project_id": a.project_id,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/asset/{asset_id}")
def get_asset_cycle(
    asset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cyclus-status voor één asset."""
    a = db.query(Asset).filter(
        Asset.id == asset_id,
        Asset.organization_id == current_user.organization_id,
        Asset.archived_at.is_(None),
    ).first()
    if not a:
        raise HTTPException(status_code=404, detail="Asset niet gevonden")
    return _asset_cycle_dict(a)


@router.post("/recompute/{asset_id}")
def recompute_asset_cycle(
    asset_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Hercomputeer cyclus op basis van huidige condition_score + last_inspection_at.

    Nuttig na handmatige aanpassing van een score, of om assets met
    leeg `next_inspection_due` te vullen na een import.
    """
    if current_user.role not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Alleen admin of manager")
    a = db.query(Asset).filter(
        Asset.id == asset_id,
        Asset.organization_id == current_user.organization_id,
    ).first()
    if not a:
        raise HTTPException(status_code=404, detail="Asset niet gevonden")
    if not a.last_inspection_at:
        raise HTTPException(
            status_code=400,
            detail="Asset is nog niet geïnspecteerd — geen basisdatum voor cyclus",
        )

    months = cycle.cycle_months_for(a.asset_type, a.condition_score)
    if not months:
        raise HTTPException(
            status_code=400,
            detail=f"Geen cyclus-regel voor asset_type '{a.asset_type}'",
        )

    a.inspection_cycle_months = months
    a.next_inspection_due = cycle.next_due_date(a.last_inspection_at, months)
    db.commit()
    db.refresh(a)
    log_action(db, request, current_user,
               action="asset.cycle_recompute",
               entity_type="asset", entity_id=a.id,
               extra={"cycle_months": months,
                      "next_due": a.next_inspection_due.isoformat() if a.next_inspection_due else None})
    return _asset_cycle_dict(a)


@router.get("/overdue")
def list_overdue(
    project_id: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    limit: int = Query(200, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lijst assets met verlopen inspectie-deadline.

    Voor het Rekenkamer-rapport / dashboard-tile "Verlopen inspecties".
    """
    now = datetime.now(timezone.utc)
    q = db.query(Asset).filter(
        Asset.organization_id == current_user.organization_id,
        Asset.archived_at.is_(None),
        Asset.next_inspection_due.isnot(None),
        Asset.next_inspection_due < now,
    )
    if project_id:
        q = q.filter(Asset.project_id == project_id)
    if asset_type:
        q = q.filter(Asset.asset_type == asset_type)
    q = q.order_by(Asset.next_inspection_due.asc()).limit(limit)
    items = [_asset_cycle_dict(a, today=now) for a in q.all()]
    return {"count": len(items), "items": items}


@router.get("/upcoming")
def list_upcoming(
    days: int = Query(30, ge=1, le=365),
    project_id: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    limit: int = Query(200, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lijst assets met inspectie binnen `days` dagen (default 30)."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days)
    q = db.query(Asset).filter(
        Asset.organization_id == current_user.organization_id,
        Asset.archived_at.is_(None),
        Asset.next_inspection_due.isnot(None),
        Asset.next_inspection_due >= now,
        Asset.next_inspection_due <= horizon,
    )
    if project_id:
        q = q.filter(Asset.project_id == project_id)
    if asset_type:
        q = q.filter(Asset.asset_type == asset_type)
    q = q.order_by(Asset.next_inspection_due.asc()).limit(limit)
    items = [_asset_cycle_dict(a, today=now) for a in q.all()]
    return {"count": len(items), "items": items, "horizon_days": days}


@router.get("/compliance-report")
def compliance_report(
    project_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Compliance-samenvatting per asset-type — voor Rekenkamer / directie.

    Geeft per asset-type:
      - aantal assets
      - aantal compliant / due-soon / overdue / unscheduled
      - percentage compliant
      - norm-referentie
    """
    now = datetime.now(timezone.utc)
    soon = now + timedelta(days=30)

    q = db.query(Asset).filter(
        Asset.organization_id == current_user.organization_id,
        Asset.archived_at.is_(None),
    )
    if project_id:
        q = q.filter(Asset.project_id == project_id)
    assets = q.all()

    # Groepeer per asset_type
    by_type: dict = {}
    for a in assets:
        t = a.asset_type or "overig"
        b = by_type.setdefault(t, {
            "asset_type": t,
            "norm_reference": cycle.norm_reference(t),
            "total": 0, "compliant": 0, "due_soon": 0,
            "overdue": 0, "unscheduled": 0,
        })
        b["total"] += 1
        status = cycle.compliance_status(a.next_inspection_due, now)
        if status == "compliant":
            b["compliant"] += 1
        elif status == "due-soon":
            b["due_soon"] += 1
        elif status == "overdue":
            b["overdue"] += 1
        else:
            b["unscheduled"] += 1

    # Berekend percentage + sorteer
    out = []
    for b in by_type.values():
        b["compliance_pct"] = round(
            100 * b["compliant"] / b["total"], 1
        ) if b["total"] else 0.0
        out.append(b)
    out.sort(key=lambda x: (-x["overdue"], -x["total"]))

    # Aggregaten
    total = sum(b["total"] for b in out)
    compliant = sum(b["compliant"] for b in out)
    overdue = sum(b["overdue"] for b in out)
    due_soon = sum(b["due_soon"] for b in out)
    unscheduled = sum(b["unscheduled"] for b in out)

    return {
        "summary": {
            "total_assets": total,
            "compliant": compliant,
            "due_soon": due_soon,
            "overdue": overdue,
            "unscheduled": unscheduled,
            "compliance_pct": round(100 * compliant / total, 1) if total else 0.0,
        },
        "by_type": out,
        "generated_at": now.isoformat(),
        "cycle_rules_version": cycle.CYCLE_RULES_VERSION,
    }
