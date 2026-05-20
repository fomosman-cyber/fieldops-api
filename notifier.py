"""Notifier — helper voor het aanmaken van Notification-records.

Gebruik vanuit andere routers:
    from notifier import notify
    notify(db, user_id=assigned_user.id, organization_id=org.id,
           notif_type='assigned', title='Toegewezen aan: ' + m.title,
           link_type='melding', link_id=m.id, icon='🎯')

Best-effort: fout in notify mag nooit hoofdactie blokkeren (try/except).

Toekomstig: email-trigger via send_email() voor specifieke notif_types
(bv. 'assigned' triggert email als user.notification_email_enabled is true).
Voor nu: alleen in-app.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
import sys

from sqlalchemy.orm import Session

from models import Notification


def notify(
    db: Session,
    *,
    user_id: str,
    organization_id: str,
    notif_type: str,
    title: str,
    body: Optional[str] = None,
    icon: Optional[str] = None,
    link_type: Optional[str] = None,
    link_id: Optional[str] = None,
    commit: bool = True,
) -> Optional[Notification]:
    """Best-effort notification create. Faalt nooit de hoofd-actie."""
    if not user_id or not organization_id:
        return None
    try:
        n = Notification(
            user_id=user_id,
            organization_id=organization_id,
            notif_type=notif_type,
            title=title[:255],
            body=body,
            icon=icon,
            link_type=link_type,
            link_id=link_id,
        )
        db.add(n)
        if commit:
            db.commit()
            db.refresh(n)
        return n
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"[notifier] notify failed: {e}", file=sys.stderr)
        return None
