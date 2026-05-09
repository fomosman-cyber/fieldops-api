"""Audit-trail helpers.

Gebruikspatroon in een router:

    from audit import log_action, ACTION

    @router.post("/")
    def create_melding(
        data: MeldingCreate,
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        melding = Melding(...)
        db.add(melding); db.commit(); db.refresh(melding)
        log_action(db, request, current_user,
                   action=ACTION.MELDING_CREATE,
                   entity_type="melding", entity_id=melding.id,
                   after={"title": melding.title, "status": melding.status})
        return melding

Het loggen mag NOOIT de business-actie laten falen — exceptions worden gevangen
en gelogd naar stdout zodat compliance-issues zichtbaar zijn maar gebruikers
geen 500's krijgen. De caller doet één commit voor de mutatie en een tweede
voor de log; bij wijzigingen die echt atomair moeten zijn, voeg de log toe
binnen dezelfde transactie (zie `log_action(commit=False)`).
"""

from __future__ import annotations
from typing import Optional, Any
import json
import uuid
from fastapi import Request
from sqlalchemy.orm import Session

from models import AuditLog, User


class ACTION:
    """Action-codes. Houd dit lijstje scherp — losse strings sluipen in en
    bemoeilijken queries later."""
    # Auth
    LOGIN_SUCCESS       = "auth.login.success"
    LOGIN_FAILED        = "auth.login.failed"
    PASSWORD_RESET_REQ  = "auth.password.reset_requested"
    PASSWORD_RESET_DONE = "auth.password.reset_completed"
    PASSWORD_CHANGED    = "auth.password.changed"

    # Users
    USER_CREATE         = "user.create"
    USER_UPDATE         = "user.update"
    USER_ROLE_CHANGE    = "user.role_change"
    USER_DEACTIVATE     = "user.deactivate"
    USER_INVITE         = "user.invite"

    # Organisations (alleen platform-eigenaar)
    ORG_CREATE          = "org.create"
    ORG_UPDATE          = "org.update"
    ORG_DELETE          = "org.delete"

    # Demo-aanvragen
    DEMO_APPROVE        = "demo.approve"
    DEMO_DELETE         = "demo.delete"

    # Projecten
    PROJECT_CREATE      = "project.create"
    PROJECT_UPDATE      = "project.update"
    PROJECT_ARCHIVE     = "project.archive"

    # Meldingen
    MELDING_CREATE      = "melding.create"
    MELDING_UPDATE      = "melding.update"
    MELDING_STATUS      = "melding.status_change"
    MELDING_DELETE      = "melding.delete"

    # Assets
    ASSET_CREATE        = "asset.create"
    ASSET_UPDATE        = "asset.update"
    ASSET_ARCHIVE       = "asset.archive"
    ASSET_BULK_IMPORT   = "asset.bulk_import"
    ASSET_INSPECTION    = "asset.inspection_logged"

    # AI Inspecties
    AI_ANALYSIS_RUN     = "ai.analysis.run"
    AI_ANALYSIS_ACCEPT  = "ai.analysis.accept"
    AI_ANALYSIS_REJECT  = "ai.analysis.reject"


def _safe_json(obj: Any) -> Optional[str]:
    """Serialiseer naar JSON; bij gekke types (bv. datetime) val terug op default=str."""
    if obj is None:
        return None
    try:
        return json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:
        return json.dumps({"_unserializable": True})


