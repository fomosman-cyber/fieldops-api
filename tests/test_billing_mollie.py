"""Tests voor de Mollie-abonnementen.

Mollie zelf wordt nergens echt aangeroepen: elke test vervangt de functies in
``mollie`` door een stub. Wat we hier borgen is het gedrag dat geld kost als het
misgaat — dubbele verwerking, een klant die niet meer kan betalen, of een
webhook die zichzelf eindeloos laat herhalen.
"""

from datetime import datetime, timedelta

import pytest

import mollie
from database import SessionLocal
from models import AccountStatus, Organization, Payment

from .conftest import auth  # noqa: F401 — fixture-import

WEBHOOK_TOKEN = "test-webhook-token"


@pytest.fixture(autouse=True)
def mollie_omgeving(monkeypatch):
    monkeypatch.setenv("MOLLIE_API_KEY", "test_abc123")
    monkeypatch.setenv("MOLLIE_WEBHOOK_TOKEN", WEBHOOK_TOKEN)
    monkeypatch.setenv("FIELDOPS_TARIEF_PER_GEBRUIKER", "9.00")
    yield


def _org_uit_db(org_id):
    db = SessionLocal()
    try:
        return db.query(Organization).filter(Organization.id == org_id).first()
    finally:
        db.close()


def _zet_org(org_id, **velden):
    db = SessionLocal()
    try:
        o = db.query(Organization).filter(Organization.id == org_id).first()
        for k, v in velden.items():
            setattr(o, k, v)
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Status en tarief
# ---------------------------------------------------------------------------

def test_status_rekent_maandbedrag_uit_actieve_gebruikers(client, admin_user):
    r = client.get("/api/billing/status", headers=auth(admin_user))
    assert r.status_code == 200
    data = r.json()
    assert data["tarief_per_gebruiker"] == "9.00"
    # één actieve gebruiker in deze organisatie
    assert data["actieve_gebruikers"] == 1
    assert data["maandbedrag"] == "9.00"
    assert data["heeft_abonnement"] is False


def test_maandbedrag_schaalt_met_gebruikers(client, admin_user, viewer_user):
    r = client.get("/api/billing/status", headers=auth(admin_user))
    data = r.json()
    assert data["actieve_gebruikers"] == 2
    assert data["maandbedrag"] == "18.00"


def test_gewone_gebruiker_mag_de_status_niet_zien(client, viewer_user):
    r = client.get("/api/billing/status", headers=auth(viewer_user))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Starten
# ---------------------------------------------------------------------------

def test_start_zonder_sleutel_geeft_503(client, admin_user, monkeypatch):
    monkeypatch.delenv("MOLLIE_API_KEY", raising=False)
    r = client.post("/api/billing/start", headers=auth(admin_user))
    assert r.status_code == 503


def test_start_zonder_webhooktoken_geeft_503(client, admin_user, monkeypatch):
    monkeypatch.delenv("MOLLIE_WEBHOOK_TOKEN", raising=False)
    r = client.post("/api/billing/start", headers=auth(admin_user))
    assert r.status_code == 503


