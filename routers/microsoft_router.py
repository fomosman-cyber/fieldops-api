"""Microsoft 365 OAuth + Outlook Calendar + OneDrive endpoints.

Flow identiek aan Google:
  1. User klikt "Verbind Microsoft" in portaal
  2. /api/microsoft/oauth/start → returnt JSON met auth_url
  3. Frontend doet window.location.href = auth_url
  4. Microsoft redirect naar /api/microsoft/oauth/callback → tokens opgeslagen
  5. User terug naar /portaal?microsoft=connected

Status-endpoint /api/microsoft/status vertelt frontend of user verbonden is.
"""

import secrets as _secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query, UploadFile, File
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from models import MicrosoftOAuthToken, MicrosoftCalendarLink, User, Melding
from auth import get_current_user, SECRET_KEY, ALGORITHM
from audit import log_action
import microsoft_integration as ms
from jose import jwt, JWTError

router = APIRouter(prefix="/api/microsoft", tags=["Microsoft"])

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
    """Of de huidige user gekoppeld is aan Microsoft 365."""
    if not ms.is_configured():
        return {"configured": False, "connected": False}
    tok = db.query(MicrosoftOAuthToken).filter(
        MicrosoftOAuthToken.user_id == current_user.id
    ).first()
    if not tok or tok.revoked_at:
        return {"configured": True, "connected": False}
    return {
        "configured": True, "connected": True,
        "ms_email": tok.ms_email,
        "ms_tenant_id": tok.ms_tenant_id,
        "scopes": (tok.scope or "").split(),
        "default_calendar_id": tok.default_calendar_id,
        "default_drive_folder_id": tok.default_drive_folder_id,
    }


@router.get("/oauth/start")
def oauth_start(
    current_user: User = Depends(get_current_user),
):
    """Returnt de Microsoft consent-URL als JSON. Frontend doet
    window.location.href = url. State-token vervalt na 10 minuten."""
    if not ms.is_configured():
        raise HTTPException(status_code=503,
                            detail="Microsoft OAuth niet geconfigureerd op de server")
    state = _make_state(current_user.id)
    return {"auth_url": ms.make_auth_url(state)}


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
        tokens = ms.exchange_code(code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Token-uitwisseling faalde: {e}")

    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    if not access:
        raise HTTPException(status_code=400, detail="Geen access_token van Microsoft ontvangen")

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(tokens.get("expires_in", 3600)))

    # Haal user-info op via Graph
    ms_email = None
    ms_user_id = None
    ms_tenant_id = None
    try:
        info = ms.fetch_userinfo(access)
        ms_email = info.get("mail") or info.get("userPrincipalName")
        ms_user_id = info.get("id")
        # Tenant is in id-token niet meteen beschikbaar; voor nu skip
    except Exception as e:
        print(f"[microsoft] userinfo fetch faalde: {e}")

    existing = db.query(MicrosoftOAuthToken).filter(
        MicrosoftOAuthToken.user_id == user.id
    ).first()
    if existing:
        existing.access_token = access
        if refresh:
            existing.refresh_token = refresh
        existing.expires_at = expires_at
        existing.scope = tokens.get("scope", existing.scope)
        existing.ms_email = ms_email or existing.ms_email
        existing.ms_user_id = ms_user_id or existing.ms_user_id
        existing.ms_tenant_id = ms_tenant_id or existing.ms_tenant_id
        existing.revoked_at = None
        rec = existing
    else:
        rec = MicrosoftOAuthToken(
            user_id=user.id, organization_id=user.organization_id,
            access_token=access, refresh_token=refresh,
            expires_at=expires_at, scope=tokens.get("scope"),
            ms_email=ms_email, ms_user_id=ms_user_id, ms_tenant_id=ms_tenant_id,
        )
        db.add(rec)
    db.commit()

    log_action(db, None, user, action="microsoft.oauth.connected",
               entity_type="microsoft_oauth_token", entity_id=rec.id,
               extra={"ms_email": ms_email})

    return RedirectResponse(url="/portaal?microsoft=connected", status_code=302)


