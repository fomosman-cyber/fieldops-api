"""De nachtelijke controle op de abonnementen.

Twee omzetlekken die geen foutmelding gaven en daarom maandenlang onopgemerkt
konden blijven:

* niemand sloot ooit een organisatie af -- `paid_until` werd geschreven maar
  nergens gelezen, dus wie opzegde of nooit betaalde hield onbeperkt toegang;
* het aantal gebruikers liep niet mee, dus wie tien collega's toevoegde betaalde
  voor een.

Wat hier vastligt:

1. **Droogloop verandert niets.** Zonder `toepassen` is dit een rapportage.
2. **De platform-organisatie wordt nooit afgesloten.** Anders sluit de eigenaar
   zichzelf buiten en kan hij niets meer herstellen.
3. **Waarschuwen gebeurt hoogstens eens per termijn.** Elke nacht dezelfde mail
   is de snelste manier om te zorgen dat niemand je mails meer leest.
4. **Twee keer draaien verandert niets extra.**
"""

from datetime import datetime, timedelta


import billing_reconciliatie as br
import mollie
from database import SessionLocal
from models import AccountStatus, Organization

from .conftest import auth


def _zet(org_id, **velden):
    db = SessionLocal()
    try:
        o = db.query(Organization).filter(Organization.id == org_id).first()
        for k, v in velden.items():
            setattr(o, k, v)
        db.commit()
    finally:
        db.close()


def _org(org_id):
    db = SessionLocal()
    try:
        return db.query(Organization).filter(Organization.id == org_id).first()
    finally:
        db.close()


def _beoordeel(org_id):
    db = SessionLocal()
    try:
        o = db.query(Organization).filter(Organization.id == org_id).first()
        return br.beoordeel(db, o)
    finally:
        db.close()


def _draai(toepassen=False):
    db = SessionLocal()
    try:
        return br.reconcilieer(db, toepassen=toepassen)
    finally:
        db.close()


def _acties(beoordeling):
    return [a["actie"] for a in beoordeling["acties"]]


# ---------------------------------------------------------------------------
# Toegang
# ---------------------------------------------------------------------------

def test_verlopen_termijn_voorbij_de_coulance_wordt_afgesloten(client, org):
    _zet(org.id, status=AccountStatus.ACTIVE,
         paid_until=datetime.utcnow() - timedelta(days=br.COULANCE_DAGEN + 2))
    assert "verlopen" in _acties(_beoordeel(org.id))

    _draai(toepassen=True)
    assert _org(org.id).status == AccountStatus.EXPIRED


def test_binnen_de_coulance_blijft_de_toegang(client, org):
    """Zeven dagen speling: een mislukte incasso is vaak zo opgelost."""
    _zet(org.id, status=AccountStatus.ACTIVE,
         paid_until=datetime.utcnow() - timedelta(days=2))
    assert "verlopen" not in _acties(_beoordeel(org.id))

    _draai(toepassen=True)
    assert _org(org.id).status == AccountStatus.ACTIVE


def test_aflopende_termijn_levert_een_waarschuwing(client, org):
    _zet(org.id, status=AccountStatus.ACTIVE,
         paid_until=datetime.utcnow() + timedelta(days=1))
    assert _acties(_beoordeel(org.id)) == ["waarschuwen"]


def test_proefperiode_telt_ook_als_einddatum(client, org):
    """Een proef die afloopt zonder abonnement hoort ook dicht te gaan."""
    _zet(org.id, status=AccountStatus.TRIAL, paid_until=None,
         trial_ends_at=datetime.utcnow() - timedelta(days=br.COULANCE_DAGEN + 1))
    assert "verlopen" in _acties(_beoordeel(org.id))


def test_organisatie_zonder_abonnement_wordt_gesignaleerd_niet_afgesloten(
        client, org):
    """Dat kan een pilot zijn die bewust openstaat -- maar hij moet wel opvallen."""
    _zet(org.id, status=AccountStatus.ACTIVE, paid_until=None,
         trial_ends_at=None, mollie_subscription_id=None)
    acties = _acties(_beoordeel(org.id))
    assert acties == ["signaleren"]

    _draai(toepassen=True)
    assert _org(org.id).status == AccountStatus.ACTIVE


