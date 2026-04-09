from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from database import get_db
from models import User, Invitation, Organization
from schemas import (
    UserResponse, UserUpdate, InvitationCreate, InvitationResponse,
    AcceptInvitationRequest,
)
from auth import get_current_user, require_admin, hash_password
from email_service import send_invitation_email, send_welcome_email
import secrets

router = APIRouter(prefix="/api/users", tags=["Gebruikers"])


@router.get("/", response_model=list[UserResponse])
def list_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alle gebruikers van de organisatie ophalen."""
    return (
        db.query(User)
        .filter(User.organization_id == current_user.organization_id)
        .order_by(User.created_at)
        .all()
    )


@router.put("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    update: UserUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Gebruiker bijwerken (alleen admin)."""
    user = db.query(User).filter(
        User.id == user_id,
        User.organization_id == current_user.organization_id,
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")

    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}")
def deactivate_user(
    user_id: str,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Gebruiker deactiveren (alleen admin)."""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Je kunt jezelf niet deactiveren")

    user = db.query(User).filter(
        User.id == user_id,
        User.organization_id == current_user.organization_id,
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")

    user.is_active = False
    db.commit()
    return {"message": f"Gebruiker {user.email} is gedeactiveerd"}


# --- Uitnodigingen ---

@router.post("/invite", response_model=InvitationResponse)
def invite_user(
    invitation: InvitationCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Gebruiker uitnodigen (alleen admin)."""
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()

    # Check max users
    current_count = db.query(User).filter(
        User.organization_id == org.id,
        User.is_active == True,
    ).count()
    if current_count >= org.max_users:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum aantal gebruikers ({org.max_users}) bereikt voor uw abonnement",
        )

    # Check of email al bestaat
    existing = db.query(User).filter(User.email == invitation.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Dit e-mailadres is al in gebruik")

    # Check op bestaande uitnodiging
    existing_inv = db.query(Invitation).filter(
        Invitation.email == invitation.email,
        Invitation.organization_id == org.id,
        Invitation.accepted == False,
    ).first()
    if existing_inv:
        raise HTTPException(status_code=400, detail="Er is al een uitnodiging verstuurd naar dit e-mailadres")

    token = secrets.token_urlsafe(32)
    inv = Invitation(
        email=invitation.email,
        role=invitation.role,
        token=token,
        invited_by=current_user.id,
        organization_id=org.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)

    # Stuur uitnodiging email
    inviter_name = f"{current_user.first_name} {current_user.last_name}".strip()
    send_invitation_email(
        to_email=invitation.email,
        inviter_name=inviter_name,
        org_name=org.name,
        role=invitation.role.value if hasattr(invitation.role, 'value') else str(invitation.role),
        token=token,
    )

    return inv


@router.get("/invitations", response_model=list[InvitationResponse])
def list_invitations(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Alle uitnodigingen ophalen (alleen admin)."""
    return (
        db.query(Invitation)
        .filter(Invitation.organization_id == current_user.organization_id)
        .order_by(Invitation.created_at.desc())
        .all()
    )


@router.post("/accept-invitation", response_model=UserResponse)
def accept_invitation(
    request: AcceptInvitationRequest,
    db: Session = Depends(get_db),
):
    """Uitnodiging accepteren en account aanmaken."""
    inv = db.query(Invitation).filter(
        Invitation.token == request.token,
        Invitation.accepted == False,
    ).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Ongeldige of verlopen uitnodiging")

    if inv.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Deze uitnodiging is verlopen")

    # Maak gebruiker aan
    user = User(
        email=inv.email,
        hashed_password=hash_password(request.password),
        first_name=request.first_name,
        last_name=request.last_name,
        phone=request.phone,
        role=inv.role,
        is_org_admin=False,
        organization_id=inv.organization_id,
    )
    db.add(user)

    inv.accepted = True
    db.commit()
    db.refresh(user)

    # Stuur welkom email
    org = db.query(Organization).filter(Organization.id == inv.organization_id).first()
    send_welcome_email(
        to_email=user.email,
        user_name=f"{user.first_name} {user.last_name}".strip(),
        org_name=org.name if org else "",
    )

    return user
