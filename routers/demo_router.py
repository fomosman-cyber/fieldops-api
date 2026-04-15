from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from database import get_db
from models import DemoRequest, Organization, User, AccountStatus, SubscriptionPlan
from schemas import DemoRequestCreate, DemoRequestResponse, UserResponse
from auth import hash_password, create_access_token, require_admin
import secrets

router = APIRouter(prefix="/api/demo", tags=["Demo Aanvragen"])


@router.post("/request", response_model=dict)
def create_demo_request(request: DemoRequestCreate, db: Session = Depends(get_db)):
    """Demo aanvraag verwerken: maakt automatisch organisatie + admin account aan."""

    # Check of email al bestaat
    existing_user = db.query(User).filter(User.email == request.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Er bestaat al een account met dit e-mailadres. Probeer in te loggen.")

    # Maak organisatie aan
    org = Organization(
        name=request.company_name,
        plan=request.plan,
        status=AccountStatus.TRIAL,
        max_users=min(request.num_users, 10),  # Max 10 voor demo
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(org)
    db.flush()

    # Genereer tijdelijk wachtwoord
    temp_password = secrets.token_urlsafe(12)

    # Maak admin gebruiker aan
    user = User(
        email=request.email,
        hashed_password=hash_password(temp_password),
        first_name=request.first_name,
        last_name=request.last_name,
        phone=request.phone,
        role="admin",
        is_org_admin=True,
        organization_id=org.id,
    )
    db.add(user)

    # Sla demo aanvraag op
    demo = DemoRequest(
        first_name=request.first_name,
        last_name=request.last_name,
        company_name=request.company_name,
        email=request.email,
        phone=request.phone,
        plan=request.plan,
        num_users=request.num_users,
        processed=True,
        organization_id=org.id,
    )
    db.add(demo)
    db.commit()

    # Verstuur welkom email met tijdelijk wachtwoord
    try:
        from email_service import send_email, PORTAAL_URL
        html = f"""<div style="font-family:'Segoe UI',sans-serif;">
<h2 style="color:#1e293b;">Welkom bij FieldOps, {request.first_name}!</h2>
<p style="color:#64748b;line-height:1.7;">Uw demo account voor <strong>{request.company_name}</strong> is aangemaakt. Hieronder vindt u uw inloggegevens.</p>
<div style="background:#f8f9fb;border:1px solid #e2e8f0;border-radius:12px;padding:20px;margin:20px 0;">
<div style="margin-bottom:10px;"><span style="font-size:12px;color:#94a3b8;">Portaal</span><br><strong><a href="{PORTAAL_URL}/portaal" style="color:#0284c7;">portaal.fieldopsapp.nl</a></strong></div>
<div style="margin-bottom:10px;"><span style="font-size:12px;color:#94a3b8;">E-mailadres</span><br><strong style="color:#1e293b;">{request.email}</strong></div>
<div><span style="font-size:12px;color:#94a3b8;">Wachtwoord</span><br><strong style="color:#0284c7;font-family:monospace;">{temp_password}</strong></div>
</div>
<p style="color:#64748b;font-size:13px;">Wijzig uw wachtwoord na het inloggen. Uw demo is 7 dagen geldig.</p>
</div>"""
        send_email(request.email, f"Welkom bij FieldOps — Uw demo account is klaar", html)
    except Exception as e:
        print(f"[DEMO] Email error: {e}")

    # Genereer login token
    token = create_access_token(data={"sub": user.id, "org": org.id, "role": user.role.value})

    return {
        "message": "Demo account is aangemaakt! U kunt nu direct inloggen.",
        "access_token": token,
        "token_type": "bearer",
        "user": UserResponse.model_validate(user).model_dump(),
        "temp_password": temp_password,
        "trial_ends_at": org.trial_ends_at.isoformat(),
        "organization": {
            "id": org.id,
            "name": org.name,
            "plan": org.plan.value,
            "max_users": org.max_users,
        },
    }


@router.get("/requests", response_model=list[DemoRequestResponse])
def list_demo_requests(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Alle demo aanvragen ophalen (alleen voor admins)."""
    return db.query(DemoRequest).order_by(DemoRequest.created_at.desc()).all()
