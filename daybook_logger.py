"""Helper voor het automatisch toevoegen van entries aan het werkdagboek.

Gebruik:
    from daybook_logger import log_daybook
    log_daybook(db, user_id=u.id, organization_id=u.organization_id,
                entry_type="melding_created", title="Melding aangemaakt: " + m.title,
                source_type="melding", source_id=m.id,
                lat=m.lat, lng=m.lng, project_id=m.project_id)

Failure-mode: best-effort — daybook is een notitie-systeem, geen critical
state. Als logging faalt mag de hoofd-actie niet falen. Daarom catchen we
exceptions en loggen alleen naar stderr.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
import sys

from sqlalchemy.orm import Session

from models import DaybookEntry


def log_daybook(
    db: Session,
    *,
    user_id: str,
    organization_id: str,
    entry_type: str,
    title: str,
    description: Optional[str] = None,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    duration_minutes: Optional[int] = None,
    project_id: Optional[str] = None,
    commit: bool = True,
) -> Optional[DaybookEntry]:
    """Voeg een auto-entry toe aan het dagboek.

    Best-effort: faalt nooit (returnt None bij fout). Roep dit aan na de
    hoofd-actie (melding aanmaken, status wijzigen, etc) zodat een dagboek-
    fout de hoofdactie nooit blokkeert.
    """
    try:
        entry = DaybookEntry(
            user_id=user_id,
            organization_id=organization_id,
            entry_type=entry_type,
            source="auto",
            source_type=source_type,
            source_id=source_id,
            occurred_at=occurred_at or datetime.now(timezone.utc),
            title=title[:255],
            description=description,
            lat=lat,
            lng=lng,
            duration_minutes=duration_minutes,
            project_id=project_id,
        )
        db.add(entry)
        if commit:
            db.commit()
            db.refresh(entry)
        return entry
    except Exception as e:
        # Best-effort: dagboek faalt nooit de hoofdactie
        try:
            db.rollback()
        except Exception:
            pass
        print(f"[daybook_logger] log_daybook failed: {e}", file=sys.stderr)
        return None
