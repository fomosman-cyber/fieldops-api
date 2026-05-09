from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from database import get_db
from models import User
from dotenv import load_dotenv
import os

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

if not SECRET_KEY or SECRET_KEY in ("fieldops-secret-key", "fieldops-secret-key-change-in-production-2024"):
    if os.getenv("RENDER") or os.getenv("ENV") == "production":
        raise RuntimeError("SECRET_KEY env variabele moet gezet zijn in productie")
    SECRET_KEY = "dev-only-not-for-production"
    print("[WARN] SECRET_KEY niet gezet — dev fallback actief")

security = HTTPBearer()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    # bcrypt.checkpw raised ValueError op een corrupte/onbruikbare hash
    # (bv. een geanonymiseerd account met placeholder-hash). In dat geval
    # is de juiste uitkomst False, niet een 500 voor de gebruiker.
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"),
                              hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Password complexity — server-side gate naast UI minlength=8
# Compliance-audit (Rekenkamer/ISO27001) kijkt of dit op de backend zit, niet
# alleen in HTML-attributes. NIST 800-63B: min 8 chars + niet uit common-list.
# We doen length + variation; de common-list-check is roadmap (zxcvbn-lite).
# ─────────────────────────────────────────────────────────────────────────────

MIN_PASSWORD_LENGTH = 8


