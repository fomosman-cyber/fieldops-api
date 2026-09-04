import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from database import get_db
from models import User, Organization, Project, AuditLog, is_reserved_org_name
from schemas import OrganizationResponse, OrganizationUpdate
from auth import get_current_user
from permissions import require_org_admin, is_platform_owner
from audit import log_action, ACTION
from branding import valideer_logo, valideer_kleur

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
    kvk_number, btw_number, logo_data_url, brand_color, de provider-links en
    de instellingen van het publieke meldpunt. Plan/status/max_users zijn
    voorbehouden aan billing-side (Shopify-flow) en super-admins.
    """
    org = db.query(Organization).filter(
        Organization.id == current_user.organization_id,
    ).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organisatie niet gevonden")

    data = update.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="Geen velden om te wijzigen")

    # SECURITY: de naam "FieldOps" is gereserveerd — is_platform_owner() en
    # require_owner() checken op deze org-naam, dus een rename hierheen zou
    # een gewone org-admin stilletjes platform-eigenaar maken.
    # De centrale guard zit op het Organization-model (@validates("name"));
    # hier vertalen we dat naar een nette 400 en geven we de platform-owner
    # expliciet opt-in om z'n eigen org (opnieuw) "FieldOps" te noemen.
    if "name" in data and data["name"] is not None and is_reserved_org_name(data["name"]):
        if not is_platform_owner(current_user):
            raise HTTPException(
                status_code=400,
                detail="Deze organisatienaam is gereserveerd voor het platform")
        org.allow_reserved_name = True

    # Zelfde regels als de super-admin-route, zie branding.py
    if "logo_data_url" in data and data["logo_data_url"]:
        data["logo_data_url"] = valideer_logo(data["logo_data_url"])
    if "brand_color" in data and data["brand_color"]:
        data["brand_color"] = valideer_kleur(data["brand_color"])

    # Slug van het publieke meldpunt: komt in een URL en moet uniek zijn over
    # alle organisaties heen, anders wijst /meld/<slug> naar de verkeerde club.
    if "public_meld_slug" in data and data["public_meld_slug"]:
        slug = data["public_meld_slug"].strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,58}[a-z0-9]", slug):
            raise HTTPException(
                status_code=400,
                detail="Meldpunt-adres mag alleen kleine letters, cijfers en "
                       "koppeltekens bevatten (3-60 tekens, niet beginnen of "
                       "eindigen met een koppelteken)")
        bezet = db.query(Organization).filter(
            Organization.public_meld_slug == slug,
            Organization.id != org.id,
        ).first()
        if bezet:
            raise HTTPException(status_code=400,
                                detail="Dit meldpunt-adres is al in gebruik")
        data["public_meld_slug"] = slug

    # Startproject moet van de eigen organisatie zijn — anders kun je via dit
    # veld naar een project van een andere klant wijzen.
    if "public_meld_default_project_id" in data and data["public_meld_default_project_id"]:
        van_ons = db.query(Project).filter(
            Project.id == data["public_meld_default_project_id"],
            Project.organization_id == org.id,
        ).first()
        if not van_ons:
            raise HTTPException(status_code=400,
                                detail="Startproject hoort niet bij deze organisatie")

    # Categorieen worden als JSON-array opgeslagen; onleesbare invoer zou het
    # publieke formulier stukmaken.
    if "public_meld_categories" in data and data["public_meld_categories"]:
        try:
            cats = json.loads(data["public_meld_categories"])
        except (ValueError, TypeError):
            raise HTTPException(status_code=400,
                                detail="Categorieen zijn geen geldige lijst")
        if not isinstance(cats, list) or not all(isinstance(c, str) for c in cats):
            raise HTTPException(status_code=400,
                                detail="Categorieen moeten een lijst met teksten zijn")
        if len(cats) > 30:
            raise HTTPException(status_code=400,
                                detail="Maximaal 30 categorieen")

    # Aanzetten kan alleen met een adres, anders is het meldpunt onbereikbaar.
    if data.get("public_meld_enabled") and not (
        data.get("public_meld_slug") or org.public_meld_slug
    ):
        raise HTTPException(status_code=400,
                            detail="Kies eerst een meldpunt-adres voordat je "
                                   "het meldpunt aanzet")

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
