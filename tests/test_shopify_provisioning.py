"""Wat een betaalde Shopify-bestelling precies aanmaakt.

De webhook maakt een organisatie + org-admin. Deze tests borgen dat wat de
klant koopt ook is wat hij krijgt: het bestelde aantal gebruikers, alleen de
modules waar de licentie recht op geeft, en een account waar hij daadwerkelijk
in kan (welkomstmail + wachtwoord wijzigen bij eerste login).
"""

import base64
import hashlib
import hmac
import json

from database import SessionLocal
from models import Organization, User, PORTAL_MODULES

SECRET = "test-shopify-secret"


def _signed_post(client, payload):
    body = json.dumps(payload).encode("utf-8")
    digest = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    return client.post(
        "/api/shopify/webhook/order-paid",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Hmac-Sha256": base64.b64encode(digest).decode("utf-8"),
        },
    )


def _order(email, *, line_items, company="Inspectiebureau Testdal"):
    return {
        "email": email,
        "customer": {"id": 999, "first_name": "Test", "last_name": "Klant",
                     "company": company},
        "line_items": line_items,
    }


def _regel(sku="FO-INSP-1M", qty=1, title="FieldOps - per gebruiker, per maand"):
    return {"title": title, "sku": sku, "quantity": qty}


def _org(org_id):
    db = SessionLocal()
    try:
        return db.query(Organization).filter(Organization.id == org_id).first()
    finally:
        db.close()


# ── Aantal gebruikers komt uit de bestelling ────────────────────────────────

def test_seats_volgen_de_bestelde_hoeveelheid(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    r = _signed_post(client, _order("zeven@test.nl", line_items=[_regel(qty=7)]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "created"
    assert body["seats"] == 7
    assert _org(body["organization_id"]).max_users == 7


def test_meerdere_regels_worden_opgeteld(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    r = _signed_post(client, _order(
        "optellen@test.nl", line_items=[_regel(qty=2), _regel(qty=3)]))
    assert r.json()["seats"] == 5


def test_ontbrekende_quantity_telt_als_een(client, monkeypatch):
    """Shopify stuurt quantity altijd mee, maar een lege waarde mag niet in
    nul gebruikers eindigen — dan kan niemand inloggen."""
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    r = _signed_post(client, _order(
        "geenqty@test.nl", line_items=[{"title": "x", "sku": "FO-INSP-1M"}]))
    assert r.json()["seats"] == 1


# ── Modules: alleen waar de licentie recht op geeft ─────────────────────────

def test_modules_worden_expliciet_gezet(client, monkeypatch):
    """Zonder dit blijft enabled_modules NULL, en NULL betekent 'alles aan'."""
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    r = _signed_post(client, _order("modules@test.nl", line_items=[_regel()]))
    org = _org(r.json()["organization_id"])
    assert org.enabled_modules is not None
    assert set(json.loads(org.enabled_modules)) == set(PORTAL_MODULES.keys())


def test_alleen_bekende_module_keys(client, monkeypatch):
    """Een key die niet in PORTAL_MODULES staat wordt nergens afgedwongen en
    zou stilzwijgend niets doen."""
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    r = _signed_post(client, _order("keys@test.nl", line_items=[_regel()]))
    org = _org(r.json()["organization_id"])
    for key in json.loads(org.enabled_modules):
        assert key in PORTAL_MODULES


# ── Onbekende producten maken geen organisatie ──────────────────────────────

def test_bestelling_zonder_licentieproduct_maakt_geen_org(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    r = _signed_post(client, _order(
        "geenlicentie@test.nl",
        line_items=[{"title": "Poster", "sku": "MERCH-01", "quantity": 1}]))
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"
    db = SessionLocal()
    try:
        assert db.query(User).filter(User.email == "geenlicentie@test.nl").count() == 0
    finally:
        db.close()


def test_titel_met_pro_erin_geeft_geen_rechten(client, monkeypatch):
    """Regressie: de oude code deed `if "pro" in title` — een product als
    'Proefmaand' kende daarmee onbeperkte gebruikers toe."""
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    r = _signed_post(client, _order(
        "proefmaand@test.nl",
        line_items=[{"title": "Proefmaand", "sku": "PROEF-01", "quantity": 1}]))
    assert r.json()["status"] == "skipped"


# ── Het account moet bruikbaar zijn ─────────────────────────────────────────

def test_welkomstmail_wordt_verstuurd_met_wachtwoord(client, monkeypatch):
    """Zonder deze mail krijgt de klant een account zonder wachtwoord."""
    verstuurd = {}

    def _vang(user, password, org, *, seats=1):
        verstuurd.update(email=user.email, password=password,
                         org=org.name, seats=seats)
        return True

    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    monkeypatch.setattr("email_service.send_license_welcome", _vang)

    r = _signed_post(client, _order("welkom@test.nl", line_items=[_regel(qty=3)]))
    assert r.json()["welcome_email_sent"] is True
    assert verstuurd["email"] == "welkom@test.nl"
    assert verstuurd["seats"] == 3
    assert len(verstuurd["password"]) >= 12


def test_wachtwoord_lekt_niet_in_de_response(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    captured = {}
    monkeypatch.setattr(
        "email_service.send_license_welcome",
        lambda u, p, o, **kw: captured.update(password=p) or True)
    r = _signed_post(client, _order("lek@test.nl", line_items=[_regel()]))
    assert captured["password"] not in json.dumps(r.json())


def test_mailfout_laat_de_bestelling_doorgaan(client, monkeypatch):
    """Shopify herhaalt bij een foutcode; dat zou een tweede org opleveren."""
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)

    def _stuk(*a, **kw):
        raise RuntimeError("mailserver plat")

    monkeypatch.setattr("email_service.send_license_welcome", _stuk)
    r = _signed_post(client, _order("mailstuk@test.nl", line_items=[_regel()]))
    assert r.status_code == 200
    assert r.json()["status"] == "created"
    assert r.json()["welcome_email_sent"] is False


def test_eerste_login_vraagt_om_nieuw_wachtwoord(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    _signed_post(client, _order("wijzig@test.nl", line_items=[_regel()]))
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "wijzig@test.nl").first()
        assert user is not None
        assert user.must_change_password is True
    finally:
        db.close()


# ── Bijbestellen mag niets afpakken ─────────────────────────────────────────

def test_bijbestellen_verlaagt_het_aantal_gebruikers_niet(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    eerste = _signed_post(client, _order("groei@test.nl", line_items=[_regel(qty=10)]))
    org_id = eerste.json()["organization_id"]

    tweede = _signed_post(client, _order("groei@test.nl", line_items=[_regel(qty=3)]))
    assert tweede.json()["status"] == "upgraded"
    assert _org(org_id).max_users == 10


def test_bijbestellen_beperkt_bestaande_modules_niet(client, monkeypatch):
    """Een org met 'alles aan' (NULL) mag niet stilletjes ingeperkt worden."""
    monkeypatch.setenv("SHOPIFY_API_SECRET", SECRET)
    eerste = _signed_post(client, _order("allesaan@test.nl", line_items=[_regel()]))
    org_id = eerste.json()["organization_id"]

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        org.enabled_modules = None  # 'alles aan'
        db.commit()
    finally:
        db.close()

    _signed_post(client, _order("allesaan@test.nl", line_items=[_regel()]))
    assert _org(org_id).enabled_modules is None
