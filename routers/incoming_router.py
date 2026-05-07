"""IoT-binnenkomend — sensors → meldingen.

Endpoints:
  POST  /api/incoming/{token}      Public ingest endpoint voor sensoren (geen JWT, alleen token)
  GET   /api/incoming/             Admin: lijst eigen tokens
  POST  /api/incoming/             Admin: nieuwe token aanmaken
  GET   /api/incoming/{id}         Admin: detail
  PUT   /api/incoming/{id}         Admin: bijwerken (naam/defaults/enabled)
  DELETE /api/incoming/{id}        Admin: verwijderen

Sensor-payload mag elk JSON-object zijn. Het systeem zoekt zinvolle velden:
  sensor_id, value, unit, message, lat, lng, timestamp
en valt anders terug op de raw payload als description.

Geen drempel-logic in v0 — elke ingest maakt een melding. Drempelregels per
sensor kunnen later in `IncomingWebhook` als JSON-veld erbij.
"""

import json
import secrets
from datetime import datetime, timezone
from typing import Optional, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import IncomingWebhook, Melding, User, Project, Asset
from auth import get_current_user
from permissions import require_org_admin
from audit import log_action, ACTION

router = APIRouter(prefix="/api/incoming", tags=["IoT-incoming"])


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic
# ─────────────────────────────────────────────────────────────────────────────

class IncomingWebhookCreate(BaseModel):
    name: str
    description: Optional[str] = None
    default_project_id: Optional[str] = None
    default_asset_id: Optional[str] = None
    default_priority: str = "normaal"
    default_category: Optional[str] = None
    title_template: Optional[str] = None  # bv. "Sensor {sensor_id}: {value} {unit}"
    enabled: bool = True


class IncomingWebhookUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    default_project_id: Optional[str] = None
    default_asset_id: Optional[str] = None
    default_priority: Optional[str] = None
    default_category: Optional[str] = None
    title_template: Optional[str] = None
    enabled: Optional[bool] = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _to_response(h: IncomingWebhook, *, include_token: bool = False) -> dict:
    out = {
        "id": h.id, "name": h.name, "description": h.description,
        "default_project_id": h.default_project_id,
        "default_asset_id": h.default_asset_id,
        "default_priority": h.default_priority,
        "default_category": h.default_category,
        "title_template": h.title_template,
        "enabled": h.enabled,
        "last_received_at": h.last_received_at.isoformat() if h.last_received_at else None,
        "received_count": h.received_count,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }
    if include_token:
        out["token"] = h.token
    return out


def _format_title(template: Optional[str], data: dict) -> str:
    if not template:
        sid = data.get("sensor_id") or data.get("id") or "sensor"
        val = data.get("value")
        unit = data.get("unit", "")
        if val is not None:
            return f"Sensor {sid}: {val} {unit}".strip()
        msg = data.get("message")
        return f"Sensor {sid}: {msg or 'event'}"[:200]
    try:
        return template.format(**{k: data.get(k, "") for k in
                                  ["sensor_id", "value", "unit", "message"]})[:200]
    except (KeyError, IndexError, ValueError):
        return template[:200]


