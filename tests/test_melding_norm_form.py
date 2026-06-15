"""Tests voor dynamisch norm-formulier bij melding aanmaken (bug #16).

Per asset-type moeten de juiste NEN/CROW-velden beschikbaar zijn, en de
ingevulde waarden moeten als norm_data op de melding opgeslagen + teruggegeven
worden.
"""
from database import SessionLocal
from models import Asset
from melding_norm_forms import norm_form_voor
from tests.conftest import auth


def _asset(org, admin_user, *, code, asset_type):
    db = SessionLocal()
    try:
        a = Asset(code=code, asset_type=asset_type, organization_id=org.id,
                  created_by=admin_user.id)
        db.add(a); db.commit(); db.refresh(a)
        return a
    finally:
        db.close()


# ── Config-module ───────────────────────────────────────────────────────────

def test_norm_form_speeltoestel_geeft_1176_velden():
    form = norm_form_voor("speeltoestel")
    keys = {v["key"] for v in form["velden"]}
    assert "en1176_categorie" in keys
    assert "1176" in form["norm_label"]


def test_norm_form_alias_normaliseert():
    # 'lantaarnpaal' → verlichting → NEN 3140
    form = norm_form_voor("lantaarnpaal")
    assert form["canonical_type"] == "verlichting"
    keys = {v["key"] for v in form["velden"]}
    assert "nen3140_isolatie_megaohm" in keys


def test_norm_form_wegdek_geen_extra_velden():
    """Verharding gebruikt de bestaande CROW 146-sectie → geen norm_data-velden."""
    form = norm_form_voor("asfalt")
    assert form["canonical_type"] == "wegdek_asfalt"
    assert form["velden"] == []


def test_norm_form_onbekend_type_leeg():
    form = norm_form_voor("ruimteschip")
    assert form["velden"] == []
    assert form["norm_label"] is None


# ── Endpoint ────────────────────────────────────────────────────────────────

def test_form_schema_endpoint_brug(client, admin_user):
    r = client.get("/api/meldingen/form-schema?asset_type=brug", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    keys = {v["key"] for v in r.json()["velden"]}
    assert "nen2767_conditiescore" in keys


def test_form_schema_endpoint_does_not_collide_with_melding_id(client, admin_user):
    """'form-schema' mag niet als melding-id worden opgevat (route-volgorde)."""
    r = client.get("/api/meldingen/form-schema?asset_type=boom", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["canonical_type"] == "boom"


# ── Opslag + teruggave ──────────────────────────────────────────────────────

def test_create_melding_met_norm_data(client, org, admin_user):
    asset = _asset(org, admin_user, code="SP-100", asset_type="speeltoestel")
    r = client.post("/api/meldingen/", headers=auth(admin_user), json={
        "title": "Knelpunt schommel",
        "asset_id": asset.id,
        "norm_data": {"en1176_categorie": "C", "nen1176_inspectie_kind": "hoofd"},
    })
    assert r.status_code == 200, r.text
    nd = r.json()["norm_data"]
    assert nd == {"en1176_categorie": "C", "nen1176_inspectie_kind": "hoofd"}


def test_create_melding_norm_data_sanitized(client, org, admin_user):
    """Onbekende keys + lege waarden worden uit norm_data gefilterd."""
    asset = _asset(org, admin_user, code="BM-100", asset_type="boom")
    r = client.post("/api/meldingen/", headers=auth(admin_user), json={
        "title": "Holte",
        "asset_id": asset.id,
        "norm_data": {"vta_risicoklasse": 4, "hack_field": "x", "vta_holte_pct": ""},
    })
    assert r.status_code == 200, r.text
    nd = r.json()["norm_data"]
    assert nd == {"vta_risicoklasse": 4}


def test_update_melding_norm_data(client, org, admin_user):
    asset = _asset(org, admin_user, code="RI-100", asset_type="riolering")
    r = client.post("/api/meldingen/", headers=auth(admin_user), json={
        "title": "Wortelingroei", "asset_id": asset.id,
    })
    mid = r.json()["id"]
    r = client.put(f"/api/meldingen/{mid}", headers=auth(admin_user), json={
        "norm_data": {"nen3399_code": "BAH", "nen3399_klasse": 4},
    })
    assert r.status_code == 200, r.text
    assert r.json()["norm_data"] == {"nen3399_code": "BAH", "nen3399_klasse": 4}