def test_start_maakt_klant_en_eerste_betaling_van_een_cent(
        client, admin_user, org, monkeypatch):
    gezien = {}

    def nep_customer(naam, email, metadata=None):
        gezien["customer"] = {"naam": naam, "email": email, "metadata": metadata}
        return {"id": "cst_1"}

    def nep_betaling(customer_id, **kw):
        gezien["betaling"] = {"customer_id": customer_id, **kw}
        return {
            "id": "tr_1",
            "status": "open",
            "sequenceType": "first",
            "amount": {"currency": "EUR", "value": kw["bedrag"]},
            "customerId": customer_id,
            "_links": {"checkout": {"href": "https://www.mollie.com/checkout/1"}},
        }

    monkeypatch.setattr(mollie, "maak_customer", nep_customer)
    monkeypatch.setattr(mollie, "maak_eerste_betaling", nep_betaling)

    r = client.post("/api/billing/start", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["checkout_url"] == "https://www.mollie.com/checkout/1"

    # Het mandaat kost een cent, niet de eerste maand.
    assert gezien["betaling"]["bedrag"] == "0.01"
    assert gezien["customer"]["metadata"]["organization_id"] == org.id

    ververst = _org_uit_db(org.id)
    assert ververst.mollie_customer_id == "cst_1"
    assert ververst.billing_status == "pending"


def test_start_weigert_als_er_al_een_abonnement_loopt(client, admin_user, org):
    _zet_org(org.id, mollie_subscription_id="sub_1")
    r = client.post("/api/billing/start", headers=auth(admin_user))
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Webhook — herkomst
# ---------------------------------------------------------------------------

def test_webhook_met_verkeerd_token_geeft_401(client):
    r = client.post("/api/billing/webhook/fout-token", data={"id": "tr_1"})
    assert r.status_code == 401


def test_webhook_zonder_geconfigureerd_token_geeft_503(client, monkeypatch):
    monkeypatch.delenv("MOLLIE_WEBHOOK_TOKEN", raising=False)
    r = client.post(f"/api/billing/webhook/{WEBHOOK_TOKEN}", data={"id": "tr_1"})
    assert r.status_code == 503


def test_webhook_negeert_betaling_van_onbekende_organisatie(
        client, monkeypatch):
    monkeypatch.setattr(mollie, "haal_betaling", lambda pid: {
        "id": pid, "status": "paid", "sequenceType": "first",
        "amount": {"currency": "EUR", "value": "0.01"},
        "customerId": "cst_onbekend", "metadata": {},
    })
    r = client.post(f"/api/billing/webhook/{WEBHOOK_TOKEN}", data={"id": "tr_x"})
    assert r.status_code == 200
    assert r.json()["status"] == "genegeerd"


def test_webhook_geeft_200_bij_interne_fout(client, org, monkeypatch):
    """Een 500 zou Mollie ruim een dag lang laten herhalen."""
    _zet_org(org.id, mollie_customer_id="cst_1")

    monkeypatch.setattr(mollie, "haal_betaling", lambda pid: {
        "id": pid, "status": "paid", "sequenceType": "first",
        "amount": {"currency": "EUR", "value": "0.01"},
        "customerId": "cst_1", "metadata": {"organization_id": org.id},
    })

    def klapt(*a, **kw):
        raise RuntimeError("stuk")

    monkeypatch.setattr(mollie, "eerste_bruikbare_mandaat", klapt)
    monkeypatch.setattr(mollie, "maak_abonnement", klapt)

    r = client.post(f"/api/billing/webhook/{WEBHOOK_TOKEN}", data={"id": "tr_2"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Webhook — eerste betaling
# ---------------------------------------------------------------------------

def _stub_eerste_betaling(monkeypatch, org, *, gemaakt: dict):
    monkeypatch.setattr(mollie, "haal_betaling", lambda pid: {
        "id": pid, "status": "paid", "sequenceType": "first",
        "amount": {"currency": "EUR", "value": "0.01"},
        "customerId": "cst_1", "metadata": {"organization_id": org.id},
        "paidAt": "2026-09-05T10:00:00+00:00",
    })
    monkeypatch.setattr(mollie, "eerste_bruikbare_mandaat",
                        lambda cid: {"id": "mdt_1", "status": "valid"})

    def nep_abonnement(customer_id, **kw):
        gemaakt.update({"customer_id": customer_id, **kw})
        return {"id": "sub_1", "status": "active"}

    monkeypatch.setattr(mollie, "maak_abonnement", nep_abonnement)


def test_eerste_betaling_start_abonnement_en_activeert_organisatie(
        client, admin_user, org, monkeypatch):
    _zet_org(org.id, mollie_customer_id="cst_1", status=AccountStatus.TRIAL)
    gemaakt = {}
    _stub_eerste_betaling(monkeypatch, org, gemaakt=gemaakt)

    r = client.post(f"/api/billing/webhook/{WEBHOOK_TOKEN}", data={"id": "tr_3"})
    assert r.status_code == 200
    assert r.json()["status"] == "abonnement gestart"

    # Het abonnement rekent het aantal gebruikers, niet een vast pakket.
    assert gemaakt["bedrag"] == "9.00"
    assert gemaakt["interval"] == "1 month"
    assert gemaakt["mandate_id"] == "mdt_1"

    ververst = _org_uit_db(org.id)
    assert ververst.mollie_subscription_id == "sub_1"
    assert ververst.mollie_mandate_id == "mdt_1"
    assert ververst.billing_status == "active"
    assert ververst.status == AccountStatus.ACTIVE
    assert ververst.paid_until is not None


def test_eerste_maand_is_gratis(client, admin_user, org, monkeypatch):
    """De eerste incasso staat een maand vooruit; zo klopt de belofte op de site."""
    _zet_org(org.id, mollie_customer_id="cst_1")
    gemaakt = {}
    _stub_eerste_betaling(monkeypatch, org, gemaakt=gemaakt)

    client.post(f"/api/billing/webhook/{WEBHOOK_TOKEN}", data={"id": "tr_4"})

    start = datetime.fromisoformat(gemaakt["start_datum"])
    vandaag = datetime.utcnow().date()
    assert (start.date() - vandaag).days >= 28


def test_webhook_verwerkt_dezelfde_betaling_niet_twee_keer(
        client, admin_user, org, monkeypatch):
    """Mollie stuurt bij elke statuswissel opnieuw; twee keer verwerken zou
    een tweede abonnement opleveren."""
    _zet_org(org.id, mollie_customer_id="cst_1")
    gemaakt = {}
    _stub_eerste_betaling(monkeypatch, org, gemaakt=gemaakt)

    aantal = {"n": 0}
    origineel = mollie.maak_abonnement

    def tel_mee(customer_id, **kw):
        aantal["n"] += 1
        return origineel(customer_id, **kw)

    monkeypatch.setattr(mollie, "maak_abonnement", tel_mee)

    client.post(f"/api/billing/webhook/{WEBHOOK_TOKEN}", data={"id": "tr_5"})
    tweede = client.post(f"/api/billing/webhook/{WEBHOOK_TOKEN}", data={"id": "tr_5"})

    assert tweede.status_code == 200
    assert tweede.json()["status"] == "al verwerkt"
    assert aantal["n"] == 1


# ---------------------------------------------------------------------------
# Webhook — maandelijkse incasso
# ---------------------------------------------------------------------------

def test_gelukte_incasso_verlengt_de_termijn(client, org, monkeypatch):
    eerder = datetime.utcnow() + timedelta(days=2)
    _zet_org(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
             paid_until=eerder)

    monkeypatch.setattr(mollie, "haal_betaling", lambda pid: {
        "id": pid, "status": "paid", "sequenceType": "recurring",
        "amount": {"currency": "EUR", "value": "9.00"},
        "customerId": "cst_1", "subscriptionId": "sub_1",
        "metadata": {"organization_id": org.id},
    })

    r = client.post(f"/api/billing/webhook/{WEBHOOK_TOKEN}", data={"id": "tr_6"})
    assert r.status_code == 200
    ververst = _org_uit_db(org.id)
    assert ververst.paid_until > eerder
    assert ververst.billing_status == "active"


def test_mislukte_incasso_sluit_niet_meteen_af(client, org, monkeypatch):
    """Toegang blijft tot de betaalde termijn plus coulance verstreken is."""
    _zet_org(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
             paid_until=datetime.utcnow() + timedelta(days=10),
             status=AccountStatus.ACTIVE)

    monkeypatch.setattr(mollie, "haal_betaling", lambda pid: {
        "id": pid, "status": "failed", "sequenceType": "recurring",
        "amount": {"currency": "EUR", "value": "9.00"},
        "customerId": "cst_1", "metadata": {"organization_id": org.id},
    })

    r = client.post(f"/api/billing/webhook/{WEBHOOK_TOKEN}", data={"id": "tr_7"})
    assert r.status_code == 200
    ververst = _org_uit_db(org.id)
    assert ververst.billing_status == "past_due"
    assert ververst.status == AccountStatus.ACTIVE


def test_mislukte_incasso_na_verlopen_termijn_zet_op_verlopen(
        client, org, monkeypatch):
    _zet_org(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
             paid_until=datetime.utcnow() - timedelta(days=30),
             status=AccountStatus.ACTIVE)

    monkeypatch.setattr(mollie, "haal_betaling", lambda pid: {
        "id": pid, "status": "failed", "sequenceType": "recurring",
        "amount": {"currency": "EUR", "value": "9.00"},
        "customerId": "cst_1", "metadata": {"organization_id": org.id},
    })

    client.post(f"/api/billing/webhook/{WEBHOOK_TOKEN}", data={"id": "tr_8"})
    assert _org_uit_db(org.id).status == AccountStatus.EXPIRED


# ---------------------------------------------------------------------------
# Een verlopen klant moet kunnen betalen
# ---------------------------------------------------------------------------

def test_verlopen_organisatie_kan_nog_bij_de_betaalroutes(
        client, admin_user, org):
    """Zonder deze uitzondering sluit je de klant op: geen toegang, dus ook
    geen mogelijkheid om te betalen."""
    _zet_org(org.id, status=AccountStatus.EXPIRED)

    geblokkeerd = client.get("/api/projects", headers=auth(admin_user))
    assert geblokkeerd.status_code == 403

    betaalpagina = client.get("/api/billing/status", headers=auth(admin_user))
    assert betaalpagina.status_code == 200


def test_opgeschorte_organisatie_blijft_overal_buiten(client, admin_user, org):
    """Schorsen is een bewuste maatregel; die mag je niet zelf afkopen."""
    _zet_org(org.id, status=AccountStatus.SUSPENDED)
    r = client.get("/api/billing/status", headers=auth(admin_user))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Beheer
# ---------------------------------------------------------------------------

def test_seats_sync_past_het_bedrag_aan(client, admin_user, viewer_user, org,
                                        monkeypatch):
    _zet_org(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
             billing_seats=1)
    gewijzigd = {}

    def nep_wijzig(customer_id, subscription_id, **kw):
        gewijzigd.update(kw)
        return {"id": subscription_id}

    monkeypatch.setattr(mollie, "wijzig_abonnement", nep_wijzig)

    r = client.post("/api/billing/seats/sync", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["actieve_gebruikers"] == 2
    assert gewijzigd["bedrag"] == "18.00"
    assert _org_uit_db(org.id).billing_seats == 2


def test_seats_sync_doet_niets_als_er_niets_verandert(
        client, admin_user, org, monkeypatch):
    _zet_org(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
             billing_seats=1)

    def mag_niet(*a, **kw):
        raise AssertionError("Mollie had niet aangeroepen mogen worden")

    monkeypatch.setattr(mollie, "wijzig_abonnement", mag_niet)

    r = client.post("/api/billing/seats/sync", headers=auth(admin_user))
    assert r.json()["status"] == "ongewijzigd"


def test_opzeggen_laat_toegang_tot_einde_termijn(client, admin_user, org,
                                                 monkeypatch):
    tot = datetime.utcnow() + timedelta(days=12)
    _zet_org(org.id, mollie_customer_id="cst_1", mollie_subscription_id="sub_1",
             paid_until=tot)
    monkeypatch.setattr(mollie, "zeg_abonnement_op",
                        lambda cid, sid: {"id": sid, "status": "canceled"})

    r = client.post("/api/billing/cancel", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["toegang_tot"] is not None

    ververst = _org_uit_db(org.id)
    assert ververst.mollie_subscription_id is None
    assert ververst.billing_status == "canceled"
    # De organisatie blijft actief tot de betaalde termijn voorbij is.
    assert ververst.status == AccountStatus.ACTIVE


def test_betalingsoverzicht_toont_alleen_de_eigen_organisatie(
        client, admin_user, org):
    db = SessionLocal()
    try:
        db.add(Payment(mollie_payment_id="tr_eigen", organization_id=org.id,
                       amount="9.00", status="paid"))
        db.add(Payment(mollie_payment_id="tr_ander", organization_id="andere-org",
                       amount="9.00", status="paid"))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/billing/betalingen", headers=auth(admin_user))
    assert r.status_code == 200
    ids = {p["mollie_id"] for p in r.json()}
    assert ids == {"tr_eigen"}
