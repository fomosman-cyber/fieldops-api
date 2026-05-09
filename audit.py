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
    ASSET_DELETE        = "asset.delete"
    PROJECT_DELETE      = "project.delete"
    ASSET_BULK_IMPORT   = "asset.bulk_import"
    ASSET_INSPECTION    = "asset.inspection_logged"
    ASSET_SPLIT         = "asset.wegvak.split"
    ASSET_MERGE         = "asset.wegvak.merge"

    # AI Inspecties
    AI_ANALYSIS_RUN     = "ai.analysis.run"
    AI_ANALYSIS_ACCEPT  = "ai.analysis.accept"
    AI_ANALYSIS_REJECT  = "ai.analysis.reject"

    # Compliance / GDPR
    AUDIT_EXPORT        = "audit.export.csv"       # admin downloadt audit-log
    DATA_EXPORT_SELF    = "user.data.export.self"  # GDPR Art.15 — DSAR door betrokkene
    DATA_EXPORT_ADMIN   = "user.data.export.admin" # admin draait DSAR namens klant
    USER_ANONYMIZE_SELF = "user.anonymize.self"    # GDPR Art.17 — door betrokkene
    USER_ANONYMIZE_ADMIN = "user.anonymize.admin"  # admin verwerkt erasure-verzoek


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

        # Google Calendar sync voor de gebruiker die de melding aanmaakte
        if action == "melding.create" and entity_type == "melding":
            try:
                _sync_melding_to_calendar(db, user, entity_id)
            except Exception as e:
                print(f"[audit] WARN: calendar-sync '{action}' faalde: {e}")


def _send_push_for_event(db, org_id: str, action: str, payload: dict) -> None:
    """Stuur push naar org-leden, met skill-based routing voor melding.create.

    v3.0 — Job Orchestration: bij een melding met gw_maatregel wordt de
    notificatie alleen naar specialisten + projectleiders/admins gestuurd,
    in plaats van broadcast naar iedereen. Voor andere events blijft de
    broadcast naar iedereen behalve actor.
    """
    from models import PushSubscription, User
    from push import send_push, is_configured
    from datetime import datetime, timezone
    if not is_configured():
        return
    actor_id = payload.get("user_id")

    # Skill-based filter voor melding.create met maatregel
    target_user_ids: Optional[set[str]] = None  # None = broadcast (default)
    details = payload.get("details") or {}
    after = details.get("after") if isinstance(details, dict) else None

    if action == "melding.create" and isinstance(after, dict):
        gw_maatregel = after.get("gw_maatregel")
        if gw_maatregel:
            try:
                from orchestration import maatregel_specialists
                # Specialisten met de juiste skill
                specialists = maatregel_specialists(db, org_id, gw_maatregel,
                                                    exclude_user_id=actor_id)
                target_ids = {u.id for u in specialists}
                # Plus alle managers/admins/contractors (zij zien altijd alles)
                from permissions import UserRole
                managers = db.query(User).filter(
                    User.organization_id == org_id,
                    User.id != actor_id,
                    User.role.in_([UserRole.ADMIN, UserRole.MANAGER, UserRole.CONTRACTOR]),
                ).all()
                target_ids.update({u.id for u in managers})
                target_user_ids = target_ids
            except Exception as e:
                print(f"[push] skill-routing fallback (broadcast): {e}")
                target_user_ids = None

    # Bouw de subscription-query
    q = db.query(PushSubscription).filter(
        PushSubscription.organization_id == org_id,
        PushSubscription.user_id != actor_id,
    )
    if target_user_ids is not None:
        if not target_user_ids:
            # Geen specialisten + geen managers? Stuur naar admins als laatste vangnet
            return
        q = q.filter(PushSubscription.user_id.in_(target_user_ids))
    subs = q.all()
    if not subs:
        return

    titles = {
        "melding.create": "Nieuwe melding voor jou",
        "melding.status_change": "Status gewijzigd",
        "ai.analysis.run": "AI-analyse uitgevoerd",
    }
    title = titles.get(action, "FieldOps")
    actor = payload.get("user_email") or "een collega"
    snippet = ""
    if isinstance(after, dict):
        # Met skill-routing: voeg de maatregel toe aan body voor context
        maatregel = after.get("gw_maatregel")
        title_text = after.get("title") or after.get("ernst") or ""
        if maatregel and target_user_ids is not None:
            snippet = f"{maatregel} — {title_text}" if title_text else maatregel
        else:
            snippet = title_text
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


def _sync_melding_to_calendar(db, user, melding_id: Optional[str]) -> None:
    """Maak een Google Calendar-event voor deze melding als de user verbonden is."""
    if not melding_id:
        return
    from models import GoogleOAuthToken, CalendarLink, Melding, Asset, Project
    import google_integration as gi
    if not gi.is_configured():
        return
    tok = db.query(GoogleOAuthToken).filter(GoogleOAuthToken.user_id == user.id).first()
    if not tok or tok.revoked_at:
        return
    access = gi.ensure_fresh_token(db, tok)
    if not access:
        return
    melding = db.query(Melding).filter(Melding.id == melding_id).first()
    if not melding:
        return
    # Dedup: als er al een link bestaat → niets doen
    existing = db.query(CalendarLink).filter(
        CalendarLink.user_id == user.id,
        CalendarLink.entity_type == "melding",
        CalendarLink.entity_id == melding_id,
    ).first()
    if existing:
        return
    asset = db.query(Asset).filter(Asset.id == melding.asset_id).first() if melding.asset_id else None
    project = db.query(Project).filter(Project.id == melding.project_id).first() if melding.project_id else None
    payload = gi.build_melding_event(melding, asset=asset, project=project)
    try:
        ev = gi.calendar_create_event(access, tok.default_calendar_id or "primary", payload)
    except Exception as e:
        print(f"[calendar] event-create faalde: {e}")
        return
    db.add(CalendarLink(
        user_id=user.id, entity_type="melding", entity_id=melding_id,
        google_event_id=ev.get("id", ""), calendar_id=tok.default_calendar_id or "primary",
    ))
    db.commit()


def assign_request_id(request: Request) -> str:
    """Hang een request_id aan request.state. Aanroepbaar uit middleware."""
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = rid
    return rid
