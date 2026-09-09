"""Tests voor de bedrijfsprofielen — het scherm waarmee de platform-eigenaar
per klant een omgeving inricht.

Wat hier bewaakt wordt:

  - Alleen de platform-eigenaar komt erbij. Een org-admin van een klant mag
    zijn eigen bedrijf niet inzien via deze route, laat staan dat van een ander.
  - Het logo staat NIET in de lijst. Het is een base64-blob; tien daarvan in
    een overzicht is precies hoe deze API eerder onderuit ging.
  - Wat je aanvinkt komt echt aan: een uitgezette module geeft 403 op de
    bijbehorende router.
"""

import json

from database import SessionLocal
from models import Organization, PORTAL_MODULES
from tests.conftest import auth


# Een piepklein PNG'je: genoeg om als logo te tellen zonder een echt bestand.
LOGO = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _klant_org(naam="Klant BV"):
    """Een tweede organisatie, zodat we afscherming echt kunnen testen."""
    from models import AccountStatus, SubscriptionPlan
    db = SessionLocal()
    try:
        o = Organization(name=naam, plan=SubscriptionPlan.STARTER,
                         status=AccountStatus.ACTIVE, max_users=15)
        db.add(o)
        db.commit()
        db.refresh(o)
        return o.id
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Afscherming
# ─────────────────────────────────────────────────────────────────────────────

def test_alleen_platform_eigenaar_ziet_een_bedrijfsprofiel(client, admin_user,
                                                           platform_owner):
    """Een org-admin van een klant is geen platform-eigenaar."""
    eigen = admin_user.organization_id

    r = client.get(f"/api/admin/organizations/{eigen}", headers=auth(admin_user))
    assert r.status_code == 403, "org-admin mag hier niet bij"

    r = client.get(f"/api/admin/organizations/{eigen}", headers=auth(platform_owner))
    assert r.status_code == 200, r.text


def test_zonder_login_geen_profiel(client, admin_user):
    r = client.get(f"/api/admin/organizations/{admin_user.organization_id}")
    assert r.status_code in (401, 403)


def test_onbekend_bedrijf_geeft_404(client, platform_owner):
    r = client.get("/api/admin/organizations/bestaat-niet", headers=auth(platform_owner))
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Wat het profiel teruggeeft
# ─────────────────────────────────────────────────────────────────────────────

def test_profiel_bevat_wat_het_scherm_nodig_heeft(client, platform_owner):
    org_id = _klant_org("Aannemer Van Dijk")
    r = client.get(f"/api/admin/organizations/{org_id}", headers=auth(platform_owner))
    assert r.status_code == 200, r.text
    b = r.json()

    assert b["name"] == "Aannemer Van Dijk"
    assert b["max_users"] == 15
    assert b["user_count"] == 0
    # Alle modules met een leesbaar label, zodat het scherm ze kan tekenen
    # zonder een eigen lijst bij te houden die uit de pas kan lopen.
    keys = {m["key"] for m in b["alle_modules"]}
    assert keys == set(PORTAL_MODULES)
    assert all(m["label"] for m in b["alle_modules"])
    # enabled_modules is None zolang er niets is ingesteld = alles aan.
    assert b["enabled_modules"] is None


def test_profiel_toont_de_gebruikers(client, platform_owner, admin_user, viewer_user):
    r = client.get(f"/api/admin/organizations/{admin_user.organization_id}",
                   headers=auth(platform_owner))
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()["gebruikers"]}
    assert admin_user.email in emails
    assert viewer_user.email in emails


# ─────────────────────────────────────────────────────────────────────────────
# Het logo hoort niet in de lijst
# ─────────────────────────────────────────────────────────────────────────────

