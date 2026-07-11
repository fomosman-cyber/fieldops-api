"""Tests voor de GeoJSON- en shapefile-importer (POST /api/imports/*)."""
import io
import json
import zipfile

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


# ── Shapefile-importer (POST /api/imports/shapefile) ──────────────────────

def _make_shp_zip(records, fields, geom="line"):
    """Bouw in-memory een shapefile-zip. records = [(coords, (waarde,...)), ...]."""
    import shapefile
    shp_b, shx_b, dbf_b = io.BytesIO(), io.BytesIO(), io.BytesIO()
    st = shapefile.POLYLINE if geom == "line" else shapefile.POINT
    w = shapefile.Writer(shp=shp_b, shx=shx_b, dbf=dbf_b, shapeType=st)
    for fname, ftype, fsize in fields:
        w.field(fname, ftype, fsize)
    for coords, rec in records:
        if geom == "line":
            w.line([coords])
        else:
            w.point(coords[0], coords[1])
        w.record(*rec)
    w.close()
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w") as zf:
        zf.writestr("data.shp", shp_b.getvalue())
        zf.writestr("data.shx", shx_b.getvalue())
        zf.writestr("data.dbf", dbf_b.getvalue())
    return zb.getvalue()


def _post_shp(client, user, zip_bytes, **form):
    return client.post(
        "/api/imports/shapefile",
        files={"file": ("wegen.zip", zip_bytes, "application/zip")},
        data={k: str(v) for k, v in form.items()},
        headers=auth(user),
    )


_SHP_FIELDS = [("straatnaam", "C", 50), ("functie", "C", 40)]


def test_shapefile_polyline_import(client, admin_user):
    zb = _make_shp_zip(
        [([[4.2070, 51.9940], [4.2080, 51.9945]], ("Dijkweg", "rijbaan asfalt")),
         ([[4.2090, 51.9950], [4.2100, 51.9955]], ("Vlietpad", "fietspad"))],
        fields=_SHP_FIELDS, geom="line",
    )
    r = _post_shp(client, admin_user, zb)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 2
    assert body["by_type"].get("wegvak_asfalt") == 1
    assert body["by_type"].get("fietspad") == 1

    a = _get_asset(admin_user.organization_id, asset_type="wegvak_asfalt")
    assert a.is_segment is True
    assert a.length_m and a.length_m > 50
    assert a.geometry_geojson and "LineString" in a.geometry_geojson
    assert a.name == "Dijkweg"


def test_shapefile_rd_converted(client, admin_user):
    zb = _make_shp_zip(
        [([[74000.0, 444000.0], [74100.0, 444050.0]], ("RD-weg", "asfalt"))],
        fields=_SHP_FIELDS, geom="line",
    )
    r = _post_shp(client, admin_user, zb)
    assert r.status_code == 200, r.text
    assert r.json()["preview"][0]["rd_geconverteerd"] is True
    a = _get_asset(admin_user.organization_id, asset_type="wegvak_asfalt")
    assert 50.0 < a.lat < 54.0 and 3.0 < a.lng < 7.0


def test_shapefile_point_import(client, admin_user):
    zb = _make_shp_zip(
        [([4.2075, 51.9942], ("LM-7", "lichtmast"))],
        fields=[("code", "C", 20), ("objecttype", "C", 30)], geom="point",
    )
    r = _post_shp(client, admin_user, zb)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1
    lm = _get_asset(admin_user.organization_id, code="LM-7")
    assert lm.is_segment is False
    assert lm.length_m is None


def test_shapefile_dry_run(client, admin_user):
    zb = _make_shp_zip(
        [([[4.2070, 51.9940], [4.2080, 51.9945]], ("Dijkweg", "asfalt"))],
        fields=_SHP_FIELDS, geom="line",
    )
    r = _post_shp(client, admin_user, zb, dry_run=True)
    assert r.status_code == 200, r.text
    assert r.json()["would_create"] == 1
    assert _get_asset(admin_user.organization_id, asset_type="wegvak_asfalt") is None


