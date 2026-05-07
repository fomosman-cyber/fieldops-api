"""Outgoing webhook tests — fanout via audit-log + HMAC-signing.

Mock httpx zodat we niet echt naar internet bellen tijdens tests.
"""

import json
from unittest.mock import patch, MagicMock

from database import SessionLocal
from models import WebhookEndpoint, WebhookDelivery
from webhooks import sign_payload, matches_pattern
from tests.conftest import auth


# ─── Helpers ──────────────────────────────────────────────────────────────

def _create_hook(client, admin_user, **overrides):
    payload = {
        "name": "Test", "url": "https://example.test/hook",
        "format_type": "generic", "secret": "shh",
        "events": ["melding.*", "webhook.test"],
    }
    payload.update(overrides)
    r = client.post("/api/webhooks/", json=payload, headers=auth(admin_user))
    assert r.status_code == 200, r.text
    return r.json()


def _mock_response(status=200, text="ok"):
    m = MagicMock()
    m.status_code = status
    m.text = text
    return m


# ─── Pure functions ──────────────────────────────────────────────────────

def test_pattern_matches_exact():
    assert matches_pattern("melding.create", "melding.create")
    assert not matches_pattern("melding.create", "asset.create")


def test_pattern_wildcard_segment():
    assert matches_pattern("melding.create", "melding.*")
    assert matches_pattern("melding.status_change", "melding.*")
    # Sterretje matcht één segment, niet meerdere niveaus diep
    assert not matches_pattern("melding.create.extra", "melding.*")


def test_pattern_full_wildcard():
    assert matches_pattern("anything.at.all", "*")


def test_signing_deterministic():
    s1 = sign_payload("secret", b"body", "1700000000")
    s2 = sign_payload("secret", b"body", "1700000000")
    assert s1 == s2
    assert len(s1) == 64  # sha256 hex


def test_signing_different_body_different_sig():
    s1 = sign_payload("secret", b"body1", "1700000000")
    s2 = sign_payload("secret", b"body2", "1700000000")
    assert s1 != s2


# ─── CRUD ────────────────────────────────────────────────────────────────

def test_create_validates_format(client, admin_user):
    r = client.post("/api/webhooks/", json={
        "name": "X", "url": "https://x.test/", "format_type": "invalid",
        "events": ["*"],
    }, headers=auth(admin_user))
    assert r.status_code == 400


def test_create_requires_events(client, admin_user):
    r = client.post("/api/webhooks/", json={
        "name": "X", "url": "https://x.test/", "format_type": "generic",
        "events": [],
    }, headers=auth(admin_user))
    assert r.status_code == 400


def test_only_admin_can_create(client, viewer_user):
    r = client.post("/api/webhooks/", json={
        "name": "X", "url": "https://x.test/", "format_type": "slack",
        "events": ["*"],
    }, headers=auth(viewer_user))
    assert r.status_code == 403


# ─── Fanout via audit-log ────────────────────────────────────────────────

@patch("httpx.Client")
def test_melding_create_triggers_webhook(mock_client_cls, client, admin_user):
    """Bij melding.create moet de matching webhook worden afgevuurd."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=_mock_response(200, "ok"))
    mock_client_cls.return_value = mock_client

    hook = _create_hook(client, admin_user)
    # Maak melding aan — moet fanout triggeren
    r = client.post("/api/meldingen/", json={"title": "x"}, headers=auth(admin_user))
    assert r.status_code == 200

    # Check dat de mock werd aangeroepen
    assert mock_client.post.called
    # Check delivery in DB
    db = SessionLocal()
    try:
        deliveries = db.query(WebhookDelivery).filter(
            WebhookDelivery.webhook_endpoint_id == hook["id"]
        ).all()
        assert any(d.action == "melding.create" for d in deliveries)
    finally:
        db.close()


@patch("httpx.Client")
def test_non_matching_event_not_delivered(mock_client_cls, client, admin_user):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=_mock_response(200, "ok"))
    mock_client_cls.return_value = mock_client

    # Hook abonneert ALLEEN op 'asset.archive'
    _create_hook(client, admin_user, events=["asset.archive"])
    # Maak melding (= melding.create) — past niet bij filter
    client.post("/api/meldingen/", json={"title": "x"}, headers=auth(admin_user))

    # post mag niet zijn aangeroepen
    assert not mock_client.post.called


@patch("httpx.Client")
def test_test_endpoint_fires(mock_client_cls, client, admin_user):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=_mock_response(200, "ok"))
    mock_client_cls.return_value = mock_client

    hook = _create_hook(client, admin_user)
    r = client.post(f"/api/webhooks/{hook['id']}/test", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["succeeded"] is True
    assert mock_client.post.called


@patch("httpx.Client")
def test_failed_delivery_logged(mock_client_cls, client, admin_user):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=_mock_response(500, "server error"))
    mock_client_cls.return_value = mock_client

    hook = _create_hook(client, admin_user)
    client.post("/api/meldingen/", json={"title": "x"}, headers=auth(admin_user))

    deliveries = client.get(f"/api/webhooks/{hook['id']}/deliveries",
                            headers=auth(admin_user)).json()
    failed = [d for d in deliveries if not d["succeeded"]]
    assert len(failed) >= 1
    assert failed[0]["status_code"] == 500
