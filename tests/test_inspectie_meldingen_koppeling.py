"""Test voor de melding-koppeling op de Inspecties-tab (#9).

CROW-inspecties tonen het aantal gekoppelde meldingen (via
InspectionDefect.melding_id) + de melding-ids voor doorklikken.
"""
from database import SessionLocal
from models import Asset, Melding, InspectionDefect
from tests.conftest import auth


def test_inspection_list_exposes_meldingen_count_and_ids(client, org, admin_user):
    db = SessionLocal()
    try:
        a = Asset(code="BR-1", asset_type="brug", organization_id=org.id,
                  created_by=admin_user.id)
        db.add(a); db.commit(); db.refresh(a)
        asset_id = a.id
    finally:
        db.close()

    insp = client.post("/api/kunstwerken-inspecties/",
                       json={"asset_id": asset_id, "title": "Brug-inspectie",
                             "auto_elements": True},
                       headers=auth(admin_user)).json()
    detail = client.get(f"/api/kunstwerken-inspecties/{insp['id']}",
                        headers=auth(admin_user)).json()
    el_id = detail["elementen"][0]["id"]
    defect = client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el_id}/defecten",
        json={"gebrek_naam": "Scheurvorming"}, headers=auth(admin_user)).json()

    # Koppel een melding aan het defect (zoals defect_to_melding doet)
    db = SessionLocal()
    try:
        m = Melding(title="Uit inspectie", organization_id=org.id,
                    created_by=admin_user.id, category="inspectie-bevinding")
        db.add(m); db.commit(); db.refresh(m)
        d = db.query(InspectionDefect).filter(InspectionDefect.id == defect["id"]).first()
        d.melding_id = m.id
        db.commit()
        melding_id = m.id
    finally:
        db.close()

    items = client.get("/api/kunstwerken-inspecties/", headers=auth(admin_user)).json()
    me = next(x for x in items if x["id"] == insp["id"])
    assert me["meldingen_count"] == 1
    assert melding_id in me["melding_ids"]


def test_inspection_without_meldingen_has_zero(client, org, admin_user):
    db = SessionLocal()
    try:
        a = Asset(code="BR-2", asset_type="brug", organization_id=org.id,
                  created_by=admin_user.id)
        db.add(a); db.commit(); db.refresh(a)
        asset_id = a.id
    finally:
        db.close()
    insp = client.post("/api/kunstwerken-inspecties/",
                       json={"asset_id": asset_id, "title": "Leeg", "auto_elements": True},
                       headers=auth(admin_user)).json()
    items = client.get("/api/kunstwerken-inspecties/", headers=auth(admin_user)).json()
    me = next(x for x in items if x["id"] == insp["id"])
    assert me["meldingen_count"] == 0
    assert me["melding_ids"] == []