def _client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    # X-Forwarded-For respecteren (Render/Cloudflare zetten 'm)
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def log_action(
    db: Session,
    request: Optional[Request],
    user: Optional[User],
    *,
    action: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    before: Any = None,
    after: Any = None,
    extra: Optional[dict] = None,
    commit: bool = True,
) -> None:
    """Schrijf één audit-record. Faalt nooit hard — alleen logging naar stdout.

    `before`/`after`/`extra` worden samen in `details` opgeslagen als JSON.
    Geef alleen door wat zinvol is om terug te kunnen zoeken — geen bulk-data.
    """
    try:
        details = None
        if before is not None or after is not None or extra:
            payload: dict[str, Any] = {}
            if before is not None: payload["before"] = before
            if after is not None: payload["after"] = after
            if extra: payload["extra"] = extra
            details = _safe_json(payload)

        rec = AuditLog(
            user_id=user.id if user else None,
            user_email=user.email if user else None,
            organization_id=user.organization_id if user else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            ip_address=_client_ip(request),
            user_agent=(request.headers.get("user-agent")[:512] if request else None),
            request_id=(getattr(request.state, "request_id", None) if request else None),
        )
        db.add(rec)
        if commit:
            db.commit()
    except Exception as e:
        # Audit mag geen request laten falen
        print(f"[audit] WARN: kon actie '{action}' niet loggen: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return  # bij audit-fail geen webhook afvuren

    # Fanout naar externe webhooks (Slack/Teams/generic) + realtime WebSocket.
    # Bewust apart van de audit-write-transactie zodat een delivery-fout
    # nooit de audit-trail corrupteert.
    if user and user.organization_id:
        from datetime import datetime, timezone
        event_payload = {
            "id": rec.id,
            "user_email": user.email,
            "user_id": user.id,
            "organization_id": user.organization_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "details": json.loads(details) if details else None,
            "request_id": rec.request_id,
            "created_at": (rec.created_at or datetime.now(timezone.utc)).isoformat(),
        }
        try:
            from webhooks import dispatch_event
            dispatch_event(db, action, user.organization_id, event_payload)
        except Exception as e:
            print(f"[audit] WARN: webhook-fanout '{action}' faalde: {e}")

        try:
            from realtime import broadcast_event
            broadcast_event(action, user.organization_id, event_payload)
        except Exception as e:
            print(f"[audit] WARN: realtime broadcast '{action}' faalde: {e}")

        # Push-notifications voor user-relevante events binnen de organisatie.
        # Lichte filter: alleen melding.create/status/assign en ai-bevindingen
        # — anders explodeert de notification-volume.
        if action in ("melding.create", "melding.status_change", "ai.analysis.run"):
            try:
                _send_push_for_event(db, user.organization_id, action, event_payload)
            except Exception as e:
                print(f"[audit] WARN: push '{action}' faalde: {e}")


def _send_push_for_event(db, org_id: str, action: str, payload: dict) -> None:
    """Stuur push naar alle org-leden behalve de actor zelf."""
    from models import PushSubscription
    from push import send_push, is_configured
    from datetime import datetime, timezone
    if not is_configured():
        return
    actor_id = payload.get("user_id")
    subs = db.query(PushSubscription).filter(
        PushSubscription.organization_id == org_id,
        PushSubscription.user_id != actor_id,
    ).all()
    if not subs:
        return

    titles = {
        "melding.create": "Nieuwe melding",
        "melding.status_change": "Status gewijzigd",
        "ai.analysis.run": "AI-analyse uitgevoerd",
    }
    title = titles.get(action, "FieldOps")
    actor = payload.get("user_email") or "een collega"
    details = payload.get("details") or {}
    after = details.get("after") if isinstance(details, dict) else None
    snippet = ""
    if isinstance(after, dict):
        snippet = after.get("title") or after.get("ernst") or ""
    body = f"{actor}{' · ' + snippet if snippet else ''}".strip()

    for s in subs:
        ok, status, _err = send_push(
            {"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}},
            title=title, body=body[:120], url="/portaal", tag=action,
            extra={"entity_id": payload.get("entity_id"), "action": action},
        )
        if ok:
            s.last_used_at = datetime.now(timezone.utc)
            s.failure_count = 0
        else:
            s.failure_count = (s.failure_count or 0) + 1
            if status in (404, 410):
                db.delete(s)
    try:
        db.commit()
    except Exception:
        db.rollback()


def assign_request_id(request: Request) -> str:
    """Hang een request_id aan request.state. Aanroepbaar uit middleware."""
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = rid
    return rid
