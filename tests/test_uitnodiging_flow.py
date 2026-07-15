"""Tests voor de uitnodiging-accept flow (end-to-end).

Bewijst dat een uitgenodigde collega:
  (a) de accept-pagina op GET /uitnodiging krijgt (200 + formulier),
  (b) via de volledige flow (invite -> accept -> login) een werkend account krijgt,
  (c) een ongeldige token 404 geeft,
  (d) een verlopen token 400 geeft.
"""

from datetime import datetime, timezone, timedelta

from database import SessionLocal
from models import Invitation, UserRole
from tests.conftest import auth


def test_uitnodiging_pagina_serveert_accept_formulier(client):
    """(a) GET /uitnodiging -> 200 en de HTML bevat het accept-formulier."""
    res = client.get("/uitnodiging")
    assert res.status_code == 200
    html = res.text
    # Kernvelden van het accept-formulier moeten aanwezig zijn
    assert 'id="acceptForm"' in html
    assert 'id="firstName"' in html
    assert 'id="lastName"' in html
    assert 'id="password"' in html
    assert 'id="confirmPassword"' in html
    # POST-doel moet exact de accept-endpoint zijn
    assert "/api/users/accept-invitation" in html


def _pak_token(email, org_id):
    """Haal de raw uitnodig-token uit de aangemaakte Invitation."""
    db = SessionLocal()
    try:
        inv = (
            db.query(Invitation)
            .filter(Invitation.email == email, Invitation.organization_id == org_id)
            .first()
        )
        return inv.token if inv else None
    finally:
        db.close()


def test_volledige_uitnodig_flow_invite_accept_login(client, admin_user):
    """(b) org-admin nodigt uit -> token -> accept -> nieuwe user kan inloggen."""
    nieuw_email = "collega@test.nl"

    # 1. Org-admin verstuurt uitnodiging
    res = client.post(
        "/api/users/invite",
        json={"email": nieuw_email, "role": "inspector"},
        headers=auth(admin_user),
    )
    assert res.status_code == 200, res.text

    # 2. Pak de token uit de aangemaakte Invitation
    token = _pak_token(nieuw_email, admin_user.organization_id)
    assert token, "Uitnodiging-token niet gevonden in de database"

    # 3. Accepteer de uitnodiging met token + wachtwoord + naam
    res = client.post(
        "/api/users/accept-invitation",
        json={
            "token": token,
            "first_name": "Nieuwe",
            "last_name": "Collega",
            "phone": "06 12345678",
            "password": "geheim-wachtwoord-123",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["email"] == nieuw_email
    # Rol uit de uitnodiging moet zijn overgenomen
    assert body["role"] == "inspector"

    # 4. De nieuwe user kan inloggen via /api/auth/login
    res = client.post(
        "/api/auth/login",
        json={"email": nieuw_email, "password": "geheim-wachtwoord-123"},
    )
    assert res.status_code == 200, res.text
    assert res.json().get("access_token")


def test_accept_met_zwak_wachtwoord_geeft_400(client, admin_user):
    """(e) accept met een geldige token maar zwak wachtwoord -> 400 server-side.

    De HTML/JS-check is triviaal te omzeilen door direct te POSTen; de
    compliance-audit (Rekenkamer/ISO27001) kijkt of de BACKEND de
    wachtwoordsterkte afdwingt. Deze test bewijst dat een uitgenodigde geen
    account met een 1-teken wachtwoord kan aanmaken.
    """
    zwak_email = "zwak@test.nl"

    # Org-admin verstuurt uitnodiging
    res = client.post(
        "/api/users/invite",
        json={"email": zwak_email, "role": "inspector"},
        headers=auth(admin_user),
    )
    assert res.status_code == 200, res.text

    token = _pak_token(zwak_email, admin_user.organization_id)
    assert token, "Uitnodiging-token niet gevonden in de database"

    # Direct POSTen met een 1-teken wachtwoord moet 400 geven (server-side gate)
    res = client.post(
        "/api/users/accept-invitation",
        json={
            "token": token,
            "first_name": "Zwak",
            "last_name": "Wachtwoord",
            "password": "a",
        },
    )
    assert res.status_code == 400, res.text

    # Ook een wachtwoord dat lang genoeg is maar te weinig categorieën heeft
    res = client.post(
        "/api/users/accept-invitation",
        json={
            "token": token,
            "first_name": "Zwak",
            "last_name": "Wachtwoord",
            "password": "allemaalkleineletters",
        },
    )
    assert res.status_code == 400, res.text

    # De uitnodiging mag NIET geaccepteerd zijn na de mislukte pogingen
    db = SessionLocal()
    try:
        inv = (
            db.query(Invitation)
            .filter(
                Invitation.email == zwak_email,
                Invitation.organization_id == admin_user.organization_id,
            )
            .first()
        )
        assert inv is not None
        assert inv.accepted is False, "Uitnodiging mag niet geaccepteerd zijn bij zwak wachtwoord"
    finally:
        db.close()


def test_accept_met_ongeldige_token_geeft_404(client):
    """(c) accept met een niet-bestaande token -> 404."""
    res = client.post(
        "/api/users/accept-invitation",
        json={
            "token": "deze-token-bestaat-niet",
            "first_name": "Niemand",
            "last_name": "Nergens",
            "password": "geheim-wachtwoord-123",
        },
    )
    assert res.status_code == 404, res.text


def test_accept_met_verlopen_token_geeft_400(client, admin_user):
    """(d) accept met een verlopen uitnodiging -> 400."""
    verlopen_email = "verlopen@test.nl"

    # Maak direct een verlopen uitnodiging aan in de DB
    db = SessionLocal()
    try:
        inv = Invitation(
            email=verlopen_email,
            role=UserRole.VIEWER,
            token="verlopen-token-abc",
            invited_by=admin_user.id,
            organization_id=admin_user.organization_id,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.add(inv)
        db.commit()
    finally:
        db.close()

    res = client.post(
        "/api/users/accept-invitation",
        json={
            "token": "verlopen-token-abc",
            "first_name": "Te",
            "last_name": "Laat",
            "password": "geheim-wachtwoord-123",
        },
    )
    assert res.status_code == 400, res.text
