"""PR-C — from_inspection-vlag op meldingen.

De Meldingen-tab schakelt tussen Klein onderhoud (onderhoud_categorie) en
CROW grote ronde (meldingen gekoppeld aan een formele inspectie via
InspectionDefect.melding_id). De lijst-API zet daarvoor `from_inspection`.
"""

from tests.conftest import auth
from database import SessionLocal
from models import Asset, Melding, Inspection, InspectionElement, InspectionDefect


def test_from_inspection_flag(client, admin_user):
    org, uid = admin_user.organization_id, admin_user.id
    db = SessionLocal()
    try:
        asset = Asset(code="KW-1", asset_type="brug", organization_id=org, created_by=uid)
        db.add(asset)
        db.flush()
        m_crow = Melding(title="CROW-melding", organization_id=org, created_by=uid)
        m_ko = Melding(title="KO-melding", organization_id=org, created_by=uid,
                       onderhoud_categorie="KO")
        db.add_all([m_crow, m_ko])
        db.flush()
        insp = Inspection(organization_id=org, asset_id=asset.id, kunstwerk_type="brug",
                          title="Inspectie brug", inspecteur_id=uid, created_by=uid)
        db.add(insp)
        db.flush()
        el = InspectionElement(inspection_id=insp.id, organization_id=org,
                               element_code="BRUG.DEK", element_naam="Brugdek")
        db.add(el)
        db.flush()
        defect = InspectionDefect(element_id=el.id, organization_id=org,
                                  gebrek_naam="Scheur", melding_id=m_crow.id)
        db.add(defect)
        db.commit()
    finally:
        db.close()

    items = {m["title"]: m for m in
             client.get("/api/meldingen/", headers=auth(admin_user)).json()}
    assert items["CROW-melding"]["from_inspection"] is True
    assert items["KO-melding"]["from_inspection"] is False
