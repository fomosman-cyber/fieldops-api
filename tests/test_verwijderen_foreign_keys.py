"""Een project of asset verwijderen mocht niet vastlopen op een foreign key.

Postgres weigert de DELETE zodra er ook maar één rij naar de entiteit verwijst.
De gebruiker zag daar een 500 van, met "onbekende fout" in het scherm, en het
project bleef gewoon staan. In de praktijk raakte dat vrijwel elk actief
project: het werkdagboek vult zich dagelijks, en assets en inspecties hangen er
per definitie aan.

Deze tests hangen bewust élke bekende verwijzing aan het object voordat ze het
verwijderen. Komt er later een nieuwe foreign key bij zonder opruimregel, dan
valt dit hier om in plaats van bij een klant.
"""

from datetime import datetime, timezone

from database import SessionLocal
from models import (Asset, DaybookEntry, EmailInboxRoute, IncomingWebhook,
                    Inspection, Melding, Oplevering, OpleveringPunt,
                    Organization, Project)

from .conftest import auth


def _project(org_id, maker_id, naam="Testproject"):
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


def _asset(org_id, maker_id, code="AS-1", project_id=None):
    db = SessionLocal()
    try:
        a = Asset(code=code, name=code, asset_type="brug",
                  organization_id=org_id, project_id=project_id,
                  created_by=maker_id)
        db.add(a)
        db.commit()
        db.refresh(a)
        return a.id
    finally:
        db.close()


def _telling(model, kolom, waarde):
    db = SessionLocal()
    try:
        return db.query(model).filter(kolom == waarde).count()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

