"""Correctheid van de assets-import (CSV + GeoJSON) — bug-hunt 2026-06-25.

Dekt: RD→WGS84 teken-fix, afronden i.p.v. afkappen, NEN-conditie 6→5,
en robuuste GeoJSON-coördinaat-parsing (geen batch-brede 500).
"""

import io
import json
import math

from tests.conftest import auth
from database import SessionLocal
from models import Asset
from routers.assets_router import _rd_to_wgs84, _parse_int_in_range


def _haversine_m(la1, lo1, la2, lo2):
    R = 6_371_000.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp = math.radians(la2 - la1); dl = math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _asset(org_id, code):
    db = SessionLocal()
    try:
        return db.query(Asset).filter(Asset.organization_id == org_id, Asset.code == code).first()
    finally:
        db.close()


# ── RD→WGS84 (Schreutelkamp) — teken-fix op de (3,0)-coëfficiënt ──────────

def test_rd_origin_amersfoort_exact():
    lat, lng = _rd_to_wgs84(155000, 463000)
    assert _haversine_m(lat, lng, 52.155172, 5.387206) < 1.0


def test_rd_groningen_within_2m():
    # Martinitoren — ver oostelijk (dx groot) → vangt de dx³ teken-fout.
    # Vóór de fix: ~14,3 m afwijking; na de fix: ~1,2 m.
    lat, lng = _rd_to_wgs84(233883, 582065)
    assert _haversine_m(lat, lng, 53.219391, 6.568208) < 2.0


# ── _parse_int_in_range: afronden i.p.v. afkappen ────────────────────────

def test_parse_int_rounds_decimal_comma():
    assert _parse_int_in_range("3,8", 1, 5) == (4, None)      # was 3 (afkap)
    assert _parse_int_in_range("39,4", 1, 200) == (39, None)


# ── NEN 2767 conditie 6 → 5 (niet stil droppen) ──────────────────────────

def test_csv_condition_6_mapped_to_5(client, admin_user):
    csv = "code,asset_type,conditie\nP-6,put,6\n"
    r = client.post(
        "/api/assets/import/csv",
        files={"file": ("a.csv", io.BytesIO(csv.encode("utf-8")), "text/csv")},
        headers=auth(admin_user),
    )
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1
    a = _asset(admin_user.organization_id, "P-6")
    assert a is not None
    assert a.condition_score == 5      # niet None (stil gedropt), niet 6


# ── GeoJSON-import: niet-numerieke / NaN-coords killen de batch niet ──────

def test_geojson_bad_coords_no_500(client, admin_user):
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"code": "G-OK", "asset_type": "put"},
         "geometry": {"type": "Point", "coordinates": [5.0, 52.0]}},
        {"type": "Feature", "properties": {"code": "G-COMMA", "asset_type": "put"},
         "geometry": {"type": "Point", "coordinates": ["4,21", "51,99"]}},   # NL-komma
        {"type": "Feature", "properties": {"code": "G-NAN", "asset_type": "put"},
         "geometry": {"type": "Point", "coordinates": ["NaN", "NaN"]}},
    ]}
    r = client.post(
        "/api/assets/import/geojson",
        files={"file": ("a.geojson", json.dumps(fc).encode("utf-8"), "application/json")},
        headers=auth(admin_user),
    )
    assert r.status_code == 200, r.text     # vóór de fix: 500 (ongevangen ValueError)

    ok = _asset(admin_user.organization_id, "G-OK")
    assert ok is not None and abs(ok.lat - 52.0) < 1e-6

    comma = _asset(admin_user.organization_id, "G-COMMA")
    assert comma is not None and abs(comma.lat - 51.99) < 1e-6   # komma-coords nu geparset

    nan = _asset(admin_user.organization_id, "G-NAN")
    assert nan is not None and nan.lat is None                    # NaN geweigerd, geen corruptie
    assert any("NaN" in (e.get("error") or "") or "eindige" in (e.get("error") or "")
               for e in r.json().get("errors", []))
