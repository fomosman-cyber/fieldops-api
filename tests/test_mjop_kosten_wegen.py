"""Tests: MJOP-kostentabel dekt alle wegen-/riolering-asset-types uit de seed.

Regressie voor de bug waarbij assets met type wegvak_asfalt, wegvak_elementen,
fietspad, trottoir, put, kolk en drainage GELUIDLOOS uit /api/mjop/preview,
/summary en /export vielen omdat mjop_kosten.KOSTEN die keys niet kende.
"""
from datetime import datetime, timedelta

from database import SessionLocal
from models import Asset
from tests.conftest import auth
import mjop_kosten as mjop


# Alle types die seed_demo_org.py aanmaakt en die vóór deze fix ontbraken.
NIEUWE_TYPES = ["wegvak_asfalt", "wegvak_elementen", "fietspad",
                "trottoir", "put", "kolk", "drainage"]

# Bestaande types die moeten blijven werken zoals ze waren.
BESTAANDE_TYPES = ["wegvak", "brug", "viaduct", "riolering", "boom",
                   "verlichting", "speeltoestel", "duiker", "gemaal",
                   "kademuur", "sluis", "lantaarnpaal"]


def _make_asset(db, *, user, code, asset_type, score=4, length_m=None,
                next_due=None):
    a = Asset(
        code=code, asset_type=asset_type,
        organization_id=user.organization_id, created_by=user.id,
        condition_score=score, length_m=length_m,
        next_inspection_due=next_due,
        last_inspection_at=datetime.utcnow() - timedelta(days=180),
    )
    db.add(a); db.commit(); db.refresh(a)
    return a


# ─────────────────────────────────────────────────────────────────────────────
# Unit: kosten-katalogus
# ─────────────────────────────────────────────────────────────────────────────

def test_nieuwe_types_hebben_maatregel_voor_alle_scores():
    """Elk nieuw type levert een maatregel voor score 1 t/m 6 (geen gaten)."""
    for atype in NIEUWE_TYPES:
        for score in range(1, 7):
            m = mjop.get_maatregel(atype, score)
            assert m is not None, f"{atype} score {score} → None"
            assert m["min_eur"] > 0
            assert m["max_eur"] >= m["min_eur"]
            assert m["maatregel"]


def test_wegvak_asfalt_alias_van_wegvak():
    """wegvak_asfalt IS asfalt — zelfde kosten als de bestaande wegvak-entry."""
    for score in range(1, 7):
        a = mjop.get_maatregel("wegvak_asfalt", score)
        w = mjop.get_maatregel("wegvak", score)
        for veld in ("maatregel", "min_eur", "max_eur", "unit", "score"):
            assert a[veld] == w[veld], f"score {score} veld {veld}"


def test_elementenverharding_vs_asfalt_kostenrelatie():
    """CROW-vuistregel: herstraten (score 3-4) per m2 duurder dan
    asfalt-slijtlaag/deklaag, maar volledige vervanging (score 6) goedkoper."""
    elem_3 = mjop.get_maatregel("wegvak_elementen", 3)
    asfalt_3 = mjop.get_maatregel("wegvak", 3)
    assert elem_3["min_eur"] > asfalt_3["min_eur"]

    elem_6 = mjop.get_maatregel("wegvak_elementen", 6)
    asfalt_6 = mjop.get_maatregel("wegvak", 6)
    assert elem_6["max_eur"] < asfalt_6["max_eur"]


def test_put_kolk_stuksprijzen():
    """Putten/kolken zijn stuksprijzen (honderden tot lage duizenden euro's)."""
    put_4 = mjop.get_maatregel("put", 4)
    assert put_4["unit"] == "per put"
    assert 100 <= put_4["min_eur"] <= 5000

    kolk_5 = mjop.get_maatregel("kolk", 5)
    assert kolk_5["unit"] == "per kolk"
    assert 100 <= kolk_5["min_eur"] <= 5000


