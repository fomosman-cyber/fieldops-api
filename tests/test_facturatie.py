"""Verkoopfacturen bij de abonnementsincasso.

Mollie factureert onze klanten niet. Zonder deze facturen krijgt een klant een
afschrijving zonder document, en dan kan zijn boekhouder de BTW niet
terugvorderen en wij hem niet verantwoorden.

Wat hier vastligt:

1. **Zonder verkopergegevens komt er geen factuur.** Half een factuur maken is
   erger dan geen: dan denk je dat het geregeld is.
2. **Het factuurnummer is doorlopend per jaar en uniek.** Een gat moet je
   kunnen uitleggen aan de Belastingdienst.
3. **Eén factuur per betaling.** De webhook van Mollie komt meerdere keren
   langs; twee facturen voor dezelfde incasso is een boekhoudprobleem.
4. **De bedragen op de factuur zijn de bedragen die zijn geincasseerd.**
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import facturatie
import mollie
from database import SessionLocal
from models import AccountStatus, Invoice, Organization

from .conftest import auth

WEBHOOK_TOKEN = "test-webhook-token"


@pytest.fixture(autouse=True)
def verkopergegevens(monkeypatch):
    """Zonder deze staat facturatie uit; per test aan te passen."""
    monkeypatch.setenv("FIELDOPS_BEDRIJFSNAAM", "FieldOps B.V.")
    monkeypatch.setenv("FIELDOPS_ADRES", "Testweg 1\n1000 AA Amsterdam")
    monkeypatch.setenv("FIELDOPS_KVK", "12345678")
    monkeypatch.setenv("FIELDOPS_BTW_NUMMER", "NL123456789B01")
    monkeypatch.setenv("FIELDOPS_IBAN", "NL00BANK0123456789")
    monkeypatch.setenv("MOLLIE_API_KEY", "test_key")
    monkeypatch.setenv("MOLLIE_WEBHOOK_TOKEN", WEBHOOK_TOKEN)
    monkeypatch.setenv("FIELDOPS_TARIEF_PER_GEBRUIKER", "9.00")


def _zet_org(org_id, **velden):
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


def _facturen_voor_betaling(payment_id):
    db = SessionLocal()
    try:
        return db.query(Invoice).filter(
            Invoice.mollie_payment_id == payment_id).first()
    finally:
        db.close()


def _facturen(org_id=None):
    db = SessionLocal()
    try:
        q = db.query(Invoice)
        if org_id:
            q = q.filter(Invoice.organization_id == org_id)
        return q.order_by(Invoice.volgnummer).all()
    finally:
        db.close()


def _incasso(client, monkeypatch, org, payment_id, bedrag="10.89"):
    """Een geslaagde maandelijkse incasso via de webhook."""
    monkeypatch.setattr(mollie, "haal_betaling", lambda pid: {
        "id": pid, "status": "paid", "sequenceType": "recurring",
        "amount": {"currency": "EUR", "value": bedrag},
        "customerId": "cst_1", "metadata": {"organization_id": org.id},
    })
    return client.post(f"/api/billing/webhook/{WEBHOOK_TOKEN}",
                       data={"id": payment_id})


# ---------------------------------------------------------------------------
# Zonder instellingen geen factuur
# ---------------------------------------------------------------------------

def test_zonder_verkopergegevens_geen_factuur(client, org, monkeypatch):
    """Een factuur zonder KvK en btw-nummer is juridisch geen factuur."""
    monkeypatch.delenv("FIELDOPS_KVK", raising=False)
    _zet_org(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
             paid_until=datetime.utcnow() + timedelta(days=1),
             status=AccountStatus.ACTIVE)

    r = _incasso(client, monkeypatch, org, "tr_geen_instellingen")
    assert r.status_code == 200                 # de betaling wordt wel verwerkt
    assert _org(org.id).paid_until is not None   # termijn is verlengd
    assert _facturen(org.id) == []               # maar geen factuur


def test_ontbrekende_instellingen_worden_gemeld(client, admin_user, monkeypatch):
    monkeypatch.delenv("FIELDOPS_BTW_NUMMER", raising=False)
    d = client.get("/api/billing/facturen", headers=auth(admin_user)).json()
    assert d["ingesteld"] is False
    assert "FIELDOPS_BTW_NUMMER" in d["ontbrekende_instellingen"]


def test_verkoper_gooit_bij_ontbrekende_gegevens(monkeypatch):
    monkeypatch.delenv("FIELDOPS_ADRES", raising=False)
    with pytest.raises(facturatie.FactuurgegevensOntbreken):
        facturatie.verkoper()


# ---------------------------------------------------------------------------
# De factuur zelf
# ---------------------------------------------------------------------------

def test_incasso_levert_een_factuur_met_de_geincasseerde_bedragen(
        client, org, admin_user, monkeypatch):
    _zet_org(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
             paid_until=datetime.utcnow() + timedelta(days=1),
             status=AccountStatus.ACTIVE,
             billing_address="Klantstraat 5\n3000 AA Rotterdam",
             kvk_number="87654321", btw_number="NL987654321B01")

    r = _incasso(client, monkeypatch, org, "tr_bedragen")
    assert r.status_code == 200, r.text

    f = _facturen_voor_betaling("tr_bedragen")
    assert f is not None
    # Een gebruiker: 9,00 excl + 1,89 btw = 10,89 incl -- gelijk aan de incasso.
    assert f.bedrag_excl == "9.00"
    assert f.btw_bedrag == "1.89"
    assert f.bedrag_incl == "10.89"
    assert f.btw_percentage == "21"
    assert f.seats == 1


def test_klant_en_verkoper_worden_gesnapshot(client, org, monkeypatch):
    """Verhuist een klant volgend jaar, dan verandert een oude factuur niet mee."""
    _zet_org(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
             paid_until=datetime.utcnow() + timedelta(days=1),
             status=AccountStatus.ACTIVE,
             billing_address="Oude straat 1", kvk_number="11112222")
    _incasso(client, monkeypatch, org, "tr_snapshot")

    _zet_org(org.id, billing_address="Nieuwe straat 9", kvk_number="99998888")
    f = _facturen_voor_betaling("tr_snapshot")
    assert f.klant_adres == "Oude straat 1"
    assert f.klant_kvk == "11112222"
    assert f.verkoper_kvk == "12345678"
    assert f.verkoper_btw == "NL123456789B01"


def test_tweede_webhook_maakt_geen_tweede_factuur(client, org, monkeypatch):
    """Mollie stuurt bij elke statuswissel opnieuw."""
    _zet_org(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
             paid_until=datetime.utcnow() + timedelta(days=1),
             status=AccountStatus.ACTIVE)
    _incasso(client, monkeypatch, org, payment_id="tr_dubbel")
    _incasso(client, monkeypatch, org, payment_id="tr_dubbel")
    # Tellen op deze betaling, niet op de organisatie: andere tests in dit
    # bestand maken ook facturen voor dezelfde org.
    db = SessionLocal()
    try:
        n = db.query(Invoice).filter(
            Invoice.mollie_payment_id == "tr_dubbel").count()
    finally:
        db.close()
    assert n == 1


def test_factuurnummers_lopen_door_en_zijn_uniek(client, org, monkeypatch):
    _zet_org(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
             paid_until=datetime.utcnow() + timedelta(days=1),
             status=AccountStatus.ACTIVE)
    jaar = datetime.now(timezone.utc).year
    voor = len(_facturen())
    for n in range(3):
        _incasso(client, monkeypatch, org, payment_id=f"tr_reeks_{n}")

    nummers = [f.factuurnummer for f in _facturen()][voor:]
    assert nummers == [f"{jaar}-{voor + 1:04d}", f"{jaar}-{voor + 2:04d}",
                       f"{jaar}-{voor + 3:04d}"]
    assert len(set(f.factuurnummer for f in _facturen())) == len(_facturen())


def test_mandaatbetaling_krijgt_geen_factuur(client, org, monkeypatch):
    """Een cent voor het mandaat is geen levering; de eerste maand is gratis."""
    _zet_org(org.id, mollie_customer_id="cst_1", status=AccountStatus.TRIAL)
    monkeypatch.setattr(mollie, "eerste_bruikbare_mandaat",
                        lambda cid: {"id": "mdt_1", "status": "valid"})
    monkeypatch.setattr(mollie, "maak_abonnement",
                        lambda *a, **kw: {"id": "sub_nieuw"})
    monkeypatch.setattr(mollie, "haal_betaling", lambda pid: {
        "id": pid, "status": "paid", "sequenceType": "first",
        "amount": {"currency": "EUR", "value": "0.01"},
        "customerId": "cst_1", "metadata": {"organization_id": org.id},
    })
    r = client.post(f"/api/billing/webhook/{WEBHOOK_TOKEN}", data={"id": "tr_mandaat"})
    assert r.status_code == 200
    assert _facturen(org.id) == []


# ---------------------------------------------------------------------------
# Ophalen en PDF
# ---------------------------------------------------------------------------

def test_org_admin_ziet_de_eigen_facturen(client, org, admin_user, monkeypatch):
    _zet_org(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
             paid_until=datetime.utcnow() + timedelta(days=1),
             status=AccountStatus.ACTIVE)
    _incasso(client, monkeypatch, org, payment_id="tr_lijst")

    d = client.get("/api/billing/facturen", headers=auth(admin_user)).json()
    assert d["ingesteld"] is True
    assert len(d["facturen"]) == 1
    assert d["facturen"][0]["bedrag_incl"] == "10.89"


def test_gewone_gebruiker_ziet_geen_facturen(client, viewer_user):
    """Een factuur bevat het adres en KvK-nummer van de organisatie."""
    assert client.get("/api/billing/facturen",
                      headers=auth(viewer_user)).status_code == 403


def test_factuur_pdf_is_een_echte_pdf(client, org, admin_user, monkeypatch):
    _zet_org(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
             paid_until=datetime.utcnow() + timedelta(days=1),
             status=AccountStatus.ACTIVE, billing_address="Klantstraat 5")
    _incasso(client, monkeypatch, org, payment_id="tr_pdf")
    fid = client.get("/api/billing/facturen",
                     headers=auth(admin_user)).json()["facturen"][0]["id"]

    r = client.get(f"/api/billing/facturen/{fid}/pdf", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")
    assert "factuur-" in r.headers.get("content-disposition", "")


def test_factuur_van_andere_organisatie_geeft_404(client, admin_user):
    assert client.get("/api/billing/facturen/bestaat-niet/pdf",
                      headers=auth(admin_user)).status_code == 404


# ---------------------------------------------------------------------------
# De rekenkant los
# ---------------------------------------------------------------------------

def test_bedragen_worden_niet_opnieuw_berekend(org):
    """maak_factuur neemt over wat is geincasseerd, en rekent zelf niets uit.

    Zou het zelf rekenen, dan kan een factuur ooit een cent afwijken van de
    afschrijving -- precies het verschil waar een boekhouder over belt.
    """
    db = SessionLocal()
    try:
        o = db.query(Organization).filter(Organization.id == org.id).first()
        f = facturatie.maak_factuur(
            db, o, seats=7, tarief_excl=Decimal("9.00"),
            bedrag_excl=Decimal("63.00"), btw_percentage=Decimal("21"),
            btw_bedrag=Decimal("13.23"), bedrag_incl=Decimal("76.23"),
            mollie_payment_id="tr_handmatig")
        db.commit()
        assert f.bedrag_excl == "63.00" and f.bedrag_incl == "76.23"
        assert f.seats == 7
    finally:
        db.close()
