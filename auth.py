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
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


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


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
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
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is gedeactiveerd")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Admin rechten vereist")
    return current_user
