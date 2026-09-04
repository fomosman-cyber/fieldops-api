from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from database import get_db
from models import DemoRequest, User, AuditLog
from schemas import DemoRequestCreate, DemoRequestResponse
from auth import require_admin, check_public_post_rate_limit
from audit import ACTION

router = APIRouter(prefix="/api/demo", tags=["Demo Aanvragen"])


@router.post("/request", response_model=dict)
def create_demo_request(request: DemoRequestCreate, http_request: Request, db: Session = Depends(get_db)):
    """Demo aanvraag opslaan als 'pending' en notificaties versturen.

    Admin moet handmatig goedkeuren via het portaal voordat er een account wordt aangemaakt.
    """
    # Anti-spam: 2 aanvragen per email of 5 per IP per uur. Strenger dan
    # /api/contact omdat een aanvraag een DB-rij + 2 emails genereert.
    check_public_post_rate_limit(
        db, action=ACTION.DEMO_REQUEST_SUBMIT, request=http_request,
        email=request.email, per_email=2, per_ip=5, window_min=60,
    )

    # Check of email al een account heeft
    existing_user = db.query(User).filter(User.email == request.email).first()
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Er bestaat al een account met dit e-mailadres. Probeer in te loggen.",
        )

    # Check of er al een pending demo is voor dit email
    existing_demo = (
        db.query(DemoRequest)
        .filter(DemoRequest.email == request.email, DemoRequest.status == "pending")
        .first()
    )
    if existing_demo:
        raise HTTPException(
            status_code=400,
            detail="Er is al een demo aanvraag in behandeling voor dit e-mailadres. We nemen zo snel mogelijk contact op.",
        )

    # Sla demo aanvraag op als 'pending'
    demo = DemoRequest(
        first_name=request.first_name,
        last_name=request.last_name,
        company_name=request.company_name,
        email=request.email,
        phone=request.phone,
        plan=request.plan,
        num_users=request.num_users,
        marketing_opt_in=request.marketing_opt_in,
        notes=request.notes,
        status="pending",
        processed=False,
    )
    db.add(demo)
    db.commit()
    db.refresh(demo)

    # Audit-event voor rate-limit-telling + analytics. Email gedenormaliseerd
    # zodat per-email rate-check werkt op publieke (user-loze) submits.
    xff = http_request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    ip_addr = xff or (http_request.client.host if http_request.client else None)
    db.add(AuditLog(
        user_email=request.email,
        action=ACTION.DEMO_REQUEST_SUBMIT,
        entity_type="demo_request",
        entity_id=demo.id,
        ip_address=ip_addr,
        user_agent=(http_request.headers.get("user-agent") or "")[:512],
    ))
    db.commit()

    # Verstuur emails (best effort, errors blokkeren submit niet) - met uitgebreide logging
    import traceback
    email_status = {"admin_notification": False, "confirmation": False, "errors": []}

    try:
        from email_service import send_demo_admin_notification
        ok = send_demo_admin_notification(demo)
        email_status["admin_notification"] = bool(ok)
        if not ok:
            email_status["errors"].append("admin_notification: send_email returned False (check RESEND_API_KEY / FROM_EMAIL / domain verification)")
        print(f"[DEMO] Admin notification sent: {ok}")
    except Exception as e:
        tb = traceback.format_exc()
        email_status["errors"].append(f"admin_notification exception: {type(e).__name__}: {e}")
        print(f"[DEMO] Admin notification error: {e}\n{tb}")

    try:
        from email_service import send_demo_confirmation
        ok = send_demo_confirmation(demo)
        email_status["confirmation"] = bool(ok)
        if not ok:
            email_status["errors"].append("confirmation: send_email returned False")
        print(f"[DEMO] Confirmation sent: {ok}")
    except Exception as e:
        tb = traceback.format_exc()
        email_status["errors"].append(f"confirmation exception: {type(e).__name__}: {e}")
        print(f"[DEMO] Confirmation error: {e}\n{tb}")

    return {
        "success": True,
        "message": "Bedankt voor uw aanvraag! We nemen binnen 1 werkdag contact met u op.",
        "email_status": email_status,  # Voor debugging: laat zien of emails verstuurd zijn
    }


@router.get("/email-health", response_model=dict)
def demo_email_health(admin: User = Depends(require_admin)):
    """Debug endpoint (admin-only): test email-configuratie zonder een echte demo.

    Was publiek → lekte de RESEND-key-prefix + platform-config aan iedereen.
    Nu achter admin-auth, en de key-prefix is verwijderd (alleen nog set/niet-set).
    """
    from email_service import RESEND_API_KEY, FROM_EMAIL, ADMIN_NOTIFICATION_EMAIL, FRONTEND_URL, PORTAAL_URL, get_last_email_error
    return {
        "resend_api_key_set": bool(RESEND_API_KEY),
        "from_email": FROM_EMAIL,
        "admin_notification_email": ADMIN_NOTIFICATION_EMAIL,
        "frontend_url": FRONTEND_URL,
        "portaal_url": PORTAAL_URL,
        "last_email_error": get_last_email_error(),
    }


@router.post("/email-test", response_model=dict)
def demo_email_test(to: str = "info@fieldopsapp.nl", admin: User = Depends(require_admin)):
    """Debug endpoint (admin-only): stuur een test-email.

    Was publiek → anonieme mail-relay via het FieldOps-domein (phishing-risico).
    Nu achter admin-auth.
    """
    from email_service import send_email, get_last_email_error
    ok = send_email(
        to,
        "FieldOps Email Test",
        "<h2>Dit is een test email</h2><p>Als je dit ziet, werkt Resend!</p>",
    )
    return {
        "success": ok,
        "last_email_error": get_last_email_error(),
    }


@router.get("/requests", response_model=list[DemoRequestResponse])
def list_demo_requests(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Alle demo aanvragen ophalen (alleen voor admins)."""
    return db.query(DemoRequest).order_by(DemoRequest.created_at.desc()).all()
