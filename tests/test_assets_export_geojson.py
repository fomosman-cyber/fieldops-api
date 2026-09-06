"""GeoJSON-export van assets — de tegenhanger van de import.

Importeren werkte al, exporteren niet, terwijl de website "geen lock-in"
belooft. Dat is het punt van deze endpoint: je haalt je eigen objecten er in
hetzelfde formaat weer uit.

Twee dingen worden hier scherp bewaakt:

  1. De route mag niet worden afgevangen door `GET /{asset_id}`. Dat pad is ook
     een enkel segment, dus als /export.geojson daarna wordt gedeclareerd krijg
     je een keurige 404 "Asset niet gevonden" in plaats van je export.
  2. Een wegvak is een LineString. Die mag niet tot een punt worden
     platgeslagen, want dan is de export waardeloos voor wie hem in QGIS opent.
"""
import io
import json

import pytest

from database import SessionLocal
from models import AccountStatus, Asset, Organization, Project, SubscriptionPlan

from .conftest import _make_user, auth


@pytest.fixture
def other_org():
    """Gebruiker in een TWEEDE organisatie. Alleen kolom-attributen gebruiken
    (.id, .organization_id): de sessie is dicht voordat de test draait."""
    db = SessionLocal()
    try:
        andere = Organization(name="AndereOrg", plan=SubscriptionPlan.PROFESSIONAL,
                              status=AccountStatus.ACTIVE, max_users=10)
        db.add(andere)
        db.commit()
        db.refresh(andere)
        return _make_user(db, "andere-export@test.nl", org=andere)
    finally:
        db.close()

# Een wegvak zoals de NWB-import het opslaat.
LIJN = json.dumps({
    "type": "LineString",
    "coordinates": [[4.6612, 52.0705], [4.6698, 52.0741], [4.6790, 52.0788]],
})


def _asset(user, *, code, asset_type="lantaarnpaal", lat=None, lng=None,
           geometry=None, project_id=None, props=None, archived=None,
           condition=None):
    db = SessionLocal()
    try:
        a = Asset(code=code, asset_type=asset_type,
                  organization_id=user.organization_id, created_by=user.id,
                  lat=lat, lng=lng, geometry_geojson=geometry,
                  project_id=project_id, properties_json=props,
                  archived_at=archived, condition_score=condition)
        db.add(a)
        db.commit()
        db.refresh(a)
        return a.id
    finally:
        db.close()


def _export(client, user, query=""):
    r = client.get("/api/assets/export.geojson" + query, headers=auth(user))
    assert r.status_code == 200, r.text
    return r


# ── De route mag niet worden afgevangen ──────────────────────────────

