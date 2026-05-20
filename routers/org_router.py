from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
from database import get_db
from models import User, Organization, AuditLog
from schemas import OrganizationResponse, OrganizationUpdate
from auth import get_current_user, require_admin
from permissions import require_org_admin
from audit import log_action, ACTION

router = APIRouter(prefix="/api/organization", tags=["Organisatie"])


@router.get("/", response_model=OrganizationResponse)
def get_organization(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Huidige organisatie ophalen."""
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organisatie niet gevonden")
    return org


@router.get("/stats")
def get_org_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dashboard statistieken ophalen."""
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    active_users = db.query(User).filter(
        User.organization_id == org.id,
        User.is_active == True,
    ).count()

    return {
        "organization": org.name,
        "plan": org.plan.value,
        "status": org.status.value,
        "active_users": active_users,
        "max_users": org.max_users,
        "trial_ends_at": org.trial_ends_at.isoformat() if org.trial_ends_at else None,
    }


@router.patch("/", response_model=OrganizationResponse)
def update_my_organization(
    update: OrganizationUpdate,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Self-service org-edit voor org-admins.

    Toegestane velden: name, contact_email, contact_phone, billing_address,
    kvk_number, btw_number, logo_data_url, brand_color. Plan/status/max_users
    zijn voorbehouden aan billing-side (Shopify-flow) en super-admins.
    """
    org = db.query(Organization).filter(
        Organization.id == current_user.organization_id,
    ).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organisatie niet gevonden")

    data = update.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="Geen velden om te wijzigen")

    # Logo-validatie: data-URL, max 500KB om DB-bloat te voorkomen
    if "logo_data_url" in data and data["logo_data_url"]:
        v = data["logo_data_url"]
        if not v.startswith("data:image/"):
            raise HTTPException(status_code=400,
                                detail="logo_data_url moet beginnen met 'data:image/...'")
        if len(v) > 700_000:  # ~500KB base64-encoded
            raise HTTPException(status_code=400,
                                detail="Logo te groot (max 500KB). Schaal de afbeelding eerst.")

    # Brand-color validatie: hex-formaat
    if "brand_color" in data and data["brand_color"]:
        bc = data["brand_color"].strip()
        if not (bc.startswith("#") and len(bc) in (4, 7)):
            raise HTTPException(status_code=400,
                                detail="brand_color moet hex-formaat hebben (#abc of #aabbcc)")
        data["brand_color"] = bc

    # Audit: zet logo-content niet in before/after (te groot). Alleen flag.
    before = {}
    after = {}
    for k in data.keys():
        old_val = getattr(org, k, None)
        if k == "logo_data_url":
            before[k] = "(set)" if old_val else "(empty)"
            after[k] = "(set)" if data[k] else "(empty)"
        else:
            before[k] = old_val
            after[k] = data[k]

    for field, value in data.items():
        if isinstance(value, str) and field != "logo_data_url":
            value = value.strip() or None
        setattr(org, field, value)
    db.commit()
    db.refresh(org)

    log_action(db, request, current_user,
               action=ACTION.ORG_UPDATE if hasattr(ACTION, "ORG_UPDATE") else "org.update",
               entity_type="organization", entity_id=org.id,
               before=before, after=after)
    return org


@router.get("/audit-summary")
def get_audit_summary(
    days: int = 30,
    limit: int = 50,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Recente audit-activiteit binnen de organisatie — alleen voor org-admins.

    Geeft top-N entries van de laatste X dagen + aggregaten per gebruiker
    + per action-type. Snelle inzicht in 'wie deed wat recent'.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (db.query(AuditLog)
              .filter(AuditLog.organization_id == current_user.organization_id,
                      AuditLog.created_at >= cutoff)
              .order_by(AuditLog.created_at.desc())
              .limit(limit).all())

    # Aggregaten — wie deed wat
    per_user: dict[str, int] = {}
    per_action: dict[str, int] = {}
    for r in rows:
        if r.user_id:
            per_user[r.user_id] = per_user.get(r.user_id, 0) + 1
        if r.action:
            per_action[r.action] = per_action.get(r.action, 0) + 1

    # User-namen erbij
    user_ids = list(per_user.keys())
    users = db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    user_names = {u.id: f"{u.first_name} {u.last_name}".strip() for u in users}

    entries = [{
        "id": r.id,
        "user_id": r.user_id,
        "user_name": user_names.get(r.user_id, "Onbekend"),
        "action": r.action,
        "entity_type": r.entity_type,
        "entity_id": r.entity_id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]

    return {
        "window_days": days,
        "total_entries": len(rows),
        "per_user": [{"user_id": uid, "user_name": user_names.get(uid, "Onbekend"), "count": cnt}
                     for uid, cnt in sorted(per_user.items(), key=lambda x: -x[1])],
        "per_action": [{"action": a, "count": cnt}
                       for a, cnt in sorted(per_action.items(), key=lambda x: -x[1])],
        "recent_entries": entries,
    }
