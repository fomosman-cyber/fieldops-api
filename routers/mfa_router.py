"""MFA / 2FA endpoints — TOTP (RFC 6238) via authenticator-apps.

Flow:
  1. User → POST /api/mfa/setup → krijg secret + QR-code base64 (eenmalig)
  2. User scant QR in Google Authenticator / Authy / 1Password
  3. User → POST /api/mfa/verify {code} → enabled=True + 10 backup-codes
  4. Bij login: na password-OK check of mfa_enabled. Zo ja, vraag TOTP-code.
  5. User → POST /api/mfa/disable {code} → secret wist, backup-codes vervalt

Backup-codes:
  - 10 codes van 8 chars (alfanumeriek, hyphen na 4)
  - Single-use: na gebruik wordt de hash uit lijst verwijderd
  - bcrypt-gehasht op disk (zelfde als wachtwoorden)
  - Hervatten via /api/mfa/backup-codes/regenerate (oude vervalt)

Audit-events:
  mfa.setup_initiated, mfa.enabled, mfa.disabled, mfa.backup_code_used,
  mfa.verify_failed, mfa.backup_codes_regenerated
"""
from datetime import datetime, timezone
import secrets
import json
import io
import base64
from typing import Optional, List

import pyotp
import qrcode
import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from database import get_db
from models import User
from auth import get_current_user
from audit import log_action

router = APIRouter(prefix="/api/mfa", tags=["2FA"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class MfaSetupResponse(BaseModel):
    secret: str  # base32 voor manual entry
    qr_code_data_url: str  # data:image/png;base64,...
    otpauth_url: str  # voor URI-import
    issuer: str = "FieldOps"


class MfaVerifyRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=10)


class MfaDisableRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=10)  # backup-code of TOTP


class MfaVerifyResponse(BaseModel):
    enabled: bool
    backup_codes: Optional[List[str]] = None  # 1x getoond bij first-enable


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _generate_backup_codes(count: int = 10) -> List[str]:
    """Generate display-friendly backup codes (8 chars, alfanumeriek)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # geen I/O/0/1 (ambiguiteit)
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        codes.append(raw[:4] + "-" + raw[4:])  # bv. "X3FK-N7QP"
    return codes


def _hash_codes(codes: List[str]) -> str:
    """Hash backup-codes met bcrypt. Stored als JSON-array."""
    hashes = [
        bcrypt.hashpw(c.replace("-", "").upper().encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        for c in codes
    ]
    return json.dumps(hashes)


def _consume_backup_code(user: User, plain_code: str) -> bool:
    """Verifieer + verwijder backup-code uit gebruikers-lijst.

    Returns True bij geldige (en consumeerde) code.
    """
    if not user.mfa_backup_codes:
        return False
    try:
        hashes = json.loads(user.mfa_backup_codes)
    except Exception:
        return False
    normalized = plain_code.replace("-", "").replace(" ", "").upper().encode("utf-8")
    for i, h in enumerate(hashes):
        try:
            if bcrypt.checkpw(normalized, h.encode("utf-8")):
                # Code geldig: verwijder uit lijst (single-use)
                hashes.pop(i)
                user.mfa_backup_codes = json.dumps(hashes)
                return True
        except Exception:
            continue
    return False


def verify_mfa_code(user: User, code: str) -> tuple[bool, str]:
    """Verifieer TOTP-code OF backup-code. Returns (ok, method)."""
    if not user.mfa_enabled or not user.mfa_secret:
        return (False, "not_enabled")
    code = code.strip().replace(" ", "")
    # Try TOTP eerst (6 cijfers)
    if len(code) == 6 and code.isdigit():
        try:
            totp = pyotp.TOTP(user.mfa_secret)
            if totp.verify(code, valid_window=1):  # ±30s drift toegestaan
                return (True, "totp")
        except Exception:
            pass
    # Try backup-code (format XXXX-XXXX)
    if _consume_backup_code(user, code):
        return (True, "backup_code")
    return (False, "invalid")


def _make_qr_data_url(otpauth_url: str) -> str:
    """Generate QR-code PNG als data-URL."""
    qr = qrcode.QRCode(version=1, box_size=8, border=2,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(otpauth_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/status")
def mfa_status(
    current_user: User = Depends(get_current_user),
):
    """Geeft 2FA-status van de huidige gebruiker."""
    backup_codes_remaining = 0
    if current_user.mfa_backup_codes:
        try:
            backup_codes_remaining = len(json.loads(current_user.mfa_backup_codes))
        except Exception:
            pass
    return {
        "enabled": bool(current_user.mfa_enabled),
        "enabled_at": current_user.mfa_enabled_at.isoformat() if current_user.mfa_enabled_at else None,
        "backup_codes_remaining": backup_codes_remaining,
    }


@router.post("/setup", response_model=MfaSetupResponse)
def setup_mfa(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Initieer 2FA-setup. Genereert een nieuwe secret + QR-code.

    Idempotent: kan opnieuw worden aangeroepen om nieuwe secret te krijgen
    (oude wordt overschreven). Pas na /verify wordt mfa_enabled=True gezet.
    """
    # Genereer base32 secret (160-bit, RFC 6238)
    secret = pyotp.random_base32()
    current_user.mfa_secret = secret
    current_user.mfa_enabled = False  # pas na /verify
    db.commit()

    # otpauth-URL voor QR. Issuer = FieldOps.
    issuer = "FieldOps"
    account = current_user.email
    otpauth_url = pyotp.totp.TOTP(secret).provisioning_uri(
        name=account, issuer_name=issuer,
    )
    qr_data_url = _make_qr_data_url(otpauth_url)

    log_action(db, request, current_user, action="mfa.setup_initiated",
               entity_type="user", entity_id=current_user.id)
    return MfaSetupResponse(
        secret=secret,
        qr_code_data_url=qr_data_url,
        otpauth_url=otpauth_url,
        issuer=issuer,
    )


