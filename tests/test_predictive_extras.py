"""Tests voor predictive v2.1 uitbreidingen:

- Trend-detectie (melding-frequentie 90d vs voorgaand 90d)
- Confidence (data-completeness)
- Geo-cluster signal op asset-niveau
- /api/predictive/clusters endpoint (wijk-alerts)
"""

from datetime import datetime, timezone, timedelta
from database import SessionLocal
from models import Asset, Melding
from predictive import compute_asset_risk, find_geo_clusters, SCORE_VERSION
from tests.conftest import auth


def _mk_asset(org, admin_user, *, code="A", lat=None, lng=None,
              installed_years_ago=None, lifespan=None, condition=None):
    db = SessionLocal()
    try:
        a = Asset(
            code=code, asset_type="put",
            organization_id=org.id, created_by=admin_user.id,
            lat=lat, lng=lng,
        )
        if installed_years_ago is not None:
            a.installed_at = datetime.now(timezone.utc) - timedelta(days=int(365 * installed_years_ago))
        if lifespan is not None:
            a.expected_lifespan_years = lifespan
        if condition is not None:
            a.condition_score = condition
        db.add(a); db.commit(); db.refresh(a)
        return a
    finally:
        db.close()


def _mk_melding(org, admin_user, *, asset_id=None, lat=None, lng=None,
                days_ago=0, klasse=None):
    db = SessionLocal()
    try:
        m = Melding(
            title="t", organization_id=org.id, created_by=admin_user.id,
            asset_id=asset_id, lat=lat, lng=lng, crow_klasse=klasse,
        )
        m.created_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
        db.add(m); db.commit(); db.refresh(m)
        return m
    finally:
        db.close()


# ─── Score-versie ─────────────────────────────────────────────────────────────

def test_score_version_is_v2_1(org, admin_user):
    a = _mk_asset(org, admin_user)
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    assert r["score_version"] == "v2.1-trend"
    assert SCORE_VERSION == "v2.1-trend"


# ─── Trend-detectie ───────────────────────────────────────────────────────────

def test_trend_zero_when_no_meldingen(org, admin_user):
    a = _mk_asset(org, admin_user)
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    assert r["trend"]["recent_90d"] == 0
    assert r["trend"]["prior_90d"] == 0
    assert r["trend"]["points"] == 0
    assert r["components"]["trend"] == 0


def test_trend_zero_when_stable(org, admin_user):
    """1 melding in elk venster = stabiel, geen trend-bonus."""
    a = _mk_asset(org, admin_user)
    _mk_melding(org, admin_user, asset_id=a.id, days_ago=30)
    _mk_melding(org, admin_user, asset_id=a.id, days_ago=120)
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    assert r["trend"]["recent_90d"] == 1
    assert r["trend"]["prior_90d"] == 1
    assert r["trend"]["points"] == 0


def test_trend_bonus_for_clear_increase(org, admin_user):
    """1 → 4 meldingen = ratio 4x = max trend-bonus."""
    a = _mk_asset(org, admin_user)
    # Prior window (90-180d ago): 1 melding
    _mk_melding(org, admin_user, asset_id=a.id, days_ago=120)
    # Recent window (0-90d): 4 meldingen
    for d in (10, 30, 50, 70):
        _mk_melding(org, admin_user, asset_id=a.id, days_ago=d)
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    assert r["trend"]["recent_90d"] == 4
    assert r["trend"]["prior_90d"] == 1
    assert r["trend"]["points"] >= 7


def test_trend_burst_from_zero(org, admin_user):
    """0 → 3 meldingen (burst uit het niets) geeft ook bonus."""
    a = _mk_asset(org, admin_user)
    for d in (5, 20, 60):
        _mk_melding(org, admin_user, asset_id=a.id, days_ago=d)
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    assert r["trend"]["points"] > 0


def test_trend_rationale_mentions_the_jump(org, admin_user):
    a = _mk_asset(org, admin_user)
    _mk_melding(org, admin_user, asset_id=a.id, days_ago=120)
    for d in (10, 30, 50):
        _mk_melding(org, admin_user, asset_id=a.id, days_ago=d)
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    text = " ".join(r["rationale"]).lower()
    assert "trend" in text


# ─── Confidence ───────────────────────────────────────────────────────────────

def test_confidence_zero_when_no_data(org, admin_user):
    a = _mk_asset(org, admin_user)
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    assert r["confidence"] == 0.0


def test_confidence_partial_when_some_data(org, admin_user):
    a = _mk_asset(org, admin_user, installed_years_ago=5, lifespan=20)
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    # 1 van 4 (alleen age) = 0.25
    assert r["confidence"] == 0.25


def test_confidence_full_with_all_signals(org, admin_user):
    a = _mk_asset(org, admin_user, installed_years_ago=10, lifespan=20, condition=3)
    _mk_melding(org, admin_user, asset_id=a.id, days_ago=30, klasse="M2")
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    # 4 van 4: age, condition, meldingen, crow
    assert r["confidence"] == 1.0


# ─── Geo-cluster signal op asset ──────────────────────────────────────────────

def test_no_geo_signal_without_coordinates(org, admin_user):
    a = _mk_asset(org, admin_user)  # geen lat/lng
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    assert r["geo_cluster"] is None