def test_shapefile_not_a_zip(client, admin_user):
    r = client.post(
        "/api/imports/shapefile",
        files={"file": ("x.zip", b"not a zip at all", "application/zip")},
        headers=auth(admin_user),
    )
    assert r.status_code == 400


def test_shapefile_viewer_forbidden(client, viewer_user):
    zb = _make_shp_zip([([[4.20, 51.99], [4.21, 51.99]], ("x", "asfalt"))], fields=_SHP_FIELDS)
    r = _post_shp(client, viewer_user, zb)
    assert r.status_code == 403


# ── Regressietests: import-robuustheid (bug-hunt 2026-06-25) ──────────────

def _single_point_shp(name, *, encoding=None, null=False):
    """Bouw een 1-punt shapefile-zip (data.*). null=True voegt een NULL-shape toe."""
    import shapefile
    shp_b, shx_b, dbf_b = io.BytesIO(), io.BytesIO(), io.BytesIO()
    kw = {"encoding": encoding} if encoding else {}
    w = shapefile.Writer(shp=shp_b, shx=shx_b, dbf=dbf_b, shapeType=shapefile.POINT, **kw)
    w.field("naam", "C", 50)
    w.point(4.21, 51.99); w.record(name)
    if null:
        w.null(); w.record("Leeg record")
    w.close()
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w") as zf:
        zf.writestr("data.shp", shp_b.getvalue())
        zf.writestr("data.shx", shx_b.getvalue())
        zf.writestr("data.dbf", dbf_b.getvalue())
    return zb.getvalue()


def test_shapefile_multiple_stems_rejected(client, admin_user):
    """ZIP met 2 shapefile-stems mag niet stil records droppen → 400."""
    import shapefile
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w") as zf:
        for stem in ("roads", "points"):
            sb, xb, db_ = io.BytesIO(), io.BytesIO(), io.BytesIO()
            w = shapefile.Writer(shp=sb, shx=xb, dbf=db_, shapeType=shapefile.POINT)
            w.field("naam", "C", 40); w.point(4.21, 51.99); w.record(stem); w.close()
            zf.writestr(f"{stem}.shp", sb.getvalue())
            zf.writestr(f"{stem}.shx", xb.getvalue())
            zf.writestr(f"{stem}.dbf", db_.getvalue())
    r = _post_shp(client, admin_user, zb.getvalue())
    assert r.status_code == 400
    assert "meerdere shapefiles" in r.json()["detail"].lower()


def test_shapefile_null_shape_does_not_kill_batch(client, admin_user):
    """Eén NULL-shape mag de geldige features niet meesleuren (geen batch-400)."""
    r = _post_shp(client, admin_user, _single_point_shp("Geldig punt", null=True))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_features"] == 2
    assert body["created"] == 1          # het geldige punt komt binnen
    assert len(body["errors"]) == 1      # de NULL-shape is een feature-fout, geen abort


def test_shapefile_cp1252_diacritics_imports(client, admin_user):
    """cp1252/latin1 .dbf met diacriet (NL-export) mag de import niet laten falen."""
    r = _post_shp(client, admin_user, _single_point_shp("Café Plein", encoding="cp1252"))
    assert r.status_code == 200, r.text   # vóór de fix: 400 (UnicodeDecodeError)
    assert r.json()["created"] == 1


def test_geojson_codeless_reimport_not_silently_skipped(client, admin_user):
    """Code-loze features bij her-import mogen niet stil worden geskipt (data loss)."""
    feats = [_feat("Point", [4.21, 51.99]), _feat("Point", [4.22, 51.98])]
    assert _post(client, admin_user, feats).json()["created"] == 2
    second = _post(client, admin_user, feats).json()
    assert second["created"] == 2        # vóór de fix: 0 (auto-codes botsten → skip)
    assert second["skipped"] == 0


def test_geojson_long_code_truncated_not_500(client, admin_user):
    """Te lange extern-id (>64) mag geen import-brede 500 op commit geven."""
    long_code = "X" * 70
    r = _post(client, admin_user, [_feat("Point", [4.21, 51.99], {"code": long_code})])
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1
    a = _get_asset(admin_user.organization_id, code=long_code[:64])
    assert a is not None and len(a.code) == 64
