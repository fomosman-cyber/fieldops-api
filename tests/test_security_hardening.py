"""Security-hardening tests (audit I4, I2, I21).

- I4: /api/demo/email-* zijn admin-only + lekken de RESEND-key-prefix niet meer.
- I2: Shopify-webhook faalt dicht zonder secret in prod + geeft temp_password
      niet meer terug in de response.
- I21: SSRF-guard op uitgaande webhook-URLs.
"""

import base64
import hashlib
import hmac
import json

from tests.conftest import auth
from webhooks import is_safe_webhook_url


# ─── I4: demo email-debug endpoints ───────────────────────────────────────────

def test_email_health_requires_auth(client):
    r = client.get("/api/demo/email-health")
    assert r.status_code in (401, 403)


def test_email_test_requires_auth(client):
    r = client.post("/api/demo/email-test", params={"to": "x@y.nl"})
    assert r.status_code in (401, 403)


def test_email_health_admin_ok_without_key_leak(client, admin_user):
    r = client.get("/api/demo/email-health", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "resend_api_key_set" in body
    # De key-prefix mag NOOIT meer in de response zitten
    assert "resend_api_key_prefix" not in body


def test_email_test_forbidden_for_viewer(client, viewer_user):
    r = client.post("/api/demo/email-test", params={"to": "x@y.nl"},
                    headers=auth(viewer_user))
    assert r.status_code == 403


# ─── I2: Shopify-webhook ───────────────────────────────────────────────────────

_SECRET = "test-shopify-secret"


def _signed_body(payload: dict) -> tuple[bytes, str]:
    body = json.dumps(payload).encode("utf-8")
    sig = base64.b64encode(
        hmac.new(_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("utf-8")
    return body, sig


def test_shopify_order_paid_omits_temp_password(client, monkeypatch):
    monkeypatch.setattr("routers.shopify_router.SHOPIFY_API_SECRET", _SECRET)
    body, sig = _signed_body({
        "email": "shopify-buyer@new-org.nl",
        "customer": {"id": 1, "first_name": "Koen", "last_name": "Koper"},
        "line_items": [{"title": "FieldOps Professional"}],
    })
    r = client.post("/api/shopify/webhook/order-paid", content=body,
                    headers={"X-Shopify-Hmac-Sha256": sig,
                             "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["status"] == "created"
    # temp_password mag niet meer lekken in de response (gaat naar Shopify-logs)
    assert "temp_password" not in res


def test_shopify_webhook_rejects_bad_hmac(client, monkeypatch):
    monkeypatch.setattr("routers.shopify_router.SHOPIFY_API_SECRET", _SECRET)
    body = json.dumps({"email": "x@y.nl"}).encode("utf-8")
    r = client.post("/api/shopify/webhook/order-paid", content=body,
                    headers={"X-Shopify-Hmac-Sha256": "ongeldig",
                             "Content-Type": "application/json"})
    assert r.status_code == 401


def test_shopify_webhook_blocks_when_no_secret_in_production(client, monkeypatch):
    # Geen secret + 'productie' → fail closed (anders open org+admin-creatie)
    monkeypatch.setattr("routers.shopify_router.SHOPIFY_API_SECRET", "")
    monkeypatch.setattr("routers.shopify_router._is_production", lambda: True)
    body = json.dumps({"email": "x@y.nl"}).encode("utf-8")
    r = client.post("/api/shopify/webhook/order-paid", content=body,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 401


# ─── I21: SSRF-guard op uitgaande webhook-URLs ─────────────────────────────────

def test_ssrf_blocks_non_https():
    ok, _ = is_safe_webhook_url("http://example.com/hook")
    assert ok is False


def test_ssrf_blocks_loopback_and_private_and_metadata():
    for url in (
        "https://127.0.0.1/hook",
        "https://10.0.0.5/hook",
        "https://192.168.1.10/hook",
        "https://169.254.169.254/latest/meta-data",  # cloud-metadata
        "https://[::1]/hook",
    ):
        ok, reason = is_safe_webhook_url(url)
        assert ok is False, f"{url} had geblokkeerd moeten worden"


def test_ssrf_allows_public_ip_literal():
    ok, reason = is_safe_webhook_url("https://8.8.8.8/hook")
    assert ok is True, reason


def test_ssrf_allows_unresolvable_host():
    # Onresolveerbaar = onbereikbaar → niet blokkeren (httpx faalt dan zelf)
    ok, _ = is_safe_webhook_url("https://nonexistent.invalid/hook")
    assert ok is True