# ─────────────────────────────────────────────────────────────────────────────
# Public ingest — GEEN JWT, alleen token
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{token}")
async def ingest(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Sensor POST hier z'n payload. Geen auth-header nodig — token is de auth."""
    hook = db.query(IncomingWebhook).filter(IncomingWebhook.token == token).first()
    if not hook or not hook.enabled:
        # Generic 404 om token-enumeration te ontmoedigen
        raise HTTPException(status_code=404, detail="Onbekende of uitgeschakelde webhook")

    # Lees JSON; ondersteun ook ruwe text als fallback
    try:
        data: Any = await request.json()
    except Exception:
        body = (await request.body()).decode("utf-8", errors="replace")
        data = {"message": body[:2000]}

    # Normaliseer naar dict zodat _format_title werkt
    if not isinstance(data, dict):
        data = {"value": data}

    # Maak melding aan
    title = _format_title(hook.title_template, data)
    desc = json.dumps(data, ensure_ascii=False, default=str)[:2000]
    lat = data.get("lat") if isinstance(data.get("lat"), (int, float)) else None
    lng = data.get("lng") if isinstance(data.get("lng"), (int, float)) else None

    # creator_id = de gebruiker die de hook aanmaakte (audit-trail blijft kloppen)
    melding = Melding(
        title=title, description=desc,
        category=hook.default_category,
        priority=hook.default_priority or "normaal",
        lat=lat, lng=lng,
        project_id=hook.default_project_id,
        asset_id=hook.default_asset_id,
        organization_id=hook.organization_id,
        created_by=hook.created_by,
    )
    db.add(melding)

    # Update hook-stats
    hook.last_received_at = datetime.now(timezone.utc)
    hook.received_count = (hook.received_count or 0) + 1

    db.commit()
    db.refresh(melding)

    # Audit + webhook-fanout op de creator-user (zelfde organisatie)
    creator = db.query(User).filter(User.id == hook.created_by).first()
    if creator:
        log_action(db, request, creator,
                   action=ACTION.MELDING_CREATE, entity_type="melding", entity_id=melding.id,
                   extra={"source": "iot-incoming", "incoming_webhook_id": hook.id,
                          "sensor_id": data.get("sensor_id")})

    return {"received": True, "melding_id": melding.id}


# ─────────────────────────────────────────────────────────────────────────────
# Admin beheer
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/")
def list_hooks(
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    rows = (db.query(IncomingWebhook)
              .filter(IncomingWebhook.organization_id == current_user.organization_id)
              .order_by(IncomingWebhook.created_at.desc()).all())
    return [_to_response(r) for r in rows]


@router.post("/")
def create_hook(
    data: IncomingWebhookCreate,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    h = IncomingWebhook(
        organization_id=current_user.organization_id,
        token=_generate_token(),
        name=data.name, description=data.description,
        default_project_id=data.default_project_id,
        default_asset_id=data.default_asset_id,
        default_priority=data.default_priority,
        default_category=data.default_category,
        title_template=data.title_template,
        enabled=data.enabled,
        created_by=current_user.id,
    )
    db.add(h); db.commit(); db.refresh(h)
    log_action(db, request, current_user, action="incoming.create",
               entity_type="incoming_webhook", entity_id=h.id,
               after={"name": h.name, "default_project_id": h.default_project_id})
    # Token alleen bij CREATE meegeven — daarna via een aparte rotate-endpoint
    return _to_response(h, include_token=True)


@router.get("/{hook_id}")
def get_hook(
    hook_id: str,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    h = db.query(IncomingWebhook).filter(
        IncomingWebhook.id == hook_id,
        IncomingWebhook.organization_id == current_user.organization_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Webhook niet gevonden")
    return _to_response(h, include_token=True)


@router.put("/{hook_id}")
def update_hook(
    hook_id: str,
    update: IncomingWebhookUpdate,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    h = db.query(IncomingWebhook).filter(
        IncomingWebhook.id == hook_id,
        IncomingWebhook.organization_id == current_user.organization_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Webhook niet gevonden")
    for k, v in update.model_dump(exclude_unset=True).items():
        setattr(h, k, v)
    db.commit(); db.refresh(h)
    log_action(db, request, current_user, action="incoming.update",
               entity_type="incoming_webhook", entity_id=h.id,
               after=update.model_dump(exclude_unset=True))
    return _to_response(h)


@router.delete("/{hook_id}")
def delete_hook(
    hook_id: str,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    h = db.query(IncomingWebhook).filter(
        IncomingWebhook.id == hook_id,
        IncomingWebhook.organization_id == current_user.organization_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Webhook niet gevonden")
    snapshot = {"name": h.name, "received_count": h.received_count}
    db.delete(h); db.commit()
    log_action(db, request, current_user, action="incoming.delete",
               entity_type="incoming_webhook", entity_id=hook_id, before=snapshot)
    return {"message": "Webhook verwijderd"}


@router.post("/{hook_id}/rotate")
def rotate_token(
    hook_id: str,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Genereer een nieuw token; oude wordt direct ongeldig."""
    h = db.query(IncomingWebhook).filter(
        IncomingWebhook.id == hook_id,
        IncomingWebhook.organization_id == current_user.organization_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Webhook niet gevonden")
    h.token = _generate_token()
    db.commit(); db.refresh(h)
    log_action(db, request, current_user, action="incoming.rotate_token",
               entity_type="incoming_webhook", entity_id=h.id)
    return {"token": h.token}
