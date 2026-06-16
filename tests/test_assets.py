"""Asset CRUD + hierarchy + CSV-import tests."""

import io
from tests.conftest import auth
from database import SessionLocal
from models import Melding


def _new_asset(client, user, **overrides):
    payload = {"code": "A-001", "asset_type": "wegdek"}
    payload.update(overrides)
    r = client.post("/api/assets/", json=payload, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


def test_admin_can_create_asset(client, admin_user):
    a = _new_asset(client, admin_user, name="Lekdijk km 12")
    assert a["code"] == "A-001"
    assert a["asset_type"] == "wegdek"
    assert a["name"] == "Lekdijk km 12"


def test_viewer_cannot_create_asset(client, viewer_user):
    r = client.post("/api/assets/", json={"code": "X", "asset_type": "put"},
                    headers=auth(viewer_user))
    assert r.status_code == 403


def test_technician_cannot_manage_assets(client, technician_user):
    """Asset-management is alleen voor admin/manager — strenger dan meldingen."""
    r = client.post("/api/assets/", json={"code": "X", "asset_type": "put"},
                    headers=auth(technician_user))
    assert r.status_code == 403


def test_manager_can_create_asset(client, manager_user):
    r = client.post("/api/assets/", json={"code": "M-1", "asset_type": "kering"},
                    headers=auth(manager_user))
    assert r.status_code == 200


def test_duplicate_code_rejected(client, admin_user):
    _new_asset(client, admin_user, code="DUP-1")
    r = client.post("/api/assets/", json={"code": "DUP-1", "asset_type": "put"},
                    headers=auth(admin_user))
    assert r.status_code == 400
    assert "DUP-1" in r.json()["detail"]


def test_invalid_condition_score_rejected(client, admin_user):
    r = client.post("/api/assets/", json={
        "code": "B-1", "asset_type": "put", "condition_score": 9,
    }, headers=auth(admin_user))
    assert r.status_code == 400
    assert "1 en 5" in r.json()["detail"]


def test_parent_must_exist_in_org(client, admin_user):
    r = client.post("/api/assets/", json={
        "code": "C-1", "asset_type": "put", "parent_asset_id": "ghost",
    }, headers=auth(admin_user))
    assert r.status_code == 400


def test_self_parent_rejected_on_update(client, admin_user):
    a = _new_asset(client, admin_user)
    r = client.put(f"/api/assets/{a['id']}", json={"parent_asset_id": a["id"]},
                   headers=auth(admin_user))
    assert r.status_code == 400


def test_archive_then_excluded_from_list(client, admin_user):
    a = _new_asset(client, admin_user, code="ARCH-1")
    r = client.delete(f"/api/assets/{a['id']}", headers=auth(admin_user))
    assert r.status_code == 200
    items = client.get("/api/assets/", headers=auth(admin_user)).json()
    assert all(i["code"] != "ARCH-1" for i in items)


def test_tree_structure(client, admin_user):
    parent = _new_asset(client, admin_user, code="PARENT")
    child = _new_asset(client, admin_user, code="CHILD",
                       parent_asset_id=parent["id"])
    r = client.get("/api/assets/tree", headers=auth(admin_user))
    assert r.status_code == 200
    tree = r.json()
    roots = [n for n in tree if n["code"] == "PARENT"]
    assert len(roots) == 1
    assert any(c["code"] == "CHILD" for c in roots[0]["children"])


def test_types_aggregation(client, admin_user):
    _new_asset(client, admin_user, code="P1", asset_type="put")
    _new_asset(client, admin_user, code="P2", asset_type="put")
    _new_asset(client, admin_user, code="W1", asset_type="wegdek")
    r = client.get("/api/assets/types", headers=auth(admin_user))
    counts = {t["asset_type"]: t["count"] for t in r.json()}
    assert counts.get("put") == 2
    assert counts.get("wegdek") == 1


def test_csv_import(client, admin_user):
    csv_content = (
        "code,asset_type,name,lat,lng,parent_code\n"
        "WV-1,wegvak,Wegvak 1,52.0,5.0,\n"
        "PUT-A,put,Put A,52.001,5.001,WV-1\n"
        "PUT-B,put,Put B,52.002,5.002,WV-1\n"
    )
    files = {"file": ("assets.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}
    r = client.post("/api/assets/import/csv", files=files, headers=auth(admin_user))
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["created"] == 3
    assert res["updated"] == 0

    # Tree moet kloppen na import
    tree = client.get("/api/assets/tree", headers=auth(admin_user)).json()
    wv = next((n for n in tree if n["code"] == "WV-1"), None)
    assert wv is not None
    assert len(wv["children"]) == 2


def _add_melding(org_id, user_id, asset_id, status):
    """Plaats een melding direct in de DB (omzeilt de status-API-regels)."""
    db = SessionLocal()
    try:
        m = Melding(title=f"M-{status}", status=status,
                    organization_id=org_id, created_by=user_id, asset_id=asset_id)
        db.add(m)
        db.commit()
    finally:
        db.close()


def test_open_meldingen_count_excludes_opgelost_en_afgerond(client, admin_user):
    """De per-asset 'Open'-teller mag alleen nog-te-doen meldingen tellen:
    'opgelost' én 'afgerond' zijn beide afgehandeld en tellen NIET mee."""
    a = _new_asset(client, admin_user, code="CNT-1")
    org_id, uid = admin_user.organization_id, admin_user.id
    _add_melding(org_id, uid, a["id"], "open")
    _add_melding(org_id, uid, a["id"], "open")
    _add_melding(org_id, uid, a["id"], "in_behandeling")
    _add_melding(org_id, uid, a["id"], "opgelost")   # mag NIET meetellen
    _add_melding(org_id, uid, a["id"], "afgerond")   # mag NIET meetellen

    row = next(i for i in client.get("/api/assets/", headers=auth(admin_user)).json()
               if i["code"] == "CNT-1")
    assert row["open_meldingen_count"] == 3

    one = client.get(f"/api/assets/{a['id']}", headers=auth(admin_user)).json()
    assert one["open_meldingen_count"] == 3


def test_asset_import_template_csv(client, admin_user):
    r = client.get("/api/assets/import/template.csv", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert "text/csv" in r.headers["content-type"]
    assert "assets-template.csv" in r.headers["content-disposition"]
    header = r.text.splitlines()[0]
    for col in ("code", "asset_type", "name", "lat", "lng", "parent_code"):
        assert col in header
