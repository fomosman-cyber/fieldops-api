"""Predictive risk_score formule + endpoints."""

from datetime import datetime, timezone, timedelta
from database import SessionLocal
from models import Asset
from predictive import compute_asset_risk
from tests.conftest import auth


def _make_asset_in_db(org, admin_user, *, code="X", installed_years_ago=None,
                      lifespan=None, condition=None):
    """Maak asset binnen org. `admin_user` levert created_by zonder lazy-load."""
    db = SessionLocal()
    try:
        a = Asset(
            code=code, asset_type="put",
            organization_id=org.id, created_by=admin_user.id,
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


def test_risk_zero_for_brand_new_asset(org, admin_user):
    a = _make_asset_in_db(org, admin_user, installed_years_ago=0, lifespan=20, condition=1)
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    assert r["score"] < 20
    assert r["band"] == "laag"


def test_risk_high_for_old_bad_condition(org, admin_user):
    a = _make_asset_in_db(org, admin_user, installed_years_ago=18, lifespan=20, condition=5)
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    # 90% leeftijd + conditie 5 = duidelijk in matig-of-hoog band
    assert r["score"] >= 50
    assert r["band"] in ("matig", "hoog")


def test_risk_with_no_data_low_score(org, admin_user):
    a = _make_asset_in_db(org, admin_user, code="NODATA")  # geen leeftijd, geen conditie
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    assert r["score"] == 0
    assert r["band"] == "laag"


def test_rationale_explains_components(org, admin_user):
    a = _make_asset_in_db(org, admin_user, installed_years_ago=15, lifespan=20, condition=4)
    db = SessionLocal()
    try:
        r = compute_asset_risk(db, a)
    finally:
        db.close()
    text = " ".join(r["rationale"]).lower()
    assert "levensduur" in text or "75%" in text
    assert "conditiescore" in text


# ─── Endpoints ───────────────────────────────────────────────────────────

def test_summary_endpoint_empty(client, admin_user):
    r = client.get("/api/predictive/summary", headers=auth(admin_user))
    assert r.status_code == 200
    s = r.json()
    assert s["total_assets"] == 0
    assert s["bands"] == {"laag": 0, "matig": 0, "hoog": 0}


def test_summary_with_assets(client, admin_user, org):
    _make_asset_in_db(org, admin_user, code="LO", installed_years_ago=1, lifespan=30, condition=1)
    _make_asset_in_db(org, admin_user, code="HI", installed_years_ago=25, lifespan=20, condition=5)
    r = client.get("/api/predictive/summary", headers=auth(admin_user))
    s = r.json()
    assert s["total_assets"] == 2
    # Beide assets hebben minstens "matig" of hoger door slechte conditie/leeftijd
    assert s["bands"]["matig"] + s["bands"]["hoog"] >= 1


def test_at_risk_endpoint_threshold(client, admin_user, org):
    _make_asset_in_db(org, admin_user, code="LOW", installed_years_ago=0, lifespan=30, condition=1)
    _make_asset_in_db(org, admin_user, code="HIGH", installed_years_ago=25, lifespan=20, condition=5)
    r = client.get("/api/predictive/at-risk?min_score=50", headers=auth(admin_user))
    items = r.json()
    codes = [i["asset_code"] for i in items]
    assert "HIGH" in codes
    assert "LOW" not in codes


def test_asset_risk_404(client, admin_user):
    r = client.get("/api/predictive/asset/does-not-exist", headers=auth(admin_user))
    assert r.status_code == 404
