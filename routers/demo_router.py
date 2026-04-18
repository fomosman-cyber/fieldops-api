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

    # Verstuur emails (best effort, errors blokkeren submit niet)
    try:
        from email_service import send_demo_admin_notification, send_demo_confirmation

        send_demo_admin_notification(demo)
        send_demo_confirmation(demo)
    except Exception as e:
        print(f"[DEMO] Email error: {e}")

    return {
        "success": True,
        "message": "Bedankt voor uw aanvraag! We nemen binnen 1 werkdag contact met u op.",
    }


@router.get("/requests", response_model=list[DemoRequestResponse])
def list_demo_requests(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Alle demo aanvragen ophalen (alleen voor admins)."""
    return db.query(DemoRequest).order_by(DemoRequest.created_at.desc()).all()
