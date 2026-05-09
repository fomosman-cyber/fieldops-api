"""Wegvak split + merge tests — fase 4 NWB-architectuur.

Dekking:
  - wegvak_geometry: split_linestring_at_points, merge_linestrings, haversine
  - POST /api/assets/{id}/split — happy path, permissions, edge cases,
    melding-migratie naar dichtstbijzijnde stuk, child-migratie
  - POST /api/assets/merge — happy path, permissions, niet-aaneengesloten,
    melding-migratie, child-migratie, condition-score = max van inputs
"""

import json

import pytest

from tests.conftest import auth
from database import SessionLocal
from models import Asset, Melding
import wegvak_geometry as wgeom


# ─────────────────────────────────────────────────────────────────────
# Geometry helpers
# ─────────────────────────────────────────────────────────────────────

def test_haversine_known_distance():
    # 1° latitude verschil ≈ 111.32 km
    d = wgeom.haversine_meters([4.0, 52.0], [4.0, 53.0])
    assert 110_000 < d < 112_000


def test_linestring_length_sums_segments():
    coords = [[4.0, 52.0], [4.0, 52.5], [4.0, 53.0]]
    length = wgeom.linestring_length_m(coords)
    assert 110_000 < length < 112_000


def test_split_linestring_simple_midpoint():
    # Een rechte lijn van (0,0) naar (0, 1): splits in midden
    coords = [[0.0, 0.0], [0.0, 1.0]]
    pieces = wgeom.split_linestring_at_points(coords, [[0.0, 0.5]])
    assert len(pieces) == 2
    # Eerste stuk eindigt op ~(0, 0.5); tweede stuk start daar
    assert pieces[0][-1] == pieces[1][0]
    # Lengte ≈ helft elk
    l1 = wgeom.linestring_length_m(pieces[0])
    l2 = wgeom.linestring_length_m(pieces[1])
    total = wgeom.linestring_length_m(coords)
    assert abs(l1 + l2 - total) < 1.0  # binnen 1 meter (rounding)
    assert abs(l1 - l2) < 1.0


def test_split_at_two_points_produces_three_pieces():
    coords = [[0.0, 0.0], [0.0, 1.0]]
    pieces = wgeom.split_linestring_at_points(coords, [[0.0, 0.33], [0.0, 0.67]])
    assert len(pieces) == 3


def test_split_rejects_endpoint_only():
    coords = [[0.0, 0.0], [0.0, 1.0]]
    # Punt ZEER dicht bij start → moet weggefilterd worden
    with pytest.raises(ValueError):
        wgeom.split_linestring_at_points(coords, [[0.0, 0.000001]])


def test_split_off_line_projects_to_nearest_segment():
    # Punt naast de lijn — moet projecteren op middenpunt
    coords = [[0.0, 0.0], [0.0, 1.0]]
    pieces = wgeom.split_linestring_at_points(coords, [[0.001, 0.5]])
    assert len(pieces) == 2
    # Projectie zou nagenoeg op (0, 0.5) moeten landen
    assert abs(pieces[0][-1][0]) < 0.0001
    assert abs(pieces[0][-1][1] - 0.5) < 0.001


def test_merge_two_aligned_linestrings():
    a = [[0.0, 0.0], [0.0, 1.0]]
    b = [[0.0, 1.0], [0.0, 2.0]]
    merged = wgeom.merge_linestrings([a, b])
    assert merged == [[0.0, 0.0], [0.0, 1.0], [0.0, 2.0]]


def test_merge_with_flip():
    a = [[0.0, 0.0], [0.0, 1.0]]
    # b is omgekeerd opgeschreven — eindigt waar a eindigt
    b = [[0.0, 2.0], [0.0, 1.0]]
    merged = wgeom.merge_linestrings([a, b])
    assert merged == [[0.0, 0.0], [0.0, 1.0], [0.0, 2.0]]


def test_merge_three_segments():
    a = [[0.0, 0.0], [0.0, 1.0]]
    b = [[0.0, 1.0], [0.0, 2.0]]
    c = [[0.0, 2.0], [0.0, 3.0]]
    merged = wgeom.merge_linestrings([a, b, c])
    assert merged[0] == [0.0, 0.0]
    assert merged[-1] == [0.0, 3.0]
    assert len(merged) == 4


