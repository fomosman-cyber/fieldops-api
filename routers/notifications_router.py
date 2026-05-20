"""Notifications router — in-app meldingen.

Endpoints:
  GET    /api/notifications              - eigen notifications (unread first)
  GET    /api/notifications/unread-count - count voor bell-badge
  POST   /api/notifications/{id}/read    - markeer als gelezen
  POST   /api/notifications/mark-all-read - markeer alle als gelezen
  POST   /api/notifications/{id}/archive - archiveer (UI hide)
  DELETE /api/notifications/clear-archived - verwijder oude archiveer-records

Notifications worden NIET via deze endpoints aangemaakt — alleen door de
`notify(...)` helper in andere routers. Externe systemen kunnen dat niet
direct (zou anders spam-vector zijn).
"""
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import Notification, User
from auth import get_current_user

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


@router.get("/")
def list_notifications(
    limit: int = 50,
    include_read: bool = True,
    include_archived: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Eigen notifications (gesorteerd: nieuwste eerst, ongelezen prioriteit)."""
    limit = max(1, min(limit, 200))
    q = db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.organization_id == current_user.organization_id,
    )
    if not include_archived:
        q = q.filter(Notification.archived_at.is_(None))
    if not include_read:
        q = q.filter(Notification.read_at.is_(None))

    items = q.order_by(
        Notification.read_at.is_(None).desc(),  # ongelezen eerst
        Notification.created_at.desc(),
    ).limit(limit).all()

    return {
        "total": len(items),
        "items": [{
            "id": n.id,
            "notif_type": n.notif_type,
            "title": n.title,
            "body": n.body,
            "icon": n.icon,
            "link_type": n.link_type,
            "link_id": n.link_id,
            "read": n.read_at is not None,
            "read_at": n.read_at.isoformat() if n.read_at else None,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        } for n in items],
    }


@router.get("/unread-count")
def unread_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aantal ongelezen notifications voor de bell-badge."""
    n = (db.query(func.count(Notification.id))
           .filter(Notification.user_id == current_user.id,
                   Notification.organization_id == current_user.organization_id,
                   Notification.read_at.is_(None),
                   Notification.archived_at.is_(None))
           .scalar() or 0)
    return {"unread": int(n)}


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Markeer specifieke notification als gelezen."""
    n = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user.id,
    ).first()
    if not n:
        raise HTTPException(404, "Notification niet gevonden")
    if not n.read_at:
        n.read_at = datetime.now(timezone.utc)
        db.commit()
    return {"id": n.id, "read": True}


@router.post("/mark-all-read")
def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Markeer alle ongelezen notifications als gelezen."""
    now = datetime.now(timezone.utc)
    updated = (db.query(Notification)
                 .filter(Notification.user_id == current_user.id,
                         Notification.organization_id == current_user.organization_id,
                         Notification.read_at.is_(None))
                 .update({"read_at": now}, synchronize_session=False))
    db.commit()
    return {"marked_read": updated}


@router.post("/{notification_id}/archive")
def archive_notification(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Archiveer een notification (verbergen uit lijst, niet verwijderen)."""
    n = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user.id,
    ).first()
    if not n:
        raise HTTPException(404, "Notification niet gevonden")
    n.archived_at = datetime.now(timezone.utc)
    if not n.read_at:
        n.read_at = n.archived_at
    db.commit()
    return {"id": n.id, "archived": True}


@router.delete("/clear-archived")
def clear_archived(
    older_than_days: int = 30,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verwijder gearchiveerde notifications ouder dan X dagen."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    deleted = (db.query(Notification)
                 .filter(Notification.user_id == current_user.id,
                         Notification.archived_at.isnot(None),
                         Notification.archived_at < cutoff)
                 .delete(synchronize_session=False))
    db.commit()
    return {"deleted": deleted}
