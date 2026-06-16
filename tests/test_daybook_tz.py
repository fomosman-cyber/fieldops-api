"""Tests voor werkdagboek-tijdzone (bug #11).

occurred_at/created_at staan als naïeve UTC in de DB (DateTime-kolom zonder
timezone). Bij serialisatie moeten ze een expliciete UTC-offset krijgen,
anders interpreteert de browser ze als lokale tijd en toont het werkdagboek
2 uur te vroeg (15:26 i.p.v. 17:26 in Europe/Amsterdam-zomertijd).
"""
from datetime import datetime, timezone

from tests.conftest import auth
from routers.daybook_router import _iso_utc


def test_iso_utc_adds_offset_to_naive():
    """Naïeve datetime krijgt expliciete UTC-offset."""
    naive = datetime(2026, 6, 15, 15, 26, 0)
    assert _iso_utc(naive) == "2026-06-15T15:26:00+00:00"


def test_iso_utc_preserves_aware():
    """Reeds tz-aware datetime blijft correct."""
    aware = datetime(2026, 6, 15, 15, 26, 0, tzinfo=timezone.utc)
    assert _iso_utc(aware) == "2026-06-15T15:26:00+00:00"


def test_iso_utc_none():
    assert _iso_utc(None) is None


def test_daybook_day_returns_utc_offset(client, admin_user):
    """GET /api/daybook/day serialiseert occurred_at/created_at met UTC-offset.

    Zonder offset zou new Date() in de browser de naïeve UTC als lokale tijd
    lezen → werkdagboek 2 uur te vroeg.
    """
    from database import SessionLocal
    from models import DaybookEntry

    naive_utc = datetime.now(timezone.utc).replace(tzinfo=None)  # zoals opgeslagen in prod
    db = SessionLocal()
    try:
        db.add(DaybookEntry(
            user_id=admin_user.id,
            organization_id=admin_user.organization_id,
            entry_type="manual_note",
            source="manual",
            occurred_at=naive_utc,
            title="TZ-test entry",
        ))
        db.commit()
    finally:
        db.close()

    today = datetime.now(timezone.utc).date().isoformat()
    r = client.get(f"/api/daybook/day?date={today}", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    assert entries, "verwacht minstens 1 entry op vandaag"
    occ = entries[0]["occurred_at"]
    assert occ.endswith("+00:00"), f"occurred_at mist UTC-offset: {occ}"
    assert entries[0]["created_at"].endswith("+00:00"), "created_at mist UTC-offset"
