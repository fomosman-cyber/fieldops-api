"""Tests voor het admin failed-login monitor endpoint:

    GET /api/audit/failed-logins?window_minutes=N

Het endpoint aggregeert mislukte login-pogingen uit het bestaande audit-log
zodat admins brute-force-pogingen tegen hun org kunnen zien zonder door
ruwe logs te spitten. Data-bron is `auth.login.failed` audit-records die
de bestaande rate-limit-pipeline al schrijft.
"""

from datetime import datetime, timezone, timedelta
import json
import pytest

from auth import (
    LOGIN_RATE_LIMIT_PER_EMAIL,
    LOGIN_RATE_LIMIT_PER_IP,
)
from database import SessionLocal
from models import AuditLog
from tests.conftest import auth


def _seed(db, *, email, ip, count, org_id=None, when=None, reason=None):
    """Helper — maak `count` failed-login audit-rijen met optioneel een reason
    in details (zoals de echte auth-router schrijft)."""
    when = when or datetime.now(timezone.utc)
    details = None
    if reason:
        details = json.dumps({"extra": {"reason": reason, "email": email}})
    for _ in range(count):
        db.add(AuditLog(
            user_email=email,
            organization_id=org_id,
            action="auth.login.failed",
            ip_address=ip,
            details=details,
            created_at=when,
        ))
    db.commit()


# ─── Authorisatie ─────────────────────────────────────────────────────────────

def test_failed_logins_requires_auth(client):
    r = client.get("/api/audit/failed-logins")
    assert r.status_code in (401, 403)


def test_failed_logins_requires_admin(client, viewer_user):
    r = client.get("/api/audit/failed-logins", headers=auth(viewer_user))
    assert r.status_code == 403


