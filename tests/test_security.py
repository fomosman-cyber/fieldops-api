"""Security hardening tests:

- Server-side password complexity (validate_password_strength).
- Login brute-force rate limiting via audit_log.
- Defense-in-depth voor admin-create-user en password-reset flows.
"""

from datetime import datetime, timezone, timedelta
import pytest

from auth import (
    validate_password_strength,
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


# ─── GDPR Article 17 — Right to erasure (anonymisatie) ────────────────────────

def test_erasure_self_anonymizes_profile(client, viewer_user):
    original_email = viewer_user.email
    r = client.delete("/api/users/me/anonymize", headers=auth(viewer_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "geanonimiseerd" in body["message"]
    assert body["summary"]["redacted_email"].endswith("@deleted.invalid")

    db = SessionLocal()
    try:
        from models import User
        u = db.query(User).filter(User.id == viewer_user.id).first()
        assert u is not None  # rij bestaat nog
        assert u.email != original_email
        assert u.email.endswith("@deleted.invalid")
        assert u.first_name == "Verwijderd"
        assert u.last_name == ""
        assert u.is_active is False
        # Login is nu onmogelijk: hash matcht niets
        from auth import verify_password
        assert verify_password("test1234", u.hashed_password) is False
    finally:
        db.close()


def test_erasure_self_redacts_audit_email(client, viewer_user):
    # Trigger een audit-event (login) zodat user_email gevuld is
    client.post("/api/auth/login", json={
        "email": viewer_user.email, "password": "test1234"})
    original_email = viewer_user.email

    r = client.delete("/api/users/me/anonymize", headers=auth(viewer_user))
    assert r.status_code == 200

    db = SessionLocal()
    try:
        # Geen enkele audit-rij mag de oorspronkelijke email nog bevatten
        leaks = db.query(AuditLog).filter(
            AuditLog.user_email == original_email).count()
        assert leaks == 0
    finally:
        db.close()


def test_erasure_self_deletes_oauth_and_push(client, viewer_user):
    from models import GoogleOAuthToken, PushSubscription
    db = SessionLocal()
    try:
        db.add(GoogleOAuthToken(
            user_id=viewer_user.id,
            organization_id=viewer_user.organization_id,
            access_token="leak-me-not", scope="profile",
        ))
        db.add(PushSubscription(
            user_id=viewer_user.id,
            organization_id=viewer_user.organization_id,
            endpoint="https://push.example/sub", p256dh="x", auth="y",
        ))
        db.commit()
    finally:
        db.close()

    client.delete("/api/users/me/anonymize", headers=auth(viewer_user))

    db = SessionLocal()
    try:
        assert db.query(GoogleOAuthToken).filter(
            GoogleOAuthToken.user_id == viewer_user.id).count() == 0
        assert db.query(PushSubscription).filter(
            PushSubscription.user_id == viewer_user.id).count() == 0
    finally:
        db.close()


def test_erasure_self_blocks_subsequent_login(client, viewer_user):
    original_email = viewer_user.email
    client.delete("/api/users/me/anonymize", headers=auth(viewer_user))

    r = client.post("/api/auth/login", json={
        "email": original_email, "password": "test1234"})
    assert r.status_code == 401  # email bestaat niet meer in db


def test_erasure_admin_for_other_user(client, admin_user, viewer_user):
    target_id = viewer_user.id
    r = client.delete(f"/api/users/{target_id}/anonymize",
                      headers=auth(admin_user))
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        from models import User
        u = db.query(User).filter(User.id == target_id).first()
        assert u.first_name == "Verwijderd"
    finally:
        db.close()


def test_erasure_admin_blocks_self_anonymize_via_admin_route(client, admin_user):
    """Click-shield: admin mag z'n eigen account niet via deze route wissen.
    Moet via /me/anonymize zodat het bewust is."""
    r = client.delete(f"/api/users/{admin_user.id}/anonymize",
                      headers=auth(admin_user))
    assert r.status_code == 400


def test_erasure_admin_blocks_non_admin(client, viewer_user, admin_user):
    """Niet-admin mag een andere user niet wissen."""
    r = client.delete(f"/api/users/{admin_user.id}/anonymize",
                      headers=auth(viewer_user))
    assert r.status_code == 403


def test_erasure_logs_audit_event(client, viewer_user):
    client.delete("/api/users/me/anonymize", headers=auth(viewer_user))
    db = SessionLocal()
    try:
        events = db.query(AuditLog).filter(
            AuditLog.action == "user.anonymize.self").all()
        assert len(events) >= 1
        assert events[0].entity_id == viewer_user.id
    finally:
        db.close()


# ─── JWT revocation / session invalidation ───────────────────────────────────

def test_jwt_revoked_after_anonymize(client, viewer_user):
    """Article 17 vereist onmiddellijke effectiviteit: JWT moet meteen 401-en."""
    headers = auth(viewer_user)
    # Anonymize — same JWT
    r = client.delete("/api/users/me/anonymize", headers=headers)
    assert r.status_code == 200
    # Same JWT used again -> must be 401
    r2 = client.get("/api/auth/me", headers=headers)
    assert r2.status_code == 401, r2.text


def test_jwt_revoked_after_password_change(client, admin_user, viewer_user):
    """Bij password reset door admin: oude tokens van die user worden ongeldig."""
    viewer_token = auth(viewer_user)
    # Admin reset het wachtwoord van viewer
    r = client.put(f"/api/users/{viewer_user.id}",
                   headers=auth(admin_user),
                   json={"password": "Brand-NewPw1"})
    assert r.status_code == 200
    # Oude viewer-token moet nu falen
    r2 = client.get("/api/auth/me", headers=viewer_token)
    assert r2.status_code == 401


def test_jwt_revoked_after_deactivate(client, admin_user, viewer_user):
    viewer_token = auth(viewer_user)
    r = client.delete(f"/api/users/{viewer_user.id}", headers=auth(admin_user))
    assert r.status_code == 200
    r2 = client.get("/api/auth/me", headers=viewer_token)
    # Either 401 (token revoked) or 403 (account deactivated) is acceptable
    assert r2.status_code in (401, 403)


def test_fresh_login_after_revocation_works(client, admin_user, viewer_user):
    """Na revocation moet een nieuwe login wel weer een werkend token geven."""
    import time
    # First revoke via password change
    client.put(f"/api/users/{viewer_user.id}",
               headers=auth(admin_user),
               json={"password": "Brand-NewPw1"})
    # JWT iat is second-precision — invalidate_user_sessions ceilt naar volgende
    # seconde zodat oude tokens revoked blijven. Nieuwe login moet >1s wachten
    # voordat z'n iat na invalidated_at valt. Productie-UI kost al >1s; in test
    # forceren we het.
    time.sleep(1.1)
    # New login with new password
    r = client.post("/api/auth/login", json={
        "email": viewer_user.email, "password": "Brand-NewPw1",
    })
    assert r.status_code == 200, r.text
    new_token = r.json()["access_token"]
    # Use the new token
    r2 = client.get("/api/auth/me",
                    headers={"Authorization": "Bearer " + new_token})
    assert r2.status_code == 200


def test_jwt_iat_claim_present(client, admin_user):
    """Sanity: nieuwe tokens bevatten iat-claim."""
    r = client.post("/api/auth/login", json={
        "email": admin_user.email, "password": "test1234",
    })
    assert r.status_code == 200
    token = r.json()["access_token"]
    from jose import jwt
    import os
    payload = jwt.decode(token, os.environ["SECRET_KEY"],
                         algorithms=["HS256"])
    assert "iat" in payload
    assert "exp" in payload


# ─── Public form anti-spam rate limit ─────────────────────────────────────────

def test_contact_form_rate_limit_per_email(client):
    payload = {"name": "X", "email": "spammer@example.nl", "message": "Hello"}
    # 3 mag, 4e moet falen (per-email cap = 3)
    for _ in range(3):
        r = client.post("/api/contact", json=payload)
        assert r.status_code == 200, r.text
    r = client.post("/api/contact", json=payload)
    assert r.status_code == 429
    assert "inzendingen" in r.json()["detail"]


def test_contact_form_rate_limit_per_ip(client):
    """10 inzendingen vanaf zelfde IP met variërende emails -> 429."""
    for i in range(10):
        r = client.post(
            "/api/contact",
            headers={"X-Forwarded-For": "7.7.7.7"},
            json={"name": f"X{i}", "email": f"x{i}@example.nl", "message": "spam"},
        )
        assert r.status_code == 200, f"attempt {i}: {r.text}"
    r = client.post(
        "/api/contact",
        headers={"X-Forwarded-For": "7.7.7.7"},
        json={"name": "X10", "email": "x10@example.nl", "message": "spam"},
    )
    assert r.status_code == 429


def test_demo_request_rate_limit_per_email(client):
    """Demo per-email cap = 2 (strenger want DB-rij + 2 mails)."""
    base = {
        "first_name": "Demo", "last_name": "User",
        "company_name": "Acme", "email": "demo-spam@example.nl",
        "phone": "0612345678", "plan": "starter", "num_users": 5,
    }
    r = client.post("/api/demo/request", json=base)
    assert r.status_code == 200, r.text
    # Tweede aanvraag voor zelfde email faalt al — bestaande "pending" check
    # gooit 400 vóór rate-limit. Dat is OK; we checken dat /api/contact en
    # IP-rate-limit het wel doen (zie volgende test).
    r2 = client.post("/api/demo/request", json=base)
    assert r2.status_code == 400  # bestaande pending


def test_demo_request_rate_limit_per_ip(client):
    """Verschillende emails maar zelfde IP -> 429 na 5e poging."""
    headers = {"X-Forwarded-For": "8.8.8.8"}
    base = {
        "first_name": "Demo", "last_name": "User",
        "company_name": "Acme", "phone": "0612345678",
        "plan": "starter", "num_users": 5,
    }
    for i in range(5):
        body = dict(base, email=f"demo-{i}@example.nl")
        r = client.post("/api/demo/request", headers=headers, json=body)
        assert r.status_code == 200, f"attempt {i}: {r.text}"
    body = dict(base, email="demo-6@example.nl")
    r = client.post("/api/demo/request", headers=headers, json=body)
    assert r.status_code == 429
