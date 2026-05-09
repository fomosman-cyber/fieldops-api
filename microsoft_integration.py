"""Microsoft 365 OAuth2 + Graph API helpers — minimal HTTP-only.

Doel identiek aan google_integration.py: simpel, geen Microsoft Graph SDK.
Alleen httpx + standaard OAuth2 + Microsoft Graph REST endpoints.

Waar dit voor is:
- Outlook Calendar events (gelijk aan Google Calendar)
- OneDrive / SharePoint upload
- (later) Teams chat-notificaties via Graph

Setup eenmalig:
1. portal.azure.com → Microsoft Entra ID → App registrations → New
2. Redirect URI: https://portaal.fieldopsapp.nl/api/microsoft/oauth/callback
                 + http://localhost:8001/api/microsoft/oauth/callback (dev)
3. Authentication → Allow public client flows: NO
4. API permissions → Microsoft Graph (delegated):
     - User.Read
     - Calendars.ReadWrite
     - Files.ReadWrite (OneDrive)
     - Sites.ReadWrite.All (SharePoint, optioneel)
     - offline_access (refresh tokens)
     Grant admin consent ✓
5. Certificates & secrets → New client secret → kopieer waarde
6. Render env:
     MS_OAUTH_CLIENT_ID=<app-id>
     MS_OAUTH_CLIENT_SECRET=<secret-value>
     MS_OAUTH_REDIRECT_URI=https://portaal.fieldopsapp.nl/api/microsoft/oauth/callback
     MS_OAUTH_TENANT=common  (of een specifieke tenant-id voor single-tenant)
"""

from __future__ import annotations
import os
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx

# Microsoft Graph delegated scopes
OAUTH_SCOPES = [
    "User.Read",
    "Calendars.ReadWrite",
    "Files.ReadWrite",          # OneDrive (eigen drive)
    "Sites.ReadWrite.All",      # SharePoint sites (optioneel — kan weg als niet gewenst)
    "offline_access",           # refresh tokens
    "openid",
    "email",
]

# Microsoft endpoints
def _tenant() -> str:
    return os.environ.get("MS_OAUTH_TENANT", "common")

def AUTH_URL() -> str:
    return f"https://login.microsoftonline.com/{_tenant()}/oauth2/v2.0/authorize"

def TOKEN_URL() -> str:
    return f"https://login.microsoftonline.com/{_tenant()}/oauth2/v2.0/token"

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def is_configured() -> bool:
    return bool(os.environ.get("MS_OAUTH_CLIENT_ID")
                and os.environ.get("MS_OAUTH_CLIENT_SECRET"))


def client_id() -> str:
    return os.environ.get("MS_OAUTH_CLIENT_ID", "")


def client_secret() -> str:
    return os.environ.get("MS_OAUTH_CLIENT_SECRET", "")


def redirect_uri() -> str:
    return os.environ.get("MS_OAUTH_REDIRECT_URI",
                          "https://portaal.fieldopsapp.nl/api/microsoft/oauth/callback")


