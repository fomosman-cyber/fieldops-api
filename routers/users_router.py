from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from database import get_db
from models import User, Invitation, Organization
from schemas import (
    UserResponse, UserUpdate, InvitationCreate, InvitationResponse,
    AcceptInvitationRequest,
)
from auth import get_current_user, hash_password, validate_password_strength
from permissions import require_org_admin, list_assignable_roles
from email_service import send_invitation_email, send_welcome_email
from audit import log_action, ACTION
from models import UserRole
import secrets

router = APIRouter(prefix="/api/users", tags=["Gebruikers"])


@router.get("/roles")
def get_assignable_roles(current_user: User = Depends(get_current_user)):
    """Lijst toewijsbare rollen voor de UI (alleen admins krijgen niet-lege lijst)."""
    return list_assignable_roles(current_user)


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
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Gebruiker bijwerken (alleen admin)."""
    # Platform owner (FieldOps org) can edit ANY user cross-org
    if current_user.is_org_admin and current_user.organization and current_user.organization.name == "FieldOps":
        user = db.query(User).filter(User.id == user_id).first()
    else:
        user = db.query(User).filter(
            User.id == user_id,
            User.organization_id == current_user.organization_id,
        ).first()
    if not user:
        raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")

    update_data = update.model_dump(exclude_unset=True)
    role_before = user.role.value if user.role else None

    # Handle password update separately (needs hashing) — niet in audit-log
    password_changed = "password" in update_data
    if password_changed:
        new_pw = update_data.pop("password")
        validate_password_strength(new_pw)
        user.hashed_password = hash_password(new_pw)
    # Snapshot before/after voor de overige velden
    before = {k: getattr(user, k) for k in update_data.keys()
              if k not in ("password",)}
    # Coerce role-enum naar str voor logging
    if "role" in before and hasattr(before["role"], "value"):
        before["role"] = before["role"].value

    for field, value in update_data.items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)

    role_after = user.role.value if user.role else None
    if role_before != role_after:
        log_action(db, request, current_user,
                   action=ACTION.USER_ROLE_CHANGE, entity_type="user", entity_id=user.id,
                   before={"role": role_before}, after={"role": role_after})
    elif update_data:
        loggable_after = {k: (v.value if hasattr(v, "value") else v)
                          for k, v in update_data.items()}
        log_action(db, request, current_user,
                   action=ACTION.USER_UPDATE, entity_type="user", entity_id=user.id,
                   before=before, after=loggable_after)

    if password_changed:
        log_action(db, request, current_user,
                   action=ACTION.PASSWORD_CHANGED, entity_type="user", entity_id=user.id,
                   extra={"by_admin": True})

    return user


@router.delete("/{user_id}")
def deactivate_user(
    user_id: str,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Gebruiker deactiveren (alleen admin)."""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Je kunt jezelf niet deactiveren")

    # Platform owner can deactivate ANY user
    if current_user.is_org_admin and current_user.organization and current_user.organization.name == "FieldOps":
        user = db.query(User).filter(User.id == user_id).first()
    else:
        user = db.query(User).filter(
            User.id == user_id,
            User.organization_id == current_user.organization_id,
        ).first()
    if not user:
        raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")

    user.is_active = False
    db.commit()
    log_action(db, request, current_user,
               action=ACTION.USER_DEACTIVATE, entity_type="user", entity_id=user.id,
               before={"is_active": True, "email": user.email})
    return {"message": f"Gebruiker {user.email} is gedeactiveerd"}


# --- Direct account aanmaken (admin) ---

class AdminCreateUser(BaseModel):
    email: str
    password: str
    first_name: str
    last_name: str
    role: str = "viewer"
    phone: str = ""

@router.post("/create", response_model=UserResponse)
def admin_create_user(
    data: AdminCreateUser,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Direct een gebruiker aanmaken (alleen admin). Geen uitnodiging nodig."""
    validate_password_strength(data.password)
    existing = db.query(User).filter(User.email == data.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Dit e-mailadres is al in gebruik")

    # Valideer rol: moet één van de bekende UserRole-waarden zijn
    try:
        role_enum = UserRole(data.role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Ongeldige rol: {data.role}")

    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        first_name=data.first_name,
        last_name=data.last_name,
        phone=data.phone,
        role=role_enum,
        is_org_admin=(role_enum == UserRole.ADMIN),
        organization_id=current_user.organization_id,
        must_change_password=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log_action(db, request, current_user,
               action=ACTION.USER_CREATE, entity_type="user", entity_id=user.id,
               after={"email": user.email, "role": data.role,
                      "organization_id": user.organization_id})

    # Stuur welkom email
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    try:
        send_welcome_email(data.email, f"{data.first_name} {data.last_name}", org.name if org else "FieldOps")
    except Exception as e:
        print(f"Welcome email error: {e}")

    return user


# --- Uitnodigingen ---

@router.post("/invite", response_model=InvitationResponse)
def invite_user(
    invitation: InvitationCreate,
    request: Request,
    current_user: User = Depends(require_org_admin),
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

    log_action(db, request, current_user,
               action=ACTION.USER_INVITE, entity_type="invitation", entity_id=inv.id,
               after={"email": invitation.email,
                      "role": invitation.role.value if hasattr(invitation.role, 'value') else str(invitation.role)})

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
    current_user: User = Depends(require_org_admin),
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
