"""IoT-binnenkomend webhook tests."""

from database import SessionLocal
from models import Melding
from tests.conftest import auth


def _create_hook(client, admin_user, **overrides):
    payload = {"name": "Sensor X", "default_priority": "hoog"}
    payload.update(overrides)
    r = client.post("/api/incoming/", json=payload, headers=auth(admin_user))
    assert r.status_code == 200, r.text
    return r.json()


def test_create_hook_returns_token(client, admin_user):
    h = _create_hook(client, admin_user)
    assert h["token"]
    assert len(h["token"]) >= 30


def test_only_admin_can_create_hook(client, viewer_user):
    r = client.post("/api/incoming/", json={"name": "X"}, headers=auth(viewer_user))
    assert r.status_code == 403


def test_ingest_creates_melding(client, admin_user):
    h = _create_hook(client, admin_user,
                     title_template="Sensor {sensor_id}: {value} {unit}")
    r = client.post(f"/api/incoming/{h['token']}", json={
        "sensor_id": "WS-12", "value": 245, "unit": "cm",
    })
    assert r.status_code == 200
    assert r.json()["received"] is True
    melding_id = r.json()["melding_id"]

    db = SessionLocal()
    try:
        m = db.query(Melding).filter(Melding.id == melding_id).first()
        assert m is not None
        assert "WS-12" in m.title
        assert m.priority == "hoog"
    finally:
        db.close()


def test_ingest_unknown_token_returns_404(client):
    r = client.post("/api/incoming/not-a-real-token",
                    json={"sensor_id": "X"})
    assert r.status_code == 404


def test_ingest_disabled_hook_returns_404(client, admin_user):
    h = _create_hook(client, admin_user)
    # Disable
    client.put(f"/api/incoming/{h['id']}", json={"enabled": False},
               headers=auth(admin_user))
    r = client.post(f"/api/incoming/{h['token']}", json={"v": 1})
    assert r.status_code == 404


def test_ingest_increments_counter(client, admin_user):
    h = _create_hook(client, admin_user)
    for _ in range(3):
        client.post(f"/api/incoming/{h['token']}", json={"v": 1})
    detail = client.get(f"/api/incoming/{h['id']}", headers=auth(admin_user)).json()
    assert detail["received_count"] == 3
    assert detail["last_received_at"] is not None


def test_rotate_token_invalidates_old(client, admin_user):
    h = _create_hook(client, admin_user)
    old = h["token"]
    rot = client.post(f"/api/incoming/{h['id']}/rotate", headers=auth(admin_user))
    assert rot.status_code == 200
    new_token = rot.json()["token"]
    assert new_token != old

    # Oude token werkt niet meer
    r = client.post(f"/api/incoming/{old}", json={"v": 1})
    assert r.status_code == 404
    # Nieuwe wel
    r2 = client.post(f"/api/incoming/{new_token}", json={"v": 1})
    assert r2.status_code == 200
