"""Webhook beheer-router. Org-admin only.

Endpoints:
  GET    /api/webhooks/             lijst
  POST   /api/webhooks/             nieuwe webhook
  GET    /api/webhooks/{id}         detail
  PUT    /api/webhooks/{id}         bijwerken
  DELETE /api/webhooks/{id}         verwijderen (hard-delete; failure_count is niet bewaard)
  POST   /api/webhooks/{id}/test    test-event afvuren naar deze hook
  GET    /api/webhooks/{id}/deliveries  delivery-historie
  GET    /api/webhooks/events       lijst van bekende event-patronen voor de UI
"""

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import get_db
from models import WebhookEndpoint, WebhookDelivery, User
from schemas import (
    WebhookEndpointCreate, WebhookEndpointUpdate, WebhookEndpointResponse,
    WebhookDeliveryResponse,
)
from permissions import require_org_admin
from audit import log_action, ACTION
from webhooks import deliver

router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])

VALID_FORMATS = {"slack", "teams", "generic"}


def _to_response(h: WebhookEndpoint) -> dict:
    try:
        events = json.loads(h.events_json or "[]")
    except json.JSONDecodeError:
        events = []
    return {
        "id": h.id, "name": h.name, "url": h.url,
        "format_type": h.format_type,
        "events": events, "enabled": h.enabled,
        "last_triggered_at": h.last_triggered_at,
        "last_status": h.last_status,
        "failure_count": h.failure_count or 0,
        "created_at": h.created_at,
    }


def _validate_format(fmt: str) -> None:
    if fmt not in VALID_FORMATS:
        raise HTTPException(status_code=400,
                            detail=f"format_type moet één van zijn: {sorted(VALID_FORMATS)}")


def _validate_events(events: list[str]) -> None:
    if not events:
        raise HTTPException(status_code=400, detail="Minstens één event-patroon vereist")
    for e in events:
        if not isinstance(e, str) or not e:
            raise HTTPException(status_code=400, detail=f"Ongeldig event-patroon: {e!r}")


@router.get("/events")
def list_event_patterns(current_user: User = Depends(require_org_admin)):
    """Bekende event-codes uit ACTION + handige wildcard-suggesties."""
    actions = sorted([v for k, v in vars(ACTION).items()
                      if not k.startswith("_") and isinstance(v, str)])
    suggestions = ["*", "melding.*", "asset.*", "ai.*", "user.*", "auth.*"]
    return {"actions": actions, "wildcards": suggestions}


@router.get("/", response_model=list[WebhookEndpointResponse])
def list_webhooks(
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    rows = (db.query(WebhookEndpoint)
              .filter(WebhookEndpoint.organization_id == current_user.organization_id)
              .order_by(WebhookEndpoint.created_at.desc()).all())
    return [_to_response(r) for r in rows]


@router.post("/", response_model=WebhookEndpointResponse)
def create_webhook(
    data: WebhookEndpointCreate,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    _validate_format(data.format_type)
    _validate_events(data.events)
    h = WebhookEndpoint(
        organization_id=current_user.organization_id,
        name=data.name, url=data.url,
        secret=data.secret if data.format_type == "generic" else None,
        format_type=data.format_type,
        events_json=json.dumps(data.events),
        enabled=data.enabled,
        created_by=current_user.id,
    )
    db.add(h); db.commit(); db.refresh(h)
    log_action(db, request, current_user, action="webhook.create",
               entity_type="webhook", entity_id=h.id,
               after={"name": h.name, "url": h.url, "format_type": h.format_type,
                      "events": data.events})
    return _to_response(h)


@router.get("/{webhook_id}", response_model=WebhookEndpointResponse)
def get_webhook(
    webhook_id: str,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    h = db.query(WebhookEndpoint).filter(
        WebhookEndpoint.id == webhook_id,
        WebhookEndpoint.organization_id == current_user.organization_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Webhook niet gevonden")
    return _to_response(h)


@router.put("/{webhook_id}", response_model=WebhookEndpointResponse)
def update_webhook(
    webhook_id: str,
    update: WebhookEndpointUpdate,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    h = db.query(WebhookEndpoint).filter(
        WebhookEndpoint.id == webhook_id,
        WebhookEndpoint.organization_id == current_user.organization_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Webhook niet gevonden")

    update_data = update.model_dump(exclude_unset=True)
    if "format_type" in update_data:
        _validate_format(update_data["format_type"])
    if "events" in update_data:
        _validate_events(update_data["events"])

    if "events" in update_data:
        h.events_json = json.dumps(update_data.pop("events"))
    for field, value in update_data.items():
        setattr(h, field, value)
    db.commit(); db.refresh(h)
    log_action(db, request, current_user, action="webhook.update",
               entity_type="webhook", entity_id=h.id, after=update_data)
    return _to_response(h)


@router.delete("/{webhook_id}")
def delete_webhook(
    webhook_id: str,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    h = db.query(WebhookEndpoint).filter(
        WebhookEndpoint.id == webhook_id,
        WebhookEndpoint.organization_id == current_user.organization_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Webhook niet gevonden")
    snapshot = {"name": h.name, "url": h.url, "format_type": h.format_type}
    db.delete(h); db.commit()
    log_action(db, request, current_user, action="webhook.delete",
               entity_type="webhook", entity_id=webhook_id, before=snapshot)
    return {"message": "Webhook verwijderd"}


@router.post("/{webhook_id}/test")
def test_webhook(
    webhook_id: str,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Vuur een dummy 'webhook.test' event af naar deze hook en log de delivery."""
    h = db.query(WebhookEndpoint).filter(
        WebhookEndpoint.id == webhook_id,
        WebhookEndpoint.organization_id == current_user.organization_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Webhook niet gevonden")

    payload = {
        "id": "test-" + h.id,
        "user_email": current_user.email,
        "user_id": current_user.id,
        "organization_id": current_user.organization_id,
        "details": {"after": {"title": "Test webhook van FieldOps",
                              "code": "TEST-001"}},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    res = deliver(h, "webhook.test", payload)
    db.add(WebhookDelivery(
        webhook_endpoint_id=h.id, action="webhook.test",
        payload_json=res.get("payload_json"),
        status_code=res.get("status_code"),
        response_snippet=res.get("response_snippet"),
        error=res.get("error"),
        succeeded=res.get("succeeded", False),
        duration_ms=res.get("duration_ms"),
    ))
    h.last_triggered_at = datetime.now(timezone.utc)
    h.last_status = res.get("status_code")
    db.commit()
    log_action(db, request, current_user, action="webhook.test",
               entity_type="webhook", entity_id=h.id,
               extra={"succeeded": res.get("succeeded"),
                      "status_code": res.get("status_code"),
                      "error": res.get("error")})
    return {
        "succeeded": res.get("succeeded"),
        "status_code": res.get("status_code"),
        "duration_ms": res.get("duration_ms"),
        "error": res.get("error"),
        "response_snippet": res.get("response_snippet"),
    }


@router.get("/{webhook_id}/deliveries", response_model=list[WebhookDeliveryResponse])
def list_deliveries(
    webhook_id: str,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    only_failed: bool = Query(False),
):
    h = db.query(WebhookEndpoint).filter(
        WebhookEndpoint.id == webhook_id,
        WebhookEndpoint.organization_id == current_user.organization_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Webhook niet gevonden")

    q = db.query(WebhookDelivery).filter(WebhookDelivery.webhook_endpoint_id == webhook_id)
    if only_failed:
        q = q.filter(WebhookDelivery.succeeded == False)
    return q.order_by(desc(WebhookDelivery.attempted_at)).limit(limit).all()