def validate_password_strength(password: str) -> None:
    """Raise HTTPException 400 als wachtwoord te zwak is. Eisen:

    - minimaal 8 tekens
    - bevat 3 van de 4 categorieën: lowercase, uppercase, digit, symbol

    Voor admin-flows (admin maakt user aan met eenmalig wachtwoord) en
    self-service password change/reset.
    """
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Wachtwoord moet minimaal {MIN_PASSWORD_LENGTH} tekens bevatten.",
        )
    has_lower = any(c.islower() for c in password)
    has_upper = any(c.isupper() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_sym = any(not c.isalnum() for c in password)
    categories = sum([has_lower, has_upper, has_digit, has_sym])
    if categories < 3:
        raise HTTPException(
            status_code=400,
            detail="Wachtwoord moet 3 van de 4 bevatten: kleine letter, hoofdletter, cijfer, symbool.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Login brute-force rate limiting via audit-log
# Geen extra dep — gebruik bestaande append-only audit_logs tabel als bron.
# Werkt over restarts heen (vs in-memory) en is per-organisatie auditable.
# ─────────────────────────────────────────────────────────────────────────────

LOGIN_RATE_LIMIT_WINDOW_MIN = 15
LOGIN_RATE_LIMIT_PER_EMAIL = 5      # bestaande accounts: strikt
LOGIN_RATE_LIMIT_PER_IP = 20        # NAT/kantoor: ruimer


def _extract_client_ip(request) -> Optional[str]:
    """X-Forwarded-For-aware client-IP. Render/Cloudflare zetten de header."""
    if request is None:
        return None
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def check_login_rate_limit(db: Session, email: str, request=None) -> None:
    """Raise 429 als deze email of dit IP recent te vaak gefaald is.

    Per-email blokkade greft op `audit_logs.user_email` — dat wordt gevuld
    zodra de email-lookup een user oplevert. Voor onbekende emails valt de
    bescherming terug op de per-IP-limit.
    """
    from datetime import datetime, timezone, timedelta
    from models import AuditLog

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=LOGIN_RATE_LIMIT_WINDOW_MIN)
    failed_action = "auth.login.failed"

    if email:
        per_email = db.query(AuditLog).filter(
            AuditLog.action == failed_action,
            AuditLog.user_email == email,
            AuditLog.created_at >= cutoff,
        ).count()
        if per_email >= LOGIN_RATE_LIMIT_PER_EMAIL:
            raise HTTPException(
                status_code=429,
                detail=f"Te veel mislukte pogingen voor dit account. Probeer over {LOGIN_RATE_LIMIT_WINDOW_MIN} minuten opnieuw of reset uw wachtwoord.",
            )

    ip_address = _extract_client_ip(request)
    if ip_address:
        per_ip = db.query(AuditLog).filter(
            AuditLog.action == failed_action,
            AuditLog.ip_address == ip_address,
            AuditLog.created_at >= cutoff,
        ).count()
        if per_ip >= LOGIN_RATE_LIMIT_PER_IP:
            raise HTTPException(
                status_code=429,
                detail=f"Te veel mislukte pogingen vanaf dit netwerk. Probeer over {LOGIN_RATE_LIMIT_WINDOW_MIN} minuten opnieuw.",
            )


# Reset-token spam — voorkomt dat iemand de inbox van een gebruiker volgooit
# of het email-quotum opbrandt door eindeloos reset-mails te triggeren.
PASSWORD_RESET_WINDOW_MIN = 60
PASSWORD_RESET_PER_EMAIL = 3
PASSWORD_RESET_PER_IP = 10


def check_password_reset_rate_limit(db: Session, email: str, request=None) -> None:
    """Raise 429 als deze email of IP recent te veel reset-aanvragen deed."""
    from datetime import datetime, timezone, timedelta
    from models import AuditLog

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=PASSWORD_RESET_WINDOW_MIN)
    action = "auth.password.reset_requested"

    if email:
        per_email = db.query(AuditLog).filter(
            AuditLog.action == action,
            AuditLog.user_email == email,
            AuditLog.created_at >= cutoff,
        ).count()
        if per_email >= PASSWORD_RESET_PER_EMAIL:
            raise HTTPException(
                status_code=429,
                detail=f"Te veel reset-aanvragen voor dit account. Probeer over {PASSWORD_RESET_WINDOW_MIN} minuten opnieuw.",
            )

    ip_address = _extract_client_ip(request)
    if ip_address:
        per_ip = db.query(AuditLog).filter(
            AuditLog.action == action,
            AuditLog.ip_address == ip_address,
            AuditLog.created_at >= cutoff,
        ).count()
        if per_ip >= PASSWORD_RESET_PER_IP:
            raise HTTPException(
                status_code=429,
                detail=f"Te veel reset-aanvragen vanaf dit netwerk. Probeer over {PASSWORD_RESET_WINDOW_MIN} minuten opnieuw.",
            )


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    # iat (issued-at) — gebruikt door get_current_user om tokens te
    # revokken die zijn uitgegeven vóór tokens_invalidated_at op de user.
    to_encode.update({"exp": expire, "iat": now})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Ongeldige token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Ongeldige token")

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Gebruiker niet gevonden")

    # Token-revocation: als de user na uitgifte van dit token z'n sessies
    # heeft geïnvalideerd (anonymisatie / password change / deactivatie),
    # is dit token niet meer geldig. Check vóór is_active zodat we het
    # juiste signaal teruggeven (401 invalid token, niet 403 deactivated).
    # iat ontbreekt op pre-3.4-tokens; dan blijft het oude gedrag (TTL als
    # enige check) — die expireren binnen 24u.
    invalidated_at = getattr(user, "tokens_invalidated_at", None)
    iat_ts = payload.get("iat")
    if invalidated_at is not None and iat_ts is not None:
        try:
            iat_dt = datetime.fromtimestamp(int(iat_ts), tz=timezone.utc)
        except (TypeError, ValueError):
            iat_dt = None
        if iat_dt is not None:
            inv_dt = invalidated_at
            if inv_dt.tzinfo is None:
                inv_dt = inv_dt.replace(tzinfo=timezone.utc)
            if iat_dt < inv_dt:
                raise HTTPException(status_code=401,
                    detail="Sessie is ongeldig — log opnieuw in.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is gedeactiveerd")
    return user


def invalidate_user_sessions(user: User) -> None:
    """Markeer alle uitstaande JWTs van deze user als ongeldig.

    Aanroepen bij wachtwoordwijziging, account-anonymisatie en admin-
    deactivatie. De caller moet zelf db.commit() doen — wij muteren alleen
    het user-object zodat de mutatie in de bestaande transactie zit.

    Implementatie-detail: JWT iat heeft seconde-precisie. We zetten
    tokens_invalidated_at op het einde van de huidige seconde (now ceiled
    naar volgende seconde) zodat:
      - alle bestaande tokens (iat = vorige seconde of eerder) revoked zijn;
      - nieuwe logins vanaf de volgende seconde direct geldig zijn.
    Het gevolg is een ~1-seconde window waarin een nieuwe login direct na
    revocation alsnog faalt — irrelevant in productie (UI-redirect duurt
    langer dan dat) maar tests moeten een sleep(1.1) toevoegen.
    """
    now = datetime.now(timezone.utc)
    if now.microsecond == 0:
        ceil = now + timedelta(seconds=1)
    else:
        ceil = now.replace(microsecond=0) + timedelta(seconds=1)
    user.tokens_invalidated_at = ceil


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Admin rechten vereist")
    return current_user