def test_overzicht_stuurt_het_logo_niet_mee(client, platform_owner, admin_user):
    """De les uit de OOM: geen blobs in een lijst-endpoint.

    Het overzicht geeft alleen of er een logo is; de blob zelf haal je op per
    bedrijf. Tien klanten met elk een logo van een paar honderd kilobyte zou
    het overzicht anders onbruikbaar zwaar maken.
    """
    org_id = admin_user.organization_id
    r = client.put(f"/api/admin/organizations/{org_id}/branding",
                   json={"logo_data_url": LOGO, "brand_color": "#123456"},
                   headers=auth(platform_owner))
    assert r.status_code == 200, r.text

    overzicht = client.get("/api/admin/overview", headers=auth(platform_owner))
    assert overzicht.status_code == 200
    rij = next(o for o in overzicht.json()["organizations"] if o["id"] == org_id)

    assert rij["heeft_logo"] is True
    assert rij["brand_color"] == "#123456"
    assert "logo_data_url" not in rij
    assert LOGO not in overzicht.text

    # In het profiel van één bedrijf hoort hij wel.
    profiel = client.get(f"/api/admin/organizations/{org_id}", headers=auth(platform_owner))
    assert profiel.json()["logo_data_url"] == LOGO


def test_contactgegevens_gaan_door_hetzelfde_scherm(client, platform_owner, admin_user):
    org_id = admin_user.organization_id
    r = client.put(f"/api/admin/organizations/{org_id}/branding",
                   json={"contact_email": "info@klant.nl", "contact_phone": "0612345678",
                         "kvk_number": "12345678", "btw_number": "NL001234567B01"},
                   headers=auth(platform_owner))
    assert r.status_code == 200, r.text

    b = client.get(f"/api/admin/organizations/{org_id}",
                   headers=auth(platform_owner)).json()
    assert b["contact_email"] == "info@klant.nl"
    assert b["kvk_number"] == "12345678"

    # Leegmaken moet ook echt leegmaken, niet stilletjes de oude waarde houden.
    client.put(f"/api/admin/organizations/{org_id}/branding",
               json={"kvk_number": ""}, headers=auth(platform_owner))
    b = client.get(f"/api/admin/organizations/{org_id}",
                   headers=auth(platform_owner)).json()
    assert b["kvk_number"] is None
    assert b["contact_email"] == "info@klant.nl", "andere velden blijven staan"


# ─────────────────────────────────────────────────────────────────────────────
# Wat je aanvinkt komt aan
# ─────────────────────────────────────────────────────────────────────────────

def test_module_uitzetten_sluit_de_router_af(client, platform_owner, admin_user):
    """Het vinkje is geen sierveld: uitzetten betekent echt geen toegang."""
    org_id = admin_user.organization_id

    # Kwaliteit staat aan zolang er niets is ingesteld.
    assert client.get("/api/kwaliteit/keuringen",
                      headers=auth(admin_user)).status_code == 200

    aan = [k for k in PORTAL_MODULES if k != "kwaliteit"]
    r = client.put(f"/api/admin/organizations/{org_id}?enabled_modules={','.join(aan)}",
                   headers=auth(platform_owner))
    assert r.status_code == 200, r.text

    assert client.get("/api/kwaliteit/keuringen",
                      headers=auth(admin_user)).status_code == 403

    # En weer aan.
    client.put(f"/api/admin/organizations/{org_id}"
               f"?enabled_modules={','.join(PORTAL_MODULES)}",
               headers=auth(platform_owner))
    assert client.get("/api/kwaliteit/keuringen",
                      headers=auth(admin_user)).status_code == 200


def test_onbekende_module_wordt_geweigerd(client, platform_owner, admin_user):
    r = client.put(f"/api/admin/organizations/{admin_user.organization_id}"
                   "?enabled_modules=kunstwerken,bestaatniet",
                   headers=auth(platform_owner))
    assert r.status_code == 400
    assert "bestaatniet" in r.json()["detail"]


def test_alles_uitzetten_laat_de_basis_staan(client, platform_owner, admin_user):
    """Dashboard, projecten, assets, meldingen en kaart zijn geen module."""
    org_id = admin_user.organization_id
    r = client.put(f"/api/admin/organizations/{org_id}?enabled_modules=",
                   headers=auth(platform_owner))
    assert r.status_code == 200

    db = SessionLocal()
    try:
        o = db.query(Organization).filter(Organization.id == org_id).first()
        assert json.loads(o.enabled_modules) == []
    finally:
        db.close()

    # Basis blijft bereikbaar.
    assert client.get("/api/projects/", headers=auth(admin_user)).status_code == 200
    assert client.get("/api/meldingen/", headers=auth(admin_user)).status_code == 200
    # Een module niet.
    assert client.get("/api/kwaliteit/keuringen",
                      headers=auth(admin_user)).status_code == 403
