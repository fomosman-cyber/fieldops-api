"""AI-inspecties tests — stub-mode (geen API key in tests)."""

import io
from tests.conftest import auth


_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _upload(client, user, **extra):
    files = {"file": ("test.jpg", io.BytesIO(_FAKE_PNG), "image/jpeg")}
    r = client.post("/api/inspecties/analyse-foto", files=files, data=extra,
                    headers=auth(user))
    return r


def test_stub_mode_returns_deterministic(client, admin_user):
    r = _upload(client, admin_user)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["model_id"] == "stub-no-api-key"
    assert res["prompt_version"] == "v1.0"
    assert res["schade_type"] == "scheur"
    assert res["confidence"] is not None


def test_viewer_cannot_run_inspection(client, viewer_user):
    r = _upload(client, viewer_user)
    assert r.status_code == 403


def test_inspector_can_run(client, inspector_user):
    r = _upload(client, inspector_user)
    assert r.status_code == 200


def test_empty_file_rejected(client, admin_user):
    files = {"file": ("empty.jpg", io.BytesIO(b""), "image/jpeg")}
    r = client.post("/api/inspecties/analyse-foto", files=files,
                    headers=auth(admin_user))
    assert r.status_code == 400


def test_list_returns_only_own_org(client, admin_user, platform_owner):
    _upload(client, admin_user)
    r = client.get("/api/inspecties/", headers=auth(platform_owner))
    assert r.status_code == 200
    # platform owner zit in andere org → leeg
    assert r.json() == []


def test_accept_flow(client, admin_user):
    res = _upload(client, admin_user).json()
    rid = res["id"]
    r = client.post(f"/api/inspecties/{rid}/accept",
                    json={"apply_to_melding": False},
                    headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["accepted_at"] is not None


def test_reject_with_reason(client, admin_user):
    res = _upload(client, admin_user).json()
    rid = res["id"]
    r = client.post(f"/api/inspecties/{rid}/reject",
                    json={"reason": "foto te onduidelijk"},
                    headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["rejected_at"] is not None
    assert r.json()["rejection_reason"] == "foto te onduidelijk"


def test_cannot_accept_after_reject(client, admin_user):
    res = _upload(client, admin_user).json()
    rid = res["id"]
    client.post(f"/api/inspecties/{rid}/reject", json={"reason": "x"},
                headers=auth(admin_user))
    r = client.post(f"/api/inspecties/{rid}/accept", json={"apply_to_melding": False},
                    headers=auth(admin_user))
    assert r.status_code == 400


def test_batch_endpoint(client, admin_user):
    files = [
        ("files", ("a.jpg", io.BytesIO(_FAKE_PNG), "image/jpeg")),
        ("files", ("b.jpg", io.BytesIO(_FAKE_PNG), "image/jpeg")),
    ]
    r = client.post("/api/inspecties/analyse-batch", files=files,
                    headers=auth(admin_user))
    assert r.status_code == 200
    res = r.json()
    assert res["total"] == 2
    assert res["succeeded"] == 2


def test_batch_too_many_rejected(client, admin_user):
    files = [("files", (f"f{i}.jpg", io.BytesIO(_FAKE_PNG), "image/jpeg"))
             for i in range(26)]
    r = client.post("/api/inspecties/analyse-batch", files=files,
                    headers=auth(admin_user))
    assert r.status_code == 400
