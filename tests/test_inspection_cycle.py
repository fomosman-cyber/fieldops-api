"""Tests voor inspectie-cyclus automatisering.

Dekking:
  - Pure cycle-regels (cycle_months_for / next_due_date / compliance_status)
  - Asset-update hook bij sign_inspection
  - 5 cycle-router endpoints
  - Multi-tenant isolation
"""
from datetime import datetime, timedelta, timezone

from database import SessionLocal
from models import Asset, Inspection
from tests.conftest import auth
import inspection_cycle as cycle


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_asset(db, *, user, code="A-001", asset_type="brug", score=None,
                last_inspected=None, next_due=None, cycle_months=None):
    a = Asset(
        code=code, name=f"Test {asset_type}",
        asset_type=asset_type,
        organization_id=user.organization_id, created_by=user.id,
        condition_score=score,
        last_inspection_at=last_inspected,
        next_inspection_due=next_due,
        inspection_cycle_months=cycle_months,
    )
    db.add(a); db.commit(); db.refresh(a)
    return a


# ─────────────────────────────────────────────────────────────────────────────
# Pure unit tests — cycle-regels
# ─────────────────────────────────────────────────────────────────────────────

def test_cycle_months_nen2767_kunstwerken():
    assert cycle.cycle_months_for("brug", 1) == 72
    assert cycle.cycle_months_for("brug", 4) == 12
    assert cycle.cycle_months_for("brug", 6) == 1
    assert cycle.cycle_months_for("viaduct", 3) == 24
    assert cycle.cycle_months_for("tunnel", 5) == 6


def test_cycle_months_nen3399_riolering():
    assert cycle.cycle_months_for("riolering", 1) == 84
    assert cycle.cycle_months_for("riolering", 5) == 6
    # NEN 3399 is 1-5 — clip 6 → 5
    assert cycle.cycle_months_for("riolering", 6) == 6


def test_cycle_months_vta_bomen():
    assert cycle.cycle_months_for("boom", 1) == 36
    assert cycle.cycle_months_for("boom", 3) == 12
    assert cycle.cycle_months_for("boom", 5) == 1
    # Default voor nieuwe bomen zonder score
    assert cycle.cycle_months_for("boom", None) == 36


def test_cycle_months_speeltoestel_vast():
    """NEN-EN 1176 — altijd 12 maanden, score-onafhankelijk."""
    assert cycle.cycle_months_for("speeltoestel", None) == 12
    assert cycle.cycle_months_for("speeltoestel", 1) == 12
    assert cycle.cycle_months_for("speeltoestel", 5) == 12


def test_cycle_months_verlichting_nen3140():
    """NEN 3140 — 5-jaarlijkse keuring elektrische installatie."""
    assert cycle.cycle_months_for("verlichting", 3) == 60
    assert cycle.cycle_months_for("lantaarnpaal", None) == 60


def test_cycle_months_unknown_type_returns_none():
    assert cycle.cycle_months_for("onbekend", 3) is None
    assert cycle.cycle_months_for(None, 3) is None
    assert cycle.cycle_months_for("", 3) is None


def test_next_due_date_basic():
    last = datetime(2026, 1, 15, tzinfo=timezone.utc)
    nxt = cycle.next_due_date(last, 12)
    # 12 maanden ≈ 365 dagen
    assert nxt is not None
    diff_days = (nxt - last).days
    assert 360 <= diff_days <= 370


def test_next_due_date_handles_missing():
    assert cycle.next_due_date(None, 12) is None
    assert cycle.next_due_date(datetime.now(timezone.utc), None) is None


def test_is_overdue_true_for_past_date():
    past = datetime(2025, 1, 1, tzinfo=timezone.utc)
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    assert cycle.is_overdue(past, now) is True


def test_is_overdue_false_for_future():
    future = datetime(2027, 1, 1, tzinfo=timezone.utc)
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    assert cycle.is_overdue(future, now) is False


def test_is_overdue_handles_none():
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    assert cycle.is_overdue(None, now) is False


def test_compliance_status_three_categories():
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    assert cycle.compliance_status(now + timedelta(days=60), now) == "compliant"
    assert cycle.compliance_status(now + timedelta(days=10), now) == "due-soon"
    assert cycle.compliance_status(now - timedelta(days=5), now) == "overdue"
    assert cycle.compliance_status(None, now) == "unscheduled"


def test_norm_reference_per_type():
    assert cycle.norm_reference("brug") == "NEN 2767-2 / CROW 134"
    assert cycle.norm_reference("riolering") == "NEN 3399"
    assert cycle.norm_reference("boom") == "VTA / CROW 200"
    assert cycle.norm_reference("speeltoestel") == "NEN-EN 1176/1177"
    assert cycle.norm_reference("verlichting") == "NEN 3140"
    assert cycle.norm_reference("wegvak") == "CROW 146"


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests — endpoints
# ─────────────────────────────────────────────────────────────────────────────

