"""Google OAuth + Calendar/Drive endpoints.

Flow:
  1. User klikt "Verbind Google" in portaal
  2. /api/google/oauth/start → 302 naar Google consent screen
  3. Google redirect naar /api/google/oauth/callback → tokens opgeslagen
  4. User terug naar portaal met success-flag

Status-endpoint /api/google/status vertelt frontend of user verbonden is.
"""

import secrets as _secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from models import GoogleOAuthToken, User
from auth import get_current_user, SECRET_KEY, ALGORITHM
from audit import log_action
import google_integration as gi
from jose import jwt, JWTError

router = APIRouter(prefix="/api/google", tags=["Google"])

# State-token: korte JWT met user_id + nonce, signed met SECRET_KEY.
# Voorkomt CSRF en zorgt dat de callback weet voor welke user-tokens te bewaren.
STATE_TTL_SECONDS = 600


def _make_state(user_id: str) -> str:
    return jwt.encode({
        "sub": user_id, "nonce": _secrets.token_urlsafe(16),
        "exp": datetime.now(timezone.utc) + timedelta(seconds=STATE_TTL_SECONDS),
    }, SECRET_KEY, algorithm=ALGORITHM)


def _read_state(state: str) -> Optional[str]:
    try:
        payload = jwt.decode(state, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


@router.get("/status")
def status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not gi.is_configured():
        return {"configured": False, "connected": False}
    tok = db.query(GoogleOAuthToken).filter(GoogleOAuthToken.user_id == current_user.id).first()
    if not tok or tok.revoked_at:
        return {"configured": True, "connected": False}
    return {
        "configured": True, "connected": True,
        "google_email": tok.google_email,
        "scopes": (tok.scope or "").split(),
        "default_calendar_id": tok.default_calendar_id,
    }


@router.get("/oauth/start")
def oauth_start(
    current_user: User = Depends(get_current_user),
):
    """Returnt de Google consent-URL als JSON. Frontend doet zelf
    `window.location.href = url` zodat de gebruiker geredirect wordt.
    Token-state vervalt na 10 minuten — XSRF-bescherming."""
    if not gi.is_configured():
        raise HTTPException(status_code=503, detail="Google OAuth niet geconfigureerd op de server")
    state = _make_state(current_user.id)
    return {"auth_url": gi.make_auth_url(state)}


@router.get("/oauth/callback")
def oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
):
    """Public callback — geen auth-header nodig. State-token bevat user-id."""
    user_id = _read_state(state)
    if not user_id:
        raise HTTPException(status_code=400, detail="Ongeldige of verlopen state-token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Gebruiker niet meer aanwezig")

    try:
        tokens = gi.exchange_code(code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Token-uitwisseling faalde: {e}")

    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    if not access:
        raise HTTPException(status_code=400, detail="Geen access_token van Google ontvangen")

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(tokens.get("expires_in", 3600)))

    google_email = None
    try:
        google_email = gi.fetch_userinfo(access).get("email")
    except Exception:
        pass

    existing = db.query(GoogleOAuthToken).filter(GoogleOAuthToken.user_id == user.id).first()
    if existing:
        existing.access_token = access
        if refresh:
            existing.refresh_token = refresh
        existing.expires_at = expires_at
        existing.scope = tokens.get("scope", existing.scope)
        existing.google_email = google_email or existing.google_email
        existing.revoked_at = None
        rec = existing
    else:
        rec = GoogleOAuthToken(
            user_id=user.id, organization_id=user.organization_id,
            access_token=access, refresh_token=refresh,
            expires_at=expires_at, scope=tokens.get("scope"),
            google_email=google_email,
        )
        db.add(rec)
    db.commit()

    log_action(db, None, user, action="google.oauth.connected",
               entity_type="google_oauth_token", entity_id=rec.id,
               extra={"google_email": google_email})

    return RedirectResponse(url="/portaal?google=connected", status_code=302)


@router.post("/disconnect")
def disconnect(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rec = db.query(GoogleOAuthToken).filter(GoogleOAuthToken.user_id == current_user.id).first()
    if not rec:
        return {"connected": False}
    rec.revoked_at = datetime.now(timezone.utc)
    db.commit()
    log_action(db, request, current_user, action="google.oauth.disconnected",
               entity_type="google_oauth_token", entity_id=rec.id)
    return {"connected": False}


@router.post("/drive/upload")
async def drive_upload(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload een PDF/blob naar de FieldOps-folder in user's Google Drive.

    Body: multipart met 'file' veld. Returnt {file_id, web_view_link}.
    """
    form = await request.form()
    f = form.get("file")
    if f is None or not hasattr(f, "filename"):
        raise HTTPException(status_code=400, detail="Geen file in multipart-payload")

    rec = db.query(GoogleOAuthToken).filter(GoogleOAuthToken.user_id == current_user.id).first()
    access = gi.ensure_fresh_token(db, rec)
    if not access:
        raise HTTPException(status_code=400, detail="Niet verbonden met Google. Verbind eerst.")

    content = await f.read()
    if not content:
        raise HTTPException(status_code=400, detail="Leeg bestand")
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Bestand te groot (max 50 MB)")

    try:
        if not rec.default_drive_folder_id:
            rec.default_drive_folder_id = gi.drive_ensure_folder(access)
            db.commit()
        result = gi.drive_upload(
            access, f.filename or "fieldops-export.pdf",
            content, f.content_type or "application/octet-stream",
            folder_id=rec.default_drive_folder_id,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Drive-upload faalde: {e}")

    log_action(db, request, current_user, action="google.drive.upload",
               entity_type="drive_file", entity_id=result.get("id"),
               extra={"filename": f.filename, "size": len(content)})
    return {
        "file_id": result.get("id"),
        "name": result.get("name"),
        "web_view_link": result.get("webViewLink"),
    }


@router.post("/test-event")
def create_test_event(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Maak een test-event in de standaard agenda om de koppeling te valideren."""
    rec = db.query(GoogleOAuthToken).filter(GoogleOAuthToken.user_id == current_user.id).first()
    access = gi.ensure_fresh_token(db, rec)
    if not access:
        raise HTTPException(status_code=400, detail="Niet verbonden met Google. Verbind eerst.")
    today_dt = datetime.now(timezone.utc).date()
    today = today_dt.isoformat()
    tomorrow = (today_dt + timedelta(days=1)).isoformat()
    payload = {
        "summary": "FieldOps: testkoppeling werkt",
        "description": "Dit event is automatisch aangemaakt vanuit FieldOps om de Google-Calendar-integratie te testen. Veilig om te verwijderen.",
        # Google Calendar all-day events: end.date is exclusive en moet strikt > start.date
        "start": {"date": today}, "end": {"date": tomorrow},
    }
    try:
        ev = gi.calendar_create_event(access, rec.default_calendar_id or "primary", payload)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Calendar-call faalde: {e}")
    log_action(db, request, current_user, action="google.calendar.test_event",
               entity_type="calendar_event", entity_id=ev.get("id"))
    return {"event_id": ev.get("id"), "html_link": ev.get("htmlLink")}
