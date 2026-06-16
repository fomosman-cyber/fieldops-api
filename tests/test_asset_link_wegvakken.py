"""Asset↔melding-koppeling incl. wegvakken (lijn-geometrie).

Achtergrond: de auto-link koppelde meldingen alleen aan PUNT-assets (lat/lng).
Wegdek-/scheur-meldingen konden daardoor nooit aan hun wegvak (LineString) koppelen
→ die assets bleven zonder gekoppelde melding en zonder Voorspeller-conditie-schatting.
Deze tests borgen de geometrie-bewuste linker (punt-tot-segment) + de dry-run
backfill-endpoint.
"""

import json

from tests.conftest import auth
from database import SessionLocal
from models import Asset, Melding
from routers.meldingen_router import (
    _geometry_pieces,
    _point_seg_dist_m,
    _haversine_m,
    _build_asset_geo_index,
    _nearest_asset_in_index,
)

# Sparse wegvak: 2 vertices, ~2.2 km lang. Het midden ligt ~1.1 km van élk
# vertex — een melding daar koppelt alléén via punt-tot-segment, niet via
# punt-tot-vertex. Precies het scenario dat de oude linker miste.
SPARSE_LINE = {"type": "LineString", "coordinates": [[4.40, 52.00], [4.40, 52.02]]}


def _mk_asset(user, **kw):
    db = SessionLocal()
    try:
        a = Asset(
            code=kw.pop("code", "A1"),
            asset_type=kw.pop("asset_type", "wegdek"),
            organization_id=user.organization_id,
            created_by=user.id,
            **kw,
        )
        db.add(a); db.commit(); db.refresh(a)
        return a.id
    finally:
        db.close()


def _mk_melding(user, **kw):
    db = SessionLocal()
    try:
        m = Melding(
            title=kw.pop("title", "M"),
            organization_id=user.organization_id,
            created_by=user.id,
            **kw,
        )
        db.add(m); db.commit(); db.refresh(m)
        return m.id
    finally:
        db.close()


def _asset_id_of(melding_id):
    db = SessionLocal()
    try:
        m = db.query(Melding).filter(Melding.id == melding_id).first()
        return m.asset_id if m else None
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────

def test_geometry_pieces_linestring_orders_lat_lng():
    pieces = _geometry_pieces(json.dumps(SPARSE_LINE))
    assert len(pieces) == 1
    assert pieces[0][0] == (52.00, 4.40)    # (lat, lng) — geojson is [lng, lat]
    assert pieces[0][-1] == (52.02, 4.40)


def test_geometry_pieces_multilinestring():
    geom = {"type": "MultiLineString",
            "coordinates": [[[4.4, 52.0], [4.4, 52.01]], [[4.5, 52.1], [4.5, 52.11]]]}
    pieces = _geometry_pieces(json.dumps(geom))
    assert len(pieces) == 2


def test_geometry_pieces_invalid_returns_empty():
    assert _geometry_pieces(None) == []
    assert _geometry_pieces("geen json") == []
    assert _geometry_pieces(json.dumps({"type": "Polygon", "coordinates": []})) == []


def test_point_seg_dist_midsegment_small_but_vertex_far():
    # ~7 m van het midden van het segment …
    d_seg = _point_seg_dist_m(52.01, 4.4001, 52.00, 4.40, 52.02, 4.40)
    assert d_seg < 15
    # … maar > 1 km van het dichtstbijzijnde vertex (bewijst de meerwaarde)
    d_vertex = min(_haversine_m(52.01, 4.4001, 52.00, 4.40),
                   _haversine_m(52.01, 4.4001, 52.02, 4.40))
    assert d_vertex > 1000


# ─────────────────────────────────────────────────────────────────────
# Index + nearest
# ─────────────────────────────────────────────────────────────────────

def test_nearest_finds_wegvak_midsegment(admin_user):
    wid = _mk_asset(admin_user, code="WVK-1", asset_type="wegdek",
                    geometry_geojson=json.dumps(SPARSE_LINE), is_segment=True)
    db = SessionLocal()
    try:
        index = _build_asset_geo_index(db.query(Asset).all())
    finally:
        db.close()
    best_id, dist = _nearest_asset_in_index(52.01, 4.4001, index, max_m=200.0)
    assert best_id == wid
    assert dist < 15