def make_auth_url(state: str) -> str:
    """Genereer OAuth2 authorize-URL voor Microsoft Entra ID."""
    params = {
        "client_id": client_id(),
        "response_type": "code",
        "redirect_uri": redirect_uri(),
        "response_mode": "query",
        "scope": " ".join(OAUTH_SCOPES),
        "state": state,
        "prompt": "select_account",  # toon account-picker
    }
    return f"{AUTH_URL()}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    """Wissel auth-code in voor access + refresh tokens."""
    data = {
        "client_id": client_id(),
        "client_secret": client_secret(),
        "code": code,
        "redirect_uri": redirect_uri(),
        "grant_type": "authorization_code",
        "scope": " ".join(OAUTH_SCOPES),
    }
    with httpx.Client(timeout=15) as cli:
        r = cli.post(TOKEN_URL(),
                     data=data,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        r.raise_for_status()
        return r.json()


def refresh_access_token(refresh_token: str) -> dict:
    """Verfris access-token met refresh-token."""
    data = {
        "client_id": client_id(),
        "client_secret": client_secret(),
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
        "scope": " ".join(OAUTH_SCOPES),
    }
    with httpx.Client(timeout=15) as cli:
        r = cli.post(TOKEN_URL(),
                     data=data,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        r.raise_for_status()
        return r.json()


def fetch_userinfo(access_token: str) -> dict:
    """Haal user-info op via Graph /me endpoint."""
    with httpx.Client(timeout=15) as cli:
        r = cli.get(f"{GRAPH_BASE}/me",
                    headers={"Authorization": f"Bearer {access_token}"})
        r.raise_for_status()
        return r.json()


def ensure_fresh_token(db, token_row) -> Optional[str]:
    """Geef geldige access-token terug. Refresh als bijna verlopen.

    `token_row` is een MicrosoftOAuthToken model-instantie. Als refresh faalt,
    return None en revoke de token (klant moet opnieuw inloggen).
    """
    now = datetime.now(timezone.utc)
    expiry = token_row.expires_at
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    # Refresh als nog 5 min geldig of expired
    needs_refresh = (not expiry) or (expiry - now < timedelta(minutes=5))
    if not needs_refresh:
        return token_row.access_token

    if not token_row.refresh_token:
        return None
    try:
        new_tokens = refresh_access_token(token_row.refresh_token)
    except Exception as e:
        print(f"[microsoft] refresh faalde: {e}")
        token_row.revoked_at = now
        db.commit()
        return None
    token_row.access_token = new_tokens["access_token"]
    if "refresh_token" in new_tokens:
        token_row.refresh_token = new_tokens["refresh_token"]
    if "expires_in" in new_tokens:
        token_row.expires_at = now + timedelta(seconds=int(new_tokens["expires_in"]))
    if "scope" in new_tokens:
        token_row.scope = new_tokens["scope"]
    db.commit()
    return token_row.access_token


# ────────────────────────────────────────────────────────────────────────────
# Outlook Calendar API
# ────────────────────────────────────────────────────────────────────────────

def calendar_create_event(access_token: str, payload: dict, calendar_id: Optional[str] = None) -> dict:
    """Maak een Outlook Calendar event."""
    url = f"{GRAPH_BASE}/me/events" if not calendar_id else f"{GRAPH_BASE}/me/calendars/{calendar_id}/events"
    with httpx.Client(timeout=15) as cli:
        r = cli.post(url,
                     json=payload,
                     headers={"Authorization": f"Bearer {access_token}",
                              "Content-Type": "application/json"})
        r.raise_for_status()
        return r.json()


def calendar_update_event(access_token: str, event_id: str, payload: dict) -> dict:
    """Update een bestaand event."""
    url = f"{GRAPH_BASE}/me/events/{event_id}"
    with httpx.Client(timeout=15) as cli:
        r = cli.patch(url,
                      json=payload,
                      headers={"Authorization": f"Bearer {access_token}",
                               "Content-Type": "application/json"})
        r.raise_for_status()
        return r.json()


def calendar_delete_event(access_token: str, event_id: str) -> bool:
    """Verwijder een event."""
    url = f"{GRAPH_BASE}/me/events/{event_id}"
    with httpx.Client(timeout=15) as cli:
        r = cli.delete(url, headers={"Authorization": f"Bearer {access_token}"})
        return r.status_code in (200, 204)


def build_melding_event(melding, asset=None, project=None) -> dict:
    """Bouw Microsoft Graph event-payload uit een Melding-record.

    Microsoft Graph gebruikt een ander schema dan Google Calendar:
    - subject (titel)
    - body (HTML/text)
    - start/end met timezone als object
    - location (object)
    - importance (low/normal/high)
    """
    title = (melding.title or "FieldOps melding").strip()[:255]
    desc_parts = []
    if melding.description:
        desc_parts.append(melding.description)
    if asset:
        desc_parts.append(f"Asset: {asset.code} ({asset.asset_type})")
    if project:
        desc_parts.append(f"Project: {project.name}")
    if melding.crow_klasse:
        desc_parts.append(f"CROW-klasse: {melding.crow_klasse}")
    if melding.gw_maatregel:
        desc_parts.append(f"Maatregel: {melding.gw_maatregel}")
    if melding.lat and melding.lng:
        desc_parts.append(f"Locatie: https://maps.google.com/?q={melding.lat},{melding.lng}")
    body_text = "\n".join(desc_parts) or "FieldOps melding zonder beschrijving"

    # Default: nu + 1 uur, voor inspectie-deadline gebruik termijn_weken
    start_ts = datetime.now(timezone.utc) + timedelta(days=1)
    duration_min = 30
    if melding.priority == "kritiek":
        start_ts = datetime.now(timezone.utc) + timedelta(hours=2)
        duration_min = 60
    end_ts = start_ts + timedelta(minutes=duration_min)

    importance = "normal"
    if melding.priority == "kritiek":
        importance = "high"
    elif melding.priority == "laag":
        importance = "low"

    payload = {
        "subject": f"[FieldOps] {title}",
        "body": {
            "contentType": "text",
            "content": body_text[:4000],
        },
        "start": {
            "dateTime": start_ts.replace(tzinfo=None).isoformat(),
            "timeZone": "Europe/Amsterdam",
        },
        "end": {
            "dateTime": end_ts.replace(tzinfo=None).isoformat(),
            "timeZone": "Europe/Amsterdam",
        },
        "importance": importance,
        "categories": ["FieldOps"],
        "isReminderOn": True,
        "reminderMinutesBeforeStart": 60,
    }
    if melding.lat and melding.lng:
        payload["location"] = {
            "displayName": f"{melding.lat:.5f}, {melding.lng:.5f}",
            "coordinates": {"latitude": melding.lat, "longitude": melding.lng},
        }
    return payload


# ────────────────────────────────────────────────────────────────────────────
# OneDrive / SharePoint
# ────────────────────────────────────────────────────────────────────────────

def onedrive_ensure_folder(access_token: str, folder_name: str = "FieldOps Rapportages") -> Optional[str]:
    """Ensure-or-create een folder op de root van OneDrive. Geef folder-id terug."""
    with httpx.Client(timeout=15) as cli:
        # Check of folder bestaat
        r = cli.get(f"{GRAPH_BASE}/me/drive/root/children",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"$filter": f"name eq '{folder_name}' and folder ne null"})
        if r.status_code == 200:
            items = r.json().get("value", [])
            if items:
                return items[0]["id"]
        # Maak nieuwe
        r = cli.post(f"{GRAPH_BASE}/me/drive/root/children",
                     headers={"Authorization": f"Bearer {access_token}",
                              "Content-Type": "application/json"},
                     json={
                         "name": folder_name,
                         "folder": {},
                         "@microsoft.graph.conflictBehavior": "rename",
                     })
        if r.status_code in (200, 201):
            return r.json().get("id")
    return None


def onedrive_upload(access_token: str, filename: str, content: bytes,
                    content_type: str = "application/octet-stream",
                    folder_id: Optional[str] = None) -> Optional[dict]:
    """Upload klein bestand (<4MB) naar OneDrive. Voor grotere files: gebruik upload-session API."""
    if folder_id:
        url = f"{GRAPH_BASE}/me/drive/items/{folder_id}:/{filename}:/content"
    else:
        url = f"{GRAPH_BASE}/me/drive/root:/{filename}:/content"
    with httpx.Client(timeout=30) as cli:
        r = cli.put(url,
                    content=content,
                    headers={"Authorization": f"Bearer {access_token}",
                             "Content-Type": content_type})
        if r.status_code in (200, 201):
            return r.json()
        return None
