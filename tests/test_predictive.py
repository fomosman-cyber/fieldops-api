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
    # v2.0-crow: 90% leeftijd + conditie 5 zonder CROW-meldingen = matig band
    # (CROW-factor = 0 zonder geclassificeerde meldingen — door ontwerp)
    assert r["score"] >= 35
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
    # v2.0-crow: zonder CROW-meldingen kan score max 70 (zonder W_CROW factor 30).
    # Threshold 40 vangt nog steeds een 25-jaar oude asset met conditie 5 (~46pt).
    r = client.get("/api/predictive/at-risk?min_score=40", headers=auth(admin_user))
    items = r.json()
    codes = [i["asset_code"] for i in items]
    assert "HIGH" in codes
    assert "LOW" not in codes


def test_asset_risk_404(client, admin_user):
    r = client.get("/api/predictive/asset/does-not-exist", headers=auth(admin_user))
    assert r.status_code == 404


def test_crow_klasse_drives_risk_score(org, admin_user):
    """CROW E3 op een melding moet score significant verhogen via W_CROW (30 pt)."""
    from models import Melding
    from datetime import datetime, timezone

    a = _make_asset_in_db(org, admin_user, code="CROW-E3", installed_years_ago=5,
                          lifespan=20, condition=2)
    db = SessionLocal()
    try:
        # Baseline zonder CROW-meldingen
        baseline = compute_asset_risk(db, a)
        # Voeg melding toe met E3-classificatie
        m = Melding(
            title="E3 test", organization_id=org.id, created_by=admin_user.id,
            asset_id=a.id, priority="kritiek",
            crow_klasse="E3", crow_ernst="E", crow_omvang="3",
            crow_schadebeeld="kuilen",
            onderhoud_categorie="acuut",
        )
        db.add(m); db.commit()
        with_crow = compute_asset_risk(db, a)
    finally:
        db.close()
    # E3 voegt ~30 punten toe via W_CROW
    assert with_crow["score"] > baseline["score"] + 20
    assert with_crow["worst_crow_klasse"] == "E3"
    assert with_crow["components"]["crow"] >= 25  # bijna max W_CROW (30)


def test_crow_klasse_lookup_KO_GO():
    """Sanity check op crow_kosten lookup-module."""
    from crow_kosten import (
        lookup_maatregel, klasse_to_categorie, klasse_to_termijn,
        klasse_to_risk_points,
    )
    # M2 → KO
    assert klasse_to_categorie("M2") == "KO"
    advies = lookup_maatregel("scheurvorming-langs", "M2", "samenhang")
    assert advies["categorie"] == "KO"
    assert "polymeer" in advies["maatregel"].lower() or "Vullen" in advies["maatregel"]
    assert advies["termijn_weken"] == 24
    # E3 → acuut
    assert klasse_to_categorie("E3") == "acuut"
    # L1 → observatie
    assert klasse_to_categorie("L1") == "observatie"
    # Risk points moeten oplopen met severity
    assert klasse_to_risk_points("L1") < klasse_to_risk_points("M2")
    assert klasse_to_risk_points("M2") < klasse_to_risk_points("E3")
