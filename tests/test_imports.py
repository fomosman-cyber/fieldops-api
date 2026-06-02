"""Tests voor de GeoJSON-wegvak-importer (POST /api/imports/geojson)."""
import json

from database import SessionLocal
from models import Asset
from tests.conftest import auth


def _get_asset(org_id, **filt):
    """Haal één asset rechtstreeks uit de DB (de list-API exposeert niet alle
    geometrie-velden zoals is_segment/length_m)."""
    db = SessionLocal()
    try:
        q = db.query(Asset).filter(Asset.organization_id == org_id)
        for k, v in filt.items():
            q = q.filter(getattr(Asset, k) == v)
        return q.first()
    finally:
        db.close()


def _fc(features):
    return json.dumps({"type": "FeatureCollection", "features": features}).encode("utf-8")


def _feat(geom_type, coords, props=None):
    return {
        "type": "Feature",
        "properties": props or {},
        "geometry": {"type": geom_type, "coordinates": coords},
    }


def _post(client, user, features, **form):
    data = {k: str(v) for k, v in form.items()}
    return client.post(
        "/api/imports/geojson",
        files={"file": ("wegvakken.geojson", _fc(features), "application/json")},
        data=data,
        headers=auth(user),
    )


# Twee WGS84-punten ~110 m uit elkaar in Naaldwijk (lng, lat).
_LINE = [[4.2070, 51.9940], [4.2080, 51.9945]]


def test_linestring_creates_wegvak(client, admin_user):
    r = _post(client, admin_user, [
        _feat("LineString", _LINE, {"straatnaam": "Dijkweg", "verharding": "asfalt"}),
    ])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 1
    assert body["by_type"] == {"wegvak_asfalt": 1}
    prev = body["preview"][0]
    assert prev["geom"] == "LineString" and prev["length_m"] > 50

    a = _get_asset(admin_user.organization_id, asset_type="wegvak_asfalt")
    assert a is not None
    assert a.is_segment is True
    assert a.length_m and a.length_m > 50
    assert a.geometry_geojson and "LineString" in a.geometry_geojson
    assert a.name == "Dijkweg"
    assert json.loads(a.properties_json)["bbox"] == [4.207, 51.994, 4.208, 51.9945]


def test_classification_variants(client, admin_user):
    feats = [
        _feat("LineString", _LINE, {"functie": "fietspad"}),
        _feat("LineString", _LINE, {"verharding": "gebakken klinkers"}),
        _feat("LineString", _LINE, {"asset_type": "trottoir"}),   # expliciete IMBOR-code
        _feat("LineString", _LINE, {"omschrijving": "geen type-hint hier"}),  # fallback
    ]
    r = _post(client, admin_user, feats)
    assert r.status_code == 200, r.text
    by_type = r.json()["by_type"]
    assert by_type.get("fietspad") == 1
    assert by_type.get("wegvak_elementen") == 1
    assert by_type.get("trottoir") == 1
    assert by_type.get("wegvak_asfalt") == 1  # fallback voor lijn zonder hint


def test_point_asset(client, admin_user):
    r = _post(client, admin_user, [
        _feat("Point", [4.2075, 51.9942], {"code": "LM-99", "objecttype": "lichtmast"}),
    ])
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1
    lm = _get_asset(admin_user.organization_id, code="LM-99")
    assert lm is not None
    assert lm.is_segment is False
    assert lm.length_m is None
    assert abs(lm.lat - 51.9942) < 1e-6


def test_rd_coordinates_converted(client, admin_user):
    # RD (EPSG:28992) nabij Naaldwijk -> moet naar WGS84 (lat ~52, lng ~4) gaan.
    rd_line = [[74000.0, 444000.0], [74100.0, 444050.0]]
    r = _post(client, admin_user, [_feat("LineString", rd_line, {"verharding": "asfalt"})])
    assert r.status_code == 200, r.text
    assert r.json()["preview"][0]["rd_geconverteerd"] is True
    assets = client.get("/api/assets/", headers=auth(admin_user)).json()
    a = next(x for x in assets if x["asset_type"] == "wegvak_asfalt")
    assert 50.0 < a["lat"] < 54.0
    assert 3.0 < a["lng"] < 7.0


def test_dry_run_persists_nothing(client, admin_user):
    feats = [_feat("LineString", _LINE, {"verharding": "asfalt"})]
    r = _post(client, admin_user, feats, dry_run=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["would_create"] == 1
    assert len(body["preview"]) == 1
    # Niets opgeslagen
    assets = client.get("/api/assets/", headers=auth(admin_user)).json()
    assert assets == []


def test_dedup_skips_existing_codes(client, admin_user):
    feats = [_feat("LineString", _LINE, {"code": "WV-DUP", "verharding": "asfalt"})]
    assert _post(client, admin_user, feats).json()["created"] == 1
    second = _post(client, admin_user, feats).json()
    assert second["created"] == 0
    assert second["skipped"] == 1


def test_viewer_forbidden(client, viewer_user):
    r = _post(client, viewer_user, [_feat("LineString", _LINE)])
    assert r.status_code == 403


def test_invalid_geojson(client, admin_user):
    r = client.post(
        "/api/imports/geojson",
        files={"file": ("bad.geojson", b"{not json", "application/json")},
        headers=auth(admin_user),
    )
    assert r.status_code == 400


def test_not_a_featurecollection(client, admin_user):
    bad = json.dumps({"type": "Feature", "geometry": {}}).encode("utf-8")
    r = client.post(
        "/api/imports/geojson",
        files={"file": ("bad.geojson", bad, "application/json")},
        headers=auth(admin_user),
    )
    assert r.status_code == 400
