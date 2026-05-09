"""Tests voor Google Calendar event-payload formatting.

Specifiek: end.date moet strikt > start.date zijn voor all-day events,
anders wordt het een 0-duration event dat niet in Google Calendar UI zichtbaar is.
"""

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import google_integration as gi


def test_build_melding_event_end_date_is_one_day_after_start():
    """All-day events vereisen end.date > start.date (Google Calendar API)."""
    melding = SimpleNamespace(
        id="m-1", title="Schade", description="test",
        organization_id="org-1",
        created_at=datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc),
        lat=None, lng=None,
    )
    event = gi.build_melding_event(melding)
    assert event["start"]["date"] == "2026-05-09"
    assert event["end"]["date"] == "2026-05-10"   # strikt 1 dag later


def test_build_melding_event_handles_missing_created_at():
    """Bij ontbrekende created_at valt 't terug op vandaag — end nog steeds +1."""
    melding = SimpleNamespace(
        id="m-2", title="x", description=None, organization_id="org-1",
        created_at=None, lat=None, lng=None,
    )
    event = gi.build_melding_event(melding)
    start = datetime.fromisoformat(event["start"]["date"])
    end = datetime.fromisoformat(event["end"]["date"])
    assert (end - start) == timedelta(days=1)


def test_build_melding_event_includes_summary_and_extended_props():
    melding = SimpleNamespace(
        id="m-3", title="Wegdek-schade", description="diepe scheur",
        organization_id="org-7",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        lat=52.0, lng=4.3,
    )
    event = gi.build_melding_event(melding)
    assert event["summary"] == "FieldOps: Wegdek-schade"
    assert event["extendedProperties"]["private"]["fieldops_melding_id"] == "m-3"
    assert event["extendedProperties"]["private"]["fieldops_org_id"] == "org-7"
    assert event["location"] == "52.0,4.3"


def test_build_melding_event_uses_asset_location_description():
    melding = SimpleNamespace(
        id="m-4", title="x", description=None, organization_id="org-1",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        lat=52.0, lng=4.3,
    )
    asset = SimpleNamespace(
        code="WL-01", name="Verdilaan",
        location_description="A4 km 12.5",
    )
    event = gi.build_melding_event(melding, asset=asset)
    assert event["location"] == "A4 km 12.5"
    assert "Asset: WL-01" in event["description"]
    assert "Locatie: A4 km 12.5" in event["description"]
