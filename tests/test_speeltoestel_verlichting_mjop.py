"""Tests voor speeltoestel (NEN-EN 1176), verlichting (NEN 3140) en MJOP-export."""
import pytest
from datetime import datetime, timedelta, timezone

from database import SessionLocal
from models import Asset, Inspection
from tests.conftest import auth
import kunstwerken_taxonomy as kt
import mjop_kosten as mjop
import inspection_cycle as cycle


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_asset(db, *, user, code, asset_type, score=None, length_m=None,
                next_due=None):
    a = Asset(
        code=code, asset_type=asset_type,
        organization_id=user.organization_id, created_by=user.id,
        condition_score=score, length_m=length_m,
        next_inspection_due=next_due,
        last_inspection_at=datetime.utcnow() - timedelta(days=180) if score else None,
    )
    db.add(a); db.commit(); db.refresh(a)
    return a


# ─────────────────────────────────────────────────────────────────────────────
# Speeltoestel — taxonomy
# ─────────────────────────────────────────────────────────────────────────────

def test_speeltoestel_in_taxonomy():
    assert "speeltoestel" in kt.KUNSTWERK_TYPES
    assert "Speeltoestel" in kt.KUNSTWERK_TYPES["speeltoestel"]


def test_speeltoestel_aliases_normalize():
    for a in ("speeltoestellen", "schommel", "glijbaan", "klimrek", "speelplek"):
        assert kt.normalize_type(a) == "speeltoestel", f"alias {a}"


def test_speeltoestel_elementen_aanwezig():
    elems = kt.elementen_voor("speeltoestel")
    codes = {e["code"] for e in elems}
    expected = {"SPEEL.CONSTRUCTIE", "SPEEL.BEWEGENDE_DELEN",
                "SPEEL.KLIMDELEN", "SPEEL.VALDEMPING", "SPEEL.VEILIGHEID"}
    assert codes == expected


def test_speeltoestel_constructie_vragen():
    """Constructie krijgt mechanische stabiliteit + boutaantrekken-check."""
    qs = kt.vragen_voor("speeltoestel", "SPEEL.CONSTRUCTIE")
    codes = [q["code"] for q in qs]
    assert "SPEEL.STABIEL" in codes
    assert "SPEEL.BOUTAANTREKKEN" in codes
    # GEEN beton/wapening-vragen
    assert "CONSTR.WAPENING" not in codes


def test_speeltoestel_veiligheid_vragen_nen1176():
    qs = kt.vragen_voor("speeltoestel", "SPEEL.VEILIGHEID")
    codes = [q["code"] for q in qs]
    assert "SPEEL.KNELPUNT_GETEST" in codes
    assert "SPEEL.VALHOOGTE" in codes


def test_speeltoestel_valdemping_gebreken_nen1177():
    gebreken = kt.gebreken_voor("speeltoestel", "SPEEL.VALDEMPING")
    codes = {g["code"] for g in gebreken}
    assert "ondergrond_te_dun" in codes
    assert "te_klein_oppervlak" in codes


# ─────────────────────────────────────────────────────────────────────────────
# Verlichting NEN 3140 — taxonomy
# ─────────────────────────────────────────────────────────────────────────────

def test_verlichting_in_taxonomy():
    assert "verlichting" in kt.KUNSTWERK_TYPES


def test_verlichting_aliases_normalize():
    for a in ("lantaarn", "lantaarnpaal", "openbare-verlichting", "ov", "NEN3140"):
        assert kt.normalize_type(a) == "verlichting", f"alias {a}"


def test_verlichting_elementen():
    elems = kt.elementen_voor("verlichting")
    codes = {e["code"] for e in elems}
    expected = {"OV.MAST", "OV.ARMATUUR", "OV.AANSLUITKAST", "OV.AARDING"}
    assert codes == expected


def test_verlichting_aarding_vragen_nen3140():
    qs = kt.vragen_voor("verlichting", "OV.AARDING")
    codes = [q["code"] for q in qs]
    assert "OV.AARDING_OK" in codes
    assert "OV.ISOLATIE_OK" in codes
    assert "OV.AARDLEK_TEST" in codes


def test_verlichting_mast_gebreken():
    gebreken = kt.gebreken_voor("verlichting", "OV.MAST")
    codes = {g["code"] for g in gebreken}
    assert "corrosie_voet" in codes
    assert "aanrijdschade" in codes


# ─────────────────────────────────────────────────────────────────────────────
# Inspection-cycle voor speeltoestel + verlichting
# ─────────────────────────────────────────────────────────────────────────────

def test_cycle_speeltoestel_12_maanden_vast():
    """NEN-EN 1176 verplicht jaarlijks ongeacht score."""
    assert cycle.cycle_months_for("speeltoestel", None) == 12
    assert cycle.cycle_months_for("speeltoestel", 1) == 12
    assert cycle.cycle_months_for("speeltoestel", 5) == 12


def test_cycle_verlichting_60_maanden_nen3140():
    """NEN 3140 vereist 5-jaarlijkse keuring."""
    assert cycle.cycle_months_for("verlichting", None) == 60
    assert cycle.cycle_months_for("verlichting", 3) == 60
    assert cycle.cycle_months_for("lantaarnpaal", None) == 60


