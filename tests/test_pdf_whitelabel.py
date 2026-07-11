"""CROW-PDF: white-label + R2/https-foto + object-metadata (blokker 5 / A, C, D).

Hergebruikt de helpers uit test_kunstwerken_inspecties voor de inspectie-setup.
PDF-inhoud is binair; we verifiëren dat de nieuwe codepaden de generatie niet
breken (status 200 + %PDF-magic) — incl. een R2-stijl https-foto die niet
bereikbaar is (moet best-effort overslaan, niet crashen).
"""

from database import SessionLocal
from models import Asset, InspectionDefect, Organization
from tests.conftest import auth
from tests.test_kunstwerken_inspecties import _make_asset, _new_inspection, _PNG_1x1


def test_export_pdf_with_brand_and_logo(client, admin_user):
    """A — org-huisstijl (kleur + logo) breekt de generatie niet."""
    db = SessionLocal()
    try:
        o = db.query(Organization).filter(
            Organization.id == admin_user.organization_id).first()
        o.brand_color = "#ea580c"
        o.logo_data_url = _PNG_1x1
        a_id = _make_asset(db, user=admin_user, asset_type="brug", code="KW-WL").id
        db.commit()
    finally:
        db.close()

    insp = _new_inspection(client, admin_user, a_id)
    r = client.get(f"/api/kunstwerken-inspecties/{insp['id']}/export.pdf",
                   headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"


def test_export_pdf_with_https_photo_does_not_crash(client, admin_user):
    """C — een R2-stijl https-foto-URL die niet bereikbaar is wordt overgeslagen."""
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="brug", code="KW-HTTPS").id
    finally:
        db.close()

    insp = _new_inspection(client, admin_user, a_id)
    el = insp["elementen"][0]
    r = client.post(
        f"/api/kunstwerken-inspecties/{insp['id']}/elementen/{el['id']}/defecten",
        json={"gebrek_naam": "Scheurvorming", "gebrek_code": "scheurvorming-langs",
              "ernst": 2, "intensiteit": 2, "omvang_klasse": 2},
        headers=auth(admin_user),
    )
    assert r.status_code == 200, r.text
    d_id = r.json()["id"]

    db = SessionLocal()
    try:
        dd = db.query(InspectionDefect).filter(InspectionDefect.id == d_id).first()
        dd.photo_url = "https://nonexistent.invalid/defect.jpg"  # onbereikbaar
        db.commit()
    finally:
        db.close()

    r = client.get(f"/api/kunstwerken-inspecties/{insp['id']}/export.pdf",
                   headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"


def test_export_pdf_with_object_metadata(client, admin_user):
    """D — bouwjaar/beheerder uit properties_json breekt de generatie niet."""
    db = SessionLocal()
    try:
        a = _make_asset(db, user=admin_user, asset_type="brug", code="KW-META")
        obj = db.query(Asset).filter(Asset.id == a.id).first()
        obj.properties_json = '{"bouwjaar": "1978", "beheerder": "Gemeente Testdam"}'
        db.commit()
        a_id = obj.id
    finally:
        db.close()

    insp = _new_inspection(client, admin_user, a_id)
    r = client.get(f"/api/kunstwerken-inspecties/{insp['id']}/export.pdf",
                   headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
