"""RBAC-matrix tests voor meldingen-router. Eén test per rol per actie."""

import pytest
from database import SessionLocal
from models import Melding
from tests.conftest import auth


def _create_melding_via_admin(client, admin_user, **overrides):
    payload = {"title": "Scheur in wegdek", "category": "scheur",
               "priority": "normaal"}
    payload.update(overrides)
    r = client.post("/api/meldingen/", json=payload, headers=auth(admin_user))
    assert r.status_code == 200, r.text
    return r.json()


# ─── CREATE ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("user_fixture,expected_status", [
    ("admin_user", 200),
    ("manager_user", 200),
    ("technician_user", 200),
    ("inspector_user", 200),
    ("contractor_user", 403),
    ("viewer_user", 403),
])
def test_create_melding_rbac(client, request, user_fixture, expected_status):
    user = request.getfixturevalue(user_fixture)
    r = client.post("/api/meldingen/", json={
        "title": "Test", "priority": "normaal",
    }, headers=auth(user))
    assert r.status_code == expected_status


# ─── UPDATE: viewer mag niets ────────────────────────────────────────────

def test_viewer_cannot_update(client, admin_user, viewer_user):
    m = _create_melding_via_admin(client, admin_user)
    r = client.put(f"/api/meldingen/{m['id']}", json={"title": "Hacked"},
                   headers=auth(viewer_user))
    assert r.status_code == 403


# ─── UPDATE: contractor alleen status (status-only payload) ─────────────

def test_contractor_can_change_status_only(client, admin_user, contractor_user):
    m = _create_melding_via_admin(client, admin_user)
    # Status mag
    r = client.put(f"/api/meldingen/{m['id']}", json={"status": "in_uitvoering"},
                   headers=auth(contractor_user))
    assert r.status_code == 200, r.text
    # Andere velden mag niet — ook niet samen met status
    r2 = client.put(f"/api/meldingen/{m['id']}",
                    json={"status": "opgelost", "title": "Andere titel"},
                    headers=auth(contractor_user))
    assert r2.status_code == 403


def test_contractor_cannot_edit_title_only(client, admin_user, contractor_user):
    m = _create_melding_via_admin(client, admin_user)
    r = client.put(f"/api/meldingen/{m['id']}", json={"title": "Andere"},
                   headers=auth(contractor_user))
    assert r.status_code == 403


# ─── UPDATE: inspector — alleen eigen meldingen, geen status ─────────────

def test_inspector_can_edit_own_but_not_status(client, inspector_user):
    # Inspector maakt zelf een melding aan
    own = client.post("/api/meldingen/", json={"title": "Mine", "priority": "laag"},
                      headers=auth(inspector_user)).json()
    # Title bewerken mag
    r = client.put(f"/api/meldingen/{own['id']}", json={"title": "Mine v2"},
                   headers=auth(inspector_user))
    assert r.status_code == 200
    # Status NIET
    r2 = client.put(f"/api/meldingen/{own['id']}", json={"status": "opgelost"},
                    headers=auth(inspector_user))
    assert r2.status_code == 403


def test_inspector_cannot_edit_others_meldingen(client, admin_user, inspector_user):
    other = _create_melding_via_admin(client, admin_user)
    r = client.put(f"/api/meldingen/{other['id']}", json={"title": "Trying"},
                   headers=auth(inspector_user))
    assert r.status_code == 403


# ─── UPDATE: admin volledige toegang ─────────────────────────────────────

def test_admin_can_edit_all_fields(client, admin_user):
    m = _create_melding_via_admin(client, admin_user)
    r = client.put(f"/api/meldingen/{m['id']}",
                   json={"title": "Updated", "status": "opgelost", "priority": "hoog"},
                   headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["title"] == "Updated"
    assert r.json()["status"] == "opgelost"


# ─── DELETE: alleen org-admin ────────────────────────────────────────────

def test_only_org_admin_can_delete(client, admin_user, manager_user, viewer_user):
    m = _create_melding_via_admin(client, admin_user)

    # Manager (niet org-admin) → 403
    r1 = client.delete(f"/api/meldingen/{m['id']}", headers=auth(manager_user))
    assert r1.status_code == 403

    # Viewer → 403
    r2 = client.delete(f"/api/meldingen/{m['id']}", headers=auth(viewer_user))
    assert r2.status_code == 403

    # Org-admin → 200
    r3 = client.delete(f"/api/meldingen/{m['id']}", headers=auth(admin_user))
    assert r3.status_code == 200


# ─── LIST: gescoped per organisatie ──────────────────────────────────────

def test_list_meldingen_only_own_org(client, admin_user, platform_owner):
    _create_melding_via_admin(client, admin_user, title="Org-A melding")
    # Platform owner is een ANDERE org (FieldOps)
    r = client.get("/api/meldingen/", headers=auth(platform_owner))
    assert r.status_code == 200
    titles = [m["title"] for m in r.json()]
    assert "Org-A melding" not in titles
