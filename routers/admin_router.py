from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta
import json
import random
import secrets
import string
from database import get_db
from models import User, Organization, DemoRequest, Project, Melding, Asset, SubscriptionPlan, AccountStatus
from auth import get_current_user, hash_password, validate_password_strength
from audit import log_action, ACTION
from routers.users_router import ANONYMIZED_EMAIL_DOMAIN


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
    """Volledig overzicht voor de platform eigenaar.

    Geanonymiseerde users (GDPR Art.17, email suffix @deleted.invalid) worden
    overal uitgefilterd — die bestaan formeel niet meer voor de UI, ook al
    is hun DB-rij behouden voor FK-integriteit en audit-trail.
    """
    # Anonymized-filter (email suffix). Wordt overal hergebruikt.
    not_anon = ~User.email.like("%@" + ANONYMIZED_EMAIL_DOMAIN)

    # Alle organisaties
    orgs = db.query(Organization).order_by(Organization.created_at.desc()).all()
    org_data = []
    for o in orgs:
        user_count = db.query(User).filter(
            User.organization_id == o.id,
            User.is_active == True,
            not_anon,
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

    # Alle gebruikers — geanonymiseerden eruit
    users = db.query(User).filter(not_anon).order_by(User.created_at.desc()).all()
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
    validate_password_strength(data.admin_password)
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

    # Maak admin gebruiker aan — must_change_password=True dwingt de admin
    # om het door de platform-eigenaar gegenereerde wachtwoord direct te
    # vervangen na eerste login.
    admin_user = User(
        email=data.admin_email,
        hashed_password=hash_password(data.admin_password),
        first_name=data.admin_first_name,
        last_name=data.admin_last_name,
        phone=data.admin_phone or "",
        role="admin",
        is_org_admin=True,
        must_change_password=True,
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


# ════════════════════════════════════════════════════════════
# Demo-data seeder — voor product demo's: vult een org met
# voldoende meldingen, assets en clusters zodat Voorspeller,
# Operations Optimizer en Mijn dag direct werken.
# ════════════════════════════════════════════════════════════

@router.post("/seed-demo-data")
def seed_demo_data(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Vult de huidige org met realistische demo-data.

    Maakt aan: 1 project (KO-Demo), 12 wegen-assets met NEN-conditie 2-5,
    20 meldingen verdeeld over statussen, waarvan een cluster-set van
    8 open meldingen met identieke gw_term + nabij elkaar (zodat
    Operations Optimizer direct ≥2 clusters genereert). Idempotent: skipt
    items die al bestaan met dezelfde code.
    """
    if not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Alleen org-admin")

    org_id = current_user.organization_id

    # ── Project ──────────────────────────────────────────────
    project = (db.query(Project)
                 .filter(Project.organization_id == org_id, Project.name == "KO-Demo")
                 .first())
    if not project:
        project = Project(
            name="KO-Demo",
            description="Demo-project voor productverkenning. Bevat assets + meldingen om Voorspeller, Operations Optimizer en Mijn dag te demonstreren.",
            gemeente="Demo Gemeente",
            status="active",
            color="#8b5cf6",
            categories=json.dumps(["wegdek", "lantaarnpaal", "voegovergang", "kunstwerk"]),
            organization_id=org_id,
            created_by=current_user.id,
        )
        db.add(project)
        db.flush()

    # ── Assets ───────────────────────────────────────────────
    asset_specs = [
        ("DEMO-WV-001", "Hoofdstraat km 0.2-1.4", "wegdek", 52.090, 4.310, 3),
        ("DEMO-WV-002", "Hoofdstraat km 1.4-2.6", "wegdek", 52.094, 4.318, 4),
        ("DEMO-WV-003", "Stationsweg km 0.0-0.8", "wegdek", 52.085, 4.305, 5),
        ("DEMO-WV-004", "Kerkstraat km 0.0-0.5", "wegdek", 52.088, 4.315, 2),
        ("DEMO-WV-005", "Marktweg km 0.0-1.0", "wegdek", 52.092, 4.302, 4),
        ("DEMO-WV-006", "Dorpsstraat km 0.0-0.9", "wegdek", 52.086, 4.320, 3),
        ("DEMO-VG-001", "Voegovergang Hoofdstraat", "voegovergang", 52.091, 4.313, 4),
        ("DEMO-VG-002", "Voegovergang Stationsweg", "voegovergang", 52.085, 4.307, 5),
        ("DEMO-LP-001", "Lantaarn Hoofdstraat 12", "lantaarnpaal", 52.090, 4.311, 3),
        ("DEMO-LP-002", "Lantaarn Stationsweg 8", "lantaarnpaal", 52.085, 4.306, 4),
        ("DEMO-LP-003", "Lantaarn Marktweg 22", "lantaarnpaal", 52.092, 4.303, 5),
        ("DEMO-KW-001", "Brug over de Demo-vaart", "kunstwerk", 52.087, 4.312, 3),
    ]
    created_assets = []
    for code, name, atype, lat, lng, conditie in asset_specs:
        existing = (db.query(Asset)
                      .filter(Asset.organization_id == org_id, Asset.code == code)
                      .first())
        if existing:
            created_assets.append(existing); continue
        installed = datetime.now(timezone.utc) - timedelta(days=random.randint(2000, 7300))
        a = Asset(
            code=code, name=name, asset_type=atype, lat=lat, lng=lng,
            location_description=f"Demo Gemeente — {name}",
            project_id=project.id, organization_id=org_id,
            condition_score=conditie,
            installed_at=installed,
            expected_lifespan_years=25 if atype == "wegdek" else (30 if atype == "lantaarnpaal" else 50),
            created_by=current_user.id,
        )
        db.add(a); db.flush()
        created_assets.append(a)

    # ── Meldingen ────────────────────────────────────────────
    # Cluster-set: 6 open scheurvulling-meldingen op nabije wegen — zorgt
    # dat Operations Optimizer ≥1 cluster genereert.
    scheur_assets = [a for a in created_assets if a.asset_type == "wegdek"][:6]
    crow_advies_scheur = {
        "crow_schadegroep": "samenhang",
        "crow_schadebeeld": "scheurvorming-langs",
        "crow_ernst": "M",
        "crow_omvang": "2",
        "crow_klasse": "M2",
        "nen_2767_conditie": 4,
        "onderhoud_categorie": "KO",
        "gw_maatregel": "Scheurvulling polymeer",
        "gw_term": "Scheurvulling polymeer (cold-pour) per m1",
        "gw_kosten_orde": "EUR 5-15 / m1",
    }
    crow_advies_lantaarn = {
        "crow_schadegroep": None, "crow_schadebeeld": None,
        "crow_ernst": "M", "crow_omvang": "1", "crow_klasse": "M1",
        "nen_2767_conditie": 4,
        "onderhoud_categorie": "KO",
        "gw_maatregel": "Armatuur vervangen",
        "gw_term": "Lichtarmatuur LED vervangen per stuk",
        "gw_kosten_orde": "EUR 250-450 / stuk",
    }
    melding_specs = []
    # 6 scheurvulling-meldingen (open, hoog) — clusterbaar
    for idx, a in enumerate(scheur_assets):
        melding_specs.append({
            "title": f"Scheur in wegdek {a.name.split(' km')[0]} — sectie {idx+1}",
            "asset": a, "status": "open", "priority": "hoog", "category": "schade",
            "crow": crow_advies_scheur, "days_ago": random.randint(1, 20),
        })
    # 2 lantaarnpaal-meldingen (open, normaal) — clusterbaar samen
    for a in [x for x in created_assets if x.asset_type == "lantaarnpaal"][:2]:
        melding_specs.append({
            "title": f"Defecte verlichting — {a.name}",
            "asset": a, "status": "open", "priority": "normaal", "category": "schade",
            "crow": crow_advies_lantaarn, "days_ago": random.randint(1, 15),
        })
    # 2 kritieke meldingen (Operations Center strip toont rood)
    if any(a.asset_type == "voegovergang" for a in created_assets):
        vg = next(a for a in created_assets if a.asset_type == "voegovergang")
        melding_specs.append({
            "title": "Voegovergang verzakt — verkeershinder",
            "asset": vg, "status": "open", "priority": "kritiek", "category": "schade",
            "crow": {"crow_ernst": "E", "crow_omvang": "2", "crow_klasse": "E2",
                     "nen_2767_conditie": 5, "onderhoud_categorie": "acuut",
                     "gw_maatregel": "Voegovergang renoveren",
                     "gw_term": "Voegovergang vervangen per stuk",
                     "gw_kosten_orde": "EUR 8.000-15.000 / stuk"},
            "days_ago": 0,
        })
    # 4 in-behandeling / afgeronde voor het verleden
    for status, prio, days in [
        ("in_behandeling", "hoog", 5),
        ("in_behandeling", "normaal", 8),
        ("opgelost", "normaal", 18),
        ("afgerond", "normaal", 28),
    ]:
        a = random.choice(created_assets)
        melding_specs.append({
            "title": f"Onderhoud {a.name}",
            "asset": a, "status": status, "priority": prio, "category": "schade",
            "crow": crow_advies_scheur if a.asset_type == "wegdek" else {},
            "days_ago": days,
        })

    created_meldingen = []
    for spec in melding_specs:
        title = spec["title"]
        existing = (db.query(Melding)
                      .filter(Melding.organization_id == org_id, Melding.title == title)
                      .first())
        if existing:
            created_meldingen.append(existing); continue
        a = spec["asset"]
        m = Melding(
            title=title,
            description=f"Demo-melding. Asset: {a.code}. Locatie: {a.location_description}.",
            category=spec["category"],
            priority=spec["priority"],
            status=spec["status"],
            lat=a.lat, lng=a.lng,
            project_id=project.id, asset_id=a.id,
            organization_id=org_id,
            created_by=current_user.id,
            created_at=datetime.now(timezone.utc) - timedelta(days=spec["days_ago"]),
        )
        for k, v in (spec.get("crow") or {}).items():
            setattr(m, k, v)
        db.add(m); db.flush()
        created_meldingen.append(m)

    db.commit()
    log_action(db, request, current_user,
               action="admin.seed_demo_data", entity_type="organization", entity_id=org_id,
               extra={"assets": len(created_assets), "meldingen": len(created_meldingen)})

    return {
        "success": True,
        "message": "Demo-data aangemaakt. Open Voorspeller, Operations Optimizer en Mijn dag om de workflow te zien.",
        "project_id": project.id,
        "assets_created": len(created_assets),
        "meldingen_created": len(created_meldingen),
        "clusterable_open": sum(1 for m in created_meldingen if m.status == "open" and m.gw_term),
    }


# ════════════════════════════════════════════════════════════
# Meldingen-seeder voor BESTAAND project — gebruikt assets die
# al in het project zitten (bv. KO-Naaldwijk met 21 wegen + 6
# lantaarnpalen) en spreidt realistische meldingen + CROW-data.
# ════════════════════════════════════════════════════════════

class SeedMeldingenRequest(BaseModel):
    project_id: str
    count: Optional[int] = 14


@router.post("/seed-meldingen-for-project")
def seed_meldingen_for_project(
    payload: SeedMeldingenRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Voegt realistische meldingen toe aan een bestaand project op basis van
    de assets die in dat project zitten. Goed voor testen van clusters,
    Voorspeller en Mijn-dag op je echte projecten (bv. KO-Naaldwijk).

    Spreiding:
      - 40% clusterbare scheurvulling-meldingen op wegen (open, hoog)
      - 20% lantaarn-meldingen (open, normaal)
      - 10% kritieke voegovergang/kunstwerk (open, kritiek)
      - 20% in_behandeling (verleden)
      - 10% afgerond/opgelost (verleden)
    """
    if not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Alleen org-admin")

    org_id = current_user.organization_id
    project = (db.query(Project)
                 .filter(Project.id == payload.project_id,
                         Project.organization_id == org_id)
                 .first())
    if not project:
        raise HTTPException(status_code=404, detail="Project niet gevonden")

    assets = (db.query(Asset)
                .filter(Asset.project_id == project.id,
                        Asset.organization_id == org_id)
                .all())
    if not assets:
        raise HTTPException(
            status_code=400,
            detail="Project heeft geen assets. Voeg eerst assets toe voordat je meldingen kunt genereren.",
        )

    wegen = [a for a in assets if a.asset_type == "wegdek"]
    lantaarns = [a for a in assets if a.asset_type == "lantaarnpaal"]
    kunstwerken = [a for a in assets if a.asset_type in ("voegovergang", "kunstwerk", "brug")]
    overig = [a for a in assets if a not in wegen and a not in lantaarns and a not in kunstwerken]

    count = max(4, min(payload.count or 14, 50))

    crow_scheur = {
        "crow_schadegroep": "samenhang",
        "crow_schadebeeld": "scheurvorming-langs",
        "crow_ernst": "M",
        "crow_omvang": "2",
        "crow_klasse": "M2",
        "nen_2767_conditie": 4,
        "onderhoud_categorie": "KO",
        "gw_maatregel": "Scheurvulling polymeer",
        "gw_term": "Scheurvulling polymeer (cold-pour) per m1",
        "gw_kosten_orde": "EUR 5-15 / m1",
    }
    crow_spoor = {
        "crow_schadegroep": "vlakheid",
        "crow_schadebeeld": "spoorvorming",
        "crow_ernst": "M",
        "crow_omvang": "2",
        "crow_klasse": "M2",
        "nen_2767_conditie": 4,
        "onderhoud_categorie": "KO",
        "gw_maatregel": "Deklaag vervangen",
        "gw_term": "Asfalt deklaag SMA 0/8 vervangen per m2",
        "gw_kosten_orde": "EUR 30-55 / m2",
    }
    crow_rafeling = {
        "crow_schadegroep": "textuur",
        "crow_schadebeeld": "rafeling",
        "crow_ernst": "L",
        "crow_omvang": "2",
        "crow_klasse": "L2",
        "nen_2767_conditie": 3,
        "onderhoud_categorie": "KO",
        "gw_maatregel": "Oppervlakbehandeling",
        "gw_term": "Oppervlakbehandeling enkelvoudig per m2",
        "gw_kosten_orde": "EUR 4-9 / m2",
    }
    crow_lantaarn = {
        "crow_ernst": "M",
        "crow_omvang": "1",
        "crow_klasse": "M1",
        "nen_2767_conditie": 4,
        "onderhoud_categorie": "KO",
        "gw_maatregel": "Armatuur vervangen",
        "gw_term": "Lichtarmatuur LED vervangen per stuk",
        "gw_kosten_orde": "EUR 250-450 / stuk",
    }
    crow_voeg = {
        "crow_schadegroep": "voegen",
        "crow_schadebeeld": "scheurvorming-voeg",
        "crow_ernst": "E",
        "crow_omvang": "2",
        "crow_klasse": "E2",
        "nen_2767_conditie": 5,
        "onderhoud_categorie": "acuut",
        "gw_maatregel": "Voegovergang renoveren",
        "gw_term": "Voegovergang vervangen per stuk",
        "gw_kosten_orde": "EUR 8.000-15.000 / stuk",
    }

    melding_specs = []

    # 40%: clusterbare scheurvulling op wegen (open, hoog) — minimaal 4 om
    # een cluster te krijgen
    n_cluster = max(4, int(count * 0.40))
    if wegen:
        for i in range(min(n_cluster, len(wegen) * 2)):
            asset = wegen[i % len(wegen)]
            melding_specs.append({
                "title": f"Scheurvorming wegdek {asset.name} — sectie {i+1}",
                "asset": asset, "status": "open", "priority": "hoog", "category": "schade",
                "crow": crow_scheur, "days_ago": random.randint(1, 14),
            })

    # 10%: spoorvorming op wegen (open, normaal)
    n_spoor = max(1, int(count * 0.10))
    if wegen:
        for i in range(min(n_spoor, len(wegen))):
            asset = random.choice(wegen)
            melding_specs.append({
                "title": f"Spoorvorming {asset.name} richting kruising",
                "asset": asset, "status": "open", "priority": "normaal", "category": "schade",
                "crow": crow_spoor, "days_ago": random.randint(2, 20),
            })

    # 10%: rafeling op wegen (open, laag)
    n_raf = max(1, int(count * 0.10))
    if wegen:
        for i in range(min(n_raf, len(wegen))):
            asset = random.choice(wegen)
            melding_specs.append({
                "title": f"Rafeling asfaltdeklaag {asset.name}",
                "asset": asset, "status": "open", "priority": "laag", "category": "schade",
                "crow": crow_rafeling, "days_ago": random.randint(3, 25),
            })

    # 15%: lantaarn-meldingen (open, normaal) — clusterbaar onder elkaar
    n_lp = max(2, int(count * 0.15))
    if lantaarns:
        for i in range(min(n_lp, len(lantaarns))):
            asset = lantaarns[i % len(lantaarns)]
            melding_specs.append({
                "title": f"Defecte verlichting — {asset.name}",
                "asset": asset, "status": "open", "priority": "normaal", "category": "schade",
                "crow": crow_lantaarn, "days_ago": random.randint(1, 10),
            })

    # 10%: kritieke voegovergang / kunstwerk (open, kritiek)
    if kunstwerken:
        for kw in kunstwerken[:max(1, int(count * 0.10))]:
            melding_specs.append({
                "title": f"Kritieke schade {kw.name} — verkeershinder",
                "asset": kw, "status": "open", "priority": "kritiek", "category": "schade",
                "crow": crow_voeg, "days_ago": 0,
            })

    # 10%: in_behandeling op willekeurige assets
    for _ in range(max(1, int(count * 0.10))):
        asset = random.choice(assets)
        melding_specs.append({
            "title": f"Onderhoud uitvoering {asset.name}",
            "asset": asset, "status": "in_behandeling", "priority": "hoog", "category": "schade",
            "crow": crow_scheur if asset.asset_type == "wegdek" else {},
            "days_ago": random.randint(3, 14),
        })

    # 5%: afgerond (verleden)
    for _ in range(max(1, int(count * 0.05))):
        asset = random.choice(assets)
        melding_specs.append({
            "title": f"Reparatie afgerond {asset.name}",
            "asset": asset, "status": "afgerond", "priority": "normaal", "category": "schade",
            "crow": crow_scheur if asset.asset_type == "wegdek" else {},
            "days_ago": random.randint(20, 60),
        })

    # 5%: inspectie (afgerond, laag) — voor reporting variation
    for _ in range(max(1, int(count * 0.05))):
        asset = random.choice(assets)
        melding_specs.append({
            "title": f"Routine-inspectie {asset.name}",
            "asset": asset, "status": "afgerond", "priority": "laag", "category": "inspectie",
            "crow": {}, "days_ago": random.randint(15, 45),
        })

    created_meldingen = []
    for spec in melding_specs:
        title = spec["title"]
        existing = (db.query(Melding)
                      .filter(Melding.organization_id == org_id,
                              Melding.project_id == project.id,
                              Melding.title == title)
                      .first())
        if existing:
            continue
        a = spec["asset"]
        m = Melding(
            title=title,
            description=f"Test-melding voor {a.name}. Asset: {a.code}. Locatie: {a.location_description or '-'}.",
            category=spec["category"],
            priority=spec["priority"],
            status=spec["status"],
            lat=a.lat, lng=a.lng,
            project_id=project.id, asset_id=a.id,
            organization_id=org_id,
            created_by=current_user.id,
            created_at=datetime.now(timezone.utc) - timedelta(days=spec["days_ago"]),
        )
        for k, v in (spec.get("crow") or {}).items():
            setattr(m, k, v)
        db.add(m); db.flush()
        created_meldingen.append(m)

    db.commit()
    log_action(db, request, current_user,
               action="admin.seed_meldingen_for_project",
               entity_type="project", entity_id=project.id,
               extra={"meldingen": len(created_meldingen)})

    return {
        "success": True,
        "message": f"{len(created_meldingen)} meldingen aangemaakt op project '{project.name}'.",
        "project_id": project.id,
        "project_name": project.name,
        "meldingen_created": len(created_meldingen),
        "clusterable_open": sum(1 for m in created_meldingen if m.status == "open" and m.gw_term),
    }


@router.post("/seed-demo-user")
def seed_demo_user(
    request: Request,
    current_user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Idempotent: maakt of reset demo@fieldopsapp.nl met wachtwoord 'demo2026'.

    Bedoeld voor de sandbox demo.html op de marketing-site die automatisch
    inlogt. VIEWER-rol zodat de demo-user geen kritieke data kan muteren.
    Wachtwoord-strength wordt bewust gebypassed — dit is een seeder die de
    server-side hash direct schrijft. must_change_password=False omdat de
    sandbox direct moet kunnen inloggen.
    """
    from models import UserRole as _UR
    DEMO_EMAIL = "demo@fieldopsapp.nl"
    DEMO_PW = "demo2026"

    org = current_user.organization
    existing = db.query(User).filter(User.email == DEMO_EMAIL).first()
    if existing:
        existing.hashed_password = hash_password(DEMO_PW)
        existing.is_active = True
        existing.must_change_password = False
        db.commit()
        log_action(db, request, current_user,
                   action="admin.seed_demo_user",
                   entity_type="user", entity_id=existing.id,
                   extra={"mode": "updated"})
        return {"success": True, "mode": "updated", "email": DEMO_EMAIL,
                "user_id": existing.id, "organization": org.name}

    demo = User(
        email=DEMO_EMAIL,
        hashed_password=hash_password(DEMO_PW),
        first_name="Demo",
        last_name="Sandbox",
        role=_UR.VIEWER,
        is_active=True,
        is_org_admin=False,
        must_change_password=False,
        organization_id=org.id,
    )
    db.add(demo)
    db.commit()
    db.refresh(demo)
    log_action(db, request, current_user,
               action="admin.seed_demo_user",
               entity_type="user", entity_id=demo.id,
               extra={"mode": "created"})
    return {"success": True, "mode": "created", "email": DEMO_EMAIL,
            "user_id": demo.id, "organization": org.name}


# ─────────────────────────────────────────────────────────────────────────────
# Backup management (super-admin only)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/backup/status")
def backup_status(
    current_user: User = Depends(require_owner),
):
    """Status van S3-backup-systeem: config-check + laatste run + recente backups.

    Geen credentials worden geretourneerd — alleen config-flag en metadata.
    """
    import backup_service
    return {
        "status": backup_service.get_status(),
        "recent_backups": backup_service.list_recent_backups(limit=20),
    }


@router.post("/backup/trigger")
def backup_trigger(
    request: Request,
    current_user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Manual trigger van een backup. Synchroon — kan 5-30s duren voor grote DBs.

    Bij Render Cron Job-setup wordt dit endpoint NIET aangeroepen; daar draait
    `python -m backup_service` direct. Dit endpoint is voor ad-hoc dumps door
    super-admin (bv. voor pre-migratie snapshot).
    """
    import backup_service
    if not backup_service.is_configured():
        raise HTTPException(
            status_code=400,
            detail="Backup-service niet geconfigureerd. Vereist env-vars: S3_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY.",
        )
    log_action(db, request, current_user,
               action="admin.backup.triggered",
               entity_type="system", entity_id=None,
               extra={"trigger": "manual"})

    result = backup_service.run_backup()

    log_action(db, request, current_user,
               action="admin.backup.completed" if result.get("success") else "admin.backup.failed",
               entity_type="system", entity_id=None,
               extra={k: v for k, v in result.items() if k != "key"})
    return result