def test_get_asset_cycle_endpoint(client, admin_user):
    db = SessionLocal()
    try:
        a = _make_asset(db, user=admin_user, asset_type="brug", score=3,
                       last_inspected=datetime.now(timezone.utc) - timedelta(days=400),
                       cycle_months=24)
        a.next_inspection_due = datetime.now(timezone.utc) + timedelta(days=200)
        db.commit()
        a_id = a.id
    finally:
        db.close()

    r = client.get(f"/api/inspection-cycle/asset/{a_id}", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["asset_id"] == a_id
    assert body["asset_type"] == "brug"
    assert body["norm_reference"] == "NEN 2767-2 / CROW 134"
    assert body["compliance_status"] == "compliant"
    assert body["inspection_cycle_months"] == 24
    assert body["days_until_due"] > 0


def test_get_asset_cycle_404(client, admin_user):
    r = client.get("/api/inspection-cycle/asset/nonexistent",
                   headers=auth(admin_user))
    assert r.status_code == 404


def test_overdue_endpoint_lists_only_overdue(client, admin_user):
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        # 1 verlopen
        a1 = _make_asset(db, user=admin_user, asset_type="brug", code="OD-1",
                        score=4,
                        last_inspected=now - timedelta(days=400))
        a1.next_inspection_due = now - timedelta(days=10)
        # 1 compliant
        a2 = _make_asset(db, user=admin_user, asset_type="brug", code="OK-1",
                        score=2,
                        last_inspected=now - timedelta(days=200))
        a2.next_inspection_due = now + timedelta(days=400)
        db.commit()
    finally:
        db.close()

    r = client.get("/api/inspection-cycle/overdue", headers=auth(admin_user))
    assert r.status_code == 200
    body = r.json()
    codes = [i["asset_code"] for i in body["items"]]
    assert "OD-1" in codes
    assert "OK-1" not in codes


def test_upcoming_endpoint_within_horizon(client, admin_user):
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        # binnen 30 dagen
        a1 = _make_asset(db, user=admin_user, asset_type="brug", code="UP-30",
                        score=3,
                        last_inspected=now - timedelta(days=700))
        a1.next_inspection_due = now + timedelta(days=15)
        # buiten 30 dagen
        a2 = _make_asset(db, user=admin_user, asset_type="brug", code="UP-FAR",
                        score=1,
                        last_inspected=now - timedelta(days=10))
        a2.next_inspection_due = now + timedelta(days=120)
        db.commit()
    finally:
        db.close()

    r = client.get("/api/inspection-cycle/upcoming?days=30",
                   headers=auth(admin_user))
    assert r.status_code == 200
    body = r.json()
    codes = [i["asset_code"] for i in body["items"]]
    assert "UP-30" in codes
    assert "UP-FAR" not in codes


def test_compliance_report_aggregates_per_type(client, admin_user):
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        # 2 bruggen (1 compliant, 1 overdue)
        a1 = _make_asset(db, user=admin_user, asset_type="brug", code="B-1", score=1)
        a1.next_inspection_due = now + timedelta(days=400)
        a2 = _make_asset(db, user=admin_user, asset_type="brug", code="B-2", score=5)
        a2.next_inspection_due = now - timedelta(days=10)
        # 1 boom (unscheduled — geen next_due)
        _make_asset(db, user=admin_user, asset_type="boom", code="T-1")
        db.commit()
    finally:
        db.close()

    r = client.get("/api/inspection-cycle/compliance-report",
                   headers=auth(admin_user))
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["total_assets"] >= 3
    types = {b["asset_type"] for b in body["by_type"]}
    assert "brug" in types
    assert "boom" in types
    brug_row = next(b for b in body["by_type"] if b["asset_type"] == "brug")
    assert brug_row["norm_reference"] == "NEN 2767-2 / CROW 134"
    assert brug_row["overdue"] >= 1


def test_recompute_requires_last_inspection_at(client, admin_user):
    db = SessionLocal()
    try:
        a = _make_asset(db, user=admin_user, asset_type="brug", code="RC-NOLAST")
        a_id = a.id
    finally:
        db.close()

    r = client.post(f"/api/inspection-cycle/recompute/{a_id}",
                    headers=auth(admin_user))
    assert r.status_code == 400
    assert "geïnspecteerd" in r.text.lower() or "inspection" in r.text.lower()


def test_recompute_updates_cycle_and_next_due(client, admin_user):
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        a = _make_asset(db, user=admin_user, asset_type="brug",
                       code="RC-OK", score=4,
                       last_inspected=now - timedelta(days=10))
        a_id = a.id
    finally:
        db.close()

    r = client.post(f"/api/inspection-cycle/recompute/{a_id}",
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    body = r.json()
    # Score 4 → 12 maanden
    assert body["inspection_cycle_months"] == 12
    assert body["compliance_status"] in ("compliant", "due-soon")


def test_multitenant_isolation_overdue(client, admin_user):
    """Org A ziet alleen eigen assets, niet die van Org B."""
    from models import Organization, UserRole
    from auth import hash_password
    from models import User as UserModel

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        # Eigen org A asset (verlopen)
        a_org_a = _make_asset(db, user=admin_user, asset_type="brug",
                              code="MT-A", score=4)
        a_org_a.next_inspection_due = now - timedelta(days=5)
        # Org B aanmaken + asset
        org_b = Organization(name="OrgB Cycle Test")
        db.add(org_b); db.commit(); db.refresh(org_b)
        user_b = UserModel(
            email="orgb-cycle@example.com",
            hashed_password=hash_password("test1234"),
            first_name="OrgB", last_name="Admin",
            role=UserRole.ADMIN, is_org_admin=True,
            organization_id=org_b.id,
        )
        db.add(user_b); db.commit(); db.refresh(user_b)
        a_org_b = Asset(
            code="MT-B", asset_type="brug",
            organization_id=org_b.id, created_by=user_b.id,
            condition_score=4,
            next_inspection_due=now - timedelta(days=5),
        )
        db.add(a_org_b); db.commit()
    finally:
        db.close()

    r = client.get("/api/inspection-cycle/overdue", headers=auth(admin_user))
    codes = {i["asset_code"] for i in r.json()["items"]}
    assert "MT-A" in codes
    assert "MT-B" not in codes  # Org B's asset hoort hier niet


# ─────────────────────────────────────────────────────────────────────────────
# Sign-trigger test — kerntest: bij sign wordt Asset auto-bijgewerkt
# ─────────────────────────────────────────────────────────────────────────────

def test_sign_inspection_updates_asset_cycle(client, admin_user):
    """Bij sign_inspection wordt asset.next_inspection_due automatisch gezet."""
    # Maak asset + inspectie
    db = SessionLocal()
    try:
        a = _make_asset(db, user=admin_user, asset_type="brug", code="SIGN-001")
        a_id = a.id
    finally:
        db.close()

    # Inspectie aanmaken
    r = client.post("/api/kunstwerken-inspecties/", json={
        "asset_id": a_id, "title": "Test", "auto_elements": True,
    }, headers=auth(admin_user))
    assert r.status_code == 200, r.text
    insp_id = r.json()["id"]

    # Status door naar completed (handmatig)
    db = SessionLocal()
    try:
        insp = db.query(Inspection).filter(Inspection.id == insp_id).first()
        insp.status = "completed"
        insp.conditiescore_overall = 3  # Redelijk → 24 mnd cyclus
        db.commit()
    finally:
        db.close()

    # Onderteken
    sig = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    r = client.post(f"/api/kunstwerken-inspecties/{insp_id}/sign",
                    json={"signature_data_url": sig},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text

    # Asset moet nu next_inspection_due hebben
    db = SessionLocal()
    try:
        a = db.query(Asset).filter(Asset.id == a_id).first()
        assert a.last_inspection_id == insp_id
        assert a.last_inspection_at is not None
        assert a.condition_score == 3
        assert a.inspection_cycle_months == 24  # NEN 2767-2 score 3 → 24 mnd
        assert a.next_inspection_due is not None
        # Cyclus moet ongeveer 2 jaar in de toekomst zijn
        # SQLite slaat naïef op — vergelijk met naïef
        now_naive = datetime.utcnow()
        nxt = a.next_inspection_due
        if nxt.tzinfo:
            nxt = nxt.replace(tzinfo=None)
        diff_days = (nxt - now_naive).days
        assert 700 <= diff_days <= 740, f"diff={diff_days}"
    finally:
        db.close()


def test_sign_inspection_boom_uses_vta_cycle(client, admin_user):
    """Boom-inspectie krijgt VTA-cyclus, niet NEN 2767-2."""
    db = SessionLocal()
    try:
        a = _make_asset(db, user=admin_user, asset_type="boom", code="BOOM-SIGN-1")
        a_id = a.id
    finally:
        db.close()

    r = client.post("/api/kunstwerken-inspecties/", json={
        "asset_id": a_id, "title": "VTA test",
        "kunstwerk_type": "boom",
        "auto_elements": True,
    }, headers=auth(admin_user))
    assert r.status_code == 200, r.text
    insp_id = r.json()["id"]

    db = SessionLocal()
    try:
        insp = db.query(Inspection).filter(Inspection.id == insp_id).first()
        insp.status = "completed"
        insp.conditiescore_overall = 3  # VTA-klasse 3 → 12 mnd
        db.commit()
    finally:
        db.close()

    sig = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    r = client.post(f"/api/kunstwerken-inspecties/{insp_id}/sign",
                    json={"signature_data_url": sig},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        a = db.query(Asset).filter(Asset.id == a_id).first()
        # VTA score 3 → 12 maanden (NIET 24 zoals NEN 2767)
        assert a.inspection_cycle_months == 12
    finally:
        db.close()
