"""Tests voor kunstwerken-inspecties (NEN 2767-2 + CROW 134).

Dekking:
  - Taxonomy endpoints (types + element-bibliotheek)
  - Inspection CRUD + auto-elementen
  - Element-update + score-aggregatie
  - Defect CRUD + worst-defect-rule
  - Status-overgang (draft → in_progress → completed → signed)
  - Ondertekening + bevriezing van afgesloten inspectie
  - Defect → Melding generatie
  - Multi-tenant isolation (org A ziet org B niet)
  - Scoring-formule unit tests
"""

from database import SessionLocal
from models import Asset, Melding
from tests.conftest import auth, _make_user
import nen2767_scoring as scoring
import kunstwerken_taxonomy as kt


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_asset(db, *, user, code="KW-001", asset_type="brug"):
    """Maak een asset gekoppeld aan de organisatie van `user`.

    Gebruikt user.organization_id (kolom-attribuut) ipv user.organization
    (lazy relation) om DetachedInstanceError te vermijden — fixtures sluiten
    hun session voordat de test draait.
    """
    a = Asset(
        code=code, name=f"Test {asset_type}",
        asset_type=asset_type,
        organization_id=user.organization_id, created_by=user.id,
    )
    db.add(a); db.commit(); db.refresh(a)
    return a


