"""PR-B — inspectie/CROW-data voedt de assets + Voorspeller.

1) De asset-CSV-import kan nu condition_score / installed_at /
   expected_lifespan_years zetten (voeden W_CONDITION + W_AGE).
2) Een geaccepteerde AI-foto-inspectie schrijft de NEN 2767-conditie terug
   naar Asset.condition_score (+ inspectie-cyclus).
"""

import io
from tests.conftest import auth
from database import SessionLocal
from models import AIAnalysis, Melding
import asset_lifespan


def _import_csv(client, user, content):
    files = {"file": ("assets.csv", io.BytesIO(content.encode("utf-8")), "text/csv")}
    return client.post("/api/assets/import/csv", files=files, headers=auth(user))


def test_csv_import_sets_condition_and_lifespan(client, admin_user):
    csv_content = (
        "code;asset_type;name;condition_score;installed_at;expected_lifespan_years\n"
        "BRUG-1;brug;Spoorbrug;4;2008;80\n"
        "WV-9;wegvak;Dorpsstraat;2;2019-06-01;40\n"
    )
    r = _import_csv(client, admin_user, csv_content)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 2

    items = {i["code"]: i for i in client.get("/api/assets/", headers=auth(admin_user)).json()}
    assert items["BRUG-1"]["condition_score"] == 4
    assert items["BRUG-1"]["expected_lifespan_years"] == 80
    assert (items["BRUG-1"]["installed_at"] or "")[:4] == "2008"
    assert items["WV-9"]["condition_score"] == 2


def test_csv_import_accepts_dutch_aliases_for_condition(client, admin_user):
    # NL-synoniemen: 'conditie' / 'bouwjaar' / 'levensduur'
    csv_content = (
        "objectnummer;objecttype;conditie;bouwjaar;levensduur\n"
        "PUT-7;put;3;1998;60\n"
    )
    r = _import_csv(client, admin_user, csv_content)
    assert r.status_code == 200, r.text
    a = next(i for i in client.get("/api/assets/", headers=auth(admin_user)).json()
             if i["code"] == "PUT-7")
    assert a["condition_score"] == 3
    assert a["expected_lifespan_years"] == 60


def test_csv_import_rejects_out_of_range_condition(client, admin_user):
    csv_content = "code;asset_type;condition_score\nX-1;put;9\n"  # 9 valt buiten 1-5
    r = _import_csv(client, admin_user, csv_content)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["created"] == 1  # asset wordt wél aangemaakt
    assert any("conditiescore" in (e.get("error") or "") for e in res.get("errors", []))
    a = next(i for i in client.get("/api/assets/", headers=auth(admin_user)).json()
             if i["code"] == "X-1")
    assert a["condition_score"] is None  # ongeldige conditie niet gezet


def test_accept_ai_inspection_writes_condition_to_asset(client, admin_user):
    a = client.post("/api/assets/", json={"code": "INSP-1", "asset_type": "brug"},
                    headers=auth(admin_user)).json()
    db = SessionLocal()
    try:
        rec = AIAnalysis(
            asset_id=a["id"], nen_2767_conditie=4,
            prompt_version="test", model_id="test-model",
            organization_id=admin_user.organization_id, created_by=admin_user.id,
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)
        analysis_id = rec.id
    finally:
        db.close()

    before = client.get(f"/api/assets/{a['id']}", headers=auth(admin_user)).json()
    assert before["condition_score"] is None

    r = client.post(f"/api/inspecties/{analysis_id}/accept",
                    json={"apply_to_melding": False}, headers=auth(admin_user))
    assert r.status_code == 200, r.text

    after = client.get(f"/api/assets/{a['id']}", headers=auth(admin_user)).json()
    assert after["condition_score"] == 4


# ── PR-D: fleet-wide predictive-readiness ──

def test_default_lifespan_for_richtwaarden():
    assert asset_lifespan.default_lifespan_for("brug") == 80
    assert asset_lifespan.default_lifespan_for("Wegvak (asfalt)") == 30
    assert asset_lifespan.default_lifespan_for("speeltoestel") == 15
    assert asset_lifespan.default_lifespan_for("iets onbekends") == 30
    assert asset_lifespan.default_lifespan_for(None) is None


def test_predictive_readiness_telt_per_veld(client, admin_user):
    client.post("/api/assets/", json={"code": "R-1", "asset_type": "brug"}, headers=auth(admin_user))
    client.post("/api/assets/", json={"code": "R-2", "asset_type": "wegvak", "condition_score": 3},
                headers=auth(admin_user))
    data = client.get("/api/assets/predictive-readiness", headers=auth(admin_user)).json()
    assert data["total"] == 2
    assert data["with_condition"] == 1
    assert data["missing_condition"] == 1


def test_predictive_backfill_lifespan_dry_run_then_commit(client, admin_user):
    a = client.post("/api/assets/", json={"code": "BF-1", "asset_type": "brug"},
                    headers=auth(admin_user)).json()
    # dry-run telt maar wijzigt niet
    r = client.post("/api/assets/predictive-backfill?set_lifespan_defaults=true&dry_run=true",
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["lifespan_set"] == 1
    assert client.get(f"/api/assets/{a['id']}", headers=auth(admin_user)).json()["expected_lifespan_years"] is None
    # commit zet de levensduur (brug → 80)
    r2 = client.post("/api/assets/predictive-backfill?set_lifespan_defaults=true&dry_run=false",
                     headers=auth(admin_user))
    assert r2.json()["lifespan_set"] == 1
    assert client.get(f"/api/assets/{a['id']}", headers=auth(admin_user)).json()["expected_lifespan_years"] == 80


def test_predictive_backfill_estimate_condition_from_melding(client, admin_user):
    a = client.post("/api/assets/", json={"code": "BF-2", "asset_type": "wegvak"},
                    headers=auth(admin_user)).json()
    db = SessionLocal()
    try:
        m = Melding(title="schade", organization_id=admin_user.organization_id,
                    created_by=admin_user.id, asset_id=a["id"], crow_klasse="M2")
        db.add(m)
        db.commit()
    finally:
        db.close()
    r = client.post("/api/assets/predictive-backfill?estimate_condition=true&set_lifespan_defaults=false&dry_run=false",
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["condition_estimated"] == 1
    # M2 → geschatte conditie 4
    assert client.get(f"/api/assets/{a['id']}", headers=auth(admin_user)).json()["condition_score"] == 4


def test_viewer_cannot_backfill(client, viewer_user):
    r = client.post("/api/assets/predictive-backfill?dry_run=false", headers=auth(viewer_user))
    assert r.status_code == 403