def test_failed_logins_admin_can_access(client, admin_user):
    r = client.get("/api/audit/failed-logins", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    data = r.json()
    assert "totals" in data and "top_emails" in data and "top_ips" in data
    assert data["scope"] == "organization"


# ─── Window-validatie ─────────────────────────────────────────────────────────

def test_failed_logins_window_too_small_rejected(client, admin_user):
    r = client.get("/api/audit/failed-logins?window_minutes=1",
                   headers=auth(admin_user))
    assert r.status_code == 422


def test_failed_logins_window_too_large_rejected(client, admin_user):
    r = client.get("/api/audit/failed-logins?window_minutes=99999",
                   headers=auth(admin_user))
    assert r.status_code == 422


# ─── Aggregaties ──────────────────────────────────────────────────────────────

def test_failed_logins_totals_count_correctly(client, admin_user):
    db = SessionLocal()
    try:
        _seed(db, email=admin_user.email, ip="1.1.1.1", count=3,
              org_id=admin_user.organization_id)
        _seed(db, email="other@test.nl", ip="2.2.2.2", count=2,
              org_id=admin_user.organization_id)
    finally:
        db.close()

    r = client.get("/api/audit/failed-logins", headers=auth(admin_user))
    assert r.status_code == 200
    t = r.json()["totals"]
    assert t["attempts"] == 5
    assert t["distinct_emails"] == 2
    assert t["distinct_ips"] == 2


def test_failed_logins_top_emails_sorted_desc(client, admin_user):
    db = SessionLocal()
    try:
        _seed(db, email="few@test.nl", ip="1.1.1.1", count=2,
              org_id=admin_user.organization_id)
        _seed(db, email="many@test.nl", ip="1.1.1.2", count=7,
              org_id=admin_user.organization_id)
        _seed(db, email="medium@test.nl", ip="1.1.1.3", count=4,
              org_id=admin_user.organization_id)
    finally:
        db.close()

    r = client.get("/api/audit/failed-logins", headers=auth(admin_user))
    rows = r.json()["top_emails"]
    assert [row["email"] for row in rows] == [
        "many@test.nl", "medium@test.nl", "few@test.nl"
    ]
    assert rows[0]["attempts"] == 7


def test_failed_logins_top_ips_count_distinct_emails(client, admin_user):
    db = SessionLocal()
    try:
        # Eén IP, drie verschillende emails — typisch credential-stuffing
        _seed(db, email="a@test.nl", ip="9.9.9.9", count=2,
              org_id=admin_user.organization_id)
        _seed(db, email="b@test.nl", ip="9.9.9.9", count=2,
              org_id=admin_user.organization_id)
        _seed(db, email="c@test.nl", ip="9.9.9.9", count=1,
              org_id=admin_user.organization_id)
    finally:
        db.close()

    r = client.get("/api/audit/failed-logins", headers=auth(admin_user))
    ips = r.json()["top_ips"]
    assert len(ips) == 1
    assert ips[0]["ip"] == "9.9.9.9"
    assert ips[0]["attempts"] == 5
    assert ips[0]["email_count"] == 3


def test_failed_logins_blocked_flag_set_at_threshold(client, admin_user):
    """Een email met >= LOGIN_RATE_LIMIT_PER_EMAIL pogingen krijgt blocked=true."""
    db = SessionLocal()
    try:
        _seed(db, email="blocked@test.nl", ip="3.3.3.3",
              count=LOGIN_RATE_LIMIT_PER_EMAIL,
              org_id=admin_user.organization_id)
        _seed(db, email="ok@test.nl", ip="3.3.3.4",
              count=LOGIN_RATE_LIMIT_PER_EMAIL - 1,
              org_id=admin_user.organization_id)
    finally:
        db.close()

    r = client.get("/api/audit/failed-logins", headers=auth(admin_user))
    by_email = {row["email"]: row for row in r.json()["top_emails"]}
    assert by_email["blocked@test.nl"]["blocked"] is True
    assert by_email["ok@test.nl"]["blocked"] is False


def test_failed_logins_window_excludes_old_attempts(client, admin_user):
    db = SessionLocal()
    try:
        # 1 oude poging (buiten window), 2 recente
        old = datetime.now(timezone.utc) - timedelta(hours=3)
        _seed(db, email="x@test.nl", ip="1.1.1.1", count=5,
              org_id=admin_user.organization_id, when=old)
        _seed(db, email="x@test.nl", ip="1.1.1.1", count=2,
              org_id=admin_user.organization_id)
    finally:
        db.close()

    # 60-min window → alleen 2 recente
    r = client.get("/api/audit/failed-logins?window_minutes=60",
                   headers=auth(admin_user))
    assert r.json()["totals"]["attempts"] == 2

    # 24u window → alle 7
    r2 = client.get("/api/audit/failed-logins?window_minutes=1440",
                    headers=auth(admin_user))
    assert r2.json()["totals"]["attempts"] == 7


# ─── Scope ────────────────────────────────────────────────────────────────────

def test_failed_logins_org_scope_isolates_orgs(client, admin_user, platform_owner):
    """Een org-admin ziet alleen pogingen tegen z'n eigen org."""
    db = SessionLocal()
    try:
        _seed(db, email="mine@test.nl", ip="5.5.5.5", count=4,
              org_id=admin_user.organization_id)
        # Andere organisatie (de platform-owner z'n FieldOps-org)
        _seed(db, email="theirs@test.nl", ip="6.6.6.6", count=10,
              org_id=platform_owner.organization_id)
    finally:
        db.close()

    r = client.get("/api/audit/failed-logins", headers=auth(admin_user))
    data = r.json()
    assert data["totals"]["attempts"] == 4
    emails = [row["email"] for row in data["top_emails"]]
    assert emails == ["mine@test.nl"]


def test_failed_logins_platform_owner_sees_all_orgs(client, admin_user, platform_owner):
    """FieldOps-org admin = platform-eigenaar → ziet alle orgs."""
    db = SessionLocal()
    try:
        _seed(db, email="orga@test.nl", ip="5.5.5.5", count=4,
              org_id=admin_user.organization_id)
        _seed(db, email="orgb@test.nl", ip="6.6.6.6", count=2,
              org_id=platform_owner.organization_id)
    finally:
        db.close()

    r = client.get("/api/audit/failed-logins", headers=auth(platform_owner))
    data = r.json()
    assert data["scope"] == "platform"
    assert data["totals"]["attempts"] == 6


def test_failed_logins_excludes_unattributable_for_org_admin(client, admin_user):
    """Pogingen op onbekende emails (organization_id NULL) blijven onzichtbaar
    voor org-admins — die kunnen we niet veilig aan één org koppelen."""
    db = SessionLocal()
    try:
        _seed(db, email="known@test.nl", ip="1.1.1.1", count=2,
              org_id=admin_user.organization_id)
        # Pogingen op onbekende email → user_email + organization_id zijn NULL
        for _ in range(5):
            db.add(AuditLog(
                user_email=None, organization_id=None,
                action="auth.login.failed", ip_address="7.7.7.7",
            ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/audit/failed-logins", headers=auth(admin_user))
    assert r.json()["totals"]["attempts"] == 2


# ─── Recent events ────────────────────────────────────────────────────────────

def test_failed_logins_recent_extracts_reason(client, admin_user):
    db = SessionLocal()
    try:
        _seed(db, email="bad@test.nl", ip="1.1.1.1", count=1,
              org_id=admin_user.organization_id, reason="invalid_credentials")
        _seed(db, email="off@test.nl", ip="1.1.1.2", count=1,
              org_id=admin_user.organization_id, reason="deactivated")
    finally:
        db.close()

    r = client.get("/api/audit/failed-logins", headers=auth(admin_user))
    recent = r.json()["recent"]
    reasons = sorted(row["reason"] for row in recent if row["reason"])
    assert reasons == ["deactivated", "invalid_credentials"]


def test_failed_logins_recent_handles_missing_reason(client, admin_user):
    """Oude audit-rijen zonder reason in details mogen geen 500 geven."""
    db = SessionLocal()
    try:
        # Geen details
        _seed(db, email="nodet@test.nl", ip="1.1.1.1", count=1,
              org_id=admin_user.organization_id)
        # Onverwacht JSON-formaat
        db.add(AuditLog(
            user_email="weird@test.nl", organization_id=admin_user.organization_id,
            action="auth.login.failed", ip_address="2.2.2.2",
            details="not-json-at-all",
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/audit/failed-logins", headers=auth(admin_user))
    assert r.status_code == 200
    for row in r.json()["recent"]:
        # reason mag None zijn maar key moet bestaan
        assert "reason" in row


def test_failed_logins_recent_respects_recent_n(client, admin_user):
    db = SessionLocal()
    try:
        _seed(db, email="x@test.nl", ip="1.1.1.1", count=20,
              org_id=admin_user.organization_id)
    finally:
        db.close()

    r = client.get("/api/audit/failed-logins?recent_n=5",
                   headers=auth(admin_user))
    assert len(r.json()["recent"]) == 5


def test_failed_logins_thresholds_in_response(client, admin_user):
    """Endpoint geeft de actieve gate-thresholds terug zodat de UI consistent
    weet wanneer iets blocked is."""
    r = client.get("/api/audit/failed-logins", headers=auth(admin_user))
    th = r.json()["thresholds"]
    assert th["per_email"] == LOGIN_RATE_LIMIT_PER_EMAIL
    assert th["per_ip"] == LOGIN_RATE_LIMIT_PER_IP
    assert th["rate_limit_window_min"] >= 1
