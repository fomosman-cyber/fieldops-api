"""Wegvak-architectuur tests — fase 1+3.

Dekkingsgebied:
  - Lichtgewicht /api/assets/map endpoint (project-filter, segment-filter,
    open-meldingen-counts, archive-exclusion, org-isolation, auth)
  - NWB-feature-simplification helpers (camelCase + lowercase fallback)
  - Lengte-berekening via haversine voor LineString + MultiLineString
"""

import json

from tests.conftest import auth
from database import SessionLocal
from models import Asset, Melding
import nwb_integration as nwb


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

LINESTRING_VERDILAAN = {
    "type": "LineString",
    "coordinates": [
        [4.2300, 52.0100],
        [4.2310, 52.0105],
        [4.2320, 52.0110],
    ],
}


def _make_wegvak(user, **overrides):
    """Schrijft een wegvak-asset rechtstreeks in de DB (geen API-route nodig)."""
    db = SessionLocal()
    try:
        a = Asset(
            code=overrides.pop("code", "WVK-12345"),
            name=overrides.pop("name", "Verdilaan"),
            asset_type=overrides.pop("asset_type", "wegdek"),
            organization_id=user.organization_id,
            created_by=user.id,
            geometry_geojson=overrides.pop("geometry_geojson",
                                           json.dumps(LINESTRING_VERDILAAN)),
            length_m=overrides.pop("length_m", 412.0),
            is_segment=overrides.pop("is_segment", True),
            nwb_wvk_id=overrides.pop("nwb_wvk_id", "12345"),
            condition_score=overrides.pop("condition_score", 3),
            **overrides,
        )
        db.add(a); db.commit(); db.refresh(a)
        return a.id
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# /api/assets/map endpoint
# ─────────────────────────────────────────────────────────────────────

def test_map_endpoint_returns_only_segments_by_default(client, admin_user):
    _make_wegvak(admin_user, code="WVK-A")
    # Asset zonder geometry → moet niet meekomen
    db = SessionLocal()
    try:
        db.add(Asset(code="LP-1", asset_type="lantaarnpaal",
                     organization_id=admin_user.organization_id,
                     created_by=admin_user.id))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/assets/map", headers=auth(admin_user))
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["code"] == "WVK-A"
    assert items[0]["is_segment"] is True


def test_map_endpoint_excludes_archived(client, admin_user):
    asset_id = _make_wegvak(admin_user, code="WVK-ARCH")
    # Soft-delete via API
    r = client.delete(f"/api/assets/{asset_id}", headers=auth(admin_user))
    assert r.status_code == 200

    items = client.get("/api/assets/map", headers=auth(admin_user)).json()
    assert all(i["code"] != "WVK-ARCH" for i in items)


def test_map_endpoint_filters_by_project(client, admin_user):
    # Project aanmaken via API zodat alle integraties netjes zijn
    proj = client.post("/api/projects/",
                       json={"name": "Westland", "color": "#16a34a"},
                       headers=auth(admin_user)).json()
    _make_wegvak(admin_user, code="WVK-IN-PROJ", project_id=proj["id"])
    _make_wegvak(admin_user, code="WVK-NO-PROJ")

    items = client.get(f"/api/assets/map?project_id={proj['id']}",
                       headers=auth(admin_user)).json()
    codes = [i["code"] for i in items]
    assert codes == ["WVK-IN-PROJ"]


def test_map_endpoint_includes_open_meldingen_count(client, admin_user):
    asset_id = _make_wegvak(admin_user, code="WVK-OPEN")
    # 2 open meldingen + 1 afgerond
    db = SessionLocal()
    try:
        for i in range(2):
            db.add(Melding(title=f"open-{i}", asset_id=asset_id, status="open",
                           organization_id=admin_user.organization_id,
                           created_by=admin_user.id))
        db.add(Melding(title="closed", asset_id=asset_id, status="afgerond",
                       organization_id=admin_user.organization_id,
                       created_by=admin_user.id))
        db.commit()
    finally:
        db.close()

    items = client.get("/api/assets/map", headers=auth(admin_user)).json()
    target = next(i for i in items if i["code"] == "WVK-OPEN")
    assert target["open_meldingen_count"] == 2


def test_map_endpoint_only_segments_false_includes_assets_with_geometry(client, admin_user):
    _make_wegvak(admin_user, code="WVK-SEG", is_segment=True)
    _make_wegvak(admin_user, code="POINT-NOSEG", is_segment=False)

    default = client.get("/api/assets/map", headers=auth(admin_user)).json()
    expanded = client.get("/api/assets/map?only_segments=false",
                          headers=auth(admin_user)).json()
    assert {i["code"] for i in default} == {"WVK-SEG"}
    assert {i["code"] for i in expanded} == {"WVK-SEG", "POINT-NOSEG"}


