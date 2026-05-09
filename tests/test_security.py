"""Security hardening tests:

- Server-side password complexity (validate_password_strength).
- Login brute-force rate limiting via audit_log.
- Defense-in-depth voor admin-create-user en password-reset flows.
"""

from datetime import datetime, timezone, timedelta
import pytest

from auth import (
    validate_password_strength,
    check_login_rate_limit,
    LOGIN_RATE_LIMIT_PER_EMAIL,
    LOGIN_RATE_LIMIT_PER_IP,
    LOGIN_RATE_LIMIT_WINDOW_MIN,
)
from database import SessionLocal
from models import AuditLog
from tests.conftest import auth
from fastapi import HTTPException


# ─── validate_password_strength ───────────────────────────────────────────────

def test_password_too_short_rejected():
    with pytest.raises(HTTPException) as ex:
        validate_password_strength("Aa1!")
    assert ex.value.status_code == 400
    assert "minimaal 8" in ex.value.detail


def test_password_only_two_categories_rejected():
    # 8+ chars maar alleen lowercase + digit (2/4 categorieën)
    with pytest.raises(HTTPException) as ex:
        validate_password_strength("password1")
    assert ex.value.status_code == 400


def test_password_three_categories_accepted():
    # lowercase + uppercase + digit (3/4)
    validate_password_strength("Password1")
    # lowercase + digit + symbol (3/4)
    validate_password_strength("password1!")


def test_password_four_categories_accepted():
    validate_password_strength("Strong1Password!")


def test_password_empty_rejected():
    with pytest.raises(HTTPException) as ex:
        validate_password_strength("")
    assert ex.value.status_code == 400


# ─── Login rate limiting ──────────────────────────────────────────────────────

def _seed_failed_logins(db, *, email: str, ip: str, count: int):
    """Seed N audit-logs met action=login.failed in het rate-limit-window."""
    now = datetime.now(timezone.utc)
    for _ in range(count):
        db.add(AuditLog(
            user_email=email,
            action="auth.login.failed",
            ip_address=ip,
            created_at=now,
        ))
    db.commit()


def test_login_rate_limit_blocks_after_threshold(client, admin_user):
    """Na N gefaalde pogingen voor dit email → 429, zelfs met juiste credentials."""
    db = SessionLocal()
    try:
        _seed_failed_logins(db, email=admin_user.email, ip="1.2.3.4",
                            count=LOGIN_RATE_LIMIT_PER_EMAIL)
    finally:
        db.close()

    # Goede credentials, maar gate slaat dicht
    r = client.post("/api/auth/login", json={
        "email": admin_user.email, "password": "test1234",
    })
    assert r.status_code == 429
    assert "mislukte pogingen" in r.json()["detail"]


def test_login_rate_limit_below_threshold_passes(client, admin_user):
    """N-1 pogingen mag nog door."""
    db = SessionLocal()
    try:
        _seed_failed_logins(db, email=admin_user.email, ip="1.2.3.4",
                            count=LOGIN_RATE_LIMIT_PER_EMAIL - 1)
    finally:
        db.close()

    r = client.post("/api/auth/login", json={
        "email": admin_user.email, "password": "test1234",
    })
    assert r.status_code == 200, r.text


def test_login_rate_limit_window_expired_passes(client, admin_user):
    """Pogingen ouder dan het window tellen niet mee."""
    db = SessionLocal()
    try:
        old = datetime.now(timezone.utc) - timedelta(
            minutes=LOGIN_RATE_LIMIT_WINDOW_MIN + 1)
        for _ in range(LOGIN_RATE_LIMIT_PER_EMAIL + 5):
            db.add(AuditLog(
                user_email=admin_user.email,
                action="auth.login.failed",
                ip_address="1.2.3.4",
                created_at=old,
            ))
        db.commit()
    finally:
        db.close()

    r = client.post("/api/auth/login", json={
        "email": admin_user.email, "password": "test1234",
    })
    assert r.status_code == 200, r.text


def test_login_rate_limit_per_ip_blocks_unknown_email(client):
    """Unknown email + veel gefaalde pogingen vanaf zelfde IP → 429."""
    db = SessionLocal()
    try:
        for _ in range(LOGIN_RATE_LIMIT_PER_IP):
            db.add(AuditLog(
                user_email=None,  # unknown email lookup
                action="auth.login.failed",
                ip_address="9.9.9.9",
            ))
        db.commit()
    finally:
        db.close()

    r = client.post(
        "/api/auth/login",
        headers={"X-Forwarded-For": "9.9.9.9"},
        json={"email": "unknown@nowhere.nl", "password": "anything"},
    )
    assert r.status_code == 429
    assert "netwerk" in r.json()["detail"]