def test_merge_rejects_disjoint():
    a = [[0.0, 0.0], [0.0, 1.0]]
    b = [[5.0, 5.0], [5.0, 6.0]]   # ver weg, niet verbonden
    with pytest.raises(ValueError):
        wgeom.merge_linestrings([a, b])


# ─────────────────────────────────────────────────────────────────────
# Helpers voor endpoint-tests
# ─────────────────────────────────────────────────────────────────────

LINE_NORTH = [[4.30, 52.00], [4.30, 52.01]]   # ~1.1km noord-zuid
LINE_NORTH2 = [[4.30, 52.01], [4.30, 52.02]]  # aansluitend


def _make_wegvak_db(user, code, coords, **overrides):
    db = SessionLocal()
    try:
        a = Asset(
            code=code,
            asset_type="wegdek",
            organization_id=user.organization_id,
            created_by=user.id,
            geometry_geojson=json.dumps({"type": "LineString", "coordinates": coords}),
            length_m=wgeom.linestring_length_m(coords),
            is_segment=True,
            condition_score=overrides.pop("condition_score", 3),
            nwb_wvk_id=overrides.pop("nwb_wvk_id", "12345"),
            **overrides,
        )
        db.add(a); db.commit(); db.refresh(a)
        return a.id
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# POST /api/assets/{id}/split
# ─────────────────────────────────────────────────────────────────────

