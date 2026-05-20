"""Notifier — helper voor het aanmaken van Notification-records.

Gebruik vanuit andere routers:
    from notifier import notify
    notify(db, user_id=assigned_user.id, organization_id=org.id,
           notif_type='assigned', title='Toegewezen aan: ' + m.title,
           link_type='melding', link_id=m.id, icon='🎯',
           email_context={'assigner_name': '...', 'melding_title': '...', ...})

Best-effort: fout in notify mag nooit hoofdactie blokkeren (try/except).

Email-trigger: bij notif_type='assigned' wordt automatisch een email
verstuurd via email_service.send_assignment_notification (vereist
RESEND_API_KEY env-var; zonder key faalt 'ie stilletjes).
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
import sys

from sqlalchemy.orm import Session

from models import Notification, User


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
    email_context: Optional[dict] = None,
    skip_email: bool = False,
) -> Optional[Notification]:
    """Best-effort notification create + optional email-trigger.

    Voor `notif_type='assigned'` wordt automatisch een email verstuurd naar
    de target-user.email. Pass `skip_email=True` om dat over te slaan
    (bv. bij silent system-notifications).
    """
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
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"[notifier] notify failed: {e}", file=sys.stderr)
        return None

    # Email-trigger — best-effort, faalt nooit de hoofdactie
    if not skip_email and notif_type == "assigned" and link_type == "melding":
        try:
            target = db.query(User).filter(
                User.id == user_id,
                User.is_active == True,  # noqa: E712
            ).first()
            if target and target.email and not target.email.endswith("@deleted.invalid"):
                from email_service import send_assignment_notification
                ctx = email_context or {}
                recipient_name = ((target.first_name or "") + " " + (target.last_name or "")).strip() or target.email
                ok = send_assignment_notification(
                    target.email,
                    recipient_name=recipient_name,
                    assigner_name=ctx.get("assigner_name", ""),
                    melding_title=ctx.get("melding_title", title.replace("🎯 Toegewezen: ", "").replace("🎯 ", "")),
                    melding_category=ctx.get("melding_category", ""),
                    melding_priority=ctx.get("melding_priority", "normaal"),
                    melding_id=link_id or "",
                )
                if ok and n:
                    n.email_sent_at = datetime.now(timezone.utc)
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
        except Exception as e:
            print(f"[notifier] email-trigger failed: {e}", file=sys.stderr)

    return n
