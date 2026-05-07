"""Audit-log scope + filtering tests."""

from tests.conftest import auth


def test_only_admin_can_read_audit_log(client, viewer_user):
    r = client.get("/api/audit/logs", headers=auth(viewer_user))
    assert r.status_code == 403


def test_admin_sees_only_own_org_logs(client, admin_user, platform_owner):
    # Admin doet een actie
    client.post("/api/meldingen/", json={"title": "Org-A"}, headers=auth(admin_user))

    # Platform owner doet een actie in z'n eigen (FieldOps) org
    client.get("/api/auth/me", headers=auth(platform_owner))

    # Admin (gewone org) ziet ALLEEN eigen org-events
    own = client.get("/api/audit/logs", headers=auth(admin_user)).json()
    own_orgs = {it["organization_id"] for it in own["items"]}
    assert own_orgs == {admin_user.organization_id}


def test_platform_owner_sees_all_orgs(client, admin_user, platform_owner):
    client.post("/api/meldingen/", json={"title": "Org-A"}, headers=auth(admin_user))
    # Platform owner ziet alles
    all_logs = client.get("/api/audit/logs", headers=auth(platform_owner)).json()
    org_ids = {it["organization_id"] for it in all_logs["items"]}
    # Minimaal 1 actie van admin's org zichtbaar
    assert admin_user.organization_id in org_ids


def test_filter_by_action(client, admin_user):
    # Twee acties
    client.post("/api/meldingen/", json={"title": "x"}, headers=auth(admin_user))
    client.get("/api/auth/me", headers=auth(admin_user))

    # Filter op melding.create
    r = client.get("/api/audit/logs?action=melding.create", headers=auth(admin_user))
    items = r.json()["items"]
    assert len(items) >= 1
    assert all(it["action"] == "melding.create" for it in items)


def test_filter_by_entity_type(client, admin_user):
    client.post("/api/meldingen/", json={"title": "x"}, headers=auth(admin_user))
    r = client.get("/api/audit/logs?entity_type=melding", headers=auth(admin_user))
    items = r.json()["items"]
    assert all(it["entity_type"] == "melding" for it in items)


def test_actions_endpoint_lists_known(client, admin_user):
    r = client.get("/api/audit/actions", headers=auth(admin_user))
    actions = r.json()
    assert "melding.create" in actions
    assert "ai.analysis.run" in actions
    assert "auth.login.success" in actions