def test_split_endpoint_creates_two_assets(client, admin_user):
    asset_id = _make_wegvak_db(admin_user, "WVK-A", LINE_NORTH)
    r = client.post(f"/api/assets/{asset_id}/split",
                    json={"split_points": [[4.30, 52.005]]},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["archived_id"] == asset_id
    assert len(data["created"]) == 2
    codes = sorted(a["code"] for a in data["created"])
    assert codes == ["WVK-A-A", "WVK-A-B"]


def test_split_endpoint_archives_original(client, admin_user):
    asset_id = _make_wegvak_db(admin_user, "WVK-ARCH", LINE_NORTH)
    client.post(f"/api/assets/{asset_id}/split",
                json={"split_points": [[4.30, 52.005]]},
                headers=auth(admin_user))
    # Original is gearchiveerd → verschijnt niet in /map
    items = client.get("/api/assets/map", headers=auth(admin_user)).json()
    codes = [i["code"] for i in items]
    assert "WVK-ARCH" not in codes
    assert "WVK-ARCH-A" in codes
    assert "WVK-ARCH-B" in codes


def test_split_endpoint_migrates_meldingen_by_proximity(client, admin_user):
    asset_id = _make_wegvak_db(admin_user, "WVK-MEL", LINE_NORTH)
    db = SessionLocal()
    try:
        # Melding dichtbij de zuidkant (52.001)
        m1 = Melding(title="zuid", asset_id=asset_id, lat=52.001, lng=4.30,
                     organization_id=admin_user.organization_id, created_by=admin_user.id)
        # Melding dichtbij de noordkant (52.009)
        m2 = Melding(title="noord", asset_id=asset_id, lat=52.009, lng=4.30,
                     organization_id=admin_user.organization_id, created_by=admin_user.id)
        db.add_all([m1, m2]); db.commit()
        m1_id, m2_id = m1.id, m2.id
    finally:
        db.close()

    r = client.post(f"/api/assets/{asset_id}/split",
                    json={"split_points": [[4.30, 52.005]]},
                    headers=auth(admin_user))
    assert r.status_code == 200
    data = r.json()
    assert data["meldingen_migrated"] == 2

    # Verifieer: zuid-melding hoort bij stuk A (zuid), noord bij stuk B (noord)
    pieces = sorted(data["created"], key=lambda x: x["code"])
    db = SessionLocal()
    try:
        m1_after = db.query(Melding).filter_by(id=m1_id).first()
        m2_after = db.query(Melding).filter_by(id=m2_id).first()
        assert m1_after.asset_id == pieces[0]["id"]   # zuid stuk
        assert m2_after.asset_id == pieces[1]["id"]   # noord stuk
    finally:
        db.close()


def test_split_endpoint_creates_subwvk_ids(client, admin_user):
    asset_id = _make_wegvak_db(admin_user, "WVK-NWB", LINE_NORTH, nwb_wvk_id="98765")
    r = client.post(f"/api/assets/{asset_id}/split",
                    json={"split_points": [[4.30, 52.005]]},
                    headers=auth(admin_user))
    pieces = r.json()["created"]
    sub_ids = sorted(p["nwb_wvk_id"] for p in pieces)
    assert sub_ids == ["98765-A", "98765-B"]


def test_split_endpoint_custom_suffixes(client, admin_user):
    asset_id = _make_wegvak_db(admin_user, "WVK-CS", LINE_NORTH)
    r = client.post(f"/api/assets/{asset_id}/split",
                    json={"split_points": [[4.30, 52.005]],
                          "code_suffixes": ["NORTH", "SOUTH"]},
                    headers=auth(admin_user))
    assert r.status_code == 200
    codes = sorted(a["code"] for a in r.json()["created"])
    assert codes == ["WVK-CS-NORTH", "WVK-CS-SOUTH"]


def test_split_endpoint_rejects_no_geometry(client, admin_user):
    db = SessionLocal()
    try:
        a = Asset(code="NO-GEOM", asset_type="put",
                  organization_id=admin_user.organization_id,
                  created_by=admin_user.id, is_segment=True)
        db.add(a); db.commit(); db.refresh(a)
        aid = a.id
    finally:
        db.close()
    r = client.post(f"/api/assets/{aid}/split",
                    json={"split_points": [[4.30, 52.005]]},
                    headers=auth(admin_user))
    assert r.status_code == 400


def test_split_endpoint_requires_admin_or_manager(client, technician_user):
    asset_id = _make_wegvak_db(technician_user, "WVK-TECH", LINE_NORTH)
    r = client.post(f"/api/assets/{asset_id}/split",
                    json={"split_points": [[4.30, 52.005]]},
                    headers=auth(technician_user))
    assert r.status_code == 403


def test_split_endpoint_404_for_other_org(client, admin_user, manager_user):
    """admin_user en manager_user zitten in DEZELFDE org via fixture-org.
    We maken een tweede org om isolatie te testen."""
    from models import Organization, User, AccountStatus, SubscriptionPlan, UserRole
    from auth import hash_password

    db = SessionLocal()
    try:
        other_org = Organization(name="Other-split", plan=SubscriptionPlan.PROFESSIONAL,
                                 status=AccountStatus.ACTIVE, max_users=10)
        db.add(other_org); db.commit(); db.refresh(other_org)
        other_user = User(email="other-split@test.nl",
                          hashed_password=hash_password("x"),
                          first_name="O", last_name="U",
                          role=UserRole.ADMIN, is_org_admin=True,
                          organization_id=other_org.id)
        db.add(other_user); db.commit(); db.refresh(other_user)
        # Wegvak in other-org
        other_asset = Asset(code="OTHER-WVK-S", asset_type="wegdek",
                            organization_id=other_org.id, created_by=other_user.id,
                            geometry_geojson=json.dumps({"type": "LineString", "coordinates": LINE_NORTH}),
                            is_segment=True, condition_score=3)
        db.add(other_asset); db.commit(); db.refresh(other_asset)
        other_asset_id = other_asset.id
    finally:
        db.close()

    # admin uit ander-org probeert te splitsen → 404
    r = client.post(f"/api/assets/{other_asset_id}/split",
                    json={"split_points": [[4.30, 52.005]]},
                    headers=auth(admin_user))
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────
# POST /api/assets/merge
# ─────────────────────────────────────────────────────────────────────

def test_merge_endpoint_combines_two_assets(client, admin_user):
    a1 = _make_wegvak_db(admin_user, "WVK-N1", LINE_NORTH, nwb_wvk_id="111")
    a2 = _make_wegvak_db(admin_user, "WVK-N2", LINE_NORTH2, nwb_wvk_id="222")
    r = client.post("/api/assets/merge",
                    json={"asset_ids": [a1, a2]},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    data = r.json()
    assert sorted(data["archived_ids"]) == sorted([a1, a2])
    new_code = data["created"]["code"]
    assert "111" in new_code and "222" in new_code
    assert data["created"]["nwb_wvk_id"] == "111+222"


def test_merge_endpoint_archives_originals(client, admin_user):
    a1 = _make_wegvak_db(admin_user, "MR-1", LINE_NORTH)
    a2 = _make_wegvak_db(admin_user, "MR-2", LINE_NORTH2)
    client.post("/api/assets/merge",
                json={"asset_ids": [a1, a2]},
                headers=auth(admin_user))
    items = client.get("/api/assets/map", headers=auth(admin_user)).json()
    codes = [i["code"] for i in items]
    assert "MR-1" not in codes
    assert "MR-2" not in codes


def test_merge_endpoint_uses_max_condition_score(client, admin_user):
    a1 = _make_wegvak_db(admin_user, "CS-1", LINE_NORTH, condition_score=2)
    a2 = _make_wegvak_db(admin_user, "CS-2", LINE_NORTH2, condition_score=4)
    r = client.post("/api/assets/merge",
                    json={"asset_ids": [a1, a2]},
                    headers=auth(admin_user))
    assert r.json()["created"]["condition_score"] == 4


def test_merge_endpoint_migrates_meldingen(client, admin_user):
    a1 = _make_wegvak_db(admin_user, "MM-1", LINE_NORTH)
    a2 = _make_wegvak_db(admin_user, "MM-2", LINE_NORTH2)
    db = SessionLocal()
    try:
        for aid in (a1, a2):
            db.add(Melding(title=f"m-{aid}", asset_id=aid,
                           organization_id=admin_user.organization_id,
                           created_by=admin_user.id))
        db.commit()
    finally:
        db.close()

    r = client.post("/api/assets/merge",
                    json={"asset_ids": [a1, a2]},
                    headers=auth(admin_user))
    assert r.json()["meldingen_migrated"] == 2
    new_id = r.json()["created"]["id"]
    db = SessionLocal()
    try:
        cnt = db.query(Melding).filter(Melding.asset_id == new_id).count()
        assert cnt == 2
    finally:
        db.close()


def test_merge_endpoint_rejects_disjoint(client, admin_user):
    a1 = _make_wegvak_db(admin_user, "DJ-1", [[4.30, 52.00], [4.30, 52.01]])
    a2 = _make_wegvak_db(admin_user, "DJ-2", [[5.00, 53.00], [5.00, 53.01]])
    r = client.post("/api/assets/merge",
                    json={"asset_ids": [a1, a2]},
                    headers=auth(admin_user))
    assert r.status_code == 400


def test_merge_endpoint_requires_min_2_assets(client, admin_user):
    a1 = _make_wegvak_db(admin_user, "SINGLE", LINE_NORTH)
    r = client.post("/api/assets/merge",
                    json={"asset_ids": [a1]},
                    headers=auth(admin_user))
    # Pydantic min_length=2 → 422 validation error
    assert r.status_code == 422


def test_merge_endpoint_404_for_unknown_id(client, admin_user):
    a1 = _make_wegvak_db(admin_user, "UNK-1", LINE_NORTH)
    r = client.post("/api/assets/merge",
                    json={"asset_ids": [a1, "ghost-id-xyz"]},
                    headers=auth(admin_user))
    assert r.status_code == 404


def test_merge_endpoint_requires_admin(client, technician_user):
    a1 = _make_wegvak_db(technician_user, "T1", LINE_NORTH)
    a2 = _make_wegvak_db(technician_user, "T2", LINE_NORTH2)
    r = client.post("/api/assets/merge",
                    json={"asset_ids": [a1, a2]},
                    headers=auth(technician_user))
    assert r.status_code == 403


def test_merge_endpoint_custom_code_and_name(client, admin_user):
    a1 = _make_wegvak_db(admin_user, "CN-1", LINE_NORTH)
    a2 = _make_wegvak_db(admin_user, "CN-2", LINE_NORTH2)
    r = client.post("/api/assets/merge",
                    json={"asset_ids": [a1, a2],
                          "new_code": "VERDILAAN-NOORD",
                          "new_name": "Verdilaan noord (gemerged)"},
                    headers=auth(admin_user))
    data = r.json()["created"]
    assert data["code"] == "VERDILAAN-NOORD"
    assert data["name"] == "Verdilaan noord (gemerged)"