@router.post("/disconnect")
def disconnect(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verbreek de Microsoft-koppeling (revoke local token; user moet ook
    op portal.microsoft.com de app intrekken voor volledige revoke)."""
    rec = db.query(MicrosoftOAuthToken).filter(
        MicrosoftOAuthToken.user_id == current_user.id
    ).first()
    if not rec:
        return {"connected": False}
    rec.revoked_at = datetime.now(timezone.utc)
    db.commit()
    log_action(db, request, current_user, action="microsoft.oauth.disconnected",
               entity_type="microsoft_oauth_token", entity_id=rec.id)
    return {"connected": False}


# ────────────────────────────────────────────────────────────────────────────
# Outlook Calendar — sync melding -> event
# ────────────────────────────────────────────────────────────────────────────

@router.post("/calendar/sync-melding/{melding_id}")
def calendar_sync_melding(
    melding_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Synchroniseer een specifieke melding naar Outlook Calendar.

    Maakt event aan als 't nog niet bestaat, anders update. Idempotent via
    MicrosoftCalendarLink-tabel.
    """
    rec = db.query(MicrosoftOAuthToken).filter(
        MicrosoftOAuthToken.user_id == current_user.id
    ).first()
    access = ms.ensure_fresh_token(db, rec) if rec else None
    if not access:
        raise HTTPException(status_code=400,
                            detail="Niet verbonden met Microsoft. Verbind eerst via /api/microsoft/oauth/start")

    melding = db.query(Melding).filter(
        Melding.id == melding_id,
        Melding.organization_id == current_user.organization_id,
    ).first()
    if not melding:
        raise HTTPException(status_code=404, detail="Melding niet gevonden")

    from models import Asset, Project
    asset = db.query(Asset).filter(Asset.id == melding.asset_id).first() if melding.asset_id else None
    project = db.query(Project).filter(Project.id == melding.project_id).first() if melding.project_id else None

    payload = ms.build_melding_event(melding, asset=asset, project=project)
    link = db.query(MicrosoftCalendarLink).filter(
        MicrosoftCalendarLink.user_id == current_user.id,
        MicrosoftCalendarLink.entity_type == "melding",
        MicrosoftCalendarLink.entity_id == melding_id,
    ).first()

    try:
        if link:
            event = ms.calendar_update_event(access, link.ms_event_id, payload)
            link.last_synced_at = datetime.now(timezone.utc)
        else:
            event = ms.calendar_create_event(access, payload, calendar_id=rec.default_calendar_id)
            link = MicrosoftCalendarLink(
                user_id=current_user.id, entity_type="melding",
                entity_id=melding_id, ms_event_id=event["id"],
                calendar_id=rec.default_calendar_id,
            )
            db.add(link)
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Microsoft Graph-fout: {e}")

    log_action(db, request, current_user, action="microsoft.calendar.synced",
               entity_type="melding", entity_id=melding_id,
               extra={"ms_event_id": event.get("id")})
    return {"synced": True, "ms_event_id": event.get("id"), "web_link": event.get("webLink")}


# ────────────────────────────────────────────────────────────────────────────
# OneDrive upload
# ────────────────────────────────────────────────────────────────────────────

@router.post("/onedrive/upload")
async def onedrive_upload(
    file: UploadFile = File(...),
    request: Request = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload bestand (PDF/foto/csv) naar FieldOps-folder in OneDrive."""
    rec = db.query(MicrosoftOAuthToken).filter(
        MicrosoftOAuthToken.user_id == current_user.id
    ).first()
    access = ms.ensure_fresh_token(db, rec) if rec else None
    if not access:
        raise HTTPException(status_code=400,
                            detail="Niet verbonden met Microsoft. Verbind eerst.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Leeg bestand")
    if len(content) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413,
                            detail="Bestand >4MB — gebruik upload-session-API (later)")

    # Ensure FieldOps folder
    folder_id = rec.default_drive_folder_id
    if not folder_id:
        folder_id = ms.onedrive_ensure_folder(access)
        if folder_id:
            rec.default_drive_folder_id = folder_id
            db.commit()

    item = ms.onedrive_upload(access, file.filename, content,
                              content_type=file.content_type or "application/octet-stream",
                              folder_id=folder_id)
    if not item:
        raise HTTPException(status_code=502, detail="OneDrive upload faalde")

    log_action(db, request, current_user, action="microsoft.onedrive.uploaded",
               entity_type="onedrive_file", entity_id=item.get("id"),
               extra={"name": item.get("name"), "size": item.get("size")})
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "web_url": item.get("webUrl"),
        "size": item.get("size"),
    }