def test_handmatig_opgeschort_wordt_met_rust_gelaten(client, org):
    _zet(org.id, status=AccountStatus.SUSPENDED,
         paid_until=datetime.utcnow() - timedelta(days=100))
    b = _beoordeel(org.id)
    assert b["acties"] == []
    assert b["overgeslagen"] == "handmatig opgeschort"


def test_platform_organisatie_wordt_nooit_afgesloten(client, platform_owner):
    """Anders sluit de eigenaar zichzelf buiten en kan hij niets herstellen."""
    db = SessionLocal()
    try:
        eigen = db.query(Organization).filter(
            Organization.id == platform_owner.organization_id).first()
        eigen.paid_until = datetime.utcnow() - timedelta(days=365)
        eigen.status = AccountStatus.ACTIVE
        db.commit()
        b = br.beoordeel(db, eigen)
    finally:
        db.close()
    assert b["acties"] == []
    assert b["overgeslagen"] == "platform-organisatie"


# ---------------------------------------------------------------------------
# Droogloop en idempotentie
# ---------------------------------------------------------------------------

def test_droogloop_verandert_niets(client, org):
    _zet(org.id, status=AccountStatus.ACTIVE,
         paid_until=datetime.utcnow() - timedelta(days=30))
    uit = _draai(toepassen=False)
    assert uit["toegepast"] is False
    assert uit["uitgevoerd"] is None
    assert uit["organisaties_met_acties"] >= 1
    assert _org(org.id).status == AccountStatus.ACTIVE


def test_tweede_keer_draaien_doet_niets_extra(client, org):
    _zet(org.id, status=AccountStatus.ACTIVE,
         paid_until=datetime.utcnow() - timedelta(days=30))
    eerste = _draai(toepassen=True)
    tweede = _draai(toepassen=True)
    assert eerste["uitgevoerd"]["verlopen"] >= 1
    # Al verlopen, dus de tweede ronde ziet er niets meer te doen.
    assert tweede["uitgevoerd"]["verlopen"] == 0


def test_waarschuwing_gaat_hoogstens_eens_per_termijn(
        client, org, admin_user, monkeypatch):
    # admin_user is nodig: de waarschuwing gaat naar de beheerders van de org.
    verzonden = []
    import email_service
    monkeypatch.setattr(email_service, "send_incasso_mislukt",
                        lambda *a, **kw: verzonden.append(1) or True)
    _zet(org.id, status=AccountStatus.ACTIVE, billing_waarschuwing_op=None,
         paid_until=datetime.utcnow() + timedelta(days=1))

    _draai(toepassen=True)
    na_eerste = len(verzonden)
    _draai(toepassen=True)
    assert na_eerste >= 1
    assert len(verzonden) == na_eerste, "tweede nacht mag niet opnieuw mailen"


# ---------------------------------------------------------------------------
# Seats
# ---------------------------------------------------------------------------

def test_afwijkend_aantal_gebruikers_wordt_gesignaleerd(client, org, admin_user,
                                                        viewer_user):
    _zet(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
         billing_seats=1, status=AccountStatus.ACTIVE,
         paid_until=datetime.utcnow() + timedelta(days=20))
    b = _beoordeel(org.id)
    seat_actie = [a for a in b["acties"] if a["actie"] == "seats_bijwerken"]
    assert seat_actie, b["acties"]
    assert seat_actie[0]["van"] == 1 and seat_actie[0]["naar"] == 2


