"""Tests voor het Shopify-webhook-schrijfpad + centrale reserved-org-name-guard.

De webhook /api/shopify/webhook/order-paid maakt een Organization + org-admin
aan. Zonder guard kon een ONGEAUTHENTICEERDE aanvaller (secret niet gezet =
verificatie werd overgeslagen) een org "FieldOps" met is_org_admin-account
aanmaken en zo platform-eigenaar worden. Deze tests bewijzen:

1. Fail-closed: zonder SHOPIFY_API_SECRET wordt de webhook geweigerd (503).
2. Met secret is een geldige HMAC-signature verplicht (401 anders).
3. De gereserveerde naam "FieldOps" kan via de webhook nooit een org-naam worden.
4. Het temp_password lekt niet meer via de HTTP-response.
5. De guard zit centraal op het Organization-model en dekt ook admin-API
   en demo-approve.
"""

import base64
import hashlib
import hmac
import json

import pytest

from tests.conftest import auth
from database import SessionLocal
from models import (
    Organization, DemoRequest, ReservedOrgNameError, is_reserved_org_name,
)

SECRET = "test-shopify-secret"


def _signed_post(client, path, payload, secret=SECRET, tamper=False):
    body = json.dumps(payload).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode("utf-8")
    if tamper:
        signature = "x" + signature[1:]
    return client.post(path, content=body, headers={
        "Content-Type": "application/json",
        "X-Shopify-Hmac-Sha256": signature,
    })


def _order_payload(company="FieldOps", email="attacker@evil.example"):
    return {
        "email": email,
        "customer": {"id": 12345, "first_name": "Evil", "last_name": "Actor",
                     "company": company},
        "line_items": [{"title": "FieldOps Professional"}],
    }


# ── 1. Fail-closed zonder secret ─────────────────────────────────────────────

def test_webhook_zonder_secret_wordt_geweigerd(client, monkeypatch):
    """KRITIEK: als SHOPIFY_API_SECRET niet gezet is mag de webhook NOOIT
    ongeauthenticeerd accounts aanmaken — voorheen werd verificatie dan
    volledig overgeslagen."""
    monkeypatch.delenv("SHOPIFY_API_SECRET", raising=False)
    r = client.post("/api/shopify/webhook/order-paid",
                    json=_order_payload())
    assert r.status_code == 503
    db = SessionLocal()
    try:
        assert db.query(Organization).count() == 0
    finally:
        db.close()


def test_cancel_webhook_zonder_secret_wordt_geweigerd(client, monkeypatch):
    monkeypatch.delenv("SHOPIFY_API_SECRET", raising=False)
    r = client.post("/api/shopify/webhook/subscription-cancelled",
                    json={"email": "iemand@test.nl"})
    assert r.status_code == 503


# ── 2. Signature verplicht ───────────────────────────────────────────────────

def test_webhook_met_foute_signature_401(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    r = _signed_post(client, "/api/shopify/webhook/order-paid",
                     _order_payload(), tamper=True)
    assert r.status_code == 401


def test_webhook_zonder_signature_header_401(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    r = client.post("/api/shopify/webhook/order-paid", json=_order_payload())
    assert r.status_code == 401


# ── 3+4. Reserved-name guard + geen wachtwoord-lek ───────────────────────────

def test_webhook_kan_geen_fieldops_org_maken(client, monkeypatch):
    """KRITIEK: zelfs een geldig gesigneerde order met company='FieldOps'
    mag geen org met de gereserveerde naam opleveren."""
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    r = _signed_post(client, "/api/shopify/webhook/order-paid",
                     _order_payload(company="FieldOps"))
    assert r.status_code == 200
    assert r.json()["status"] == "created"
    db = SessionLocal()
    try:
        for org in db.query(Organization).all():
            assert not is_reserved_org_name(org.name)
    finally:
        db.close()


def test_webhook_fieldops_varianten_geblokkeerd(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    for i, variant in enumerate(("fieldops", "FIELDOPS", "  FieldOps  ", "Field Ops")):
        _signed_post(client, "/api/shopify/webhook/order-paid",
                     _order_payload(company=variant,
                                    email=f"attacker{i}@evil.example"))
    db = SessionLocal()
    try:
        for org in db.query(Organization).all():
            assert not is_reserved_org_name(org.name)
    finally:
        db.close()


def test_webhook_response_lekt_geen_wachtwoord(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    r = _signed_post(client, "/api/shopify/webhook/order-paid",
                     _order_payload(company="Aannemer Jansen BV",
                                    email="klant@jansen.nl"))
    assert r.status_code == 200
    assert "temp_password" not in r.json()
    assert "password" not in json.dumps(r.json()).lower()


def test_webhook_legitieme_order_werkt_gewoon(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    r = _signed_post(client, "/api/shopify/webhook/order-paid",
                     _order_payload(company="Gemeente Voorbeeld",
                                    email="beheer@voorbeeld.nl"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "created"
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(
            Organization.id == body["organization_id"]).first()
        assert org.name == "Gemeente Voorbeeld"
    finally:
        db.close()


# ── 5. Centrale model-guard ──────────────────────────────────────────────────

def test_model_blokkeert_gereserveerde_naam_hard():
    """De guard zit op het model zelf: elk (toekomstig) schrijfpad is gedekt."""
    with pytest.raises(ReservedOrgNameError):
        Organization(name="FieldOps")
    with pytest.raises(ReservedOrgNameError):
        Organization(name="  field ops  ")


def test_model_opt_in_voor_platform_bootstrap():
    org = Organization()
    org.allow_reserved_name = True
    org.name = "FieldOps"  # mag: expliciete bootstrap-opt-in
    assert org.name == "FieldOps"


def test_admin_kan_geen_tweede_fieldops_org_aanmaken(client, platform_owner):
    r = client.post("/api/admin/organizations", json={
        "name": "FieldOps",
        "plan": "starter",
        "max_users": 5,
        "admin_email": "tweede-owner@evil.example",
        "admin_password": "SterkWachtwoord1!",
        "admin_first_name": "Evil",
        "admin_last_name": "Actor",
    }, headers=auth(platform_owner))
    assert r.status_code == 400
    assert "gereserveerd" in r.json()["detail"].lower()


def test_demo_approve_met_fieldops_naam_geblokkeerd(client, platform_owner):
    db = SessionLocal()
    try:
        demo = DemoRequest(first_name="Evil", last_name="Actor",
                           company_name="FieldOps",
                           email="demo-attacker@evil.example")
        db.add(demo); db.commit(); db.refresh(demo)
        demo_id = demo.id
    finally:
        db.close()
    r = client.post(f"/api/admin/demo/{demo_id}/approve",
                    headers=auth(platform_owner))
    assert r.status_code == 400
    assert "gereserveerd" in r.json()["detail"].lower()
