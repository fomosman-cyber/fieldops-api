"""Auth-flow tests: login success/fail + password reset + audit-trail."""

from database import SessionLocal
from models import AuditLog, PasswordResetToken
from tests.conftest import auth


def test_login_success_returns_token_and_logs(client, admin_user):
    r = client.post("/api/auth/login", json={
        "email": admin_user.email, "password": "test1234",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["access_token"]
    assert data["user"]["email"] == admin_user.email

    db = SessionLocal()
    try:
        logs = db.query(AuditLog).filter(AuditLog.action == "auth.login.success").all()
        assert len(logs) == 1
        assert logs[0].user_email == admin_user.email
    finally:
        db.close()


def test_login_wrong_password_logs_failed(client, admin_user):
    r = client.post("/api/auth/login", json={
        "email": admin_user.email, "password": "WRONG",
    })
    assert r.status_code == 401

    db = SessionLocal()
    try:
        logs = db.query(AuditLog).filter(AuditLog.action == "auth.login.failed").all()
        assert len(logs) == 1
    finally:
        db.close()


def test_login_unknown_email_does_not_leak(client):
    r = client.post("/api/auth/login", json={
        "email": "ghost@example.com", "password": "anything",
    })
    assert r.status_code == 401
    # Generieke message (geen "user not found")
    assert "Onjuist" in r.json()["detail"]


def test_me_endpoint(client, admin_user):
    r = client.get("/api/auth/me", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["email"] == admin_user.email


def test_me_without_token_fails(client):
    r = client.get("/api/auth/me")
    assert r.status_code in (401, 403)


def test_me_is_super_admin_false_voor_klant_org_admin(client, admin_user):
    """Org-admin van een klant-org is GEEN platform-eigenaar — de frontend
    mag de Admin-tab dan niet tonen (backend require_owner geeft 403)."""
    r = client.get("/api/auth/me", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["is_super_admin"] is False


def test_me_is_super_admin_true_voor_fieldops_org_admin(client, platform_owner):
    """Org-admin binnen de FieldOps-org = super-admin (zelfde logica als
    require_owner in admin_router)."""
    r = client.get("/api/auth/me", headers=auth(platform_owner))
    assert r.status_code == 200
    assert r.json()["is_super_admin"] is True


def test_password_reset_request_creates_db_token(client, admin_user):
    r = client.post("/api/auth/reset-password-request", json={"email": admin_user.email})
    assert r.status_code == 200

    db = SessionLocal()
    try:
        tokens = db.query(PasswordResetToken).filter(PasswordResetToken.user_id == admin_user.id).all()
        assert len(tokens) == 1
        # Hash, niet plaintext
        assert len(tokens[0].token_hash) == 64  # sha256 hex
        assert tokens[0].used_at is None
    finally:
        db.close()


def test_password_reset_request_unknown_email_returns_same_message(client):
    """Anti-enumeration: zelfde response, geen DB-rij."""
    r = client.post("/api/auth/reset-password-request", json={"email": "nobody@test.nl"})
    assert r.status_code == 200
    assert "ontvangt u een reset-link" in r.json()["message"]


def test_password_reset_short_password_rejected(client, admin_user):
    r = client.post("/api/auth/reset-password", json={
        "token": "any", "new_password": "kort",
    })
    assert r.status_code == 400
    assert "minimaal 8" in r.json()["detail"]


def test_password_reset_invalid_token_rejected(client):
    r = client.post("/api/auth/reset-password", json={
        "token": "definitely-not-a-real-token", "new_password": "newpass1234",
    })
    assert r.status_code == 400