def test_failed_login_now_records_user_email(client, admin_user):
    """Regression: bij invalid_credentials moet user_email gevuld zijn op de
    audit-rij (was None vóór de security commit), zodat per-account rate-limit
    werkt."""
    r = client.post("/api/auth/login", json={
        "email": admin_user.email, "password": "WRONG",
    })
    assert r.status_code == 401

    db = SessionLocal()
    try:
        log = db.query(AuditLog).filter(
            AuditLog.action == "auth.login.failed").first()
        assert log is not None
        assert log.user_email == admin_user.email
    finally:
        db.close()


# ─── Admin create flows ───────────────────────────────────────────────────────

def test_admin_create_user_rejects_weak_password(client, admin_user):
    r = client.post("/api/users/create",
                    headers=auth(admin_user),
                    json={
                        "email": "newby@test.nl",
                        "password": "password",   # 1 categorie
                        "first_name": "X", "last_name": "Y",
                        "role": "viewer",
                    })
    assert r.status_code == 400
    assert "Wachtwoord" in r.json()["detail"]


def test_admin_create_user_accepts_strong_password(client, admin_user):
    r = client.post("/api/users/create",
                    headers=auth(admin_user),
                    json={
                        "email": "newby2@test.nl",
                        "password": "Strong1Password!",
                        "first_name": "X", "last_name": "Y",
                        "role": "viewer",
                    })
    assert r.status_code == 200, r.text
    # must_change_password forceert eerste-login-rotatie
    db = SessionLocal()
    try:
        from models import User
        u = db.query(User).filter(User.email == "newby2@test.nl").first()
        assert u is not None
        assert u.must_change_password is True
    finally:
        db.close()


def test_admin_update_user_rejects_weak_password(client, admin_user, viewer_user):
    r = client.put(f"/api/users/{viewer_user.id}",
                   headers=auth(admin_user),
                   json={"password": "weak"})
    assert r.status_code == 400


def test_admin_update_user_accepts_strong_password(client, admin_user, viewer_user):
    r = client.put(f"/api/users/{viewer_user.id}",
                   headers=auth(admin_user),
                   json={"password": "Brand-NewPw1"})
    assert r.status_code == 200, r.text


# ─── Security response headers ────────────────────────────────────────────────

def test_security_headers_present_on_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    h = r.headers
    assert h.get("x-frame-options") == "DENY"
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "camera=()" in h.get("permissions-policy", "")
    assert h.get("cross-origin-opener-policy") == "same-origin"


def test_hsts_absent_outside_production(client):
    """HSTS mag niet op dev/test — anders cachen browsers de policy ook
    voor localhost en dev-certs gaan kapot."""
    r = client.get("/api/health")
    # In tests is RENDER env niet gezet, dus HSTS hoort er niet te zijn.
    assert "strict-transport-security" not in {k.lower() for k in r.headers.keys()}


# ─── Password reset rate limiting ─────────────────────────────────────────────

def test_password_reset_rate_limit_per_email(client, admin_user):
    from auth import PASSWORD_RESET_PER_EMAIL

    # PASSWORD_RESET_PER_EMAIL aanvragen mag — daarna throttled
    for _ in range(PASSWORD_RESET_PER_EMAIL):
        r = client.post("/api/auth/reset-password-request",
                        json={"email": admin_user.email})
        assert r.status_code == 200, r.text

    # N+1 → 429
    r = client.post("/api/auth/reset-password-request",
                    json={"email": admin_user.email})
    assert r.status_code == 429
    assert "reset-aanvragen" in r.json()["detail"]


def test_password_reset_rate_limit_unknown_email_does_not_create_dos(client):
    """Onbekende emails moeten ook gerate-limit worden (per-IP), anders kan
    je via een fake-email een 'er is geen rate limit'-channel openhouden."""
    from auth import PASSWORD_RESET_PER_IP

    for _ in range(PASSWORD_RESET_PER_IP):
        r = client.post(
            "/api/auth/reset-password-request",
            headers={"X-Forwarded-For": "5.5.5.5"},
            json={"email": "ghost@example.nl"})
        assert r.status_code == 200, r.text

    r = client.post(
        "/api/auth/reset-password-request",
        headers={"X-Forwarded-For": "5.5.5.5"},
        json={"email": "ghost@example.nl"})
    assert r.status_code == 429


