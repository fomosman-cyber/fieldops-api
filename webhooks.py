"""Webhook-framework: events uit de audit-log → externe systemen.

De `dispatch_event()`-functie wordt aangeroepen vanuit `audit.log_action()` en
zoekt webhooks die op het action-patroon abonneren. Per match wordt de payload
geformatteerd voor het juiste platform (Slack / Teams / generic) en synchroon
afgevuurd met een korte timeout. Falende deliveries worden gelogd in
`webhook_deliveries` zodat ze terug te zien zijn in de UI.

Sync afvuren is bewust: per request maximaal een paar webhooks, elk met 5s
timeout — totaal max ~10s extra latency in worst case. Async/queue komt zodra
volume het rechtvaardigt.

Veiligheid:
- Generic format → HMAC-SHA256(secret, body) in 'X-FieldOps-Signature' header
- 'X-FieldOps-Timestamp' header (Unix epoch) voor replay-bescherming
- Slack/Teams gebruiken hun eigen URL-secrets; geen extra signing.
"""

from __future__ import annotations
import hashlib
import hmac
import json
import time
from typing import Any, Optional

import httpx

DELIVERY_TIMEOUT_SEC = 5.0
PAYLOAD_PREVIEW_LEN = 4096
RESPONSE_PREVIEW_LEN = 500


# ─────────────────────────────────────────────────────────────────────────────
# Event-pattern matching
# ─────────────────────────────────────────────────────────────────────────────

def matches_pattern(action: str, pattern: str) -> bool:
    """`pattern` mag '*' bevatten als wildcard voor één of meer segmenten.
    Voorbeelden:
        'melding.*'   matcht 'melding.create', 'melding.status_change'
        '*'           matcht alles
        'asset.create' matcht alleen exact dat
    """
    if pattern == "*":
        return True
    if "*" not in pattern:
        return action == pattern
    # Eenvoudige glob: '.' is letterlijk, '*' = [^.]+ of .*
    a_parts = action.split(".")
    p_parts = pattern.split(".")
    if len(a_parts) != len(p_parts):
        return False
    return all(p == "*" or p == a for p, a in zip(p_parts, a_parts))


def matches_any(action: str, patterns: list[str]) -> bool:
    return any(matches_pattern(action, p) for p in patterns)


# ─────────────────────────────────────────────────────────────────────────────
# Payload formatters
# ─────────────────────────────────────────────────────────────────────────────

def _human_summary(action: str, details: Optional[dict], user_email: Optional[str]) -> str:
    """Korte mensleesbare regel voor Slack/Teams."""
    actor = user_email or "Onbekende gebruiker"
    pretty = {
        "melding.create": "heeft een nieuwe melding aangemaakt",
        "melding.update": "heeft een melding bijgewerkt",
        "melding.status_change": "heeft de status van een melding gewijzigd",
        "melding.delete": "heeft een melding verwijderd",
        "asset.create": "heeft een asset aangemaakt",
        "asset.update": "heeft een asset bijgewerkt",
        "asset.archive": "heeft een asset gearchiveerd",
        "asset.bulk_import": "heeft assets bulk-geïmporteerd",
        "ai.analysis.run": "heeft een AI-foto-analyse uitgevoerd",
        "ai.analysis.accept": "heeft een AI-bevinding geaccepteerd",
        "ai.analysis.reject": "heeft een AI-bevinding afgewezen",
        "user.invite": "heeft een gebruiker uitgenodigd",
        "user.role_change": "heeft een gebruikersrol gewijzigd",
        "user.deactivate": "heeft een gebruiker gedeactiveerd",
        "demo.approve": "heeft een demo-aanvraag goedgekeurd",
    }.get(action, f"actie '{action}'")
    return f"{actor} {pretty}"


def format_slack(action: str, payload: dict) -> dict:
    """Slack-incoming-webhook payload (text + optionele blocks)."""
    summary = _human_summary(action, payload.get("details"), payload.get("user_email"))
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*FieldOps* · {summary}"}},
    ]
    fields = []
    det = payload.get("details") or {}
    after = det.get("after") if isinstance(det, dict) else None
    if isinstance(after, dict):
        for k in ("title", "code", "asset_type", "ernst", "schade_type", "priority"):
            if k in after and after[k]:
                fields.append({"type": "mrkdwn", "text": f"*{k}:* {after[k]}"})
    if fields:
        blocks.append({"type": "section", "fields": fields[:8]})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn",
                      "text": f"action `{action}` · {payload.get('created_at','')}"}],
    })
    return {"text": summary, "blocks": blocks}


