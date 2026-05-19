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
from models import User, GoogleOAuthToken, MicrosoftOAuthToken, Melding, Organization
from auth import get_current_user
from permissions import require_org_admin
from pydantic import BaseModel
from typing import Optional
import google_integration as gi
import microsoft_integration as ms
import urllib.parse

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


# ─────────────────────────────────────────────────────────────────────────────
# Linear / GitHub deep-link bridge
# MVP zonder OAuth: knop op melding genereert URL die issue pre-vult in Linear
# of GitHub. Gebruiker klikt → tool opent → save → handmatige terugkoppeling.
#
# Voor volledige bidirectionele sync (status-updates terug naar melding) is
# een OAuth-flow + webhook-receiver nodig. Komt in v2 als klanten dit vragen.
# ─────────────────────────────────────────────────────────────────────────────

class ExternalLinkRequest(BaseModel):
    target: str                                # 'linear' of 'github'
    team_or_repo: str                          # Linear team-key (bv. 'eng') of GitHub 'owner/repo'
    extra_labels: Optional[list[str]] = None   # GitHub-labels of Linear-labels


@router.post("/external-link/melding/{melding_id}")
def make_external_link(
    melding_id: str,
    req: ExternalLinkRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genereer deep-link naar Linear/GitHub voor 1 melding.

    Voorbeeld:
      target='linear', team_or_repo='eng'
      → https://linear.app/eng/issue/new?title=...&description=...

      target='github', team_or_repo='fomosman-cyber/fieldops-api'
      → https://github.com/fomosman-cyber/fieldops-api/issues/new?title=...&body=...

    Gebruiker klikt de URL, beslist of issue wordt aangemaakt. Geen
    automatische sync — handmatige opvolging. Voldoende voor MVP-use-cases:
    klant wil melding loggen in eigen ticket-systeem.
    """
    melding = db.query(Melding).filter(
        Melding.id == melding_id,
        Melding.organization_id == current_user.organization_id,
    ).first()
    if not melding:
        raise HTTPException(status_code=404, detail="Melding niet gevonden")

    target = (req.target or "").lower()
    team = (req.team_or_repo or "").strip()
    if target not in ("linear", "github"):
        raise HTTPException(status_code=400, detail="target moet 'linear' of 'github' zijn")
    if not team:
        raise HTTPException(status_code=400, detail="team_or_repo is verplicht")

    # Bouw description met context: lat/lng, categorie, prioriteit, FieldOps-link
    portal_url = "https://fieldops-api-8txr.onrender.com/portaal"
    description_lines = [
        f"**Bron**: FieldOps melding `{melding.id}`",
        f"**Categorie**: {melding.category or '—'}",
        f"**Prioriteit**: {melding.priority or '—'}",
        f"**Status**: {melding.status or 'open'}",
    ]
    if melding.lat and melding.lng:
        description_lines.append(f"**Locatie**: {melding.lat:.6f}, {melding.lng:.6f} "
                                  f"([Google Maps](https://maps.google.com/?q={melding.lat},{melding.lng}))")
    description_lines.append("")
    description_lines.append(melding.description or "")
    description_lines.append("")
    description_lines.append(f"---")
    description_lines.append(f"Open in FieldOps: {portal_url}")
    description = "\n".join(description_lines)

    title = melding.title or "Melding zonder titel"
    if target == "linear":
        # Linear deep-link: https://linear.app/{team}/issue/new
        params = {"title": title, "description": description}
        if req.extra_labels:
            params["labels"] = ",".join(req.extra_labels)
        url = f"https://linear.app/{urllib.parse.quote(team)}/issue/new?" + urllib.parse.urlencode(params)
    else:
        # GitHub: /{owner}/{repo}/issues/new?title=...&body=...&labels=label1,label2
        # team_or_repo moet 'owner/repo' formaat zijn
        if "/" not in team:
            raise HTTPException(status_code=400,
                                 detail="github team_or_repo moet 'owner/repo' formaat hebben")
        params = {"title": title, "body": description}
        if req.extra_labels:
            params["labels"] = ",".join(req.extra_labels)
        url = f"https://github.com/{team}/issues/new?" + urllib.parse.urlencode(params)

    return {
        "url": url,
        "target": target,
        "team_or_repo": team,
        "title": title,
        "description_length": len(description),
        "method": "deep-link",
        "note": "Klik om in nieuw tabblad te openen. Volledige sync (webhook + OAuth) komt in v2.",
    }


@router.get("/external-targets")
def list_supported_external_targets(
    current_user: User = Depends(get_current_user),
):
    """Lijst van beschikbare deep-link targets + voorbeeld-gebruik."""
    return {
        "supported": [
            {
                "key": "linear",
                "label": "Linear",
                "icon": "📋",
                "team_or_repo_format": "team-slug (bv. 'eng', 'support')",
                "example_url_template": "https://linear.app/{team}/issue/new",
                "docs": "https://linear.app/docs/issue-templates",
            },
            {
                "key": "github",
                "label": "GitHub Issues",
                "icon": "🐙",
                "team_or_repo_format": "owner/repo (bv. 'acme/infra-tickets')",
                "example_url_template": "https://github.com/{owner}/{repo}/issues/new",
                "docs": "https://docs.github.com/en/issues/tracking-your-work-with-issues/creating-an-issue",
            },
        ],
        "future": [
            "Volledige OAuth + webhook-sync — komt als 3+ klanten dit aanvragen",
            "Slack-bridge — open melding in Slack-kanaal",
            "Jira Cloud — issue auto-creatie",
        ],
    }