def test_seats_worden_bij_mollie_bijgewerkt(client, org, admin_user, viewer_user,
                                            monkeypatch):
    gewijzigd = {}
    monkeypatch.setattr(mollie, "wijzig_abonnement",
                        lambda cid, sid, **kw: gewijzigd.update(kw) or {"id": sid})
    monkeypatch.setenv("FIELDOPS_TARIEF_PER_GEBRUIKER", "9.00")
    _zet(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
         billing_seats=1, status=AccountStatus.ACTIVE,
         paid_until=datetime.utcnow() + timedelta(days=20))

    _draai(toepassen=True)
    # Twee gebruikers: 18,00 excl + 3,78 btw = 21,78 incl.
    assert gewijzigd["bedrag"] == "21.78"
    assert _org(org.id).billing_seats == 2


def test_mollie_onbereikbaar_laat_de_rest_doorgaan(client, org, monkeypatch):
    """Een storing bij Mollie mag de hele nachtelijke ronde niet stoppen."""
    def stuk(*a, **kw):
        raise RuntimeError("Mollie onbereikbaar")

    monkeypatch.setattr(mollie, "wijzig_abonnement", stuk)
    _zet(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
         billing_seats=99, status=AccountStatus.ACTIVE,
         paid_until=datetime.utcnow() + timedelta(days=20))

    uit = _draai(toepassen=True)
    assert uit["uitgevoerd"]["seats_bijgewerkt"] == 0
    assert _org(org.id).billing_seats == 99      # onveranderd, morgen weer


# ---------------------------------------------------------------------------
# Het endpoint
# ---------------------------------------------------------------------------

def test_alleen_de_eigenaar_mag_reconcilieren(client, admin_user):
    """Dit kan organisaties afsluiten; niet iets voor een klantbeheerder."""
    assert client.post("/api/billing/reconciliatie",
                       headers=auth(admin_user)).status_code == 403


def test_eigenaar_kan_droogloop_draaien(client, platform_owner):
    r = client.post("/api/billing/reconciliatie", headers=auth(platform_owner))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["toegepast"] is False
    assert "beoordelingen" in d


# ---------------------------------------------------------------------------
# Directe sync bij gebruikerswijzigingen
# ---------------------------------------------------------------------------

def test_gebruiker_deactiveren_trekt_het_bedrag_meteen_gelijk(
        client, org, admin_user, viewer_user, monkeypatch):
    gewijzigd = {}
    monkeypatch.setattr(mollie, "wijzig_abonnement",
                        lambda cid, sid, **kw: gewijzigd.update(kw) or {"id": sid})
    monkeypatch.setenv("FIELDOPS_TARIEF_PER_GEBRUIKER", "9.00")
    _zet(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
         billing_seats=2, status=AccountStatus.ACTIVE)

    r = client.delete(f"/api/users/{viewer_user.id}", headers=auth(admin_user))
    assert r.status_code in (200, 204), r.text
    # Nog een actieve gebruiker over: 9,00 + 1,89 = 10,89.
    assert gewijzigd.get("bedrag") == "10.89"


def test_mollie_storing_laat_de_gebruikersactie_slagen(
        client, org, admin_user, viewer_user, monkeypatch):
    """Iemand die een collega verwijdert hoort geen Mollie-fout te zien."""
    def stuk(*a, **kw):
        raise RuntimeError("Mollie onbereikbaar")

    monkeypatch.setattr(mollie, "wijzig_abonnement", stuk)
    _zet(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
         billing_seats=2, status=AccountStatus.ACTIVE)

    r = client.delete(f"/api/users/{viewer_user.id}", headers=auth(admin_user))
    assert r.status_code in (200, 204), r.text


def test_zonder_abonnement_wordt_er_niets_gesynct(client, org, admin_user,
                                                  viewer_user, monkeypatch):
    def mag_niet(*a, **kw):
        raise AssertionError("Mollie had niet aangeroepen mogen worden")

    monkeypatch.setattr(mollie, "wijzig_abonnement", mag_niet)
    _zet(org.id, mollie_subscription_id=None, billing_seats=None)
    r = client.delete(f"/api/users/{viewer_user.id}", headers=auth(admin_user))
    assert r.status_code in (200, 204)