def format_teams(action: str, payload: dict) -> dict:
    """Microsoft Teams MessageCard (legacy connector format — breed compatibel)."""
    summary = _human_summary(action, payload.get("details"), payload.get("user_email"))
    facts = [{"name": "Actie", "value": action}]
    det = payload.get("details") or {}
    after = det.get("after") if isinstance(det, dict) else None
    if isinstance(after, dict):
        for k in ("title", "code", "asset_type", "ernst", "schade_type", "priority"):
            if k in after and after[k]:
                facts.append({"name": k, "value": str(after[k])})
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": "FieldOps event",
        "themeColor": "0284C7",
        "title": "FieldOps",
        "text": summary,
        "sections": [{"facts": facts[:10]}],
    }


def format_generic(action: str, payload: dict) -> dict:
    """Ruwe FieldOps event-envelope. Identiek aan een audit-log-entry +
    'event_type' veld voor consumer-compatibiliteit."""
    return {"event_type": action, **payload}


def _format_for(format_type: str, action: str, payload: dict) -> dict:
    if format_type == "slack":
        return format_slack(action, payload)
    if format_type == "teams":
        return format_teams(action, payload)
    return format_generic(action, payload)


# ─────────────────────────────────────────────────────────────────────────────
# Signing
# ─────────────────────────────────────────────────────────────────────────────

def sign_payload(secret: str, body: bytes, timestamp: str) -> str:
    """HMAC-SHA256(secret, timestamp.body) — receiver herberekent zelfde."""
    msg = (timestamp + ".").encode("ascii") + body
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher — synchroon, kort timeout, log altijd
# ─────────────────────────────────────────────────────────────────────────────

def deliver(endpoint, action: str, payload: dict) -> dict:
    """Vuur één webhook af. Geeft delivery-info terug; werpt geen exceptions."""
    body_obj = _format_for(endpoint.format_type, action, payload)
    body_bytes = json.dumps(body_obj, ensure_ascii=False, default=str).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if endpoint.format_type == "generic" and endpoint.secret:
        ts = str(int(time.time()))
        headers["X-FieldOps-Timestamp"] = ts
        headers["X-FieldOps-Signature"] = sign_payload(endpoint.secret, body_bytes, ts)
        headers["X-FieldOps-Action"] = action

    started = time.time()
    status = None
    response_snippet = None
    error = None
    try:
        with httpx.Client(timeout=DELIVERY_TIMEOUT_SEC) as cli:
            r = cli.post(endpoint.url, content=body_bytes, headers=headers)
            status = r.status_code
            response_snippet = r.text[:RESPONSE_PREVIEW_LEN]
    except httpx.TimeoutException:
        error = "timeout"
    except Exception as e:
        error = f"{type(e).__name__}: {str(e)[:200]}"
    duration_ms = int((time.time() - started) * 1000)
    succeeded = status is not None and 200 <= status < 300

    return {
        "status_code": status,
        "response_snippet": response_snippet,
        "error": error,
        "succeeded": succeeded,
        "duration_ms": duration_ms,
        "payload_json": body_bytes.decode("utf-8")[:PAYLOAD_PREVIEW_LEN],
    }


def dispatch_event(db, action: str, organization_id: Optional[str], payload: dict) -> None:
    """Hoofdentry. Zoek matching webhooks voor deze org en vuur ze af.
    Mag NOOIT exceptions doorzetten — falen mag de business-actie niet stoppen."""
    if not organization_id:
        return
    try:
        from models import WebhookEndpoint, WebhookDelivery
        from datetime import datetime, timezone

        hooks = db.query(WebhookEndpoint).filter(
            WebhookEndpoint.organization_id == organization_id,
            WebhookEndpoint.enabled == True,
        ).all()
        if not hooks:
            return

        now = datetime.now(timezone.utc)
        for h in hooks:
            try:
                patterns = json.loads(h.events_json or "[]")
            except json.JSONDecodeError:
                patterns = []
            if not isinstance(patterns, list) or not matches_any(action, patterns):
                continue

            res = deliver(h, action, payload)
            db.add(WebhookDelivery(
                webhook_endpoint_id=h.id, action=action,
                payload_json=res.get("payload_json"),
                status_code=res.get("status_code"),
                response_snippet=res.get("response_snippet"),
                error=res.get("error"),
                succeeded=res.get("succeeded", False),
                duration_ms=res.get("duration_ms"),
            ))
            h.last_triggered_at = now
            h.last_status = res.get("status_code")
            if res.get("succeeded"):
                h.failure_count = 0
            else:
                h.failure_count = (h.failure_count or 0) + 1
        db.commit()
    except Exception as e:
        print(f"[webhooks] dispatch_event faalde voor {action}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