def test_geo_signal_with_nearby_meldingen(org, admin_user):
    # Asset op (52.0, 5.0). Andere asset met meldingen ~50m verderop.
    a = _mk_asset(org, admin_user, code="A", lat=52.0, lng=5.0)
    other = _mk_asset(org, admin_user, code="B", lat=52.0005, lng=5.0)  # ~55m
    for d in (5, 10, 15):
        _mk_melding(org, admin_user, asset_id=other.id, lat=52.0005, lng=5.0,
                    days_ago=d, klasse="M2")

    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    sig = r["geo_cluster"]
    assert sig is not None
    assert sig["nearby_count"] == 3
    assert sig["radius_m"] == 200
    assert sig["hottest_klasse"] == "M2"


def test_geo_signal_excludes_assets_own_meldingen(org, admin_user):
    a = _mk_asset(org, admin_user, code="A", lat=52.0, lng=5.0)
    # Eigen meldingen — moeten NIET in de geo-cluster signal komen
    for d in (5, 10):
        _mk_melding(org, admin_user, asset_id=a.id, lat=52.0, lng=5.0, days_ago=d)
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    assert r["geo_cluster"] is None


def test_geo_signal_excludes_far_meldingen(org, admin_user):
    """Meldingen >200m weg tellen niet."""
    a = _mk_asset(org, admin_user, code="A", lat=52.0, lng=5.0)
    other = _mk_asset(org, admin_user, code="B", lat=52.01, lng=5.0)  # ~1100m
    for d in (5, 10, 15):
        _mk_melding(org, admin_user, asset_id=other.id, lat=52.01, lng=5.0, days_ago=d)
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    assert r["geo_cluster"] is None


# ─── /api/predictive/clusters endpoint ────────────────────────────────────────

def test_clusters_endpoint_empty_response(client, admin_user):
    r = client.get("/api/predictive/clusters", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json() == []


def test_clusters_endpoint_finds_dense_group(client, admin_user, org):
    # 3 meldingen binnen ~50m van elkaar → cluster van 3
    base_lat, base_lng = 52.10, 5.20
    for i in range(3):
        _mk_melding(org, admin_user, lat=base_lat + i * 0.0001, lng=base_lng,
                    days_ago=5, klasse="M2")

    r = client.get("/api/predictive/clusters", headers=auth(admin_user))
    assert r.status_code == 200
    clusters_ = r.json()
    assert len(clusters_) == 1
    c = clusters_[0]
    assert c["count"] == 3
    assert c["hottest_klasse"] == "M2"
    assert c["severity"] in ("laag", "matig", "hoog")
    assert "center_lat" in c and "center_lng" in c


def test_clusters_endpoint_respects_min_count(client, admin_user, org):
    # Slechts 2 meldingen — onder default min_count=3, dus geen cluster
    for i in range(2):
        _mk_melding(org, admin_user, lat=52.0 + i * 0.0001, lng=5.0, days_ago=3)

    r = client.get("/api/predictive/clusters", headers=auth(admin_user))
    assert r.json() == []

    # Met min_count=2 → wel cluster
    r2 = client.get("/api/predictive/clusters?min_count=2", headers=auth(admin_user))
    assert len(r2.json()) == 1


def test_clusters_endpoint_respects_window(client, admin_user, org):
    # Meldingen 60d geleden — buiten default 30d-window
    for i in range(3):
        _mk_melding(org, admin_user, lat=52.0 + i * 0.0001, lng=5.0, days_ago=60)

    r = client.get("/api/predictive/clusters", headers=auth(admin_user))
    assert r.json() == []

    # Window naar 90d → wel cluster
    r2 = client.get("/api/predictive/clusters?window_days=90", headers=auth(admin_user))
    assert len(r2.json()) == 1


def test_clusters_endpoint_org_isolation(client, admin_user, org, platform_owner):
    """Meldingen in andere org tellen niet mee."""
    from models import Organization
    # Cluster in admin's eigen org
    for i in range(3):
        _mk_melding(org, admin_user, lat=52.0 + i * 0.0001, lng=5.0, days_ago=3)
    # Cluster in andere org (platform_owner) op zelfde locatie — refetch org
    db = SessionLocal()
    try:
        other_org = db.query(Organization).filter(
            Organization.id == platform_owner.organization_id
        ).first()
    finally:
        db.close()
    for i in range(3):
        _mk_melding(other_org, platform_owner,
                    lat=52.0 + i * 0.0001, lng=5.0, days_ago=3)

    r = client.get("/api/predictive/clusters", headers=auth(admin_user))
    assert len(r.json()) == 1  # alleen eigen org


def test_clusters_endpoint_param_validation(client, admin_user):
    r = client.get("/api/predictive/clusters?radius_m=10", headers=auth(admin_user))
    assert r.status_code == 422  # onder ge=50
    r = client.get("/api/predictive/clusters?window_days=5", headers=auth(admin_user))
    assert r.status_code == 422  # onder ge=7


def test_clusters_severity_high_for_E_klasse(client, admin_user, org):
    base_lat, base_lng = 52.0, 5.0
    for i in range(3):
        _mk_melding(org, admin_user, lat=base_lat + i * 0.0001, lng=base_lng,
                    days_ago=5, klasse="E3")
    r = client.get("/api/predictive/clusters", headers=auth(admin_user))
    c = r.json()[0]
    assert c["severity"] == "hoog"
    assert c["hottest_klasse"] == "E3"
