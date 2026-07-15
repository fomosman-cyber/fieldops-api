"""Performance/OOM-tests (audit V3, V4, V5).

- V3: composite-indexen op meldingen(org,status) + assets(archived_at).
- V4: meldingen-lijst laadt de base64-foto's niet meer, maar geeft has_photo
      via func.length() (verborgen OOM weg).
- V5: base64 -> R2/S3 backfill-endpoint (dry-run/batch/idempotent, admin-only).
"""

import base64

from sqlalchemy import inspect

from database import engine
from tests.conftest import auth


# Een base64-foto die groot genoeg is om realistisch te zijn.
_DATA_URL = "data:image/png;base64," + base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 6000).decode("ascii")


# ─── V3: indexen ───────────────────────────────────────────────────────────────

def test_v3_melding_and_asset_indexes_exist():
    insp = inspect(engine)
    melding_idx = {i["name"] for i in insp.get_indexes("meldingen")}
    assert "ix_meldingen_org_status" in melding_idx, melding_idx
    asset_idx = {i["name"] for i in insp.get_indexes("assets")}
    assert "ix_assets_archived_at" in asset_idx, asset_idx


# ─── V4: meldingen-lijst zonder base64 ─────────────────────────────────────────

def test_v4_list_light_hides_photo_blob_but_keeps_flag(client, admin_user):
    r = client.post("/api/meldingen/",
                    json={"title": "Met foto", "photo_url": _DATA_URL},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text

    rows = client.get("/api/meldingen/", headers=auth(admin_user)).json()
    assert len(rows) == 1
    assert rows[0]["photo_url"] is None          # blob NIET in de lijst
    assert rows[0]["has_photo"] is True          # vlag wel
    assert rows[0]["has_photo_after"] is False


def test_v4_list_with_photos_returns_blob(client, admin_user):
    client.post("/api/meldingen/",
                json={"title": "Met foto", "photo_url": _DATA_URL},
                headers=auth(admin_user))
    full = client.get("/api/meldingen/?with_photos=true",
                      headers=auth(admin_user)).json()
    assert full[0]["photo_url"] == _DATA_URL
    assert full[0]["has_photo"] is True


# ─── V5: R2-backfill ────────────────────────────────────────────────────────────

def test_v5_backfill_dry_run_without_s3(client, admin_user):
    client.post("/api/meldingen/",
                json={"title": "x", "photo_url": _DATA_URL},
                headers=auth(admin_user))
    r = client.post("/api/meldingen/backfill-photos", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["s3_configured"] is False
    assert body["candidates"] >= 1
    assert body["offloaded_fields"] == 0  # niets verplaatst in dry-run


def test_v5_backfill_requires_org_admin(client, viewer_user):
    r = client.post("/api/meldingen/backfill-photos", headers=auth(viewer_user))
    assert r.status_code == 403


def test_v5_backfill_offloads_when_configured(client, admin_user, monkeypatch):
    client.post("/api/meldingen/",
                json={"title": "x", "photo_url": _DATA_URL},
                headers=auth(admin_user))
    monkeypatch.setattr("photo_storage.is_configured", lambda: True)
    monkeypatch.setattr("photo_storage.offload_photo",
                        lambda data_url, **kwargs: "https://cdn.example/p.jpg")

    r = client.post("/api/meldingen/backfill-photos?dry_run=false",
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["offloaded_fields"] >= 1
    assert body["remaining_after_batch"] == 0  # geen base64 meer over

    full = client.get("/api/meldingen/?with_photos=true",
                      headers=auth(admin_user)).json()
    assert full[0]["photo_url"] == "https://cdn.example/p.jpg"