def test_route_wordt_niet_opgeslokt_door_asset_id(client, admin_user):
    """Zonder de juiste volgorde vangt GET /{asset_id} dit pad af en krijg je
    404 'Asset niet gevonden' in plaats van een export."""
    r = client.get("/api/assets/export.geojson", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["type"] == "FeatureCollection"


def test_content_type_en_bestandsnaam(client, admin_user):
    r = _export(client, admin_user)
    assert r.headers["content-type"].startswith("application/geo+json")
    assert ".geojson" in r.headers.get("content-disposition", "")


# ── Geometrie ────────────────────────────────────────────────────────

def test_punt_uit_lat_lng(client, admin_user):
    _asset(admin_user, code="LNT-001", lat=52.0705, lng=4.6612)
    f = _export(client, admin_user).json()["features"][0]
    assert f["geometry"]["type"] == "Point"
    # GeoJSON is lng, lat -- in die volgorde. Omgekeerd zet je Nederland in Somalie.
    assert f["geometry"]["coordinates"] == [4.6612, 52.0705]


def test_wegvak_blijft_een_linestring(client, admin_user):
    """Een wegvak platslaan tot een punt maakt de export waardeloos."""
    _asset(admin_user, code="WVK-001", asset_type="wegvak",
           geometry=LIJN, lat=52.07, lng=4.66)
    f = _export(client, admin_user).json()["features"][0]
    assert f["geometry"]["type"] == "LineString"
    assert len(f["geometry"]["coordinates"]) == 3


def test_asset_zonder_coordinaten_valt_niet_weg(client, admin_user):
    """Een object zonder coordinaten bestaat wel degelijk; geometry null is
    geldig GeoJSON en beter dan het weglaten van de asset."""
    _asset(admin_user, code="PUT-001")
    data = _export(client, admin_user).json()
    assert len(data["features"]) == 1
    assert data["features"][0]["geometry"] is None
    assert data["features"][0]["properties"]["code"] == "PUT-001"


def test_kapotte_geometrie_valt_terug_op_het_punt(client, admin_user):
    _asset(admin_user, code="WVK-002", geometry="{dit is geen json",
           lat=52.07, lng=4.66)
    f = _export(client, admin_user).json()["features"][0]
    assert f["geometry"]["type"] == "Point"


# ── Eigenschappen ────────────────────────────────────────────────────

def test_eigen_velden_blijven_behouden(client, admin_user):
    _asset(admin_user, code="LNT-002", lat=52.0, lng=4.0,
           props=json.dumps({"mast_hoogte": "4m", "eigenaar": "Gemeente"}))
    p = _export(client, admin_user).json()["features"][0]["properties"]
    assert p["mast_hoogte"] == "4m"
    assert p["eigenaar"] == "Gemeente"


def test_eigen_velden_overschrijven_de_vaste_kolommen_niet(client, admin_user):
    """Anders verandert een import/export-ronde stilletjes de betekenis van
    bijvoorbeeld `code`."""
    _asset(admin_user, code="ECHT-001",
           props=json.dumps({"code": "VERZONNEN", "asset_type": "onzin"}))
    p = _export(client, admin_user).json()["features"][0]["properties"]
    assert p["code"] == "ECHT-001"
    assert p["asset_type"] == "lantaarnpaal"


# ── Filters en scoping ───────────────────────────────────────────────

def test_gearchiveerde_assets_standaard_niet_mee(client, admin_user):
    from datetime import datetime, timezone
    _asset(admin_user, code="ACTIEF-001")
    _asset(admin_user, code="OUD-001", archived=datetime.now(timezone.utc))

    codes = [f["properties"]["code"] for f in _export(client, admin_user).json()["features"]]
    assert codes == ["ACTIEF-001"]

    met = _export(client, admin_user, "?include_archived=true").json()["features"]
    assert sorted(f["properties"]["code"] for f in met) == ["ACTIEF-001", "OUD-001"]
    assert [f for f in met if f["properties"]["code"] == "OUD-001"][0]["properties"]["archived"] is True


def test_filter_op_project_en_type(client, admin_user):
    db = SessionLocal()
    try:
        p = Project(name="N207", organization_id=admin_user.organization_id,
                    status="active", created_by=admin_user.id)
        db.add(p)
        db.commit()
        db.refresh(p)
        pid = p.id
    finally:
        db.close()

    _asset(admin_user, code="IN-PROJECT", project_id=pid)
    _asset(admin_user, code="BUITEN-PROJECT")
    _asset(admin_user, code="EEN-PUT", asset_type="put", project_id=pid)

    codes = [f["properties"]["code"]
             for f in _export(client, admin_user, "?project_id=" + pid).json()["features"]]
    assert sorted(codes) == ["EEN-PUT", "IN-PROJECT"]

    codes = [f["properties"]["code"]
             for f in _export(client, admin_user, "?asset_type=put").json()["features"]]
    assert codes == ["EEN-PUT"]


def test_export_bevat_alleen_je_eigen_organisatie(client, admin_user, other_org):
    _asset(admin_user, code="VAN-MIJ")
    _asset(other_org, code="VAN-EEN-ANDER")

    codes = [f["properties"]["code"] for f in _export(client, admin_user).json()["features"]]
    assert codes == ["VAN-MIJ"]


# ── Het punt van de hele endpoint ────────────────────────────────────

def test_export_kan_weer_geimporteerd_worden(client, admin_user):
    """Geen lock-in is pas waar als het er ook weer in gaat."""
    _asset(admin_user, code="RT-001", lat=52.0705, lng=4.6612, condition=3)
    _asset(admin_user, code="RT-002", asset_type="wegvak", geometry=LIJN)

    uitvoer = _export(client, admin_user).content

    # Alles weggooien en de export terugzetten.
    db = SessionLocal()
    try:
        db.query(Asset).delete()
        db.commit()
    finally:
        db.close()

    r = client.post("/api/assets/import/geojson",
                    files={"file": ("export.geojson", io.BytesIO(uitvoer), "application/geo+json")},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text

    codes = [f["properties"]["code"] for f in _export(client, admin_user).json()["features"]]
    assert sorted(codes) == ["RT-001", "RT-002"]
