"""MJOP-PDF met tekens die niet in latin-1 passen.

fpdf2 gebruikt de core-font Helvetica en die is latin-1. Een en-dash, een
krulaanhalingsteken of een euroteken laat de generatie klappen zodra het
ongesaneerd in een cel belandt. De router heeft daar een `_safe()` voor, maar
die werd niet toegepast op de plekken waar juist KLANTDATA binnenkomt: de
organisatie- en projectnaam op de omslag, asset-codes en -types, de
meldingcategorie en de maatregel-omschrijving.

Dat is geen theoretisch geval. "N207 - Alphen" typt een beheerder met een
liggend streepje, en dan viel het hele meerjarenonderhoudsplan om.
"""
from datetime import datetime, timedelta

from database import SessionLocal
from models import Asset, Melding, Project

from .conftest import auth

# Alle tekens die de Nederlandse praktijk oplevert en die latin-1 niet aankan.
LASTIG = "–—‘’“”…→×·€"


def _project(org_id, maker_id, naam):
    db = SessionLocal()
    try:
        p = Project(name=naam, organization_id=org_id, status="active",
                    created_by=maker_id)
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id
    finally:
        db.close()


def _asset(org_id, maker_id, project_id=None, *, code, asset_type, score=3):
    db = SessionLocal()
    try:
        a = Asset(code=code, asset_type=asset_type,
                  organization_id=org_id, created_by=maker_id,
                  project_id=project_id, condition_score=score,
                  last_inspection_at=datetime.now() - timedelta(days=180))
        db.add(a)
        db.commit()
        db.refresh(a)
        return a.id
    finally:
        db.close()


def test_pdf_met_liggend_streepje_in_projectnaam(client, admin_user):
    """De melding uit de praktijk: een en-dash in de projectnaam."""
    pid = _project(admin_user.organization_id, admin_user.id, "N207 – Alphen")
    _asset(admin_user.organization_id, admin_user.id, pid,
           code="WGV-001", asset_type="wegvak")

    r = client.get("/api/mjop/export.pdf?years=10&project_id=" + pid,
                   headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:4] == b"%PDF"


def test_pdf_met_alle_lastige_tekens_in_klantdata(client, admin_user):
    """Asset-code, -type, categorie en projectnaam vol niet-latin-1-tekens."""
    pid = _project(admin_user.organization_id, admin_user.id,
                   "Onderhoud " + LASTIG + " 2026")
    _asset(admin_user.organization_id, admin_user.id, pid,
           code="BRG–001", asset_type="brug — beweegbaar", score=4)

    db = SessionLocal()
    try:
        db.add(Melding(title="Scheur – dek", organization_id=admin_user.organization_id,
                       project_id=pid, status="open", priority="hoog",
                       category="schade — constructief", created_by=admin_user.id))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/mjop/export.pdf?years=10&project_id=" + pid,
                   headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    assert len(r.content) > 1000


def test_pdf_organisatiebreed_met_lastige_tekens(client, admin_user):
    """Zonder project_id staat de organisatienaam op de omslag."""
    _asset(admin_user.organization_id, admin_user.id,
           code="LNT–042", asset_type="lantaarnpaal · LED")

    r = client.get("/api/mjop/export.pdf?years=5", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"


def test_pdf_blijft_werken_zonder_bijzondere_tekens(client, admin_user):
    """Controle dat de sanering niets gewoons stukmaakt."""
    pid = _project(admin_user.organization_id, admin_user.id, "Regulier project")
    _asset(admin_user.organization_id, admin_user.id, pid,
           code="PUT-100", asset_type="put")

    r = client.get("/api/mjop/export.pdf?years=10&project_id=" + pid,
                   headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