def test_map_endpoint_org_isolation(client, admin_user, manager_user):
    # `manager_user` zit in DEZELFDE org als admin_user (zelfde fixture-org).
    # Voor isolatie maken we expliciet een tweede org via de DB.
    from models import Organization, User, AccountStatus, SubscriptionPlan, UserRole
    from auth import hash_password

    db = SessionLocal()
    try:
        other_org = Organization(name="OtherOrg",
                                 plan=SubscriptionPlan.PROFESSIONAL,
                                 status=AccountStatus.ACTIVE, max_users=10)
        db.add(other_org); db.commit(); db.refresh(other_org)
        other_user = User(email="other@test.nl",
                          hashed_password=hash_password("x"),
                          first_name="O", last_name="U",
                          role=UserRole.ADMIN, is_org_admin=True,
                          organization_id=other_org.id)
        db.add(other_user); db.commit(); db.refresh(other_user)
        # Wegvak in other-org
        db.add(Asset(code="OTHER-WVK", asset_type="wegdek",
                     organization_id=other_org.id, created_by=other_user.id,
                     geometry_geojson=json.dumps(LINESTRING_VERDILAAN),
                     is_segment=True, condition_score=2))
        db.commit()
    finally:
        db.close()

    _make_wegvak(admin_user, code="MY-WVK")

    items = client.get("/api/assets/map", headers=auth(admin_user)).json()
    codes = [i["code"] for i in items]
    assert "MY-WVK" in codes
    assert "OTHER-WVK" not in codes


def test_map_endpoint_requires_auth(client):
    r = client.get("/api/assets/map")
    assert r.status_code in (401, 403)


# ─────────────────────────────────────────────────────────────────────
# NWB-feature-simplification (nwb_integration._simplify_feature)
# ─────────────────────────────────────────────────────────────────────

def test_simplify_feature_camelcase():
    feature = {
        "properties": {
            "wvkId": 12345,
            "wvkBegdat": "2024-01-15T00:00:00",
            "jteIdBeg": 999.0,
            "jteIdEnd": "1000",
            "sttNaam": "Verdilaan",
            "gmeNaam": "Westland",
            "wegnummer": None,
        },
        "geometry": LINESTRING_VERDILAAN,
    }
    out = nwb._simplify_feature(feature)
    assert out["wvk_id"] == "12345"
    assert out["wvk_begdat"] == "2024-01-15"
    assert out["jte_id_beg"] == "999"
    assert out["jte_id_end"] == "1000"
    assert out["straatnaam"] == "Verdilaan"
    assert out["gemeente"] == "Westland"
    assert out["wegnummer"] is None
    assert out["geometry"] == LINESTRING_VERDILAAN
    assert out["length_m"] is not None
    assert out["length_m"] > 0


def test_simplify_feature_lowercase_fallback():
    feature = {
        "properties": {
            "wvk_id": "67890",
            "stt_naam": "Maasdijk",
            "gme_naam": "Westland",
        },
        "geometry": {"type": "LineString", "coordinates": [[4.2, 52.0], [4.21, 52.01]]},
    }
    out = nwb._simplify_feature(feature)
    assert out["wvk_id"] == "67890"
    assert out["straatnaam"] == "Maasdijk"


# ─────────────────────────────────────────────────────────────────────
# Haversine-lengte
# ─────────────────────────────────────────────────────────────────────

def test_calc_length_linestring():
    # ~111 km tussen 1° latitude verschil bij gelijke longitude
    geom = {"type": "LineString", "coordinates": [[4.0, 52.0], [4.0, 53.0]]}
    length = nwb._calc_length_meters(geom)
    assert 110_000 < length < 112_000


def test_calc_length_multilinestring():
    geom = {
        "type": "MultiLineString",
        "coordinates": [
            [[4.0, 52.0], [4.0, 52.5]],
            [[4.0, 52.5], [4.0, 53.0]],
        ],
    }
    length = nwb._calc_length_meters(geom)
    assert 110_000 < length < 112_000


def test_calc_length_handles_invalid_geom():
    assert nwb._calc_length_meters(None) is None
    assert nwb._calc_length_meters({}) is None
    assert nwb._calc_length_meters({"type": "Point", "coordinates": [4, 52]}) is None
