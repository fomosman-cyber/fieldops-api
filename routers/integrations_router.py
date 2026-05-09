"""Integraties — overzicht en coverage van OAuth-koppelingen per gebruiker.

Endpoints:
  - GET /api/integrations/status    — own status (combined Google + Microsoft)
  - GET /api/integrations/coverage  — org-admin-only: wie heeft wat gekoppeld

`is_configured()` reflecteert wat de server-config (env-vars) heeft. Als
die ontbreekt, blijft de UI duidelijk over wat te doen (zie GOOGLE-SETUP.md
en MICROSOFT-SETUP.md voor de admin-stappen).
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import User, GoogleOAuthToken, MicrosoftOAuthToken
from auth import get_current_user
from permissions import require_org_admin
import google_integration as gi
import microsoft_integration as ms

router = APIRouter(prefix="/api/integrations", tags=["Integraties"])


@router.get("/status")
def my_integrations_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Combined status van alle integraties voor de huidige gebruiker.

    Eén call ipv twee — handig voor settings-page en mobile WebView. Bevat
    voor elke integratie:
      - `configured`: server-side env-vars OK
      - `connected`: deze user heeft een geldige (niet-revoked) token
      - `email`: gekoppeld account-email (indien verbonden)
      - `setup_doc`: pad naar de setup-doc als niet geconfigureerd
    """
    google_tok = (db.query(GoogleOAuthToken)
                    .filter(GoogleOAuthToken.user_id == current_user.id)
                    .first())
    ms_tok = (db.query(MicrosoftOAuthToken)
                .filter(MicrosoftOAuthToken.user_id == current_user.id)
                .first())

    google_configured = gi.is_configured()
    ms_configured = ms.is_configured()

    return {
        "google": {
            "configured": google_configured,
            "connected": bool(google_tok and not google_tok.revoked_at),
            "email": (google_tok.google_email if google_tok and not google_tok.revoked_at else None),
            "setup_doc": None if google_configured else "GOOGLE-SETUP.md",
        },
        "microsoft": {
            "configured": ms_configured,
            "connected": bool(ms_tok and not ms_tok.revoked_at),
            "email": (ms_tok.ms_email if ms_tok and not ms_tok.revoked_at else None),
            "setup_doc": None if ms_configured else "MICROSOFT-SETUP.md",
        },
    }


@router.get("/coverage")
def org_integration_coverage(
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Org-admin-only: per integratie hoeveel users gekoppeld zijn.

    Returnt totalen + lijstjes met user-info zodat admin gerichte follow-up kan
    doen ("Stuur reminder naar Mark + Lisa om hun MS te koppelen").
    """
    org_id = current_user.organization_id

    total_users = (db.query(func.count(User.id))
                     .filter(User.organization_id == org_id,
                             User.is_active == True)  # noqa: E712
                     .scalar() or 0)

    # Per-user koppel-status
    users = (db.query(User.id, User.email, User.first_name, User.last_name)
               .filter(User.organization_id == org_id,
                       User.is_active == True)  # noqa: E712
               .all())

    google_connected_ids = {
        r[0] for r in db.query(GoogleOAuthToken.user_id)
                        .filter(GoogleOAuthToken.organization_id == org_id,
                                GoogleOAuthToken.revoked_at.is_(None))
                        .all()
    }
    ms_connected_ids = {
        r[0] for r in db.query(MicrosoftOAuthToken.user_id)
                        .filter(MicrosoftOAuthToken.organization_id == org_id,
                                MicrosoftOAuthToken.revoked_at.is_(None))
                        .all()
    }

    rows = []
    for uid, email, fn, ln in users:
        rows.append({
            "user_id": uid,
            "email": email,
            "name": ((fn or "") + " " + (ln or "")).strip() or email,
            "google": uid in google_connected_ids,
            "microsoft": uid in ms_connected_ids,
        })

    return {
        "total_users": total_users,
        "google": {
            "configured": gi.is_configured(),
            "connected": len(google_connected_ids),
            "missing": total_users - len(google_connected_ids),
        },
        "microsoft": {
            "configured": ms.is_configured(),
            "connected": len(ms_connected_ids),
            "missing": total_users - len(ms_connected_ids),
        },
        "users": rows,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
