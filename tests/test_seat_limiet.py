"""Het gebruikersplafond moet op élk pad gelden dat een account laat ontstaan.

De controle stond alleen bij het versturen van een uitnodiging. Daardoor was het
plafond op drie manieren te omzeilen: rechtstreeks aanmaken, accepteren van een
uitnodiging, en het heractiveren van een uitgeschakelde gebruiker. Bij een prijs
per gebruiker is dat direct omzet die weglekt.
"""

from datetime import datetime, timedelta, timezone

from database import SessionLocal
from models import Organization, User, Invitation, UserRole

from .conftest import auth


def _zet_max(org_id, aantal):
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        org.max_users = aantal
        db.commit()
    finally:
        db.close()


def _tel_actief(org_id):
    db = SessionLocal()
    try:
        return db.query(User).filter(
            User.organization_id == org_id, User.is_active == True  # noqa: E712
        ).count()
    finally:
        db.close()


def _nieuwe_gebruiker(n):
    return {
        "email": f"nieuw{n}@testdal.nl",
        "password": "SterkWachtwoord1!",
        "first_name": "Nieuw",
        "last_name": f"Persoon{n}",
        "role": "viewer",
    }


# ── Rechtstreeks aanmaken ───────────────────────────────────────────────────

def test_rechtstreeks_aanmaken_respecteert_plafond(client, admin_user):
    """Dit was het grootste lek: dit endpoint telde helemaal niet."""
    _zet_max(admin_user.organization_id, 2)  # admin zelf is er al 1
    assert client.post("/api/users/create", headers=auth(admin_user),
                       json=_nieuwe_gebruiker(1)).status_code == 200

    r = client.post("/api/users/create", headers=auth(admin_user),
                    json=_nieuwe_gebruiker(2))
    assert r.status_code == 400
    assert "maximum" in r.json()["detail"].lower()
    assert _tel_actief(admin_user.organization_id) == 2


def test_ruim_plafond_laat_gewoon_toe(client, admin_user):
    _zet_max(admin_user.organization_id, 10)
    for i in range(3):
        r = client.post("/api/users/create", headers=auth(admin_user),
                        json=_nieuwe_gebruiker(10 + i))
        assert r.status_code == 200, r.text


# ── Uitnodigen ──────────────────────────────────────────────────────────────

def test_uitnodigen_respecteert_plafond(client, admin_user):
    _zet_max(admin_user.organization_id, 1)  # admin vult 'm al
    r = client.post("/api/users/invite", headers=auth(admin_user),
                    json={"email": "gast@testdal.nl", "role": "viewer"})
    assert r.status_code == 400


def test_openstaande_uitnodigingen_tellen_mee(client, admin_user):
    """Zonder dit kon je bij 5 van de 10 plaatsen honderd uitnodigingen sturen
    die daarna allemaal accepteren."""
    _zet_max(admin_user.organization_id, 3)  # admin + 2 vrij
    for i in range(2):
        r = client.post("/api/users/invite", headers=auth(admin_user),
                        json={"email": f"gast{i}@testdal.nl", "role": "viewer"})
        assert r.status_code == 200, r.text

    r = client.post("/api/users/invite", headers=auth(admin_user),
                    json={"email": "eentjeteveel@testdal.nl", "role": "viewer"})
    assert r.status_code == 400
    assert "uitnodiging" in r.json()["detail"].lower()


def test_verlopen_uitnodiging_telt_niet_mee(client, admin_user, org):
    """Een uitnodiging die nooit geaccepteerd is mag geen plaats blijven bezetten."""
    _zet_max(org.id, 2)
    db = SessionLocal()
    try:
        db.add(Invitation(
            email="vergeten@testdal.nl", organization_id=org.id,
            role=UserRole.VIEWER, token="verlopen-token-abc",
            invited_by=admin_user.id, accepted=False,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        ))
        db.commit()
    finally:
        db.close()

    r = client.post("/api/users/create", headers=auth(admin_user),
                    json=_nieuwe_gebruiker(50))
    assert r.status_code == 200, r.text


# ── Accepteren ──────────────────────────────────────────────────────────────