def test_nearest_point_asset_unchanged(admin_user):
    pid = _mk_asset(admin_user, code="LP-1", asset_type="lantaarnpaal",
                    lat=52.10, lng=4.50)
    db = SessionLocal()
    try:
        index = _build_asset_geo_index(db.query(Asset).all())
    finally:
        db.close()
    best_id, dist = _nearest_asset_in_index(52.1001, 4.50, index, max_m=200.0)
    assert best_id == pid
    assert dist < 50


def test_nearest_respects_large_max_m_bbox_margin(admin_user):
    # Borgt dat de bbox-quick-reject met max_m meeschaalt: een punt-asset ~800 m
    # ver moet bij max_m=1000 koppelen (met een vaste 0.004°-marge zou dit
    # ten onrechte weggefilterd worden).
    pid = _mk_asset(admin_user, code="LP-far", asset_type="lantaarnpaal",
                    lat=52.10, lng=4.50)
    # ~800 m oostelijk: 0.0117° lng op breedte 52° ≈ 800 m
    far_lat, far_lng = 52.10, 4.50 + 0.0117
    db = SessionLocal()
    try:
        index = _build_asset_geo_index(db.query(Asset).all())
    finally:
        db.close()
    assert _nearest_asset_in_index(far_lat, far_lng, index, max_m=200.0)[0] is None
    best_id, dist = _nearest_asset_in_index(far_lat, far_lng, index, max_m=1000.0)
    assert best_id == pid
    assert 700 < dist < 900


def test_nearest_returns_none_outside_threshold(admin_user):
    _mk_asset(admin_user, code="LP-1", asset_type="lantaarnpaal", lat=52.10, lng=4.50)
    db = SessionLocal()
    try:
        index = _build_asset_geo_index(db.query(Asset).all())
    finally:
        db.close()
    best_id, dist = _nearest_asset_in_index(53.0, 5.0, index, max_m=200.0)
    assert best_id is None and dist is None


# ─────────────────────────────────────────────────────────────────────
# Backfill-endpoint
# ─────────────────────────────────────────────────────────────────────

def _seed_mixed(admin_user):
    """1 wegvak + 1 punt-asset + 4 meldingen (lijn / punt / geen-gps / ver-weg)."""
    wid = _mk_asset(admin_user, code="WVK-1", asset_type="wegdek",
                    geometry_geojson=json.dumps(SPARSE_LINE), is_segment=True)
    pid = _mk_asset(admin_user, code="LP-1", asset_type="lantaarnpaal",
                    lat=52.10, lng=4.50)
    m_line = _mk_melding(admin_user, title="scheur in wegdek", lat=52.01, lng=4.4001)
    m_point = _mk_melding(admin_user, title="lantaarn stuk", lat=52.1001, lng=4.50)
    m_nogps = _mk_melding(admin_user, title="geen coordinaten")
    m_far = _mk_melding(admin_user, title="ver buiten bereik", lat=53.0, lng=5.0)
    return dict(wid=wid, pid=pid, m_line=m_line, m_point=m_point,
                m_nogps=m_nogps, m_far=m_far)


def test_backfill_dry_run_reports_without_mutating(client, admin_user):
    s = _seed_mixed(admin_user)
    r = client.post("/api/meldingen/backfill-asset-links?dry_run=true",
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["dry_run"] is True
    assert b["newly_linked"] == 2
    assert b["linked_via_wegvak_line"] == 1
    assert b["linked_via_point_asset"] == 1
    assert b["skipped_no_gps"] == 1
    assert b["skipped_no_asset_in_range"] == 1
    assert b["point_assets_available"] == 1
    assert b["line_assets_available"] == 1
    assert b["distinct_assets_touched"] == 2
    # Niets opgeslagen
    assert _asset_id_of(s["m_line"]) is None
    assert _asset_id_of(s["m_point"]) is None


def test_backfill_commit_then_idempotent(client, admin_user):
    s = _seed_mixed(admin_user)
    r = client.post("/api/meldingen/backfill-asset-links?dry_run=false",
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["newly_linked"] == 2
    assert _asset_id_of(s["m_line"]) == s["wid"]
    assert _asset_id_of(s["m_point"]) == s["pid"]

    # Tweede run koppelt niets nieuws meer
    r2 = client.post("/api/meldingen/backfill-asset-links?dry_run=false",
                     headers=auth(admin_user))
    b2 = r2.json()
    assert b2["newly_linked"] == 0
    assert b2["already_linked"] == 2


def test_backfill_requires_edit_rights(client, viewer_user):
    r = client.post("/api/meldingen/backfill-asset-links?dry_run=true",
                    headers=auth(viewer_user))
    assert r.status_code == 403
