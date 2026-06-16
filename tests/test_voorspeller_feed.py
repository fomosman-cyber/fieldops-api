"""PR-B — inspectie/CROW-data voedt de assets + Voorspeller.

1) De asset-CSV-import kan nu condition_score / installed_at /
   expected_lifespan_years zetten (voeden W_CONDITION + W_AGE).
2) Een geaccepteerde AI-foto-inspectie schrijft de NEN 2767-conditie terug
   naar Asset.condition_score (+ inspectie-cyclus).
"""

import io
from tests.conftest import auth
from database import SessionLocal
from models import AIAnalysis


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