def _new_inspection(client, user, asset_id, **overrides):
    payload = {
        "asset_id": asset_id,
        "title": "Periodieke inspectie",
        "auto_elements": True,
    }
    payload.update(overrides)
    r = client.post("/api/kunstwerken-inspecties/", json=payload, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests: scoring + taxonomy
# ─────────────────────────────────────────────────────────────────────────────

def test_defect_score_grenswaarden():
    assert scoring.defect_to_score(1, 1, 1) == 1
    assert scoring.defect_to_score(3, 3, 5) == 6
    assert scoring.defect_to_score(2, 2, 3) == 4


def test_defect_score_incomplete_returns_none():
    assert scoring.defect_to_score(None, 2, 3) is None
    assert scoring.defect_to_score(2, None, 3) is None
    assert scoring.defect_to_score(2, 2, None) is None


def test_omvang_klasse_from_percentage():
    assert scoring.omvang_klasse_from_percentage(1) == 1
    assert scoring.omvang_klasse_from_percentage(5) == 2
    assert scoring.omvang_klasse_from_percentage(20) == 3
    assert scoring.omvang_klasse_from_percentage(50) == 4
    assert scoring.omvang_klasse_from_percentage(80) == 5
    assert scoring.omvang_klasse_from_percentage(None) is None


def test_element_score_worst_defect():
    assert scoring.element_score([1, 3, 4]) == 4
    assert scoring.element_score([None, 2, None]) == 2
    assert scoring.element_score([None, None]) is None


def test_object_score_worst_element():
    assert scoring.object_score([1, 2, 3]) == 3
    assert scoring.object_score([None, 2]) == 2


def test_normalize_type_aliases():
    assert kt.normalize_type("Brug") == "brug"
    assert kt.normalize_type("Bruggen") == "brug"
    assert kt.normalize_type("kade") == "kademuur"
    assert kt.normalize_type("Onbekend") is None


def test_elementen_voor_brug_heeft_kernconstructie():
    codes = {e["code"] for e in kt.elementen_voor("brug")}
    assert "BRUG.ONDERBOUW" in codes
    assert "BRUG.BOVENBOUW" in codes
    assert "BRUG.VOEGOVERGANGEN" in codes


def test_elementen_voor_tunnel():
    codes = {e["code"] for e in kt.elementen_voor("tunnel")}
    assert "TUNNEL.TUNNELDAK" in codes
    assert "TUNNEL.VENTILATIE" in codes


# ─────────────────────────────────────────────────────────────────────────────
# Taxonomy endpoints
# ─────────────────────────────────────────────────────────────────────────────

def test_get_types(client, admin_user):
    r = client.get("/api/kunstwerken-inspecties/taxonomy/types", headers=auth(admin_user))
    assert r.status_code == 200
    keys = {t["key"] for t in r.json()["types"]}
    assert "brug" in keys
    assert "tunnel" in keys
    assert "sluis" in keys
    assert "kademuur" in keys


def test_get_taxonomy_for_brug(client, admin_user):
    r = client.get("/api/kunstwerken-inspecties/taxonomy/brug", headers=auth(admin_user))
    assert r.status_code == 200
    data = r.json()
    assert data["kunstwerk_type"] == "brug"
    assert any(e["code"] == "BRUG.ONDERBOUW" for e in data["elementen"])
    assert "scoring" in data
    assert data["scoring"]["labels"]["6"] == "Zeer slecht"


def test_get_taxonomy_unknown_404(client, admin_user):
    r = client.get("/api/kunstwerken-inspecties/taxonomy/eskimo-iglo", headers=auth(admin_user))
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Inspection CRUD
# ─────────────────────────────────────────────────────────────────────────────

def test_create_inspection_auto_elements(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()

    insp = _new_inspection(client, admin_user, a_id, title="Brug-inspectie")
    assert insp["kunstwerk_type"] == "brug"
    assert insp["status"] == "draft"
    assert insp["elementen_count"] >= 8  # brug heeft 10 elementen
    codes = {e["element_code"] for e in insp["elementen"]}
    assert "BRUG.ONDERBOUW" in codes


def test_create_inspection_voor_alle_4_kunstwerk_types(client, admin_user):
    """Smoke-test: types die de user wil ondersteunen kunnen allemaal opgevoerd worden."""
    db = SessionLocal()
    try:
        # Capture id's binnen session — Asset is detached na close
        asset_ids = {
            t: _make_asset(db, user=admin_user,
                           code=f"KW-{t}", asset_type=t).id
            for t in ("brug", "tunnel", "sluis", "duiker", "kademuur")
        }
    finally:
        db.close()
    for t, asset_id in asset_ids.items():
        insp = _new_inspection(client, admin_user, asset_id, title=f"{t}-test")
        assert insp["kunstwerk_type"] == t
        assert insp["elementen_count"] > 0, f"{t} heeft 0 elementen"


def test_create_inspection_zonder_asset_404(client, admin_user):
    r = client.post("/api/kunstwerken-inspecties/", json={
        "asset_id": "non-existing",
        "title": "Bestaat-niet",
    }, headers=auth(admin_user))
    assert r.status_code == 404


def test_create_inspection_unknown_kunstwerk_type_400(client, admin_user):
    """Asset met onbekend asset_type krijgt 400 (geen taxonomy-match)."""
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user,
                        code="VR-001", asset_type="vuilcontainer").id
    finally:
        db.close()
    r = client.post("/api/kunstwerken-inspecties/", json={
        "asset_id": a_id, "title": "Foute koppeling",
    }, headers=auth(admin_user))
    assert r.status_code == 400


def test_inspection_list_filtert_op_eigen_org(client, admin_user, org):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    _new_inspection(client, admin_user, a_id)
    r = client.get("/api/kunstwerken-inspecties/", headers=auth(admin_user))
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_inspection_isolation_cross_org(client, admin_user, org):
    """Inspectie in org A onzichtbaar voor user in org B."""
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
        # Maak een tweede org + user
        from models import Organization, AccountStatus, SubscriptionPlan
        other_org = Organization(name="OtherOrg",
                                 plan=SubscriptionPlan.PROFESSIONAL,
                                 status=AccountStatus.ACTIVE, max_users=10)
        db.add(other_org); db.commit(); db.refresh(other_org)
        other_user = _make_user(db, "other@test.nl", org=other_org)
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    # Other user mag niet zien
    r = client.get(f"/api/kunstwerken-inspecties/{insp['id']}", headers=auth(other_user))
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Defecten → element-score → object-score
# ─────────────────────────────────────────────────────────────────────────────

def test_defect_toevoegen_berekent_score(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    r = client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
        json={"gebrek_naam": "Scheurvorming", "ernst": 2, "intensiteit": 2, "omvang_klasse": 3},
        headers=auth(admin_user),
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["defect_score"] == 4
    assert d["defect_score_label"] == "Matig"

    # Element-score updated
    r = client.get(f"/api/kunstwerken-inspecties/{insp['id']}", headers=auth(admin_user))
    elements = r.json()["elementen"]
    updated = next(e for e in elements if e["id"] == el["id"])
    assert updated["conditiescore"] == 4
    assert updated["beoordeeld"] is True


def test_worst_defect_wint_voor_element(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    # Drie defecten met verschillende score
    for ernst, intensiteit, omvang in [(1, 1, 1), (3, 3, 5), (2, 1, 2)]:
        client.post(
            f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
            json={"gebrek_naam": "X", "ernst": ernst,
                  "intensiteit": intensiteit, "omvang_klasse": omvang},
            headers=auth(admin_user),
        )
    detail = client.get(f"/api/kunstwerken-inspecties/{insp['id']}", headers=auth(admin_user)).json()
    el_after = next(e for e in detail["elementen"] if e["id"] == el["id"])
    assert el_after["conditiescore"] == 6  # zwaarste defect domineert


def test_omvang_percentage_wordt_naar_klasse_gemapt(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    r = client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
        json={"gebrek_naam": "Lek", "ernst": 2, "intensiteit": 2, "omvang_percentage": 45},
        headers=auth(admin_user),
    )
    d = r.json()
    assert d["omvang_klasse"] == 4
    assert d["defect_score"] == 5  # 3 (ernst=2) + 1 (intensiteit=2) + 1 (omvang_klasse=4)


def test_defect_update_herberekent(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    d = client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
        json={"gebrek_naam": "X", "ernst": 1, "intensiteit": 1, "omvang_klasse": 1},
        headers=auth(admin_user),
    ).json()
    assert d["defect_score"] == 1
    # Update naar zwaarder
    r = client.patch(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten/{d['id']}",
        json={"ernst": 3, "intensiteit": 3, "omvang_klasse": 5},
        headers=auth(admin_user),
    )
    assert r.status_code == 200
    assert r.json()["defect_score"] == 6


def test_defect_delete_herberekent_element(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    d1 = client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
        json={"gebrek_naam": "Klein", "ernst": 1, "intensiteit": 1, "omvang_klasse": 1},
        headers=auth(admin_user),
    ).json()
    d2 = client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
        json={"gebrek_naam": "Groot", "ernst": 3, "intensiteit": 3, "omvang_klasse": 5},
        headers=auth(admin_user),
    ).json()
    # Verwijder zwaarste
    r = client.delete(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten/{d2['id']}",
        headers=auth(admin_user),
    )
    assert r.status_code == 200
    # Element-score moet nu 1 zijn (alleen kleine over)
    detail = client.get(f"/api/kunstwerken-inspecties/{insp['id']}", headers=auth(admin_user)).json()
    el_after = next(e for e in detail["elementen"] if e["id"] == el["id"])
    assert el_after["conditiescore"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Status-flow
# ─────────────────────────────────────────────────────────────────────────────

def test_status_auto_naar_in_progress(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    # Defect toevoegen → status moet automatisch in_progress
    client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
        json={"gebrek_naam": "X", "ernst": 1, "intensiteit": 1, "omvang_klasse": 1},
        headers=auth(admin_user),
    )
    detail = client.get(f"/api/kunstwerken-inspecties/{insp['id']}", headers=auth(admin_user)).json()
    assert detail["status"] == "in_progress"


def test_complete_zonder_beoordeling_400(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    r = client.post(f"/api/kunstwerken-inspecties/{insp['id']}/complete", headers=auth(admin_user))
    assert r.status_code == 400


def test_complete_berekent_overall_score(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    # Eén ernstig defect
    client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
        json={"gebrek_naam": "Scheur", "ernst": 3, "intensiteit": 2, "omvang_klasse": 4},
        headers=auth(admin_user),
    )
    r = client.post(f"/api/kunstwerken-inspecties/{insp['id']}/complete", headers=auth(admin_user))
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "completed"
    assert d["conditiescore_overall"] == 6
    assert d["conditiescore_label"] == "Zeer slecht"
    assert d["maatregel_advies"]["categorie"] == "acuut"


def test_sign_zonder_complete_400(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
        json={"gebrek_naam": "X", "ernst": 1, "intensiteit": 1, "omvang_klasse": 1},
        headers=auth(admin_user),
    )
    # Sign zonder eerst complete
    r = client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/sign",
        json={"signature_data_url": "data:image/png;base64,abc"},
        headers=auth(admin_user),
    )
    assert r.status_code == 400


def test_signed_inspection_bevroren(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
        json={"gebrek_naam": "X", "ernst": 1, "intensiteit": 1, "omvang_klasse": 1},
        headers=auth(admin_user),
    )
    client.post(f"/api/kunstwerken-inspecties/{insp['id']}/complete", headers=auth(admin_user))
    sig = "data:image/png;base64," + "A" * 100
    r = client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/sign",
        json={"signature_data_url": sig},
        headers=auth(admin_user),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "signed"
    # Nieuwe defect na sign → 409
    r2 = client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
        json={"gebrek_naam": "Late toevoeging", "ernst": 1, "intensiteit": 1, "omvang_klasse": 1},
        headers=auth(admin_user),
    )
    assert r2.status_code == 409
    # Delete inspectie → 409
    r3 = client.delete(f"/api/kunstwerken-inspecties/{insp['id']}", headers=auth(admin_user))
    assert r3.status_code == 409


def test_sign_invalid_signature(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
        json={"gebrek_naam": "X", "ernst": 1, "intensiteit": 1, "omvang_klasse": 1},
        headers=auth(admin_user),
    )
    client.post(f"/api/kunstwerken-inspecties/{insp['id']}/complete", headers=auth(admin_user))
    # Geen data-URL
    r = client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/sign",
        json={"signature_data_url": "not-a-data-url"},
        headers=auth(admin_user),
    )
    assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Defect → Melding
# ─────────────────────────────────────────────────────────────────────────────

def test_defect_to_melding_creates_orchestration_link(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    d = client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
        json={"gebrek_naam": "Wapeningscorrosie", "ernst": 3, "intensiteit": 2,
              "omvang_klasse": 3, "gw_maatregel": "Reparatie betondekking"},
        headers=auth(admin_user),
    ).json()
    r = client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten/{d['id']}/to-melding",
        json={},
        headers=auth(admin_user),
    )
    assert r.status_code == 200
    melding_id = r.json()["melding_id"]
    # Idempotent: tweede call retourneert dezelfde melding
    r2 = client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten/{d['id']}/to-melding",
        json={},
        headers=auth(admin_user),
    )
    assert r2.json()["melding_id"] == melding_id

    # Melding bestaat met juiste velden
    db = SessionLocal()
    try:
        m = db.query(Melding).filter(Melding.id == melding_id).first()
        assert m is not None
        # ernst=3, intensiteit=2, omvang_klasse=3 → 5 + 1 + 0 = 6 → "kritiek"
        assert m.priority == "kritiek"
        assert m.gw_maatregel == "Reparatie betondekking"
        assert m.asset_id == a_id
        assert "NEN 2767-2" in (m.description or "")
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# RBAC
# ─────────────────────────────────────────────────────────────────────────────

