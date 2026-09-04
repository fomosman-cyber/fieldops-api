"""De status van een organisatie geldt bij élke aanvraag, niet alleen bij inloggen.

Voorheen werd alleen bij het inloggen gekeken of de organisatie geschorst of
verlopen was. Wie op het moment van schorsing al was ingelogd, hield tot 24 uur
volledige toegang — inclusief exports — omdat het uitgegeven token gewoon bleef
werken. Bij een abonnement waarvan de betaling stopt is dat een gat dat direct
geld kost.
"""

from database import SessionLocal
from models import Organization, AccountStatus

from .conftest import auth

# Een willekeurig endpoint achter get_current_user; de controle zit in de
# dependency, dus welk endpoint je kiest maakt niet uit.
BESCHERMD = "/api/projects/"


def _zet_status(org_id, status):
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        org.status = status
        db.commit()
    finally:
        db.close()


def test_actieve_organisatie_komt_binnen(client, admin_user):
    _zet_status(admin_user.organization_id, AccountStatus.ACTIVE)
    assert client.get(BESCHERMD, headers=auth(admin_user)).status_code == 200


def test_proefperiode_komt_binnen(client, admin_user):
    """TRIAL is een geldige toestand — die mag niet buitengesloten worden."""
    _zet_status(admin_user.organization_id, AccountStatus.TRIAL)
    assert client.get(BESCHERMD, headers=auth(admin_user)).status_code == 200


def test_geschorste_organisatie_wordt_geweigerd(client, admin_user):
    _zet_status(admin_user.organization_id, AccountStatus.SUSPENDED)
    r = client.get(BESCHERMD, headers=auth(admin_user))
    assert r.status_code == 403
    assert "opgeschort" in r.json()["detail"].lower()


def test_verlopen_organisatie_wordt_geweigerd(client, admin_user):
    _zet_status(admin_user.organization_id, AccountStatus.EXPIRED)
    r = client.get(BESCHERMD, headers=auth(admin_user))
    assert r.status_code == 403
    assert "abonnement" in r.json()["detail"].lower()


def test_lopend_token_verliest_toegang_zodra_de_org_geschorst_wordt(client, admin_user):
    """De kern van deze wijziging: het token blijft technisch geldig, maar de
    toegang stopt onmiddellijk in plaats van pas als het token verloopt."""
    kop = auth(admin_user)
    assert client.get(BESCHERMD, headers=kop).status_code == 200

    _zet_status(admin_user.organization_id, AccountStatus.SUSPENDED)

    assert client.get(BESCHERMD, headers=kop).status_code == 403


def test_weer_activeren_geeft_direct_toegang_terug(client, admin_user):
    """Na betaling moet de klant meteen verder kunnen, zonder opnieuw inloggen."""
    kop = auth(admin_user)
    _zet_status(admin_user.organization_id, AccountStatus.SUSPENDED)
    assert client.get(BESCHERMD, headers=kop).status_code == 403

    _zet_status(admin_user.organization_id, AccountStatus.ACTIVE)
    assert client.get(BESCHERMD, headers=kop).status_code == 200


def test_platform_eigenaar_sluit_zichzelf_niet_buiten(client, platform_owner):
    """Zonder deze uitzondering kan een verkeerde statuswijziging op de eigen
    organisatie de eigenaar permanent buitensluiten — en dan is er niemand meer
    die het kan herstellen."""
    _zet_status(platform_owner.organization_id, AccountStatus.SUSPENDED)
    assert client.get(BESCHERMD, headers=auth(platform_owner)).status_code == 200

    _zet_status(platform_owner.organization_id, AccountStatus.EXPIRED)
    assert client.get(BESCHERMD, headers=auth(platform_owner)).status_code == 200
