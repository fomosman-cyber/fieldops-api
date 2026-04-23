from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import DemoRequest, User
from schemas import DemoRequestCreate, DemoRequestResponse
from auth import require_admin

router = APIRouter(prefix="/api/demo", tags=["Demo Aanvragen"])


@router.post("/request", response_model=dict)
def create_demo_request(request: DemoRequestCreate, db: Session = Depends(get_db)):
    """Demo aanvraag opslaan als 'pending' en notificaties versturen.

    Admin moet handmatig goedkeuren via het portaal voordat er een account wordt aangemaakt.
    """

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
        status="pending",
        processed=False,
    )
    db.add(demo)
    db.commit()
    db.refresh(demo)

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
def demo_email_health():
    """Debug endpoint: test email configuratie zonder een echte demo aan te maken."""
    import os
    from email_service import RESEND_API_KEY, FROM_EMAIL, ADMIN_NOTIFICATION_EMAIL, FRONTEND_URL, PORTAAL_URL
    return {
        "resend_api_key_set": bool(RESEND_API_KEY),
        "resend_api_key_prefix": RESEND_API_KEY[:7] + "..." if RESEND_API_KEY else None,
        "from_email": FROM_EMAIL,
        "admin_notification_email": ADMIN_NOTIFICATION_EMAIL,
        "frontend_url": FRONTEND_URL,
        "portaal_url": PORTAAL_URL,
    }


@router.get("/requests", response_model=list[DemoRequestResponse])
def list_demo_requests(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Alle demo aanvragen ophalen (alleen voor admins)."""
    return db.query(DemoRequest).order_by(DemoRequest.created_at.desc()).all()