def test_verharding_units_per_m2():
    for atype in ("wegvak_elementen", "fietspad", "trottoir"):
        assert mjop.get_maatregel(atype, 4)["unit"] == "per m2", atype


def test_imbor_aliassen_resolven():
    """IMBOR-taxonomy-codes (import) moeten ook een maatregel opleveren."""
    for alias in ("put_riool", "riool_vrijverval", "gemaal_riool",
                  "brug_vast", "brug_beweegbaar", "lichtmast",
                  "boom_laan", "elementenverharding", "voetpad"):
        assert mjop.get_maatregel(alias, 4) is not None, f"alias {alias}"


def test_bestaande_types_onveranderd():
    """Regressie: bestaande entries mogen niet wijzigen door de nieuwe."""
    assert mjop.get_maatregel("brug", 4)["min_eur"] == 50000
    assert mjop.get_maatregel("brug", 4)["max_eur"] == 150000
    assert mjop.get_maatregel("wegvak", 4)["min_eur"] == 40
    assert mjop.get_maatregel("wegvak", 4)["max_eur"] == 80
    assert mjop.get_maatregel("riolering", 4)["min_eur"] == 200
    assert mjop.get_maatregel("boom", 5)["maatregel"] == "Kappen + herplanting"
    for atype in BESTAANDE_TYPES:
        assert mjop.get_maatregel(atype, 4) is not None, atype


# ─────────────────────────────────────────────────────────────────────────────
# E2E: elk nieuw type met score 4 → MJOP-regel in preview + summary + CSV
# ─────────────────────────────────────────────────────────────────────────────

def test_mjop_preview_bevat_alle_nieuwe_types(client, admin_user):
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        for i, atype in enumerate(NIEUWE_TYPES):
            _make_asset(db, user=admin_user, code=f"MJ-NEW-{atype.upper()}",
                        asset_type=atype, score=4, length_m=100,
                        next_due=now + timedelta(days=30 + i))
        # Referentie-asset van bestaand type moet er óók in blijven staan
        _make_asset(db, user=admin_user, code="MJ-REF-WEGVAK",
                    asset_type="wegvak", score=4, length_m=100,
                    next_due=now + timedelta(days=30))
    finally:
        db.close()

    r = client.get("/api/mjop/preview?years=10", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    codes = {x["asset_code"] for x in items}

    assert "MJ-REF-WEGVAK" in codes  # bestaand type blijft werken
    for atype in NIEUWE_TYPES:
        code = f"MJ-NEW-{atype.upper()}"
        assert code in codes, f"{atype} valt uit MJOP-preview"
        row = next(x for x in items if x["asset_code"] == code)
        assert row["min_total"] > 0, f"{atype}: min_total 0"
        assert row["max_total"] >= row["min_total"]
        assert row["maatregel"]


def test_mjop_summary_telt_nieuwe_types_mee(client, admin_user):
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        for atype in ("put", "kolk", "trottoir"):
            _make_asset(db, user=admin_user, code=f"MJ-SUM-{atype.upper()}",
                        asset_type=atype, score=4, length_m=50,
                        next_due=now + timedelta(days=60))
    finally:
        db.close()

    r = client.get("/api/mjop/summary?years=10", headers=auth(admin_user))
    assert r.status_code == 200
    body = r.json()
    by_type = {t["asset_type"] for t in body["by_type"]}
    for atype in ("put", "kolk", "trottoir"):
        assert atype in by_type, f"{atype} ontbreekt in summary by_type"
    assert body["grand_total"]["min"] > 0


def test_mjop_csv_export_bevat_nieuw_type(client, admin_user):
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        _make_asset(db, user=admin_user, code="MJ-CSV-FIETS-1",
                    asset_type="fietspad", score=4, length_m=250,
                    next_due=now + timedelta(days=90))
    finally:
        db.close()

    r = client.get("/api/mjop/export.csv?years=10", headers=auth(admin_user))
    assert r.status_code == 200
    assert "MJ-CSV-FIETS-1" in r.text
    assert "fietspad" in r.text
