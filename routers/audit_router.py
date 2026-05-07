"""Read-only audit-log inzage. Alleen org-admins zien hun eigen organisatie;
platform-eigenaar (FieldOps-org) ziet alles.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional
from datetime import datetime

from database import get_db
from models import AuditLog, User
from auth import get_current_user
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
