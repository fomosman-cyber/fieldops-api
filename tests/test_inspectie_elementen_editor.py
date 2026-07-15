"""Flexibele bouwdelen-editor: eigen element toevoegen + bouwdeel verwijderen.

Elk kunstwerk is anders; de standaard NEN 2767-2 / CROW 134-decompositie is een
vertrekpunt. De inspecteur moet eigen bouwdelen kunnen toevoegen en niet-
toepasselijke standaard-bouwdelen kunnen verwijderen.
"""

from database import SessionLocal
from models import Inspection
from tests.conftest import auth
from tests.test_kunstwerken_inspecties import _make_asset, _new_inspection


def _kademuur_inspectie(client, admin_user, code):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="kademuur", code=code).id
    finally:
        db.close()
    return _new_inspection(client, admin_user, a_id, kunstwerk_type="kademuur")


def test_taxonomy_levert_standaard_bouwdelen(client, admin_user):
    r = client.get("/api/kunstwerken-inspecties/taxonomy/kademuur",
                   headers=auth(admin_user))
    assert r.status_code == 200, r.text
    codes = [e["code"] for e in r.json()["elementen"]]
    assert "KADE.MUUR" in codes  # standaard kademuur-decompositie


def test_eigen_bouwdeel_toevoegen(client, admin_user):
    insp = _kademuur_inspectie(client, admin_user, "KW-EL1")
    n_before = len(insp["elementen"])
    r = client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen",
        json={"element_code": "EIGEN.REMMINGWERK", "element_naam": "Remmingwerk / fender",
              "element_groep": "constructief", "order_index": 100},
        headers=auth(admin_user),
    )
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/kunstwerken-inspecties/{insp['id']}",
                        headers=auth(admin_user)).json()
    codes = [e["element_code"] for e in detail["elementen"]]
    assert "EIGEN.REMMINGWERK" in codes
    assert len(detail["elementen"]) == n_before + 1


def test_bouwdeel_verwijderen(client, admin_user):
    insp = _kademuur_inspectie(client, admin_user, "KW-EL2")
    el = insp["elementen"][0]
    n_before = len(insp["elementen"])

    r = client.delete(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}",
        headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == el["id"]

    detail = client.get(f"/api/kunstwerken-inspecties/{insp['id']}",
                        headers=auth(admin_user)).json()
    ids = [e["id"] for e in detail["elementen"]]
    assert el["id"] not in ids
    assert len(detail["elementen"]) == n_before - 1


def test_verwijderen_geblokkeerd_bij_afgesloten_inspectie(client, admin_user):
    insp = _kademuur_inspectie(client, admin_user, "KW-EL3")
    db = SessionLocal()
    try:
        i = db.query(Inspection).filter(Inspection.id == insp["id"]).first()
        i.status = "signed"
        db.commit()
    finally:
        db.close()
    el = insp["elementen"][0]
    r = client.delete(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}",
        headers=auth(admin_user))
    assert r.status_code == 409
