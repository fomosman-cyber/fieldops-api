"""Email Inbox — inkomende emails worden automatisch meldingen.

Use-case: een gemeente publiceert een email-adres voor meldingen
('meldingen@gemeente.nl'). Mailgun/Postmark/SendGrid inbound-parser ontvangt
de mail en POST naar deze endpoint. Wij maken automatisch een melding aan.

Endpoints:
  POST /api/incoming/email/{token}  - webhook (Mailgun/Postmark/SendGrid format)
  GET  /api/email-inbox/             - eigen routes lijst
  POST /api/email-inbox/             - nieuwe route aanmaken
  PATCH /api/email-inbox/{id}        - route updaten (defaults, whitelist, active)
  DELETE /api/email-inbox/{id}       - route deactiveren
  POST /api/email-inbox/{id}/regenerate-token - nieuwe token (oude vervalt)

Format-detection: we proberen achtereenvolgens Mailgun, Postmark, SendGrid
en fallback naar generic JSON. Resultaat: titel = subject, description = body
(text-only), foto's komen uit attachments als data-URL.
"""
from datetime import datetime, timezone
from typing import Optional, List
import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from database import get_db
from models import EmailInboxRoute, Melding, Project, User
from permissions import require_org_admin
from audit import log_action

router = APIRouter(prefix="/api/email-inbox", tags=["Email Inbox"])
incoming_router = APIRouter(prefix="/api/incoming/email", tags=["Email Inbox (webhook)"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class EmailInboxRouteCreate(BaseModel):
    label: str = Field(..., min_length=2, max_length=120)
    address_prefix: Optional[str] = Field(None, max_length=80)
    default_project_id: Optional[str] = None
    default_category: Optional[str] = None
    default_priority: str = "normaal"
    allowed_senders: Optional[List[str]] = None  # email-adressen of domeinen


class EmailInboxRouteUpdate(BaseModel):
    label: Optional[str] = None
    address_prefix: Optional[str] = None
    default_project_id: Optional[str] = None
    default_category: Optional[str] = None
    default_priority: Optional[str] = None
    allowed_senders: Optional[List[str]] = None
    is_active: Optional[bool] = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _generate_token() -> str:
    """64-char URL-safe token (256-bit entropy)."""
    return secrets.token_urlsafe(48)[:64]


def _serialize_route(r: EmailInboxRoute, *, include_token: bool = False) -> dict:
    senders = []
    if r.allowed_senders:
        try:
            senders = json.loads(r.allowed_senders)
        except Exception:
            senders = []
    out = {
        "id": r.id,
        "label": r.label,
        "address_prefix": r.address_prefix,
        "default_project_id": r.default_project_id,
        "default_category": r.default_category,
        "default_priority": r.default_priority,
        "allowed_senders": senders,
        "is_active": r.is_active,
        "received_count": r.received_count,
        "last_received_at": r.last_received_at.isoformat() if r.last_received_at else None,
        "last_sender": r.last_sender,
        "last_error": r.last_error,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "webhook_url_suffix": f"/api/incoming/email/{r.token[:8]}...",
    }
    if include_token:
        out["token"] = r.token
        out["webhook_url"] = f"/api/incoming/email/{r.token}"
    return out


def _is_sender_allowed(sender: str, allowed_senders: Optional[str]) -> bool:
    """Whitelist check. Leeg = alle senders ok."""
    if not allowed_senders:
        return True
    try:
        whitelist = json.loads(allowed_senders)
    except Exception:
        return True
    if not whitelist:
        return True

    sender_lower = (sender or "").strip().lower()
    if "<" in sender_lower:
        # Extract email uit "Name <addr@example.com>"
        sender_lower = sender_lower.split("<", 1)[1].rstrip(">").strip()

    for w in whitelist:
        w = (w or "").strip().lower()
        if not w:
            continue
        if "@" in w:
            # Exact email match
            if sender_lower == w:
                return True
        else:
            # Domein match
            if sender_lower.endswith("@" + w) or sender_lower == w:
                return True
    return False


def _parse_inbound_email(body: dict, content_type: str = "") -> dict:
    """Parse de inbound-payload van diverse providers naar standaard dict.

    Detecteert Mailgun / Postmark / SendGrid / generic JSON.
    Output: {sender, subject, body_text, attachments[]}
    """
    # Mailgun: heeft 'sender', 'subject', 'body-plain', 'attachment-N' keys
    if "sender" in body and "body-plain" in body:
        attachments = []
        # Mailgun attachments zitten als attachment-1, attachment-2, etc als URL
        # Voor MVP: skippen (vereist extra fetch met API-key)
        return {
            "sender": body.get("sender", ""),
            "subject": body.get("subject", ""),
            "body_text": body.get("body-plain", "") or body.get("stripped-text", ""),
            "attachments": attachments,
            "provider": "mailgun",
        }

    # Postmark: keys 'From', 'Subject', 'TextBody', 'Attachments'
    if "From" in body and ("TextBody" in body or "HtmlBody" in body):
        attachments = []
        for att in body.get("Attachments", []) or []:
            content = att.get("Content")  # base64
            name = att.get("Name", "")
            ctype = att.get("ContentType", "")
            if content and ctype.startswith("image/"):
                attachments.append({
                    "filename": name,
                    "content_type": ctype,
                    "data_url": f"data:{ctype};base64,{content}",
                })
        return {
            "sender": body.get("From", ""),
            "subject": body.get("Subject", ""),
            "body_text": body.get("TextBody", "") or _strip_html(body.get("HtmlBody", "")),
            "attachments": attachments,
            "provider": "postmark",
        }

    # SendGrid Inbound Parse: keys 'from', 'subject', 'text', 'attachment-info'
    if "from" in body and "text" in body and "attachment-info" in str(body):
        attachments = []
        # SendGrid attachments zijn als file-uploads (multipart) — niet hier
        return {
            "sender": body.get("from", ""),
            "subject": body.get("subject", ""),
            "body_text": body.get("text", ""),
            "attachments": attachments,
            "provider": "sendgrid",
        }

    # Generic JSON fallback
    return {
        "sender": body.get("sender") or body.get("from") or body.get("From", ""),
        "subject": body.get("subject") or body.get("Subject", ""),
        "body_text": (body.get("body_text") or body.get("text") or body.get("body")
                      or body.get("TextBody") or body.get("body-plain", "")),
        "attachments": body.get("attachments", []) or [],
        "provider": "generic",
    }


def _strip_html(html: str) -> str:
    """Naieve HTML-strip — voldoende voor email-body's. Voor productie: BeautifulSoup."""
    if not html:
        return ""
    import re
    # Verwijder script/style
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    # Verwijder tags
    text = re.sub(r"<[^>]+>", "\n", html)
    # Decode HTML-entities
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    # Normaliseer whitespace
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Webhook endpoint (no auth — token in URL)
# ─────────────────────────────────────────────────────────────────────────────

@incoming_router.post("/{token}")
async def receive_email(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Webhook voor inkomende emails. Token in URL = auth.

    Accepteert JSON of multipart-form-data (form-urlencoded werkt ook).
    Detecteert Mailgun/Postmark/SendGrid format automatisch.

    Maakt een melding aan. Subject = title, body_text = description,
    eerste image-attachment = photo_url. Defaults uit EmailInboxRoute config.
    """
    route = db.query(EmailInboxRoute).filter(
        EmailInboxRoute.token == token,
        EmailInboxRoute.is_active == True,  # noqa: E712
    ).first()
    if not route:
        raise HTTPException(404, "Email-route niet gevonden of inactief")

    # Parse payload — diverse formats
    content_type = (request.headers.get("content-type") or "").lower()
    try:
        if "application/json" in content_type:
            body = await request.json()
        elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
            form = await request.form()
            body = dict(form)
        else:
            # Raw body fallback — probeer JSON
            raw = await request.body()
            try:
                body = json.loads(raw)
            except Exception:
                body = {"body_text": raw.decode("utf-8", errors="replace")}
    except Exception as e:
        raise HTTPException(400, f"Kon payload niet parsen: {e}")

    parsed = _parse_inbound_email(body, content_type)
    sender = parsed["sender"]

    # Whitelist check
    if not _is_sender_allowed(sender, route.allowed_senders):
        route.last_error = f"Geweigerd: sender '{sender}' niet op whitelist"
        db.commit()
        # 202 om reply-loops te voorkomen bij sender (geen 4xx = geen retry)
        return {"accepted": False, "reason": "sender not whitelisted"}

    # Maak melding aan
    title = (parsed["subject"] or "Email zonder onderwerp")[:255]
    description = (parsed["body_text"] or "").strip()
    # Voeg sender + provider als footer aan description
    footer = f"\n\n---\nVan: {sender}\nOntvangen via: email ({parsed['provider']})"
    description = (description + footer)[:8000]  # cap voor DB

    # Eerste image-attachment als photo_url
    photo_url = None
    for att in parsed.get("attachments", []):
        if isinstance(att, dict) and att.get("data_url"):
            photo_url = att["data_url"]
            break

    try:
        melding = Melding(
            title=title,
            description=description,
            category=route.default_category,
            priority=route.default_priority or "normaal",
            project_id=route.default_project_id,
            photo_url=photo_url,
            organization_id=route.organization_id,
            created_by=route.created_by,
        )
        db.add(melding)

        # Update route-stats
        route.received_count = (route.received_count or 0) + 1
        route.last_received_at = datetime.now(timezone.utc)
        route.last_sender = sender[:255]
        route.last_error = None

        db.commit()
        db.refresh(melding)

        # Audit-log
        from audit import ACTION
        log_action(db, request, None,
                   action=ACTION.MELDING_CREATE if hasattr(ACTION, "MELDING_CREATE") else "melding.create",
                   entity_type="melding", entity_id=melding.id,
                   extra={"via": "email-inbox", "route_id": route.id,
                          "sender": sender, "provider": parsed["provider"]})

        return {
            "accepted": True,
            "melding_id": melding.id,
            "title": melding.title,
            "provider": parsed["provider"],
            "has_photo": bool(photo_url),
        }
    except Exception as e:
        db.rollback()
        route.last_error = f"Melding-create failed: {str(e)[:200]}"
        try:
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(500, f"Kon melding niet aanmaken: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Management endpoints (auth required)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/")
def list_routes(
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Lijst van email-routes binnen organisatie (alleen org-admin)."""
    rows = (db.query(EmailInboxRoute)
              .filter(EmailInboxRoute.organization_id == current_user.organization_id)
              .order_by(EmailInboxRoute.created_at.desc())
              .all())
    return {"routes": [_serialize_route(r) for r in rows]}


@router.post("/")
def create_route(
    payload: EmailInboxRouteCreate,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Nieuwe email-route + token genereren. Token wordt 1× geretourneerd."""
    # Validate project
    if payload.default_project_id:
        proj = db.query(Project).filter(
            Project.id == payload.default_project_id,
            Project.organization_id == current_user.organization_id,
        ).first()
        if not proj:
            raise HTTPException(400, "default_project_id niet in jouw organisatie")

    route = EmailInboxRoute(
        organization_id=current_user.organization_id,
        token=_generate_token(),
        label=payload.label,
        address_prefix=payload.address_prefix,
        default_project_id=payload.default_project_id,
        default_category=payload.default_category,
        default_priority=payload.default_priority or "normaal",
        allowed_senders=json.dumps(payload.allowed_senders) if payload.allowed_senders else None,
        created_by=current_user.id,
    )
    db.add(route)
    db.commit()
    db.refresh(route)
    log_action(db, request, current_user, action="email_inbox.create",
               entity_type="email_inbox_route", entity_id=route.id,
               extra={"label": route.label})
    return _serialize_route(route, include_token=True)


@router.patch("/{route_id}")
def update_route(
    route_id: str,
    payload: EmailInboxRouteUpdate,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Route-defaults updaten."""
    route = db.query(EmailInboxRoute).filter(
        EmailInboxRoute.id == route_id,
        EmailInboxRoute.organization_id == current_user.organization_id,
    ).first()
    if not route:
        raise HTTPException(404, "Route niet gevonden")

    data = payload.model_dump(exclude_unset=True)
    if "default_project_id" in data and data["default_project_id"]:
        proj = db.query(Project).filter(
            Project.id == data["default_project_id"],
            Project.organization_id == current_user.organization_id,
        ).first()
        if not proj:
            raise HTTPException(400, "default_project_id niet in jouw organisatie")
    if "allowed_senders" in data:
        senders = data.pop("allowed_senders") or []
        route.allowed_senders = json.dumps(senders) if senders else None

    for field, value in data.items():
        setattr(route, field, value)

    db.commit()
    db.refresh(route)
    log_action(db, request, current_user, action="email_inbox.update",
               entity_type="email_inbox_route", entity_id=route.id)
    return _serialize_route(route)


@router.post("/{route_id}/regenerate-token")
def regenerate_token(
    route_id: str,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Genereer een nieuwe token (oude vervalt direct)."""
    route = db.query(EmailInboxRoute).filter(
        EmailInboxRoute.id == route_id,
        EmailInboxRoute.organization_id == current_user.organization_id,
    ).first()
    if not route:
        raise HTTPException(404, "Route niet gevonden")
    route.token = _generate_token()
    db.commit()
    db.refresh(route)
    log_action(db, request, current_user, action="email_inbox.regenerate_token",
               entity_type="email_inbox_route", entity_id=route.id)
    return _serialize_route(route, include_token=True)


@router.delete("/{route_id}")
def delete_route(
    route_id: str,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Route deactiveren (soft via is_active=False)."""
    route = db.query(EmailInboxRoute).filter(
        EmailInboxRoute.id == route_id,
        EmailInboxRoute.organization_id == current_user.organization_id,
    ).first()
    if not route:
        raise HTTPException(404, "Route niet gevonden")
    route.is_active = False
    db.commit()
    log_action(db, request, current_user, action="email_inbox.delete",
               entity_type="email_inbox_route", entity_id=route.id)
    return {"deactivated": True}
