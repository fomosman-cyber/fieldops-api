"""Tests voor type-specifieke inspectie-formulieren.

Dekt alle 5 normen die naast NEN 2767-2 leven:
  - NEN-EN 1176 (speeltoestel) — A/B/C/D + acute afsluiting + multi-cyclus
  - VTA Mattheck (boom)        — risicoklasse 1-5 + holte% + t/r-ratio
  - NEN 3140 (verlichting)     — isolatie / aarding / aardlek meetwaarden
  - CROW 145 (wegmarkering)    — retroreflectie droog + nat
  - NEN 3399 (riolering)       — BAA-BAQ schadecode + eindklasse 1-5

Plus cross-type validatie: een veld is alleen accepteerbaar voor het juiste
kunstwerk_type (bv. vta_risicoklasse op een brug-defect = 400).
"""

from database import SessionLocal
from models import Asset
from tests.conftest import auth


def _make_asset(db, *, user, code, asset_type):
    a = Asset(
        code=code, name=f"Test {asset_type}", asset_type=asset_type,
        organization_id=user.organization_id, created_by=user.id,
    )
    db.add(a); db.commit(); db.refresh(a)
    return a


def _new_inspection(client, user, asset_id, **overrides):
    payload = {"asset_id": asset_id, "title": "Test", "auto_elements": True}
    payload.update(overrides)
    r = client.post("/api/kunstwerken-inspecties/", json=payload, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


def _first_element_id(client, user, inspection_id):
    r = client.get(f"/api/kunstwerken-inspecties/{inspection_id}", headers=auth(user))
    assert r.status_code == 200
    elementen = r.json().get("elementen") or []
    assert elementen, "verwacht minimaal 1 auto-element"
    return elementen[0]["id"]


def _add_defect(client, user, inspection_id, element_id, payload):
    return client.post(
        f"/api/kunstwerken-inspecties/{inspection_id}/elementen/{element_id}/defecten",
        json=payload, headers=auth(user),
    )


# ─────────────────────────────────────────────────────────────────────────────
# NEN-EN 1176 speeltoestel
# ─────────────────────────────────────────────────────────────────────────────

def test_nen1176_kind_accepted_voor_speeltoestel(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="SP-001", asset_type="speeltoestel")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id, nen1176_inspectie_kind="hoofd")
    assert insp["nen1176_inspectie_kind"] == "hoofd"
    assert insp["kunstwerk_type"] == "speeltoestel"


def test_nen1176_kind_invalid_value_400(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="SP-002", asset_type="speeltoestel")
    finally:
        db.close()
    r = client.post(
        "/api/kunstwerken-inspecties/",
        json={"asset_id": asset.id, "title": "X", "auto_elements": True,
              "nen1176_inspectie_kind": "weekly"},
        headers=auth(admin_user),
    )
    assert r.status_code == 400
    assert "routine" in r.json()["detail"].lower() or "hoofd" in r.json()["detail"].lower()


def test_nen1176_kind_rejected_voor_brug(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="BR-001", asset_type="brug")
    finally:
        db.close()
    r = client.post(
        "/api/kunstwerken-inspecties/",
        json={"asset_id": asset.id, "title": "X", "auto_elements": True,
              "nen1176_inspectie_kind": "hoofd"},
        headers=auth(admin_user),
    )
    assert r.status_code == 400
    assert "speeltoestel" in r.json()["detail"].lower()


def test_en1176_categorie_c_auto_acute(client, admin_user):
    """Categorie C of D moet acute_afsluiting=True automatisch krijgen."""
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="SP-003", asset_type="speeltoestel")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id)
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "Knelpunt in glijbaan",
        "en1176_categorie": "C",
    })
    assert r.status_code == 200, r.text
    # Auto-set: niet expliciet doorgegeven, maar server moet 'm True maken
    d = r.json()
    assert d["en1176_categorie"] == "C"
    assert d["en1176_acute_afsluiting"] is True


def test_en1176_categorie_a_geen_acute(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="SP-004", asset_type="speeltoestel")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id)
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "Kleine kras",
        "en1176_categorie": "A",
    })
    assert r.status_code == 200
    assert r.json()["en1176_acute_afsluiting"] is False


def test_en1176_categorie_invalid_400(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="SP-005", asset_type="speeltoestel")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id)
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "X", "en1176_categorie": "Z",
    })
    assert r.status_code == 400
    assert "A" in r.json()["detail"]


def test_en1176_categorie_rejected_voor_brug(client, admin_user):
    """Categorie A/B/C/D mag niet op een brug-defect."""
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="BR-002", asset_type="brug")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id)
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "Scheur", "en1176_categorie": "B",
    })
    assert r.status_code == 400
    assert "speeltoestel" in r.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# VTA Mattheck boom