def test_project_met_alle_verwijzingen_is_verwijderbaar(client, admin_user, org):
    """Elke bekende verwijzing tegelijk — dit is het geval dat een 500 gaf."""
    project_id = _project(org.id, admin_user.id, "Vol project")
    asset_id = _asset(org.id, admin_user.id, "AS-VOL", project_id=project_id)

    db = SessionLocal()
    try:
        db.add(Melding(title="Melding", description="x", organization_id=org.id,
                       project_id=project_id, created_by=admin_user.id))
        db.add(Oplevering(title="Oplevering", organization_id=org.id,
                          project_id=project_id, created_by=admin_user.id))
        db.add(DaybookEntry(organization_id=org.id, project_id=project_id,
                            user_id=admin_user.id, entry_type="werk",
                            title="Uren", occurred_at=datetime.now(timezone.utc)))
        db.add(Inspection(organization_id=org.id, project_id=project_id,
                          asset_id=asset_id, kunstwerk_type="brug",
                          title="Inspectie", inspecteur_id=admin_user.id,
                          created_by=admin_user.id))
        db.add(EmailInboxRoute(organization_id=org.id, default_project_id=project_id,
                               token="tok-inbox-1", label="Inbox",
                               created_by=admin_user.id))
        db.add(IncomingWebhook(organization_id=org.id, default_project_id=project_id,
                               token="tok-hook-1", name="hook",
                               created_by=admin_user.id))
        # De verraderlijkste: de organisatie zelf wijst naar dit project.
        o = db.query(Organization).filter(Organization.id == org.id).first()
        o.public_meld_default_project_id = project_id
        db.commit()
    finally:
        db.close()

    r = client.delete(f"/api/projects/{project_id}?hard=true", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True

    db = SessionLocal()
    try:
        assert db.query(Project).filter(Project.id == project_id).first() is None
    finally:
        db.close()


def test_project_verwijderen_laat_de_historie_staan(client, admin_user, org):
    """De melding blijft bestaan voor de audit; alleen de koppeling verdwijnt."""
    project_id = _project(org.id, admin_user.id, "Historie")
    db = SessionLocal()
    try:
        m = Melding(title="Blijft bestaan", description="x", organization_id=org.id,
                    project_id=project_id, created_by=admin_user.id)
        db.add(m)
        db.commit()
        melding_id = m.id
    finally:
        db.close()

    r = client.delete(f"/api/projects/{project_id}?hard=true", headers=auth(admin_user))
    assert r.status_code == 200

    db = SessionLocal()
    try:
        m = db.query(Melding).filter(Melding.id == melding_id).first()
        assert m is not None, "de melding mag niet mee verwijderd worden"
        assert m.project_id is None
    finally:
        db.close()


def test_project_verwijderen_laat_de_assets_staan(client, admin_user, org):
    """Assets horen bij de organisatie, niet bij het project."""
    project_id = _project(org.id, admin_user.id, "Met assets")
    asset_id = _asset(org.id, admin_user.id, "AS-BLIJFT", project_id=project_id)

    r = client.delete(f"/api/projects/{project_id}?hard=true", headers=auth(admin_user))
    assert r.status_code == 200

    db = SessionLocal()
    try:
        a = db.query(Asset).filter(Asset.id == asset_id).first()
        assert a is not None
        assert a.project_id is None
    finally:
        db.close()


def test_burgerportaal_standaard_wordt_losgemaakt(client, admin_user, org):
    """Zonder deze opruiming blokkeert de organisatie-rij zelf de verwijdering."""
    project_id = _project(org.id, admin_user.id, "Meldpunt-standaard")
    db = SessionLocal()
    try:
        o = db.query(Organization).filter(Organization.id == org.id).first()
        o.public_meld_default_project_id = project_id
        db.commit()
    finally:
        db.close()

    r = client.delete(f"/api/projects/{project_id}?hard=true", headers=auth(admin_user))
    assert r.status_code == 200

    db = SessionLocal()
    try:
        o = db.query(Organization).filter(Organization.id == org.id).first()
        assert o.public_meld_default_project_id is None
    finally:
        db.close()


def test_archiveren_blijft_de_standaard(client, admin_user, org):
    """Zonder ?hard=true wordt er niets weggegooid."""
    project_id = _project(org.id, admin_user.id, "Archiveer mij")
    r = client.delete(f"/api/projects/{project_id}", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["deleted"] is False

    db = SessionLocal()
    try:
        p = db.query(Project).filter(Project.id == project_id).first()
        assert p is not None
        assert p.status == "archived"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Asset
# ---------------------------------------------------------------------------

def test_asset_met_inspectie_is_verwijderbaar(client, admin_user, org):
    """Inspection.asset_id is NOT NULL — elke ooit geïnspecteerde asset was
    hierdoor onverwijderbaar."""
    asset_id = _asset(org.id, admin_user.id, "AS-INSP")
    db = SessionLocal()
    try:
        db.add(Inspection(organization_id=org.id, asset_id=asset_id,
                          kunstwerk_type="brug", title="Inspectie",
                          inspecteur_id=admin_user.id, created_by=admin_user.id))
        db.commit()
    finally:
        db.close()

    r = client.delete(f"/api/assets/{asset_id}?hard=true", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["verwijderd"]["inspecties"] == 1
    assert _telling(Inspection, Inspection.asset_id, asset_id) == 0


def test_asset_met_opleverpunt_en_webhook_is_verwijderbaar(client, admin_user, org):
    asset_id = _asset(org.id, admin_user.id, "AS-PUNT")
    db = SessionLocal()
    try:
        opl = Oplevering(title="Opl", organization_id=org.id, created_by=admin_user.id)
        db.add(opl)
        db.commit()
        db.refresh(opl)
        db.add(OpleveringPunt(oplevering_id=opl.id, asset_id=asset_id,
                              organization_id=org.id, code="OP-001",
                              omschrijving="punt"))
        db.add(IncomingWebhook(organization_id=org.id, default_asset_id=asset_id,
                               token="tok-hook-2", name="hook-asset",
                               created_by=admin_user.id))
        db.commit()
    finally:
        db.close()

    r = client.delete(f"/api/assets/{asset_id}?hard=true", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert _telling(OpleveringPunt, OpleveringPunt.asset_id, asset_id) == 0
    assert _telling(IncomingWebhook, IncomingWebhook.default_asset_id, asset_id) == 0


def test_asset_verwijderen_laat_melding_staan(client, admin_user, org):
    asset_id = _asset(org.id, admin_user.id, "AS-MELD")
    db = SessionLocal()
    try:
        m = Melding(title="Bij asset", description="x", organization_id=org.id,
                    asset_id=asset_id, created_by=admin_user.id)
        db.add(m)
        db.commit()
        melding_id = m.id
    finally:
        db.close()

    r = client.delete(f"/api/assets/{asset_id}?hard=true", headers=auth(admin_user))
    assert r.status_code == 200

    db = SessionLocal()
    try:
        m = db.query(Melding).filter(Melding.id == melding_id).first()
        assert m is not None
        assert m.asset_id is None
    finally:
        db.close()


def test_kind_asset_blijft_bestaan(client, admin_user, org):
    ouder_id = _asset(org.id, admin_user.id, "AS-OUDER")
    db = SessionLocal()
    try:
        kind = Asset(code="AS-KIND", name="kind", asset_type="brug",
                     organization_id=org.id, parent_asset_id=ouder_id,
                     created_by=admin_user.id)
        db.add(kind)
        db.commit()
        kind_id = kind.id
    finally:
        db.close()

    r = client.delete(f"/api/assets/{ouder_id}?hard=true", headers=auth(admin_user))
    assert r.status_code == 200

    db = SessionLocal()
    try:
        k = db.query(Asset).filter(Asset.id == kind_id).first()
        assert k is not None
        assert k.parent_asset_id is None
    finally:
        db.close()
