"""Read-only audit-log inzage. Alleen org-admins zien hun eigen organisatie;
platform-eigenaar (FieldOps-org) ziet alles.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, distinct
from typing import Optional
from datetime import datetime, timezone, timedelta
import csv
import io

from database import get_db
from models import AuditLog, User
from auth import (
    get_current_user,
    LOGIN_RATE_LIMIT_PER_EMAIL,
    LOGIN_RATE_LIMIT_PER_IP,
    LOGIN_RATE_LIMIT_WINDOW_MIN,
)
from audit import log_action, ACTION
import json

router = APIRouter(prefix="/api/audit", tags=["Audit-log"])


def _is_platform_owner(u: User) -> bool:
    return bool(u.is_org_admin and u.organization and u.organization.name == "FieldOps")


@router.get("/logs")
def list_audit_logs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
    cursor: Optional[str] = Query(None, description="ID van laatste record uit vorige pagina"),
    action: Optional[str] = Query(None, description="Filter op exacte action-code"),
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
):
    """Lijst audit-records gepagineerd op `created_at desc`. Cursor-based: geef
    de `id` van de laatste record uit de vorige pagina mee."""
    if not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Alleen beheerders mogen het audit-log inzien")

    q = db.query(AuditLog)

    # Scope: platform-eigenaar ziet alles, andere admins alleen eigen org
    if not _is_platform_owner(current_user):
        q = q.filter(AuditLog.organization_id == current_user.organization_id)

    if action: q = q.filter(AuditLog.action == action)
    if entity_type: q = q.filter(AuditLog.entity_type == entity_type)
    if entity_id: q = q.filter(AuditLog.entity_id == entity_id)
    if user_id: q = q.filter(AuditLog.user_id == user_id)
    if since: q = q.filter(AuditLog.created_at >= since)
    if until: q = q.filter(AuditLog.created_at <= until)

    if cursor:
        cursor_rec = db.query(AuditLog).filter(AuditLog.id == cursor).first()
        if cursor_rec:
            q = q.filter(AuditLog.created_at < cursor_rec.created_at)

    rows = q.order_by(desc(AuditLog.created_at), desc(AuditLog.id)).limit(limit).all()

    items = [{
        "id": r.id,
        "user_id": r.user_id,
        "user_email": r.user_email,
        "organization_id": r.organization_id,
        "action": r.action,
        "entity_type": r.entity_type,
        "entity_id": r.entity_id,
        "details": json.loads(r.details) if r.details else None,
        "ip_address": r.ip_address,
        "request_id": r.request_id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]

    return {
        "items": items,
        "next_cursor": items[-1]["id"] if len(items) == limit else None,
    }


@router.get("/actions")
def list_audit_actions(current_user: User = Depends(get_current_user)):
    """Geef de bekende action-codes terug — handig voor UI-dropdowns."""
    if not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Alleen beheerders mogen het audit-log inzien")
    from audit import ACTION
    return [v for k, v in vars(ACTION).items() if not k.startswith("_") and isinstance(v, str)]


# Procurement-grade CSV-export voor de audit-log. Rekenkamer/ISO27001/SOC2-audits
# vragen exporteerbare logs in archiefformaat. RFC 4180-conform; UTF-8 met BOM
# zodat Excel-NL het direct opent.
_CSV_HEADERS = [
    "id", "created_at_utc", "organization_id", "user_id", "user_email",
    "action", "entity_type", "entity_id", "ip_address", "request_id", "details_json",
]
EXPORT_MAX_ROWS = 50_000  # bovengrens om memory + sales-tabblad redelijk te houden


@router.get("/logs/export.csv")
def export_audit_logs_csv(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    action: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
):
    """Audit-log als CSV. Alleen admins; scope is identiek aan `/logs`.

    Filter-parameters identiek aan `GET /logs` zodat de UI dezelfde query
    kan hergebruiken voor "exporteer huidige weergave".
    """
    if not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Alleen beheerders mogen het audit-log inzien")

    q = db.query(AuditLog)
    if not _is_platform_owner(current_user):
        q = q.filter(AuditLog.organization_id == current_user.organization_id)
    if action: q = q.filter(AuditLog.action == action)
    if entity_type: q = q.filter(AuditLog.entity_type == entity_type)
    if entity_id: q = q.filter(AuditLog.entity_id == entity_id)
    if user_id: q = q.filter(AuditLog.user_id == user_id)
    if since: q = q.filter(AuditLog.created_at >= since)
    if until: q = q.filter(AuditLog.created_at <= until)

    rows = q.order_by(desc(AuditLog.created_at), desc(AuditLog.id)).limit(EXPORT_MAX_ROWS).all()

    # In-memory CSV — voor 50k rijen ruim binnen Render-tier RAM
    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM voor Excel-NL
    w = csv.writer(buf, dialect="excel", lineterminator="\r\n")
    w.writerow(_CSV_HEADERS)
    for r in rows:
        w.writerow([
            r.id,
            r.created_at.isoformat() if r.created_at else "",
            r.organization_id or "",
            r.user_id or "",
            r.user_email or "",
            r.action,
            r.entity_type or "",
            r.entity_id or "",
            r.ip_address or "",
            r.request_id or "",
            r.details or "",
        ])
    csv_bytes = buf.getvalue().encode("utf-8")

    # Eigen export ook auditeren — zelf onderdeel van het audit-spoor.
    log_action(db, request, current_user,
               action=ACTION.AUDIT_EXPORT,
               entity_type="audit_log", entity_id=None,
               extra={"row_count": len(rows),
                      "filters": {k: v for k, v in {
                          "action": action, "entity_type": entity_type,
                          "entity_id": entity_id, "user_id": user_id,
                          "since": since.isoformat() if since else None,
                          "until": until.isoformat() if until else None,
                      }.items() if v}})

    fname = f"fieldops-auditlog-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.csv"
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Cache-Control": "no-store",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Failed-login monitor — admin-zichtbaar overzicht van brute-force pogingen.
# Aggregaties op `auth.login.failed` events in een rolling window. Toont per
# email + per IP wie er hamert, plus de meest recente events met reason.
# Data bestaat al via de bestaande rate-limit-pipeline; deze endpoint maakt
# het zichtbaar voor admins zonder dat ze door het ruwe audit-log moeten
# spitten.
# ─────────────────────────────────────────────────────────────────────────────

FAILED_LOGIN_ACTION = "auth.login.failed"
FAILED_LOGIN_MAX_WINDOW_MIN = 1440   # 24u — verder terug bevraagt CSV-export
FAILED_LOGIN_MIN_WINDOW_MIN = 5
FAILED_LOGIN_MAX_RECENT = 200


def _extract_reason(details_json: Optional[str]) -> Optional[str]:
    """Haal de reason-tag uit het details-veld als die bestaat. We loggen
    `extra.reason` bij login-failures (bv. invalid_credentials, deactivated).
    Bij oude/onverwachte structuur valt het terug op None."""
    if not details_json:
        return None
    try:
        d = json.loads(details_json)
    except (TypeError, ValueError):
        return None
    extra = d.get("extra") if isinstance(d, dict) else None
    if isinstance(extra, dict):
        r = extra.get("reason")
        if isinstance(r, str):
            return r
    return None


@router.get("/failed-logins")
def failed_login_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    window_minutes: int = Query(60, ge=FAILED_LOGIN_MIN_WINDOW_MIN, le=FAILED_LOGIN_MAX_WINDOW_MIN),
    top_n: int = Query(10, ge=1, le=50),
    recent_n: int = Query(50, ge=1, le=FAILED_LOGIN_MAX_RECENT),
):
    """Aggregeer mislukte login-pogingen in het opgegeven window.

    Scope: platform-eigenaar (FieldOps-org) ziet alle organisaties; andere
    org-admins zien alleen pogingen tegen hun eigen org-emails. Pogingen
    op onbekende emails (user_email is NULL) worden alleen getoond aan
    de platform-eigenaar — voor andere orgs zijn die niet attribueerbaar.
    """
    if not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Alleen beheerders mogen dit overzicht inzien")

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    is_platform = _is_platform_owner(current_user)

    base = db.query(AuditLog).filter(
        AuditLog.action == FAILED_LOGIN_ACTION,
        AuditLog.created_at >= cutoff,
    )
    if not is_platform:
        # Org-admin scope: kijk alleen naar pogingen die te koppelen zijn aan
        # deze organisatie. Pogingen op onbekende emails (user_email NULL,
        # organization_id NULL) blijven onzichtbaar omdat we ze niet veilig
        # aan één org kunnen toewijzen.
        base = base.filter(AuditLog.organization_id == current_user.organization_id)

    # ── Totals ────────────────────────────────────────────────────────────
    total_attempts = base.count()
    distinct_emails = base.with_entities(
        func.count(distinct(AuditLog.user_email))
    ).filter(AuditLog.user_email.isnot(None)).scalar() or 0
    distinct_ips = base.with_entities(
        func.count(distinct(AuditLog.ip_address))
    ).filter(AuditLog.ip_address.isnot(None)).scalar() or 0

    # ── Top emails ────────────────────────────────────────────────────────
    email_rows = base.with_entities(
        AuditLog.user_email,
        func.count(AuditLog.id).label("attempts"),
        func.count(distinct(AuditLog.ip_address)).label("ip_count"),
        func.max(AuditLog.created_at).label("last_attempt"),
    ).filter(AuditLog.user_email.isnot(None)).group_by(
        AuditLog.user_email
    ).order_by(desc("attempts")).limit(top_n).all()

    top_emails = [{
        "email": r.user_email,
        "attempts": int(r.attempts),
        "ip_count": int(r.ip_count or 0),
        "last_attempt_at": r.last_attempt.isoformat() if r.last_attempt else None,
        "blocked": int(r.attempts) >= LOGIN_RATE_LIMIT_PER_EMAIL,
    } for r in email_rows]

    # ── Top IPs ───────────────────────────────────────────────────────────
    ip_rows = base.with_entities(
        AuditLog.ip_address,
        func.count(AuditLog.id).label("attempts"),
        func.count(distinct(AuditLog.user_email)).label("email_count"),
        func.max(AuditLog.created_at).label("last_attempt"),
    ).filter(AuditLog.ip_address.isnot(None)).group_by(
        AuditLog.ip_address
    ).order_by(desc("attempts")).limit(top_n).all()

    top_ips = [{
        "ip": r.ip_address,
        "attempts": int(r.attempts),
        "email_count": int(r.email_count or 0),
        "last_attempt_at": r.last_attempt.isoformat() if r.last_attempt else None,
        "blocked": int(r.attempts) >= LOGIN_RATE_LIMIT_PER_IP,
    } for r in ip_rows]

    # ── Recente events ────────────────────────────────────────────────────
    recent_rows = base.order_by(
        desc(AuditLog.created_at), desc(AuditLog.id)
    ).limit(recent_n).all()

    recent = [{
        "id": r.id,
        "email": r.user_email,
        "ip": r.ip_address,
        "reason": _extract_reason(r.details),
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in recent_rows]

    return {
        "window_minutes": window_minutes,
        "scope": "platform" if is_platform else "organization",
        "thresholds": {
            "per_email": LOGIN_RATE_LIMIT_PER_EMAIL,
            "per_ip": LOGIN_RATE_LIMIT_PER_IP,
            "rate_limit_window_min": LOGIN_RATE_LIMIT_WINDOW_MIN,
        },
        "totals": {
            "attempts": int(total_attempts),
            "distinct_emails": int(distinct_emails),
            "distinct_ips": int(distinct_ips),
        },
        "top_emails": top_emails,
        "top_ips": top_ips,
        "recent": recent,
    }