# ─── Audit-log CSV export ─────────────────────────────────────────────────────

def test_audit_csv_export_admin_only(client, admin_user, viewer_user):
    """Niet-admins mogen de export niet."""
    r = client.get("/api/audit/logs/export.csv", headers=auth(viewer_user))
    assert r.status_code == 403


def test_audit_csv_export_returns_csv(client, admin_user):
    # Seed wat events
    client.get("/api/auth/me", headers=auth(admin_user))  # genereert geen audit
    client.post("/api/auth/login", json={
        "email": admin_user.email, "password": "test1234"})

    r = client.get("/api/audit/logs/export.csv", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in r.headers["content-disposition"]
    # CSV-headers
    body = r.content.decode("utf-8-sig")  # strip BOM
    first_line = body.splitlines()[0]
    assert first_line.startswith("id,created_at_utc,organization_id")


def test_audit_csv_export_logs_itself(client, admin_user):
    """De export wordt zelf gelogd — meta-audit."""
    client.get("/api/audit/logs/export.csv", headers=auth(admin_user))

    db = SessionLocal()
    try:
        export_logs = db.query(AuditLog).filter(
            AuditLog.action == "audit.export.csv").all()
        assert len(export_logs) >= 1
        assert export_logs[0].user_email == admin_user.email
    finally:
        db.close()


# ─── DSAR — GDPR Article 15 ──────────────────────────────────────────────────

def test_dsar_self_export_returns_profile(client, admin_user):
    r = client.get("/api/users/me/export", headers=auth(admin_user))
    assert r.status_code == 200
    body = r.json()
    assert body["schema_version"] == "1"
    assert body["subject"]["user_id"] == admin_user.id
    assert body["subject"]["email"] == admin_user.email
    assert body["profile"]["email"] == admin_user.email
    # Password-hash mag NOOIT in DSAR-output
    assert "hashed_password" not in body["profile"]
    # Lijsten zijn aanwezig (ook al leeg)
    for key in ("meldingen_created", "audit_actor", "audit_about_me",
                "push_subscriptions", "oauth_google_metadata"):
        assert key in body
        assert isinstance(body[key], list)


def test_dsar_self_export_redacts_oauth_tokens(client, admin_user):
    """OAuth tokens (google + microsoft) mogen NOOIT in de DSAR-output."""
    from models import GoogleOAuthToken
    db = SessionLocal()
    try:
        tok = GoogleOAuthToken(
            user_id=admin_user.id,
            organization_id=admin_user.organization_id,
            access_token="SECRET-NEVER-LEAK",
            refresh_token="SECRET-REFRESH",
            scope="profile email",
            google_email="admin@gmail.com",
        )
        db.add(tok); db.commit()
    finally:
        db.close()

    r = client.get("/api/users/me/export", headers=auth(admin_user))
    assert r.status_code == 200
    body_str = r.text
    assert "SECRET-NEVER-LEAK" not in body_str
    assert "SECRET-REFRESH" not in body_str
    # Maar metadata is wel aanwezig
    assert len(r.json()["oauth_google_metadata"]) == 1


def test_dsar_self_export_logs_audit_event(client, admin_user):
    client.get("/api/users/me/export", headers=auth(admin_user))
    db = SessionLocal()
    try:
        events = db.query(AuditLog).filter(
            AuditLog.action == "user.data.export.self").all()
        assert len(events) >= 1
        assert events[0].user_email == admin_user.email
    finally:
        db.close()


def test_dsar_admin_export_for_other_user(client, admin_user, viewer_user):
    r = client.get(f"/api/users/{viewer_user.id}/export",
                   headers=auth(admin_user))
    assert r.status_code == 200
    body = r.json()
    assert body["subject"]["user_id"] == viewer_user.id


def test_dsar_admin_export_blocks_non_admin(client, viewer_user, admin_user):
    """Niet-admin mag geen DSAR voor andermans data trekken."""
    r = client.get(f"/api/users/{admin_user.id}/export",
                   headers=auth(viewer_user))
    assert r.status_code == 403
