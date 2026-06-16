"""Tests voor melding-document velden (bug #12).

Het melding-document (PDF van één losse melding) miste GPS, foto en
asset-codering. GPS/foto zaten al in de response; de asset-koppeling én de
CROW/NEN-classificatie ontbraken volledig in MeldingResponse. Deze tests
borgen dat die velden nu worden meegestuurd, zodat het document (en de
bewerk-modal) ze kunnen tonen.
"""
from database import SessionLocal
from models import Asset, Melding
from tests.conftest import auth


def _asset(org, admin_user, *, code="PUT-001"):
    db = SessionLocal()
    try:
        a = Asset(code=code, asset_type="put", organization_id=org.id,
                  created_by=admin_user.id)
        db.add(a); db.commit(); db.refresh(a)
        return a
    finally:
        db.close()


def _melding(org, admin_user, *, asset_id=None, **extra):
    db = SessionLocal()
    try:
        m = Melding(title="Scheur in put", organization_id=org.id,
                    created_by=admin_user.id, asset_id=asset_id,
                    lat=52.37, lng=4.89, photo_url="data:image/jpeg;base64,AAAA",
                    status="open", **extra)
        db.add(m); db.commit(); db.refresh(m)
        return m
    finally:
        db.close()


def test_list_includes_asset_and_gps_and_photo(client, org, admin_user):
    a = _asset(org, admin_user, code="PUT-042")
    _melding(org, admin_user, asset_id=a.id,
             crow_klasse="M2", nen_2767_conditie=3, onderhoud_categorie="KO")

    r = client.get("/api/meldingen/", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    m = r.json()[0]
    # asset-codering (#12 kern)
    assert m["asset_id"] == a.id
    assert m["asset_code"] == "PUT-042"
    assert m["asset_type"] == "put"
    assert m["lat"] == 52.37 and m["lng"] == 4.89
    # Lijst is light (perf): geen base64-foto, wél has_photo-vlag.
    assert m["photo_url"] is None
    assert m["has_photo"] is True
    # CROW/NEN-classificatie voor CROW-conform document
    assert m["crow_klasse"] == "M2"
    assert m["nen_2767_conditie"] == 3
    assert m["onderhoud_categorie"] == "KO"

    # with_photos=1 levert de volledige base64 (voor kleine gefilterde lijsten)
    r = client.get("/api/meldingen/?with_photos=1", headers=auth(admin_user))
    assert r.json()[0]["photo_url"].startswith("data:image/")
    # Single-GET geeft altijd de volledige foto (voor document + lazy-load)
    r = client.get(f"/api/meldingen/{r.json()[0]['id']}", headers=auth(admin_user))
    assert r.json()["photo_url"].startswith("data:image/")


def test_get_one_includes_asset_fields(client, org, admin_user):
    a = _asset(org, admin_user, code="BRUG-7")
    m = _melding(org, admin_user, asset_id=a.id)
    r = client.get(f"/api/meldingen/{m.id}", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["asset_code"] == "BRUG-7"
    assert body["asset_type"] == "put"


def test_melding_without_asset_has_null_asset_fields(client, org, admin_user):
    _melding(org, admin_user, asset_id=None)
    r = client.get("/api/meldingen/", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    m = r.json()[0]
    assert m["asset_id"] is None
    assert m["asset_code"] is None
    assert m["asset_type"] is None
