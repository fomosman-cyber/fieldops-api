from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta
from database import get_db
from models import User
from schemas import LoginRequest, TokenResponse, UserResponse, PasswordResetRequest, PasswordResetConfirm
from auth import verify_password, create_access_token, get_current_user, hash_password
from email_service import send_password_reset_email
import secrets

router = APIRouter(prefix="/api/auth", tags=["Authenticatie"])

# In-memory store voor password reset tokens: {token: {"email": ..., "expires": ...}}
_reset_tokens: dict[str, dict] = {}


@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == request.email).first()
    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Onjuist e-mailadres of wachtwoord")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is gedeactiveerd")

    # Check of organisatie nog actief is
    org = user.organization
    if org.status == "expired":
        raise HTTPException(status_code=403, detail="Uw proefperiode is verlopen. Neem een abonnement.")
    if org.status == "suspended":
        raise HTTPException(status_code=403, detail="Account is opgeschort. Neem contact op met support.")

    user.last_login = datetime.now(timezone.utc)
    db.commit()

    token = create_access_token(data={"sub": user.id, "org": user.organization_id, "role": user.role.value})

    return TokenResponse(
        access_token=token,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/reset-password-request")
def reset_password_request(request: PasswordResetRequest, db: Session = Depends(get_db)):
    """Genereer een wachtwoord-reset token voor het opgegeven e-mailadres."""
    user = db.query(User).filter(User.email == request.email).first()

    # Altijd hetzelfde bericht teruggeven (voorkomt email enumeration)
    success_msg = {"message": "Als dit e-mailadres bij ons bekend is, ontvangt u een reset-link."}

    if not user:
        return success_msg

    # Genereer token
    token = secrets.token_urlsafe(48)
    _reset_tokens[token] = {
        "email": user.email,
        "expires": datetime.now(timezone.utc) + timedelta(hours=1),
    }

    # Cleanup verlopen tokens
    now = datetime.now(timezone.utc)
    expired = [t for t, data in _reset_tokens.items() if data["expires"] < now]
    for t in expired:
        del _reset_tokens[t]

    # Stuur e-mail met reset link
    user_name = f"{user.first_name} {user.last_name}".strip()
    send_password_reset_email(to_email=user.email, token=token, user_name=user_name)

    return success_msg


@router.post("/reset-password")
def reset_password(request: PasswordResetConfirm, db: Session = Depends(get_db)):
    """Stel een nieuw wachtwoord in met een geldig reset-token."""
    token_data = _reset_tokens.get(request.token)
    if not token_data:
        raise HTTPException(status_code=400, detail="Ongeldige of verlopen reset-link.")

    # Check expiry
    if datetime.now(timezone.utc) > token_data["expires"]:
        del _reset_tokens[request.token]
        raise HTTPException(status_code=400, detail="Deze reset-link is verlopen. Vraag een nieuwe aan.")

    # Validatie nieuw wachtwoord
    if len(request.new_password) < 8:
        raise HTTPException(status_code=400, detail="Wachtwoord moet minimaal 8 tekens bevatten.")

    # Update wachtwoord
    user = db.query(User).filter(User.email == token_data["email"]).first()
    if not user:
        raise HTTPException(status_code=400, detail="Gebruiker niet gevonden.")

    user.hashed_password = hash_password(request.new_password)
    db.commit()

    # Verwijder gebruikte token
    del _reset_tokens[request.token]

    return {"message": "Wachtwoord succesvol gewijzigd. U kunt nu inloggen met uw nieuwe wachtwoord."}