def test_viewer_kan_geen_inspectie_maken(client, viewer_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=viewer_user, asset_type="brug").id
    finally:
        db.close()
    r = client.post("/api/kunstwerken-inspecties/", json={
        "asset_id": a_id, "title": "Test",
    }, headers=auth(viewer_user))
    assert r.status_code == 403


def test_inspector_kan_inspectie_maken(client, inspector_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=inspector_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, inspector_user, a_id)
    assert insp["status"] == "draft"


# ─────────────────────────────────────────────────────────────────────────────
# Vragenlijst (NEN/CROW-conform)
# ─────────────────────────────────────────────────────────────────────────────

def test_taxonomy_vragen_voor_brug_onderbouw_bevat_generiek_plus_constructief():
    qs = kt.vragen_voor("brug", "BRUG.ONDERBOUW")
    codes = {q["code"] for q in qs}
    # Generieke vragen
    assert "GEN.STAAT" in codes
    assert "GEN.VEILIG" in codes
    assert "GEN.INSPECTEERBAARHEID" in codes
    assert "GEN.URGENTIE" in codes
    # Constructief-groep
    assert "CONSTR.SCHEUR" in codes
    assert "CONSTR.WAPENING" in codes


def test_taxonomy_vragen_voor_voegovergangen_heeft_element_specifiek():
    qs = kt.vragen_voor("brug", "BRUG.VOEGOVERGANGEN")
    codes = {q["code"] for q in qs}
    assert "VOEG.LEK" in codes
    assert "VOEG.GELUID" in codes