# ─────────────────────────────────────────────────────────────────────────────
# MJOP-export tests
# ─────────────────────────────────────────────────────────────────────────────

def test_mjop_kosten_brug_score_4():
    m = mjop.get_maatregel("brug", 4)
    assert m is not None
    assert m["min_eur"] == 50000
    assert m["max_eur"] == 150000
    assert "onderhoud" in m["maatregel"].lower()


def test_mjop_kosten_riolering_per_meter():
    m = mjop.get_maatregel("riolering", 4)
    assert m["unit"] == "per m"
    assert m["min_eur"] == 200


def test_mjop_kosten_verlichting_per_mast():
    m = mjop.get_maatregel("verlichting", 3)
    assert m["unit"] == "per mast"
    assert "LED" in m["maatregel"]


def test_mjop_kosten_speeltoestel_per_toestel():
    m = mjop.get_maatregel("speeltoestel", 5)
    assert m["unit"] == "per toestel"
    assert m["min_eur"] >= 2000  # vervanging is duur


def test_mjop_kosten_boom_per_boom():
    m = mjop.get_maatregel("boom", 5)
    assert m["unit"] == "per boom"


def test_mjop_estimate_total_with_multiplier():
    m = mjop.get_maatregel("riolering", 4)   # 200-500 per m
    t = mjop.estimate_total(m, 50)            # 50 m streng
    assert t["min_total"] == 10000
    assert t["max_total"] == 25000


def test_mjop_is_actionable():
    assert mjop.is_actionable(3) is True
    assert mjop.is_actionable(6) is True
    assert mjop.is_actionable(2) is False
    assert mjop.is_actionable(None) is False


def test_mjop_alias_resolution():
    """lantaarnpaal en wegdek zouden via alias moeten werken."""
    assert mjop.get_maatregel("lantaarnpaal", 4) is not None
    assert mjop.get_maatregel("wegdek", 4) is not None


# ─────────────────────────────────────────────────────────────────────────────
# MJOP-router endpoints
# ─────────────────────────────────────────────────────────────────────────────

def test_mjop_preview_endpoint(client, admin_user):
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        _make_asset(db, user=admin_user, code="MJ-BRUG-1",
                   asset_type="brug", score=4,
                   next_due=now + timedelta(days=60))
        _make_asset(db, user=admin_user, code="MJ-LANT-1",
                   asset_type="verlichting", score=3,
                   next_due=now + timedelta(days=365))
    finally:
        db.close()

    r = client.get("/api/mjop/preview?years=10", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 2
    codes = {x["asset_code"] for x in body["items"]}
    assert "MJ-BRUG-1" in codes
    assert "MJ-LANT-1" in codes


def test_mjop_preview_skips_low_scores(client, admin_user):
    """Score 1-2 worden default niet meegenomen (preventief budget elders)."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        _make_asset(db, user=admin_user, code="MJ-LOW-1",
                   asset_type="brug", score=2,
                   next_due=now + timedelta(days=60))
    finally:
        db.close()

    r = client.get("/api/mjop/preview?years=10", headers=auth(admin_user))
    codes = {x["asset_code"] for x in r.json()["items"]}
    assert "MJ-LOW-1" not in codes


def test_mjop_preview_include_score_2(client, admin_user):
    """Met include_score_2=True ook score-2 assets erbij."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        _make_asset(db, user=admin_user, code="MJ-PREV-1",
                   asset_type="brug", score=2,
                   next_due=now + timedelta(days=60))
    finally:
        db.close()

    r = client.get("/api/mjop/preview?years=10&include_score_2=true",
                   headers=auth(admin_user))
    codes = {x["asset_code"] for x in r.json()["items"]}
    assert "MJ-PREV-1" in codes


def test_mjop_summary_aggregates(client, admin_user):
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        _make_asset(db, user=admin_user, code="MJ-SUM-1",
                   asset_type="brug", score=5,
                   next_due=now + timedelta(days=30))
    finally:
        db.close()

    r = client.get("/api/mjop/summary?years=10", headers=auth(admin_user))
    assert r.status_code == 200
    body = r.json()
    assert body["total_assets"] >= 1
    assert body["grand_total"]["min"] > 0
    assert body["grand_total"]["max"] > body["grand_total"]["min"]


def test_mjop_csv_export(client, admin_user):
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        _make_asset(db, user=admin_user, code="MJ-CSV-1",
                   asset_type="brug", score=4,
                   next_due=now + timedelta(days=90))
    finally:
        db.close()

    r = client.get("/api/mjop/export.csv?years=10", headers=auth(admin_user))
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers.get("content-disposition", "")
    body = r.text
    assert "MJ-CSV-1" in body
    assert "brug" in body
    assert "Min € totaal" in body or "Min" in body  # header check


def test_mjop_horizon_filter(client, admin_user):
    """Asset met next_due > horizon mag niet in export."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        _make_asset(db, user=admin_user, code="MJ-FAR-1",
                   asset_type="brug", score=4,
                   next_due=now + timedelta(days=365 * 50))  # 50 jaar weg
    finally:
        db.close()

    r = client.get("/api/mjop/preview?years=5", headers=auth(admin_user))
    codes = {x["asset_code"] for x in r.json()["items"]}
    assert "MJ-FAR-1" not in codes
