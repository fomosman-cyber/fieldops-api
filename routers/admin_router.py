from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
import secrets
import string
from database import get_db
from models import User, Organization, DemoRequest, Project, Melding, SubscriptionPlan, AccountStatus
from auth import get_current_user, hash_password
from audit import log_action, ACTION


def _generate_demo_password(length: int = 14) -> str:
    """Cryptografisch veilig random wachtwoord. Mix van letters, cijfers en
    veilige speciale tekens (geen ambigue chars). Gebruikt voor demo-accounts;
    user wordt gedwongen het bij eerste login te wijzigen (must_change_password)."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    # Garandeer minstens 1 letter, 1 cijfer, 1 speciaal teken
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pw)
                and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw)
                and any(c in "!@#$%^&*-_=+" for c in pw)):
            return pw

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

    # Demo aanvragen - defensief gebruik van getattr voor velden die later zijn toegevoegd
    demos = db.query(DemoRequest).order_by(DemoRequest.created_at.desc()).all()
    demos_data = []
    for d in demos:
        status_val = getattr(d, "status", None) or ("approved" if d.processed else "pending")
        demos_data.append({
            "id": d.id,
            "first_name": d.first_name,
            "last_name": d.last_name,
            "company_name": d.company_name,
            "email": d.email,
            "phone": getattr(d, "phone", None) or "",
            "plan": d.plan.value if d.plan else None,
            "num_users": d.num_users,
            "status": status_val,
            "processed": d.processed,
            "organization_id": getattr(d, "organization_id", None),
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
    request: Request,
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

    log_action(db, request, current_user,
               action=ACTION.ORG_CREATE, entity_type="organization", entity_id=org.id,
               after={"name": org.name, "plan": org.plan.value, "max_users": org.max_users,
                      "admin_email": admin_user.email})
    log_action(db, request, current_user,
               action=ACTION.USER_CREATE, entity_type="user", entity_id=admin_user.id,
               after={"email": admin_user.email, "role": "admin", "is_org_admin": True,
                      "organization_id": org.id})

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
    request: Request,
    current_user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Organisatie en alle bijbehorende data verwijderen (alleen platform eigenaar)."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organisatie niet gevonden")
    if org.name == "FieldOps":
        raise HTTPException(status_code=400, detail="Kan de platform organisatie niet verwijderen")

    snapshot = {"name": org.name, "plan": org.plan.value if org.plan else None,
                "user_count": db.query(User).filter(User.organization_id == org_id).count()}

    from models import Invitation
    try:
        db.query(Invitation).filter(Invitation.organization_id == org_id).delete()
    except Exception:
        pass
    try:
        db.query(DemoRequest).filter(DemoRequest.organization_id == org_id).delete()
    except Exception:
        pass
    projects = db.query(Project).filter(Project.organization_id == org_id).all()
    for p in projects:
        db.query(Melding).filter(Melding.project_id == p.id).delete()
    db.query(Project).filter(Project.organization_id == org_id).delete()
    db.query(User).filter(User.organization_id == org_id).delete()
    db.delete(org)
    db.commit()

    log_action(db, request, current_user,
               action=ACTION.ORG_DELETE, entity_type="organization", entity_id=org_id,
               before=snapshot)

    return {"success": True, "message": f"Organisatie '{snapshot['name']}' en alle data verwijderd"}


@router.delete("/demo/{demo_id}")
def delete_demo_request(
    demo_id: str,
    request: Request,
    current_user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Demo aanvraag verwijderen (alleen platform eigenaar)."""
    demo = db.query(DemoRequest).filter(DemoRequest.id == demo_id).first()
    if not demo:
        raise HTTPException(status_code=404, detail="Demo aanvraag niet gevonden")
    snapshot = {"email": demo.email, "company_name": demo.company_name, "status": demo.status}
    db.delete(demo)
    db.commit()
    log_action(db, request, current_user,
               action=ACTION.DEMO_DELETE, entity_type="demo_request", entity_id=demo_id,
               before=snapshot)
    return {"success": True, "message": "Demo aanvraag verwijderd"}


@router.post("/demo/{demo_id}/approve")
def approve_demo_request(
    demo_id: str,
    request: Request,
    current_user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Demo aanvraag goedkeuren: maakt organisatie + admin account aan met
    een uniek, willekeurig wachtwoord. Verstuurt welkomstmail met inloggegevens.
    Gebruiker moet bij eerste login het wachtwoord wijzigen.
    """
    demo = db.query(DemoRequest).filter(DemoRequest.id == demo_id).first()
    if not demo:
        raise HTTPException(status_code=404, detail="Demo aanvraag niet gevonden")
    if demo.status == "approved" or demo.processed:
        raise HTTPException(status_code=400, detail="Deze demo aanvraag is al goedgekeurd")

    existing_user = db.query(User).filter(User.email == demo.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail=f"Er bestaat al een account met {demo.email}")

    org = Organization(
        name=demo.company_name,
        plan=demo.plan or SubscriptionPlan.STARTER,
        status=AccountStatus.ACTIVE,
        max_users=demo.num_users or 10,
    )
    db.add(org)
    db.flush()

    # Genereer uniek random wachtwoord — wordt eenmalig per email verstuurd,
    # daarna nooit meer leesbaar (alleen de bcrypt-hash zit in DB).
    plain_password = _generate_demo_password()

    admin_user = User(
        email=demo.email,
        hashed_password=hash_password(plain_password),
        first_name=demo.first_name,
        last_name=demo.last_name,
        phone=demo.phone or "",
        role="admin",
        is_org_admin=True,
        must_change_password=True,
        organization_id=org.id,
    )
    db.add(admin_user)

    demo.status = "approved"
    demo.processed = True
    demo.organization_id = org.id

    db.commit()
    db.refresh(org)
    db.refresh(admin_user)

    log_action(db, request, current_user,
               action=ACTION.DEMO_APPROVE, entity_type="demo_request", entity_id=demo.id,
               after={"email": demo.email, "company_name": demo.company_name,
                      "organization_id": org.id, "user_id": admin_user.id})

    try:
        from email_service import send_demo_welcome
        send_demo_welcome(admin_user, plain_password, org)
    except Exception as e:
        print(f"[ADMIN] Welcome email error: {e}")

    return {
        "success": True,
        "message": f"Demo goedgekeurd. Welkomstmail verstuurd naar {demo.email}",
        "organization_id": org.id,
        "user_id": admin_user.id,
    }
