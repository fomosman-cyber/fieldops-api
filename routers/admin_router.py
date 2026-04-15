from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from database import get_db
from models import User, Organization, DemoRequest, Project, Melding, SubscriptionPlan, AccountStatus
from auth import get_current_user, hash_password

router = APIRouter(prefix="/api/admin", tags=["Admin"])


def require_owner(current_user: User = Depends(get_current_user)) -> User:
    """Alleen de eigenaar (fomosman@gmail.com) of org admins met org 'FieldOps'."""
    if not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Geen toegang - eigenaar rechten vereist")
    org = current_user.organization
    if org.name != "FieldOps":
        raise HTTPException(status_code=403, detail="Geen toegang - alleen platform eigenaar")
    return current_user


@router.get("/overview")
def admin_overview(
    current_user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Volledig overzicht voor de platform eigenaar."""
    # Alle organisaties
    orgs = db.query(Organization).order_by(Organization.created_at.desc()).all()
    org_data = []
    for o in orgs:
        user_count = db.query(User).filter(
            User.organization_id == o.id,
            User.is_active == True,
        ).count()
        org_data.append({
            "id": o.id,
            "name": o.name,
            "plan": o.plan.value if o.plan else None,
            "status": o.status.value if o.status else None,
            "max_users": o.max_users,
            "user_count": user_count,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        })

    # Alle gebruikers
    users = db.query(User).order_by(User.created_at.desc()).all()
    users_data = []
    for u in users:
        org_name = u.organization.name if u.organization else "-"
        users_data.append({
            "id": u.id,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "email": u.email,
            "role": u.role.value if u.role else None,
            "is_active": u.is_active,
            "is_org_admin": u.is_org_admin,
            "org_name": org_name,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login": u.last_login.isoformat() if u.last_login else None,
        })

    # Demo aanvragen
    demos = db.query(DemoRequest).order_by(DemoRequest.created_at.desc()).all()
    demos_data = []
    for d in demos:
        demos_data.append({
            "id": d.id,
            "first_name": d.first_name,
            "last_name": d.last_name,
            "company_name": d.company_name,
            "email": d.email,
            "plan": d.plan.value if d.plan else None,
            "num_users": d.num_users,
            "processed": d.processed,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        })

    # Projecten & meldingen counts
    total_projects = db.query(Project).count()
    active_projects = db.query(Project).filter(Project.status == "active").count()
    total_meldingen = db.query(Melding).count()
    open_meldingen = db.query(Melding).filter(Melding.status == "open").count()

    return {
        "organizations": org_data,
        "all_users": users_data,
        "total_users": len(users_data),
        "demo_requests": demos_data,
        "total_projects": total_projects,
        "active_projects": active_projects,
        "total_meldingen": total_meldingen,
        "open_meldingen": open_meldingen,
    }


# --- Organisatie beheer ---

class CreateOrganization(BaseModel):
    name: str
    plan: str = "starter"  # starter or professional
    max_users: int = 10
    admin_email: str
    admin_password: str
    admin_first_name: str
    admin_last_name: str
    admin_phone: Optional[str] = ""


@router.post("/organizations")
def create_organization(
    data: CreateOrganization,
    current_user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Nieuwe organisatie aanmaken met een beheerder (alleen platform eigenaar)."""
    # Check of email al bestaat
    existing = db.query(User).filter(User.email == data.admin_email).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"E-mailadres {data.admin_email} is al in gebruik")

    # Maak organisatie aan
    plan = SubscriptionPlan.PROFESSIONAL if data.plan == "professional" else SubscriptionPlan.STARTER
    org = Organization(
        name=data.name,
        plan=plan,
        status=AccountStatus.ACTIVE,
        max_users=data.max_users,
    )
    db.add(org)
    db.flush()  # Get org.id

    # Maak admin gebruiker aan
    admin_user = User(
        email=data.admin_email,
        hashed_password=hash_password(data.admin_password),
        first_name=data.admin_first_name,
        last_name=data.admin_last_name,
        phone=data.admin_phone or "",
        role="admin",
        is_org_admin=True,
        organization_id=org.id,
    )
    db.add(admin_user)
    db.commit()
    db.refresh(org)
    db.refresh(admin_user)

    return {
        "success": True,
        "message": f"Organisatie '{data.name}' aangemaakt met beheerder {data.admin_email}",
        "organization": {
            "id": org.id,
            "name": org.name,
            "plan": org.plan.value,
            "status": org.status.value,
            "max_users": org.max_users,
        },
        "admin": {
            "id": admin_user.id,
            "email": admin_user.email,
            "name": f"{admin_user.first_name} {admin_user.last_name}",
        },
    }


@router.put("/organizations/{org_id}")
def update_organization(
    org_id: str,
    current_user: User = Depends(require_owner),
    db: Session = Depends(get_db),
    name: Optional[str] = None,
    plan: Optional[str] = None,
    max_users: Optional[int] = None,
    status: Optional[str] = None,
):
    """Organisatie bijwerken (alleen platform eigenaar)."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organisatie niet gevonden")
    if name:
        org.name = name
    if plan:
        org.plan = SubscriptionPlan.PROFESSIONAL if plan == "professional" else SubscriptionPlan.STARTER
    if max_users is not None:
        org.max_users = max_users
    if status:
        status_map = {"active": AccountStatus.ACTIVE, "trial": AccountStatus.TRIAL, "expired": AccountStatus.EXPIRED, "suspended": AccountStatus.SUSPENDED}
        org.status = status_map.get(status, AccountStatus.ACTIVE)
    db.commit()
    db.refresh(org)
    return {
        "id": org.id,
        "name": org.name,
        "plan": org.plan.value,
        "status": org.status.value,
        "max_users": org.max_users,
    }


@router.delete("/organizations/{org_id}")
def delete_organization(
    org_id: str,
    current_user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Organisatie en alle bijbehorende data verwijderen (alleen platform eigenaar)."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organisatie niet gevonden")
    if org.name == "FieldOps":
        raise HTTPException(status_code=400, detail="Kan de platform organisatie niet verwijderen")

    # Verwijder alle data van deze organisatie
    try:
        from models import Invitation
        db.query(Invitation).filter(Invitation.organization_id == org_id).delete()
    except Exception:
        pass  # Invitation table might not exist
    projects = db.query(Project).filter(Project.organization_id == org_id).all()
    for p in projects:
        db.query(Melding).filter(Melding.project_id == p.id).delete()
    db.query(Project).filter(Project.organization_id == org_id).delete()
    db.query(User).filter(User.organization_id == org_id).delete()
    db.delete(org)
    db.commit()
    return {"success": True, "message": f"Organisatie '{org.name}' en alle data verwijderd"}