@router.post("/verify", response_model=MfaVerifyResponse)
def verify_and_enable_mfa(
    payload: MfaVerifyRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verifieer eerste TOTP-code en activeer 2FA. Returnt 10 backup-codes (1x)."""
    if not current_user.mfa_secret:
        raise HTTPException(400, "Geen MFA-setup gestart — call /api/mfa/setup eerst")

    code = payload.code.strip().replace(" ", "")
    if not (len(code) == 6 and code.isdigit()):
        raise HTTPException(400, "Code moet 6 cijfers zijn")

    try:
        totp = pyotp.TOTP(current_user.mfa_secret)
        if not totp.verify(code, valid_window=1):
            log_action(db, request, current_user, action="mfa.verify_failed",
                       entity_type="user", entity_id=current_user.id)
            raise HTTPException(400, "Ongeldige code — controleer je authenticator-app + tijd-sync")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Ongeldige code")

    # Activeer + genereer backup-codes
    backup_codes = _generate_backup_codes(10)
    current_user.mfa_enabled = True
    current_user.mfa_enabled_at = datetime.now(timezone.utc)
    current_user.mfa_backup_codes = _hash_codes(backup_codes)
    db.commit()

    log_action(db, request, current_user, action="mfa.enabled",
               entity_type="user", entity_id=current_user.id)
    return MfaVerifyResponse(enabled=True, backup_codes=backup_codes)


@router.post("/disable")
def disable_mfa(
    payload: MfaDisableRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Schakel 2FA uit. Vereist huidige TOTP-code of backup-code."""
    if not current_user.mfa_enabled:
        return {"disabled": True, "already_disabled": True}

    ok, method = verify_mfa_code(current_user, payload.code)
    if not ok:
        log_action(db, request, current_user, action="mfa.disable_failed",
                   entity_type="user", entity_id=current_user.id)
        raise HTTPException(400, "Ongeldige code")

    current_user.mfa_enabled = False
    current_user.mfa_secret = None
    current_user.mfa_backup_codes = None
    current_user.mfa_enabled_at = None
    db.commit()
    log_action(db, request, current_user, action="mfa.disabled",
               entity_type="user", entity_id=current_user.id,
               extra={"verify_method": method})
    return {"disabled": True}


@router.post("/backup-codes/regenerate")
def regenerate_backup_codes(
    payload: MfaVerifyRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genereer nieuwe set van 10 backup-codes. Oude vervalt direct.

    Vereist huidige TOTP-code (geen backup-code — anders kan een gestolen
    backup-code de hele set vervangen).
    """
    if not current_user.mfa_enabled:
        raise HTTPException(400, "2FA staat niet aan")

    code = payload.code.strip().replace(" ", "")
    if not (len(code) == 6 and code.isdigit()):
        raise HTTPException(400, "Vereist 6-cijferige TOTP-code (geen backup-code)")
    totp = pyotp.TOTP(current_user.mfa_secret)
    if not totp.verify(code, valid_window=1):
        raise HTTPException(400, "Ongeldige TOTP-code")

    new_codes = _generate_backup_codes(10)
    current_user.mfa_backup_codes = _hash_codes(new_codes)
    db.commit()
    log_action(db, request, current_user, action="mfa.backup_codes_regenerated",
               entity_type="user", entity_id=current_user.id)
    return {"backup_codes": new_codes}
