"""Inspectie-flow: per-object bouwdelen-template (backend).

Een getailorde bouwdelen-set wordt op het object (asset) bewaard, zodat een
volgende inspectie van dat object de set hergebruikt i.p.v. de generieke
type-standaard (NEN 2767-4: object-decompositie). De flow-knoppen jump-next /
bulk / AI-in-defect-modal zijn frontend (los geverifieerd via node --check).
"""

from database import SessionLocal
from tests.conftest import auth
from tests.test_kunstwerken_inspecties import _make_asset, _new_inspection


def test_save_template_reused_by_next_inspection(client, admin_user):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="kademuur", code="KW-TPL").id
    finally:
        db.close()

    insp1 = _new_inspection(client, admin_user, a_id, kunstwerk_type="kademuur")
    # eigen bouwdeel toevoegen
    client.post(
        f"/api/kunstwerken-inspecties/{insp1['id']}/elementen",
        json={"element_code": "EIGEN.REMMING", "element_naam": "Remmingwerk",
              "element_groep": "constructief", "order_index": 100},
        headers=auth(admin_user))
    # een standaard-bouwdeel verwijderen
    client.delete(
        f"/api/kunstwerken-inspecties/{insp1['id']}/elementen/{insp1['elementen'][0]['id']}",
        headers=auth(admin_user))

    d1 = client.get(f"/api/kunstwerken-inspecties/{insp1['id']}",
                    headers=auth(admin_user)).json()
    tailored = sorted(e["element_code"] for e in d1["elementen"])
    assert "EIGEN.REMMING" in tailored

    # template opslaan op het object
    r = client.post(f"/api/kunstwerken-inspecties/{insp1['id']}/save-elements-template",
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == len(tailored)

    # nieuwe inspectie op HETZELFDE object -> hergebruikt de getailorde set
    insp2 = _new_inspection(client, admin_user, a_id, kunstwerk_type="kademuur")
    codes2 = sorted(e["element_code"] for e in insp2["elementen"])
    assert codes2 == tailored, (codes2, tailored)


def test_save_template_other_object_unaffected(client, admin_user):
    """Template op object A mag een inspectie op object B niet beïnvloeden."""
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="kademuur", code="KW-A").id
        b_id = _make_asset(db, user=admin_user, asset_type="kademuur", code="KW-B").id
    finally:
        db.close()

    insp_a = _new_inspection(client, admin_user, a_id, kunstwerk_type="kademuur")
    client.post(
        f"/api/kunstwerken-inspecties/{insp_a['id']}/elementen",
        json={"element_code": "EIGEN.UNIEK_A", "element_naam": "Uniek A",
              "element_groep": "overig", "order_index": 100},
        headers=auth(admin_user))
    client.post(f"/api/kunstwerken-inspecties/{insp_a['id']}/save-elements-template",
                headers=auth(admin_user))

    insp_b = _new_inspection(client, admin_user, b_id, kunstwerk_type="kademuur")
    codes_b = [e["element_code"] for e in insp_b["elementen"]]
    assert "EIGEN.UNIEK_A" not in codes_b  # object B krijgt de generieke standaard
