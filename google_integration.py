"""Google OAuth2 + Calendar/Drive helpers — minimal HTTP-only, geen google-api-python-client.

Doel: simpel houden. We gebruiken httpx + de standaard OAuth2 flows zonder
de zware Google SDK. Dat scheelt een dependency-blob en ze SDK doet voor onze
use-case (Calendar-events, Drive-uploads) niets wat httpx niet kan.

Setup eenmalig:
1. console.cloud.google.com → Project → APIs & Services → Credentials
2. Create OAuth 2.0 Client ID → Web application
3. Authorized redirect URIs: https://portaal.fieldopsapp.nl/api/google/oauth/callback
   en http://localhost:8001/api/google/oauth/callback (dev)
4. Enable: Google Calendar API + Google Drive API
5. Render env:
     GOOGLE_OAUTH_CLIENT_ID=...
     GOOGLE_OAUTH_CLIENT_SECRET=...
     GOOGLE_OAUTH_REDIRECT_URI=https://portaal.fieldopsapp.nl/api/google/oauth/callback
"""

from __future__ import annotations
import os
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx

OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.file",  # alleen files door deze app aangemaakt
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"


def is_configured() -> bool:
    return bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
                and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"))


def client_id() -> str:
    return os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")


def client_secret() -> str:
    return os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")


def redirect_uri() -> str:
    return os.environ.get("GOOGLE_OAUTH_REDIRECT_URI",
                          "https://portaal.fieldopsapp.nl/api/google/oauth/callback")


def make_auth_url(state: str) -> str:
    params = {
        "client_id": client_id(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(OAUTH_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "include_granted_scopes": "true",
    }
    return AUTH_URL + "?" + urlencode(params)


def exchange_code(code: str) -> dict:
    """Wissel auth-code in voor access+refresh token."""
    with httpx.Client(timeout=15.0) as cli:
        r = cli.post(TOKEN_URL, data={
            "code": code,
            "client_id": client_id(),
            "client_secret": client_secret(),
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        })
        r.raise_for_status()
        return r.json()


def refresh_access_token(refresh_token: str) -> dict:
    with httpx.Client(timeout=15.0) as cli:
        r = cli.post(TOKEN_URL, data={
            "refresh_token": refresh_token,
            "client_id": client_id(),
            "client_secret": client_secret(),
            "grant_type": "refresh_token",
        })
        r.raise_for_status()
        return r.json()


def fetch_userinfo(access_token: str) -> dict:
    with httpx.Client(timeout=10.0) as cli:
        r = cli.get(USERINFO_URL, headers={"Authorization": "Bearer " + access_token})
        r.raise_for_status()
        return r.json()


# ─────────────────────────────────────────────────────────────────────────────
# Token-resolver: garandeert verse access-token (auto-refresh)
# ─────────────────────────────────────────────────────────────────────────────

def ensure_fresh_token(db, token_row) -> Optional[str]:
    """Geef een geldige access-token terug. Refresh als 'ie verlopen is.

    `token_row` is een GoogleOAuthToken model-instantie. Als refresh faalt
    (refresh-token revoked) → revoked_at wordt gezet en None geretourneerd.
    """
    if token_row is None or token_row.revoked_at:
        return None
    now = datetime.now(timezone.utc)
    expires = token_row.expires_at
    # Geef 60s buffer
    if expires and expires.replace(tzinfo=timezone.utc) > (now + timedelta(seconds=60)):
        return token_row.access_token
    if not token_row.refresh_token:
        return None
    try:
        new_tokens = refresh_access_token(token_row.refresh_token)
    except Exception as e:
        print(f"[google] refresh faalde: {e}")
        token_row.revoked_at = now
        db.commit()
        return None
    token_row.access_token = new_tokens["access_token"]
    if "expires_in" in new_tokens:
        token_row.expires_at = now + timedelta(seconds=int(new_tokens["expires_in"]))
    if new_tokens.get("scope"):
        token_row.scope = new_tokens["scope"]
    db.commit()
    return token_row.access_token


# ─────────────────────────────────────────────────────────────────────────────
# Calendar helpers
# ─────────────────────────────────────────────────────────────────────────────

def calendar_create_event(access_token: str, calendar_id: str, payload: dict) -> dict:
    with httpx.Client(timeout=15.0) as cli:
        r = cli.post(f"{CALENDAR_API}/calendars/{calendar_id}/events",
                     headers={"Authorization": "Bearer " + access_token,
                              "Content-Type": "application/json"},
                     json=payload)
        r.raise_for_status()
        return r.json()


def calendar_update_event(access_token: str, calendar_id: str, event_id: str, payload: dict) -> dict:
    with httpx.Client(timeout=15.0) as cli:
        r = cli.patch(f"{CALENDAR_API}/calendars/{calendar_id}/events/{event_id}",
                      headers={"Authorization": "Bearer " + access_token,
                               "Content-Type": "application/json"},
                      json=payload)
        r.raise_for_status()
        return r.json()


def calendar_delete_event(access_token: str, calendar_id: str, event_id: str) -> bool:
    with httpx.Client(timeout=15.0) as cli:
        r = cli.delete(f"{CALENDAR_API}/calendars/{calendar_id}/events/{event_id}",
                       headers={"Authorization": "Bearer " + access_token})
        return r.status_code in (200, 204, 404, 410)


def build_melding_event(melding, asset=None, project=None) -> dict:
    """Maak een Google Calendar event-payload van een melding."""
    title = "FieldOps: " + (melding.title or "Inspectie")
    desc_parts = []
    if melding.description: desc_parts.append(melding.description)
    if asset:
        desc_parts.append(f"Asset: {asset.code}" + (f" — {asset.name}" if asset.name else ""))
        if asset.location_description: desc_parts.append(f"Locatie: {asset.location_description}")
    if project: desc_parts.append(f"Project: {project.name}")
    desc_parts.append(f"Open in FieldOps: https://portaal.fieldopsapp.nl/portaal#melding={melding.id}")

    # Standaard: hele dag op de creatie-datum, anders op streefdatum
    start_date = (melding.created_at or datetime.now(timezone.utc)).date().isoformat()

    event = {
        "summary": title[:255],
        "description": "\n\n".join(desc_parts)[:8000],
        "start": {"date": start_date},
        "end": {"date": start_date},
        "extendedProperties": {
            "private": {
                "fieldops_melding_id": melding.id,
                "fieldops_org_id": melding.organization_id,
            }
        },
    }
    if melding.lat and melding.lng:
        loc = (asset.location_description if asset and asset.location_description
               else f"{melding.lat},{melding.lng}")
        event["location"] = loc
    return event