def test_accepteren_faalt_als_plaatsen_intussen_weg_zijn(client, admin_user, org):
    """Tussen uitnodigen en accepteren kan het abonnement verlaagd zijn."""
    _zet_max(org.id, 5)
    r = client.post("/api/users/invite", headers=auth(admin_user),
                    json={"email": "laatkomer@testdal.nl", "role": "viewer"})
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        inv = db.query(Invitation).filter(
            Invitation.email == "laatkomer@testdal.nl").first()
        token = inv.token
    finally:
        db.close()

    _zet_max(org.id, 1)  # abonnement verlaagd, admin vult 'm al

    r = client.post("/api/users/accept-invitation", json={
        "token": token, "password": "SterkWachtwoord1!",
        "first_name": "Laat", "last_name": "Komer",
    })
    assert r.status_code == 400
    assert "maximum" in r.json()["detail"].lower()


def test_accepteren_lukt_binnen_het_plafond(client, admin_user, org):
    _zet_max(org.id, 5)
    client.post("/api/users/invite", headers=auth(admin_user),
                json={"email": "welkom@testdal.nl", "role": "viewer"})
    db = SessionLocal()
    try:
        token = db.query(Invitation).filter(
            Invitation.email == "welkom@testdal.nl").first().token
    finally:
        db.close()

    r = client.post("/api/users/accept-invitation", json={
        "token": token, "password": "SterkWachtwoord1!",
        "first_name": "Wel", "last_name": "Kom",
    })
    assert r.status_code == 200, r.text


# ── Heractiveren ────────────────────────────────────────────────────────────

def test_heractiveren_kost_een_plaats(client, admin_user):
    """Deactiveer tien, koop er een bij, zet ze allemaal weer aan — dat kon."""
    _zet_max(admin_user.organization_id, 3)
    aangemaakt = client.post("/api/users/create", headers=auth(admin_user),
                             json=_nieuwe_gebruiker(60))
    assert aangemaakt.status_code == 200, aangemaakt.text
    user_id = aangemaakt.json()["id"]

    r = client.put(f"/api/users/{user_id}", headers=auth(admin_user),
                   json={"is_active": False})
    assert r.status_code == 200, r.text

    # Plaats opgevuld door iemand anders
    assert client.post("/api/users/create", headers=auth(admin_user),
                       json=_nieuwe_gebruiker(61)).status_code == 200
    _zet_max(admin_user.organization_id, 2)

    r = client.put(f"/api/users/{user_id}", headers=auth(admin_user),
                   json={"is_active": True})
    assert r.status_code == 400
    assert "maximum" in r.json()["detail"].lower()


def test_al_actieve_gebruiker_bijwerken_kost_geen_plaats(client, admin_user):
    """Een naamswijziging mag niet stuklopen op het plafond."""
    _zet_max(admin_user.organization_id, 2)
    aangemaakt = client.post("/api/users/create", headers=auth(admin_user),
                             json=_nieuwe_gebruiker(70))
    user_id = aangemaakt.json()["id"]

    r = client.put(f"/api/users/{user_id}", headers=auth(admin_user),
                   json={"first_name": "Andere", "is_active": True})
    assert r.status_code == 200, r.text


# ── Randgevallen ────────────────────────────────────────────────────────────

def test_max_users_nul_geeft_nette_melding(client, admin_user):
    _zet_max(admin_user.organization_id, 0)
    r = client.post("/api/users/create", headers=auth(admin_user),
                    json=_nieuwe_gebruiker(80))
    assert r.status_code == 400
    assert "gebruikersplaatsen" in r.json()["detail"].lower()


def test_max_users_leeg_geeft_geen_serverfout(client, admin_user):
    """max_users is nullable; een NULL gaf eerder een TypeError bij de
    vergelijking en dus een 500."""
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(
            Organization.id == admin_user.organization_id).first()
        org.max_users = None
        db.commit()
    finally:
        db.close()

    r = client.post("/api/users/create", headers=auth(admin_user),
                    json=_nieuwe_gebruiker(81))
    assert r.status_code == 400, f"verwachtte nette 400, kreeg {r.status_code}"
