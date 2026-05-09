from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta
import hashlib
import secrets

from database import get_db
from models import User, PasswordResetToken
from schemas import LoginRequest, TokenResponse, UserResponse, PasswordResetRequest, PasswordResetConfirm
from auth import verify_password, create_access_token, get_current_user, hash_password, validate_password_strength, check_login_rate_limit, check_password_reset_rate_limit
from email_service import send_password_reset_email
from audit import log_action, ACTION

router = APIRouter(prefix="/api/auth", tags=["Authenticatie"])


def _hash_token(token: str) -> str:
    """SHA-256 van de raw token. Alleen de hash zit in DB; de raw waarde gaat
    één keer per email naar de gebruiker. Lekt de DB, dan zijn tokens niet bruikbaar."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, http_request: Request, db: Session = Depends(get_db)):
    # Brute-force gate: bevraagt audit_logs voor recent gefaalde pogingen.
    # Werpt 429 zodat client weet dat het rate-limit is, niet credential-issue.
    check_login_rate_limit(db, payload.email, http_request)

    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        # user wordt meegegeven (kan None zijn): bij bestaande email vult dat
        # user_email in op de audit-rij, zodat de rate-limit-query exact werkt.
        log_action(db, http_request, user,
                   action=ACTION.LOGIN_FAILED,
                   entity_type="user", entity_id=user.id if user else None,
                   extra={"email": payload.email, "reason": "invalid_credentials"})
        raise HTTPException(status_code=401, detail="Onjuist e-mailadres of wachtwoord")
    if not user.is_active:
        log_action(db, http_request, user,
                   action=ACTION.LOGIN_FAILED,
                   entity_type="user", entity_id=user.id,
                   extra={"reason": "deactivated"})
        raise HTTPException(status_code=403, detail="Account is gedeactiveerd")

    org = user.organization
    if org.status == "expired":
        raise HTTPException(status_code=403, detail="Uw proefperiode is verlopen. Neem een abonnement.")
    if org.status == "suspended":
        raise HTTPException(status_code=403, detail="Account is opgeschort. Neem contact op met support.")

    user.last_login = datetime.now(timezone.utc)
    db.commit()

    token = create_access_token(data={"sub": user.id, "org": user.organization_id, "role": user.role.value})
    log_action(db, http_request, user, action=ACTION.LOGIN_SUCCESS, entity_type="user", entity_id=user.id)

    return TokenResponse(
        access_token=token,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/reset-password-request")
def reset_password_request(payload: PasswordResetRequest, http_request: Request, db: Session = Depends(get_db)):
    """Genereer een wachtwoord-reset token en stuur deze per email.

    - Token wordt gehasht in DB opgeslagen (geen plaintext)
    - Tokens 1 uur geldig
    - Verlopen + gebruikte tokens periodiek opgeruimd
    - Antwoord is altijd hetzelfde (voorkomt email enumeration)
    """
    success_msg = {"message": "Als dit e-mailadres bij ons bekend is, ontvangt u een reset-link."}

    # Rate-limit vóór de DB-lookup zodat we niet onnodig DB-werk doen voor
    # spam. Werpt 429 zodat een aanvaller weet dat er een limiet is — dat
    # is acceptable; de timing-side-channel die anti-enumeration beschermt
    # blijft intact bij rate-limit hits.
    check_password_reset_rate_limit(db, payload.email, http_request)

    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        # Log onbekende email-pogingen ook (voor enumeration-detectie)
        log_action(db, http_request, None,
                   action=ACTION.PASSWORD_RESET_REQ,
                   extra={"email": payload.email, "user_found": False})
        return success_msg

    # Cleanup: verwijder verlopen + gebruikte tokens (eenvoudig housekeeping)
    now = datetime.now(timezone.utc)
    db.query(PasswordResetToken).filter(
        (PasswordResetToken.expires_at < now) | (PasswordResetToken.used_at.isnot(None))
    ).delete(synchronize_session=False)

    raw_token = secrets.token_urlsafe(48)
    db.add(PasswordResetToken(
        token_hash=_hash_token(raw_token),
        user_id=user.id,
        expires_at=now + timedelta(hours=1),
    ))
    db.commit()

    log_action(db, http_request, user,
               action=ACTION.PASSWORD_RESET_REQ,
               entity_type="user", entity_id=user.id,
               extra={"user_found": True})

    user_name = f"{user.first_name} {user.last_name}".strip()
    send_password_reset_email(to_email=user.email, token=raw_token, user_name=user_name)

    return success_msg


@router.post("/reset-password")
def reset_password(payload: PasswordResetConfirm, http_request: Request, db: Session = Depends(get_db)):
    """Stel een nieuw wachtwoord in met een geldig reset-token."""
    validate_password_strength(payload.new_password)

    token_hash = _hash_token(payload.token)
    rec = db.query(PasswordResetToken).filter(PasswordResetToken.token_hash == token_hash).first()

    if not rec or rec.used_at is not None:
        raise HTTPException(status_code=400, detail="Ongeldige of al gebruikte reset-link.")
    if rec.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Deze reset-link is verlopen. Vraag een nieuwe aan.")

    user = db.query(User).filter(User.id == rec.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="Gebruiker niet gevonden.")

    user.hashed_password = hash_password(payload.new_password)
    if hasattr(user, "must_change_password"):
        user.must_change_password = False
    rec.used_at = datetime.now(timezone.utc)
    db.commit()

    log_action(db, http_request, user,
               action=ACTION.PASSWORD_RESET_DONE,
               entity_type="user", entity_id=user.id)

    return {"message": "Wachtwoord succesvol gewijzigd. U kunt nu inloggen met uw nieuwe wachtwoord."}
