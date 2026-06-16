"""Tests voor melding↔asset-koppeling (bug: meldingen koppelden niet aan assets).

- POST koppelt nu automatisch aan de dichtstbijzijnde asset (binnen 200m) als
  er geen handmatige asset is gekozen.
- Een handmatig gekozen asset wordt nooit overschreven.
- PATCH zónder asset_id behoudt de bestaande koppeling (het contract waar de
  frontend-guard op leunt om data-verlies bij bewerken te voorkomen).
"""
from database import SessionLocal
from models import Asset
from tests.conftest import auth


def _asset(org, admin_user, *, code, lat, lng):
    db = SessionLocal()
    try:
        a = Asset(code=code, asset_type="put", organization_id=org.id,
                  created_by=admin_user.id, lat=lat, lng=lng)
        db.add(a); db.commit(); db.refresh(a)
        return a
    finally:
        db.close()


def test_create_autolinks_nearby_asset(client, org, admin_user):
    asset = _asset(org, admin_user, code="PUT-1", lat=52.1000, lng=5.1000)
    r = client.post("/api/meldingen/", headers=auth(admin_user), json={
        "title": "Scheur", "lat": 52.1008, "lng": 5.1000,  # ~89m
    })
    assert r.status_code == 200, r.text
    assert r.json()["asset_id"] == asset.id


def test_create_no_autolink_when_far(client, org, admin_user):
    _asset(org, admin_user, code="PUT-2", lat=52.1000, lng=5.1000)
    r = client.post("/api/meldingen/", headers=auth(admin_user), json={
        "title": "Ver weg", "lat": 52.1030, "lng": 5.1000,  # ~334m > 200m
    })
    assert r.status_code == 200, r.text
    assert r.json()["asset_id"] is None


def test_create_no_autolink_without_gps(client, org, admin_user):
    _asset(org, admin_user, code="PUT-3", lat=52.1, lng=5.1)
    r = client.post("/api/meldingen/", headers=auth(admin_user), json={"title": "Geen GPS"})
    assert r.status_code == 200, r.text
    assert r.json()["asset_id"] is None


def test_manual_asset_not_overridden_by_autolink(client, org, admin_user):
    _asset(org, admin_user, code="NEAR", lat=52.1000, lng=5.1000)
    chosen = _asset(org, admin_user, code="CHOSEN", lat=52.5000, lng=5.5000)
    r = client.post("/api/meldingen/", headers=auth(admin_user), json={
        "title": "Handmatig", "lat": 52.1001, "lng": 5.1000, "asset_id": chosen.id,
    })
    assert r.status_code == 200, r.text
    assert r.json()["asset_id"] == chosen.id


def test_patch_without_asset_id_preserves_coupling(client, org, admin_user):
    asset = _asset(org, admin_user, code="KEEP", lat=52.0, lng=5.0)
    r = client.post("/api/meldingen/", headers=auth(admin_user),
                    json={"title": "M", "asset_id": asset.id})
    mid = r.json()["id"]
    assert r.json()["asset_id"] == asset.id
    # PATCH zonder asset_id → koppeling blijft (frontend laat asset_id dan weg)
    r = client.put(f"/api/meldingen/{mid}", headers=auth(admin_user), json={"title": "M2"})
    assert r.status_code == 200, r.text
    assert r.json()["asset_id"] == asset.id


def test_patch_with_null_asset_id_unlinks(client, org, admin_user):
    asset = _asset(org, admin_user, code="UNLINK", lat=52.0, lng=5.0)
    r = client.post("/api/meldingen/", headers=auth(admin_user),
                    json={"title": "M", "asset_id": asset.id})
    mid = r.json()["id"]
    # Expliciet null → ontkoppelen blijft mogelijk
    r = client.put(f"/api/meldingen/{mid}", headers=auth(admin_user), json={"asset_id": None})
    assert r.status_code == 200, r.text
    assert r.json()["asset_id"] is None


def test_create_stores_photo_url(client, org, admin_user):
    """Foto die met de melding meekomt wordt opgeslagen en teruggegeven."""
    r = client.post("/api/meldingen/", headers=auth(admin_user), json={
        "title": "Met foto", "photo_url": "data:image/jpeg;base64,/9j/AAAQ",
    })
    assert r.status_code == 200, r.text
    assert (r.json()["photo_url"] or "").startswith("data:image/")
