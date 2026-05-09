"""Web Push endpoints — subscribe / unsubscribe / public-key uitlevering."""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import PushSubscription, User
from auth import get_current_user
from audit import log_action
from push import vapid_public_key, is_configured

router = APIRouter(prefix="/api/push", tags=["Push notifications"])


class SubscribeRequest(BaseModel):
    endpoint: str
    keys: dict          # {"p256dh": "...", "auth": "..."}
    user_agent: Optional[str] = None


@router.get("/public-key")
def get_public_key():
    """Frontend roept dit aan voordat hij subscribe() doet — VAPID public-key
    moet bij browser-vendor bekend zijn als `applicationServerKey`."""
    return {
        "public_key": vapid_public_key(),
        "configured": is_configured(),
    }


@router.post("/subscribe")
def subscribe(
    payload: SubscribeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bewaar de browser push-subscription bij deze user.

    Als dezelfde endpoint al bestaat: update keys en koppel aan deze user
    (kan gebeuren als hetzelfde device door een andere collega geleend is)."""
    if not is_configured():
        raise HTTPException(status_code=503, detail="Push notifications niet geconfigureerd op de server")

    p256dh = payload.keys.get("p256dh")
    auth = payload.keys.get("auth")
    if not p256dh or not auth:
        raise HTTPException(status_code=400, detail="keys.p256dh en keys.auth zijn verplicht")

    existing = db.query(PushSubscription).filter(
        PushSubscription.endpoint == payload.endpoint,
    ).first()

    if existing:
        existing.user_id = current_user.id
        existing.organization_id = current_user.organization_id
        existing.p256dh = p256dh
        existing.auth = auth
        existing.user_agent = payload.user_agent or existing.user_agent
        existing.failure_count = 0
        sub = existing
    else:
        sub = PushSubscription(
            user_id=current_user.id,
            organization_id=current_user.organization_id,
            endpoint=payload.endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=payload.user_agent,
        )
        db.add(sub)

    db.commit(); db.refresh(sub)
    log_action(db, request, current_user,
               action="push.subscribe", entity_type="push_subscription", entity_id=sub.id,
               extra={"user_agent": (payload.user_agent or "")[:120]})
    return {"id": sub.id, "subscribed": True}


@router.post("/unsubscribe")
def unsubscribe(
    payload: SubscribeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sub = db.query(PushSubscription).filter(
        PushSubscription.endpoint == payload.endpoint,
        PushSubscription.user_id == current_user.id,
    ).first()
    if sub:
        db.delete(sub); db.commit()
        log_action(db, request, current_user,
                   action="push.unsubscribe", entity_type="push_subscription", entity_id=sub.id)
    return {"unsubscribed": True}


@router.post("/test")
def test_push(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stuur een test-push naar alle subscriptions van de huidige user."""
    from push import send_push
    subs = db.query(PushSubscription).filter(
        PushSubscription.user_id == current_user.id,
    ).all()
    if not subs:
        raise HTTPException(status_code=404, detail="Je hebt geen push-subscriptions. Activeer notificaties eerst.")

    results = []
    for s in subs:
        ok, status, err = send_push(
            {"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}},
            title="FieldOps test", body="Push notificaties werken — succes!",
            url="/portaal",
        )
        if ok:
            s.last_used_at = datetime.now(timezone.utc)
            s.failure_count = 0
        else:
            s.failure_count = (s.failure_count or 0) + 1
            # 410 Gone of 404 = dood — opruimen
            if status in (404, 410):
                db.delete(s)
        results.append({"id": s.id, "ok": ok, "status": status, "error": err})
    db.commit()
    return {"sent": sum(1 for r in results if r["ok"]), "failed": sum(1 for r in results if not r["ok"]),
            "details": results}
