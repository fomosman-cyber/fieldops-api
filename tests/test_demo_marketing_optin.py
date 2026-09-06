"""Expliciete marketing-opt-in op de demo-aanvraag.

Het aanvragen van een demo is een verzoek om contact, geen aanmelding voor een
nieuwsbrief. Deze tests borgen dat het onderscheid blijft bestaan: zonder
expliciet vinkje komt een aanvrager nooit als 'mailbaar' in het systeem, ook
niet per ongeluk via een default.
"""

from database import SessionLocal
from models import DemoRequest

from .conftest import auth


def _body(email, **extra):
    return dict({
        "first_name": "Demo",
        "last_name": "Aanvrager",
        "company_name": "Inspectiebureau Testdal",
        "email": email,
        "phone": "0612345678",
        "plan": "starter",
        "num_users": 5,
    }, **extra)


def _fetch(email):
    db = SessionLocal()
    try:
        return db.query(DemoRequest).filter(DemoRequest.email == email).one()
    finally:
        db.close()


def test_opt_in_default_is_false(client):
    """Veld weglaten mag nooit stilzwijgend toestemming opleveren."""
    email = "geen-vinkje@example.nl"
    r = client.post("/api/demo/request", json=_body(email))
    assert r.status_code == 200, r.text
    assert _fetch(email).marketing_opt_in is False


def test_opt_in_false_wordt_bewaard(client):
    email = "vinkje-uit@example.nl"
    r = client.post("/api/demo/request", json=_body(email, marketing_opt_in=False))
    assert r.status_code == 200, r.text
    assert _fetch(email).marketing_opt_in is False


def test_opt_in_true_wordt_bewaard(client):
    """Alleen een expliciet vinkje maakt de aanvrager mailbaar."""
    email = "vinkje-aan@example.nl"
    r = client.post("/api/demo/request", json=_body(email, marketing_opt_in=True))
    assert r.status_code == 200, r.text
    assert _fetch(email).marketing_opt_in is True


def test_opt_in_zichtbaar_voor_eigenaar(client, platform_owner):
    """De lijst moet de opt-in tonen, anders kun je 'm niet exporteren."""
    email = "admin-ziet-optin@example.nl"
    assert client.post(
        "/api/demo/request", json=_body(email, marketing_opt_in=True)
    ).status_code == 200

    r = client.get(
        "/api/demo/requests",
        headers=auth(platform_owner),
    )
    assert r.status_code == 200, r.text
    row = next(d for d in r.json() if d["email"] == email)
    assert row["marketing_opt_in"] is True


def test_notes_wordt_bewaard(client):
    """Rol en vraag uit het formulier mogen niet stil verdwijnen."""
    email = "met-vraag@example.nl"
    tekst = "Rol: Asset-manager | Hoe pakken jullie NEN 2767-2 kunstwerken op?"
    r = client.post("/api/demo/request", json=_body(email, notes=tekst))
    assert r.status_code == 200, r.text
    assert _fetch(email).notes == tekst


def test_notes_optioneel(client):
    email = "zonder-vraag@example.nl"
    assert client.post("/api/demo/request", json=_body(email)).status_code == 200
    assert _fetch(email).notes is None


def test_notes_zichtbaar_voor_eigenaar(client, platform_owner):
    email = "notes-admin@example.nl"
    client.post("/api/demo/request", json=_body(email, notes="Vraag over BAG-koppeling"))
    r = client.get("/api/demo/requests", headers=auth(platform_owner))
    row = next(d for d in r.json() if d["email"] == email)
    assert row["notes"] == "Vraag over BAG-koppeling"


def test_klantbeheerder_ziet_de_salespipeline_niet(client, admin_user):
    """Regressietest op een datalek over organisatiegrenzen heen.

    ``/api/demo/requests`` hing op ``require_admin``, en dat controleert alleen
    ``is_org_admin``. Elke beheerder van elke klantorganisatie kon daarmee naam,
    e-mailadres en telefoonnummer van iedere prospect opvragen.
    """
    email = "prospect-mag-niet-lekken@example.nl"
    assert client.post("/api/demo/request", json=_body(email)).status_code == 200

    r = client.get("/api/demo/requests", headers=auth(admin_user))
    assert r.status_code == 403, r.text
    assert email not in r.text


def test_klantbeheerder_kan_niet_mailen_namens_fieldops(client, admin_user):
    """``/api/demo/email-test`` mailt naar een vrij invulbaar adres.

    Onder ``require_admin`` was dat een phishing-relay op het FieldOps-domein
    voor elke klantbeheerder.
    """
    r = client.post(
        "/api/demo/email-test?to=slachtoffer@example.nl",
        headers=auth(admin_user),
    )
    assert r.status_code == 403, r.text