def test_taxonomy_alle_vragen_hebben_uitleg_en_normref():
    """Elke vraag moet uitleg + norm_ref hebben — anders is 't niet NEN/CROW-conform."""
    for q in kt.GENERIEKE_VRAGEN:
        assert q.get("uitleg") and q.get("norm_ref"), f"Vraag {q['code']} mist uitleg/norm_ref"
    for groep, qs in kt.VRAGEN_PER_GROEP.items():
        for q in qs:
            assert q.get("uitleg") and q.get("norm_ref"), f"Vraag {q['code']} (groep {groep}) mist uitleg/norm_ref"


def test_create_inspection_maakt_vragen_aan(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    # Voor één element vragen ophalen
    el = insp["elementen"][0]
    r = client.get(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/vragen",
        headers=auth(admin_user),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["version"]
    # 4 generieke + 3 constructief (voor onderbouw) = 7
    codes = {it["question"]["code"] for it in data["items"]}
    assert "GEN.STAAT" in codes
    assert "CONSTR.SCHEUR" in codes
    # Antwoorden zijn aangemaakt maar leeg
    assert all(it["answer"]["answered"] is False for it in data["items"])


def test_antwoord_score_1_6_trigger_aandacht(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    # Score 4 of hoger → aandacht
    r = client.patch(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/vragen/GEN.STAAT",
        json={"answer_score": 5},
        headers=auth(admin_user),
    )
    assert r.status_code == 200
    assert r.json()["requires_attention"] is True
    assert r.json()["answered"] is True


def test_antwoord_score_lager_dan_4_geen_aandacht(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    r = client.patch(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/vragen/GEN.STAAT",
        json={"answer_score": 2},
        headers=auth(admin_user),
    )
    assert r.json()["requires_attention"] is False


def test_antwoord_ja_op_veiligheidsvraag_is_aandacht(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    # GEN.VEILIG: attention_when=True → ja = aandacht
    r = client.patch(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/vragen/GEN.VEILIG",
        json={"answer_bool": True, "toelichting": "Loszittende leuning, valgevaar"},
        headers=auth(admin_user),
    )
    assert r.json()["requires_attention"] is True


def test_antwoord_status_naar_in_progress(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    assert insp["status"] == "draft"
    el = insp["elementen"][0]
    client.patch(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/vragen/GEN.STAAT",
        json={"answer_score": 2},
        headers=auth(admin_user),
    )
    r = client.get(f"/api/kunstwerken-inspecties/{insp['id']}", headers=auth(admin_user))
    assert r.json()["status"] == "in_progress"


def test_antwoord_op_gesloten_inspectie_geblokkeerd(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
        json={"gebrek_naam": "X", "ernst": 1, "intensiteit": 1, "omvang_klasse": 1},
        headers=auth(admin_user),
    )
    client.post(f"/api/kunstwerken-inspecties/{insp['id']}/complete", headers=auth(admin_user))
    sig = "data:image/png;base64," + "A" * 100
    client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/sign",
        json={"signature_data_url": sig},
        headers=auth(admin_user),
    )
    r = client.patch(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/vragen/GEN.STAAT",
        json={"answer_score": 4},
        headers=auth(admin_user),
    )
    assert r.status_code == 409


def test_onbekende_question_code_404(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    r = client.patch(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/vragen/NEP.NIETBESTAAND",
        json={"answer_score": 3},
        headers=auth(admin_user),
    )
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────────────────────

def test_metrics_endpoint_returns_zero_voor_lege_inspectie(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    r = client.get(f"/api/kunstwerken-inspecties/{insp['id']}/metrics",
                   headers=auth(admin_user))
    assert r.status_code == 200
    m = r.json()
    assert m["vragen_beantwoord"] == 0
    assert m["vragen_aandacht"] == 0
    assert m["defecten_totaal"] == 0
    assert m["voortgang_pct"] == 0
    # Brug heeft elementen — controleer
    assert m["elementen_totaal"] >= 8


def test_metrics_telt_aandacht_en_kritiek_correct(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    # 2 aandachtspunten + 1 niet
    client.patch(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/vragen/GEN.STAAT",
        json={"answer_score": 5},
        headers=auth(admin_user),
    )
    client.patch(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/vragen/GEN.VEILIG",
        json={"answer_bool": True, "toelichting": "Risico"},
        headers=auth(admin_user),
    )
    client.patch(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/vragen/GEN.INSPECTEERBAARHEID",
        json={"answer_value_text": "volledig"},
        headers=auth(admin_user),
    )
    # 1 kritiek defect
    client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
        json={"gebrek_naam": "Zware scheur", "ernst": 3, "intensiteit": 2, "omvang_klasse": 4},
        headers=auth(admin_user),
    )

    r = client.get(f"/api/kunstwerken-inspecties/{insp['id']}/metrics",
                   headers=auth(admin_user))
    m = r.json()
    assert m["vragen_beantwoord"] == 3
    assert m["vragen_aandacht"] == 2
    assert m["defecten_totaal"] == 1
    assert m["defecten_kritiek"] == 1  # score >= 5


def test_metrics_in_inspection_detail(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    r = client.get(f"/api/kunstwerken-inspecties/{insp['id']}", headers=auth(admin_user))
    assert r.status_code == 200
    assert "metrics" in r.json()
    assert r.json()["metrics"]["elementen_totaal"] > 0


def test_metrics_elementen_per_conditie_keys_zijn_strings(client, admin_user):
    """Frontend verwacht string keys '1'..'6' (JSON-conventie)."""
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id)
    r = client.get(f"/api/kunstwerken-inspecties/{insp['id']}/metrics",
                   headers=auth(admin_user))
    epc = r.json()["elementen_per_conditie"]
    assert set(epc.keys()) == {"1", "2", "3", "4", "5", "6"}


# ─────────────────────────────────────────────────────────────────────────────
# Bomen-VTA flow tests (NEN-conform via shared Inspection-architectuur)
# ─────────────────────────────────────────────────────────────────────────────

def test_boom_type_in_taxonomy():
    """Boom moet beschikbaar zijn naast kunstwerken."""
    assert "boom" in kt.KUNSTWERK_TYPES
    assert kt.KUNSTWERK_TYPES["boom"] == "Boom (VTA)"


def test_boom_aliases_normalize():
    """Variaties op 'boom' moeten naar de canonieke key normaliseren."""
    for alias in ("bomen", "laanboom", "monumentale-boom", "park-boom", "VTA", "vta"):
        assert kt.normalize_type(alias) == "boom", f"alias '{alias}' faalde"


def test_boom_elementen_compleet():
    """5 verplichte VTA-elementen aanwezig."""
    elems = kt.elementen_voor("boom")
    codes = {e["code"] for e in elems}
    expected = {"BOOM.STAM", "BOOM.WORTELAANLOOP", "BOOM.HOOFDTAKKEN",
                "BOOM.KROON", "BOOM.STANDPLAATS"}
    assert codes == expected


def test_boom_stam_vragenlijst_geen_beton():
    """BOOM.STAM krijgt VTA-vragen, GEEN beton/wapening vragen."""
    qs = kt.vragen_voor("boom", "BOOM.STAM")
    codes = [q["code"] for q in qs]
    # Wapeningscorrosie-vraag is constructief (beton) en mag NIET bij bomen
    assert "CONSTR.WAPENING" not in codes
    # In plaats daarvan: vegetatief-groep vragen
    assert "VEG.SCHEUR" in codes
    assert "VEG.SCHEEF" in codes
    # Plus element-specifieke VTA-vragen
    assert "VTA.STAM_HOLTE_PCT" in codes
    assert "VTA.STAM_ZWAM" in codes
    assert "VTA.STAM_KLOPTEST" in codes


def test_boom_kroon_heeft_vitaliteit():
    """Kroon-element heeft NTS vitaliteitsklasse."""
    qs = kt.vragen_voor("boom", "BOOM.KROON")
    codes = [q["code"] for q in qs]
    assert "VTA.VITALITEIT" in codes
    assert "VTA.DOOD_HOUT_PCT" in codes


def test_boom_standplaats_heeft_dbh_en_doel_risico():
    """Standplaats vraagt om DBH en doel-risico-type voor risicoberekening."""
    qs = kt.vragen_voor("boom", "BOOM.STANDPLAATS")
    codes = [q["code"] for q in qs]
    assert "VTA.DBH" in codes
    assert "VTA.STANDPLAATS_TYPE" in codes


def test_boom_gebreken_bibliotheek():
    """BOOM.STAM heeft VTA-specifieke gebreken (zwam, holte, etc)."""
    gebreken = kt.gebreken_voor("boom", "BOOM.STAM")
    codes = {g["code"] for g in gebreken}
    assert "zwam" in codes
    assert "holte" in codes
    assert "kanker" in codes
    assert "bliksem" in codes


def test_kunstwerk_flow_geen_vta_pollution():
    """Regression: kunstwerk-flow mag GEEN VTA-vragen krijgen."""
    qs_brug = kt.vragen_voor("brug", "BRUG.ONDERBOUW")
    codes = [q["code"] for q in qs_brug]
    assert "CONSTR.WAPENING" in codes  # brug heeft beton-vragen
    for c in codes:
        assert not c.startswith("VTA."), f"Kunstwerk kreeg VTA-vraag: {c}"
        assert not c.startswith("VEG."), f"Kunstwerk kreeg vegetatief-vraag: {c}"


def test_boom_taxonomy_types_endpoint(client, admin_user):
    """API /taxonomy/types geeft boom-type terug."""
    r = client.get("/api/kunstwerken-inspecties/taxonomy/types",
                   headers=auth(admin_user))
    assert r.status_code == 200
    keys = [t["key"] for t in r.json()["types"]]
    assert "boom" in keys


def test_boom_inspection_creates_with_auto_elements(client, admin_user):
    """Boom-inspectie maakt 5 elementen automatisch aan."""
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="boom", code="BOOM-001").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id, kunstwerk_type="boom")
    assert insp["kunstwerk_type"] == "boom"
    # Elementen-lijst via detail-endpoint
    r = client.get(f"/api/kunstwerken-inspecties/{insp['id']}",
                   headers=auth(admin_user))
    assert r.status_code == 200
    elems = r.json().get("elementen", [])
    codes = {e["element_code"] for e in elems}
    assert codes == {"BOOM.STAM", "BOOM.WORTELAANLOOP", "BOOM.HOOFDTAKKEN",
                     "BOOM.KROON", "BOOM.STANDPLAATS"}


def test_boom_vragen_endpoint(client, admin_user):
    """API /vragen geeft VTA-specifieke vragen voor boom-elementen."""
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="boom", code="BOOM-002").id
    finally:
        db.close()
    insp = _new_inspection(client, admin_user, a_id, kunstwerk_type="boom")
    # Pak BOOM.STAM via detail
    detail = client.get(f"/api/kunstwerken-inspecties/{insp['id']}",
                       headers=auth(admin_user)).json()
    elems = detail.get("elementen", [])
    stam = next(e for e in elems if e["element_code"] == "BOOM.STAM")
    # Vragen zitten al in element-detail (via include_antwoorden)
    vragen_codes = [v["question"]["code"] for v in stam.get("vragen", [])]
    assert "VTA.STAM_HOLTE_PCT" in vragen_codes
    assert "VTA.STAM_ZWAM" in vragen_codes
    assert "CONSTR.WAPENING" not in vragen_codes  # geen beton-vragen voor bomen
