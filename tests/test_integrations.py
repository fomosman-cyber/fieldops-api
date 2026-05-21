"""Tests voor /api/integrations endpoints + setup-doc serving.

Dekking:
  - GET /api/integrations/status — own status combined Google + Microsoft
  - GET /api/integrations/coverage — admin-only, per-user koppel-overzicht
  - GET /<DOC>.md — whitelisted setup-docs serving
"""

from datetime import datetime, timezone, timedelta

from tests.conftest import auth
from database import SessionLocal
from models import GoogleOAuthToken, User
from auth import hash_password


# ─────────────────────────────────────────────────────────────────────
# /api/integrations/status
# ─────────────────────────────────────────────────────────────────────

def test_status_unconfigured_when_env_missing(client, admin_user, monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MS_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MS_OAUTH_CLIENT_SECRET", raising=False)

    r = client.get("/api/integrations/status", headers=auth(admin_user))
    assert r.status_code == 200
    data = r.json()
    assert data["google"]["configured"] is False
    assert data["google"]["connected"] is False
    assert data["google"]["setup_doc"] == "GOOGLE-SETUP.md"
    assert data["microsoft"]["configured"] is False
    assert data["microsoft"]["setup_doc"] == "MICROSOFT-SETUP.md"


def test_status_configured_but_not_connected(client, admin_user, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "fake")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "fake")
    monkeypatch.setenv("MS_OAUTH_CLIENT_ID", "fake")
    monkeypatch.setenv("MS_OAUTH_CLIENT_SECRET", "fake")

    r = client.get("/api/integrations/status", headers=auth(admin_user))
    data = r.json()
    assert data["google"]["configured"] is True
    assert data["google"]["connected"] is False
    assert data["google"]["setup_doc"] is None    # geconfigureerd → geen setup-link nodig
    assert data["microsoft"]["configured"] is True
    assert data["microsoft"]["connected"] is False


def test_status_connected_when_token_exists(client, admin_user, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "fake")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "fake")

    db = SessionLocal()
    try:
        db.add(GoogleOAuthToken(
            user_id=admin_user.id, organization_id=admin_user.organization_id,
            access_token="atk", refresh_token="rtk",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            google_email="admin@test.nl", scope="calendar.events",
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/integrations/status", headers=auth(admin_user))
    data = r.json()
    assert data["google"]["connected"] is True
    assert data["google"]["email"] == "admin@test.nl"


def test_status_revoked_token_treated_as_disconnected(client, admin_user, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "fake")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "fake")

    db = SessionLocal()
    try:
        db.add(GoogleOAuthToken(
            user_id=admin_user.id, organization_id=admin_user.organization_id,
            access_token="atk", expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            google_email="admin@test.nl",
            revoked_at=datetime.now(timezone.utc),
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/integrations/status", headers=auth(admin_user))
    assert r.json()["google"]["connected"] is False


def test_status_requires_auth(client):
    r = client.get("/api/integrations/status")
    assert r.status_code in (401, 403)


# ─────────────────────────────────────────────────────────────────────
# /api/integrations/coverage
# ─────────────────────────────────────────────────────────────────────

def test_coverage_admin_can_access(client, admin_user, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "fake")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "fake")

    r = client.get("/api/integrations/coverage", headers=auth(admin_user))
    assert r.status_code == 200
    data = r.json()
    assert data["total_users"] >= 1
    assert "google" in data and "microsoft" in data
    assert data["google"]["configured"] is True


def test_coverage_non_admin_forbidden(client, viewer_user):
    r = client.get("/api/integrations/coverage", headers=auth(viewer_user))
    assert r.status_code == 403


def test_coverage_counts_connected_users(client, admin_user, manager_user, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "fake")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "fake")

    # Koppel admin aan Google, manager niet
    db = SessionLocal()
    try:
        db.add(GoogleOAuthToken(
            user_id=admin_user.id, organization_id=admin_user.organization_id,
            access_token="atk", expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            google_email="admin@test.nl",
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/integrations/coverage", headers=auth(admin_user))
    data = r.json()
    assert data["google"]["connected"] == 1
    # Manager niet gekoppeld → in missing-count
    assert any(u["google"] for u in data["users"])
    assert any(not u["google"] for u in data["users"])


def test_coverage_excludes_revoked_tokens(client, admin_user):
    db = SessionLocal()
    try:
        # Revoked token → mag niet meetellen als 'connected'
        db.add(GoogleOAuthToken(
            user_id=admin_user.id, organization_id=admin_user.organization_id,
            access_token="atk", expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            google_email="admin@test.nl",
            revoked_at=datetime.now(timezone.utc),
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/integrations/coverage", headers=auth(admin_user))
    assert r.json()["google"]["connected"] == 0


def test_coverage_org_isolation(client, admin_user):
    """Tokens uit een andere org tellen niet mee."""
    from models import Organization, AccountStatus, SubscriptionPlan, UserRole

    db = SessionLocal()
    try:
        other_org = Organization(name="OtherCov", plan=SubscriptionPlan.PROFESSIONAL,
                                 status=AccountStatus.ACTIVE, max_users=5)
        db.add(other_org); db.commit(); db.refresh(other_org)
        other_user = User(email="other-cov@test.nl",
                          hashed_password=hash_password("x"),
                          first_name="O", last_name="C",
                          role=UserRole.ADMIN, is_org_admin=True,
                          organization_id=other_org.id)
        db.add(other_user); db.commit(); db.refresh(other_user)
        # Token in OTHER org
        db.add(GoogleOAuthToken(
            user_id=other_user.id, organization_id=other_org.id,
            access_token="atk", expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            google_email="other@test.nl",
        ))
        db.commit()
    finally:
        db.close()

    # admin_user (mijn org) ziet alleen z'n eigen org
    r = client.get("/api/integrations/coverage", headers=auth(admin_user))
    data = r.json()
    assert data["google"]["connected"] == 0
    emails = [u["email"] for u in data["users"]]
    assert "other-cov@test.nl" not in emails


# ─────────────────────────────────────────────────────────────────────
# Setup-doc serving
# ─────────────────────────────────────────────────────────────────────

def test_setup_doc_google_served(client):
    r = client.get("/GOOGLE-SETUP.md")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert "Google" in r.text


def test_setup_doc_microsoft_served(client):
    r = client.get("/MICROSOFT-SETUP.md")
    assert r.status_code == 200
    assert "Microsoft" in r.text


def test_setup_doc_disallowed_returns_404(client):
    r = client.get("/SECRET-INTERNAL.md")
    assert r.status_code == 404


def test_setup_doc_path_traversal_blocked(client):
    """Pad-injectie naar buiten de whitelist mag niet werken."""
    r = client.get("/etc/passwd.md")
    assert r.status_code in (404, 422)