# ─────────────────────────────────────────────────────────────────────────────

def test_vta_risicoklasse_accepted_voor_boom(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="BM-001", asset_type="boom")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id)
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "Holte in stam",
        "vta_risicoklasse": 4,
        "vta_holte_pct": 35.0,
        "vta_t_r_ratio": 0.28,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["vta_risicoklasse"] == 4
    assert d["vta_holte_pct"] == 35.0
    assert d["vta_t_r_ratio"] == 0.28


def test_vta_risicoklasse_out_of_range_400(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="BM-002", asset_type="boom")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id)
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "X", "vta_risicoklasse": 6,
    })
    assert r.status_code == 400


def test_vta_holte_pct_out_of_range_400(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="BM-003", asset_type="boom")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id)
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "X", "vta_risicoklasse": 3, "vta_holte_pct": 150,
    })
    assert r.status_code == 400


def test_vta_rejected_voor_brug(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="BR-003", asset_type="brug")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id)
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "X", "vta_risicoklasse": 2,
    })
    assert r.status_code == 400
    assert "boom" in r.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# NEN 3140 verlichting
# ─────────────────────────────────────────────────────────────────────────────

def test_nen3140_metingen_accepted_voor_verlichting(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="LM-001", asset_type="verlichting")
    finally:
        db.close()
    # NEN 3140-keuring vereist nu inspecteur_certificaat (VOP/VP/VIOP)
    insp = _new_inspection(client, admin_user, asset.id,
                           inspecteur_certificaat="VP")
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "Slechte isolatie",
        "nen3140_isolatie_megaohm": 0.3,
        "nen3140_aardingsweerstand_ohm": 22.5,
        "nen3140_aardlek_ms": 180,
        "nen3140_aardlek_ma": 30.0,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["nen3140_isolatie_megaohm"] == 0.3
    assert d["nen3140_aardlek_ms"] == 180


def test_nen3140_rejected_voor_brug(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="BR-004", asset_type="brug")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id)
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "X", "nen3140_isolatie_megaohm": 1.0,
    })
    assert r.status_code == 400
    assert "verlichting" in r.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# CROW 145 wegmarkering
# ─────────────────────────────────────────────────────────────────────────────

def test_crow145_retroreflectie_accepted_voor_wegmarkering(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="WM-001", asset_type="wegmarkering")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id)
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "Slijtage zijstreep",
        "crow145_rl_droog_mcd": 75,
        "crow145_rl_nat_mcd": 22,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["crow145_rl_droog_mcd"] == 75
    assert d["crow145_rl_nat_mcd"] == 22


def test_crow145_rejected_voor_brug(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="BR-005", asset_type="brug")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id)
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "X", "crow145_rl_droog_mcd": 100,
    })
    assert r.status_code == 400
    assert "wegmarkering" in r.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# NEN 3399 riolering
# ─────────────────────────────────────────────────────────────────────────────

def test_nen3399_code_accepted_voor_riolering(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="RI-001", asset_type="riolering")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id)
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "Wortelingroei",
        "nen3399_code": "BAH",
        "nen3399_klasse": 4,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["nen3399_code"] == "BAH"
    assert d["nen3399_klasse"] == 4


def test_nen3399_code_invalid_format_400(client, admin_user):
    """Code moet 3 chars zijn beginnend met 'BA'."""
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="RI-002", asset_type="riolering")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id)
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "X", "nen3399_code": "XYZ",
    })
    assert r.status_code == 400


def test_nen3399_klasse_out_of_range_400(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="RI-003", asset_type="riolering")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id)
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "X", "nen3399_code": "BAA", "nen3399_klasse": 7,
    })
    assert r.status_code == 400


def test_nen3399_rejected_voor_brug(client, admin_user):
    db = SessionLocal()
    try:
        asset = _make_asset(db, user=admin_user, code="BR-006", asset_type="brug")
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, asset.id)
    el_id = _first_element_id(client, admin_user, insp["id"])
    r = _add_defect(client, admin_user, insp["id"], el_id, {
        "gebrek_naam": "X", "nen3399_code": "BAA", "nen3399_klasse": 3,
    })
    assert r.status_code == 400
    assert "riolering" in r.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# Cyclus-helper unit tests
# ─────────────────────────────────────────────────────────────────────────────

def test_nen1176_next_due_days():
    import inspection_cycle as cycle
    assert cycle.nen1176_next_due_days("routine") == 7
    assert cycle.nen1176_next_due_days("operationeel") == 30
    assert cycle.nen1176_next_due_days("hoofd") == 365
    assert cycle.nen1176_next_due_days(None) is None
    assert cycle.nen1176_next_due_days("onbekend") is None
