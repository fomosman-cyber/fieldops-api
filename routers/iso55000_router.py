"""ISO 55000 / NEN-ISO 55001 asset management compliance dashboard.

Voor grote infra-organisaties (provincies, RWS-regio's, grotere gemeenten).
ISO 55000 stelt eisen aan asset-management governance — strategie,
beleidskader, planning, uitvoering, monitoring. Een dashboard dat
compliance toont opent enterprise-markt.

Endpoints:
  GET /api/iso55000/checklist            ISO 55001-eisen + status
  GET /api/iso55000/score                Aggregate compliance-score

Dit is een **starter**-versie met de hoofd-clauses uit ISO 55001.
Voor volledig certificeer-bewijs is een externe auditor nodig.
"""
from __future__ import annotations
from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import User, Asset, Inspection, AuditLog
from auth import get_current_user

router = APIRouter(prefix="/api/iso55000", tags=["ISO 55000"])


# ─────────────────────────────────────────────────────────────────────────────
# ISO 55001 hoofd-clauses (gesimplificeerd voor self-assessment)
# ─────────────────────────────────────────────────────────────────────────────

CHECKLIST_ITEMS = [
    {
        "clause": "4.1",
        "title": "Context van de organisatie",
        "vraag": "Heb je een vastgelegde scope voor asset management?",
        "auto_check": "asset_count_positive",
    },
    {
        "clause": "4.2",
        "title": "Belanghebbenden",
        "vraag": "Zijn belanghebbenden (raad, inwoners, opdrachtgevers) geïdentificeerd?",
        "auto_check": None,
    },
    {
        "clause": "5.1",
        "title": "Leiderschap / commitment",
        "vraag": "Heeft directie asset-management goedgekeurd en gemandateerd?",
        "auto_check": None,
    },
    {
        "clause": "5.2",
        "title": "Asset management beleid",
        "vraag": "Bestaat er een vastgelegd asset-management beleid?",
        "auto_check": None,
    },
    {
        "clause": "6.1",
        "title": "Risico's en kansen",
        "vraag": "Worden risico's per asset systematisch beoordeeld?",
        "auto_check": "rams_used",
    },
    {
        "clause": "6.2",
        "title": "Doelstellingen + SAMP",
        "vraag": "Bestaat een Strategic Asset Management Plan (SAMP)?",
        "auto_check": "mjop_exists",
    },
    {
        "clause": "7.1",
        "title": "Middelen",
        "vraag": "Zijn er voldoende middelen (budget + mensen) toegewezen?",
        "auto_check": None,
    },
    {
        "clause": "7.2",
        "title": "Bekwaamheid",
        "vraag": "Zijn inspecteurs gecertificeerd (VCA-VOL + NEN/CROW)?",
        "auto_check": None,
    },
    {
        "clause": "7.5",
        "title": "Gedocumenteerde informatie",
        "vraag": "Worden inspecties en mutaties auditeerbaar vastgelegd?",
        "auto_check": "audit_log_active",
    },
    {
        "clause": "8.1",
        "title": "Operationele planning",
        "vraag": "Worden onderhoudsplannen jaarlijks geactualiseerd?",
        "auto_check": "recent_inspections",
    },
    {
        "clause": "9.1",
        "title": "Monitoring + meting",
        "vraag": "Worden assets periodiek beoordeeld volgens NEN 2767-2?",
        "auto_check": "nen2767_used",
    },
    {
        "clause": "9.2",
        "title": "Interne audit",
        "vraag": "Vindt jaarlijks een interne asset-management audit plaats?",
        "auto_check": None,
    },
    {
        "clause": "10.1",
        "title": "Continue verbetering",
        "vraag": "Wordt na elke incident-melding een lessons-learned vastgelegd?",
        "auto_check": None,
    },
]


def _auto_check(db: Session, organization_id: str, check_name: Optional[str]) -> Optional[bool]:
    """Voer een automatische check uit voor één checklist-item.

    Returns:
      True = check geslaagd
      False = check niet geslaagd
      None = geen automatische check (handmatig vlaggen)
    """
    if not check_name:
        return None
    if check_name == "asset_count_positive":
        return db.query(Asset).filter(
            Asset.organization_id == organization_id,
            Asset.archived_at.is_(None),
        ).count() > 0
    if check_name == "rams_used":
        # Hebben we assets met condition_score ingevuld? = RAMS-startbasis
        return db.query(Asset).filter(
            Asset.organization_id == organization_id,
            Asset.condition_score.isnot(None),
        ).count() > 0
    if check_name == "mjop_exists":
        # Hebben we assets met next_inspection_due? = MJOP-basis
        return db.query(Asset).filter(
            Asset.organization_id == organization_id,
            Asset.next_inspection_due.isnot(None),
        ).count() > 0
    if check_name == "audit_log_active":
        # Bestaan er audit-log entries voor deze org?
        return db.query(AuditLog).filter(
            AuditLog.organization_id == organization_id,
        ).count() > 0
    if check_name == "recent_inspections":
        # Inspecties laatste 12 mnd?
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        return db.query(Inspection).filter(
            Inspection.organization_id == organization_id,
            Inspection.created_at >= cutoff,
        ).count() > 0
    if check_name == "nen2767_used":
        # Inspecties met conditiescore_overall ingevuld?
        return db.query(Inspection).filter(
            Inspection.organization_id == organization_id,
            Inspection.conditiescore_overall.isnot(None),
        ).count() > 0
    return None


@router.get("/checklist")
def iso55000_checklist(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ISO 55001-checklist met auto-checks waar mogelijk."""
    items = []
    auto_pass = 0
    auto_fail = 0
    manual = 0
    for c in CHECKLIST_ITEMS:
        status = _auto_check(db, current_user.organization_id, c.get("auto_check"))
        if status is True:
            auto_pass += 1
        elif status is False:
            auto_fail += 1
        else:
            manual += 1
        items.append({
            "clause": c["clause"],
            "title": c["title"],
            "vraag": c["vraag"],
            "auto_status": "ok" if status is True else "todo" if status is False else "manual",
            "auto_check": c.get("auto_check"),
        })
    total = len(items)
    completion_pct = round(100 * auto_pass / total, 1) if total else 0
    return {
        "checklist": items,
        "summary": {
            "total": total,
            "auto_pass": auto_pass,
            "auto_fail": auto_fail,
            "manual_check": manual,
            "auto_completion_pct": completion_pct,
        },
        "standard": "ISO 55000 / NEN-ISO 55001:2014",
        "note": "Self-assessment. Voor formele certificering een externe auditor inschakelen (DEKRA, Lloyd's, Kiwa).",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/score")
def iso55000_score(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Korte maturity-score 1-5 voor directie-dashboard."""
    auto_pass = 0
    total_auto = 0
    for c in CHECKLIST_ITEMS:
        if c.get("auto_check"):
            total_auto += 1
            if _auto_check(db, current_user.organization_id, c.get("auto_check")):
                auto_pass += 1
    if total_auto == 0:
        return {"score": 1, "label": "Initieel"}
    pct = (auto_pass / total_auto) * 100
    if pct >= 90:
        score, label = 5, "Geoptimaliseerd"
    elif pct >= 70:
        score, label = 4, "Beheerd kwantitatief"
    elif pct >= 50:
        score, label = 3, "Gedefinieerd"
    elif pct >= 25:
        score, label = 2, "Beheerd"
    else:
        score, label = 1, "Initieel"
    return {
        "score": score,
        "label": label,
        "auto_completion_pct": round(pct, 1),
        "checks_passed": auto_pass,
        "checks_total": total_auto,
    }
