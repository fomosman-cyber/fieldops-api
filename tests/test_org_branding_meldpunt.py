"""Huisstijl en publiek meldpunt per organisatie.

Twee dingen worden hier geborgd. Ten eerste dat de meldpunt-instellingen
daadwerkelijk worden opgeslagen: die stonden wel in het formulier maar niet in
het schema, waardoor Pydantic ze weggooide en de gebruiker "opgeslagen" te zien
kreeg terwijl er niets veranderde. Ten tweede dat de platform-eigenaar de
huisstijl van een klant kan zetten zonder als die klant in te loggen.
"""

import json

from database import SessionLocal
from models import Organization, Project

from .conftest import auth

LOGO = "data:image/png;base64," + "A" * 200


def _org(org_id):
    db = SessionLocal()
    try:
        return db.query(Organization).filter(Organization.id == org_id).first()
    finally:
        db.close()


# ── Meldpunt: wordt het écht opgeslagen ─────────────────────────────────────

def test_meldpunt_instellingen_worden_bewaard(client, admin_user):
    r = client.patch("/api/organization/", headers=auth(admin_user), json={
        "public_meld_slug": "gemeente-testdal",
        "public_meld_enabled": True,
        "public_meld_intro_text": "Meld hier een probleem in de openbare ruimte.",
        "public_meld_categories": json.dumps(["Verlichting", "Wegdek"]),
    })
    assert r.status_code == 200, r.text
    org = _org(admin_user.organization_id)
    assert org.public_meld_slug == "gemeente-testdal"
    assert org.public_meld_enabled is True
    assert "openbare ruimte" in org.public_meld_intro_text
    assert json.loads(org.public_meld_categories) == ["Verlichting", "Wegdek"]


def test_slug_wordt_kleingeschreven(client, admin_user):
    r = client.patch("/api/organization/", headers=auth(admin_user),
                     json={"public_meld_slug": "Gemeente-TESTDAL"})
    assert r.status_code == 200
    assert _org(admin_user.organization_id).public_meld_slug == "gemeente-testdal"


def test_ongeldige_slug_wordt_geweigerd(client, admin_user):
    for slug in ("ab", "-begint-fout", "eindigt-fout-", "met spatie", "MET/SLASH"):
        r = client.patch("/api/organization/", headers=auth(admin_user),
                         json={"public_meld_slug": slug})
        assert r.status_code == 400, f"{slug!r} werd geaccepteerd"


def test_slug_moet_uniek_zijn_over_organisaties(client, admin_user):
    """De slug zit in een publieke URL: twee organisaties met hetzelfde adres
    zou meldingen bij de verkeerde partij laten binnenkomen. De database heeft
    hier ook een constraint op; deze check zorgt dat de gebruiker een nette
    melding krijgt in plaats van een 500."""
    db = SessionLocal()
    try:
        andere = Organization(name="Buurbedrijf Slug BV")
        andere.public_meld_slug = "al-in-gebruik"
        db.add(andere); db.commit()
        andere_id = andere.id
    finally:
        db.close()

    try:
        r = client.patch("/api/organization/", headers=auth(admin_user),
                         json={"public_meld_slug": "al-in-gebruik"})
        assert r.status_code == 400, r.text
        assert "in gebruik" in r.json()["detail"].lower()
    finally:
        db = SessionLocal()
        try:
            db.query(Organization).filter(Organization.id == andere_id).delete()
            db.commit()
        finally:
            db.close()


def test_eigen_slug_opnieuw_zetten_mag(client, admin_user):
    """Anders zou opslaan zonder wijziging een foutmelding geven."""
    for _ in range(2):
        r = client.patch("/api/organization/", headers=auth(admin_user),
                         json={"public_meld_slug": "mijn-eigen-adres"})
        assert r.status_code == 200, r.text


def test_startproject_van_andere_org_geweigerd(client, admin_user):
    """Zonder deze check kun je het publieke meldpunt op andermans project
    richten en zo meldingen in een vreemde organisatie laten landen."""
    db = SessionLocal()
    try:
        vreemde_org = Organization(name="Buurbedrijf BV")
        db.add(vreemde_org); db.flush()
        vreemd_project = Project(name="Niet van ons",
                                 organization_id=vreemde_org.id,
                                 created_by=admin_user.id)
        db.add(vreemd_project); db.commit()
        vreemd_id = vreemd_project.id
        org_id = vreemde_org.id
    finally:
        db.close()

    r = client.patch("/api/organization/", headers=auth(admin_user),
                     json={"public_meld_default_project_id": vreemd_id})
    assert r.status_code == 400
    assert "organisatie" in r.json()["detail"].lower()

    db = SessionLocal()
    try:
        db.query(Project).filter(Project.id == vreemd_id).delete()
        db.query(Organization).filter(Organization.id == org_id).delete()
        db.commit()
    finally:
        db.close()


def test_aanzetten_zonder_adres_geweigerd(client, admin_user):
    r = client.patch("/api/organization/", headers=auth(admin_user),
                     json={"public_meld_enabled": True})
    assert r.status_code == 400
    assert "adres" in r.json()["detail"].lower()


def test_categorieen_moeten_een_lijst_zijn(client, admin_user):
    for waarde in ('{"niet": "lijst"}', "gewoon tekst", json.dumps([1, 2, 3])):
        r = client.patch("/api/organization/", headers=auth(admin_user),
                         json={"public_meld_categories": waarde})
        assert r.status_code == 400, f"{waarde!r} werd geaccepteerd"


# ── Huisstijl door de org-admin zelf ────────────────────────────────────────

def test_org_admin_zet_eigen_huisstijl(client, admin_user):
    r = client.patch("/api/organization/", headers=auth(admin_user),
                     json={"logo_data_url": LOGO, "brand_color": "#0284c7"})
    assert r.status_code == 200, r.text
    org = _org(admin_user.organization_id)
    assert org.logo_data_url == LOGO
    assert org.brand_color == "#0284c7"


def test_kleur_moet_hex_zijn(client, admin_user):
    for kleur in ("blauw", "0284c7", "#12345", "rgb(1,2,3)"):
        r = client.patch("/api/organization/", headers=auth(admin_user),
                         json={"brand_color": kleur})
        assert r.status_code == 400, f"{kleur!r} werd geaccepteerd"


def test_logo_moet_afbeelding_zijn(client, admin_user):
    r = client.patch("/api/organization/", headers=auth(admin_user),
                     json={"logo_data_url": "https://elders.nl/logo.png"})
    assert r.status_code == 400


def test_te_groot_logo_geweigerd(client, admin_user):
    r = client.patch("/api/organization/", headers=auth(admin_user),
                     json={"logo_data_url": "data:image/png;base64," + "A" * 800_000})
    assert r.status_code == 400
    assert "groot" in r.json()["detail"].lower()


# ── Huisstijl door de platform-eigenaar, voor een klant ─────────────────────

def test_eigenaar_zet_huisstijl_van_klant(client, platform_owner, admin_user):
    """De wens: de beheerder richt het portaal per bedrijf in, zonder als die
    klant te hoeven inloggen."""
    klant_org = admin_user.organization_id
    r = client.put(f"/api/admin/organizations/{klant_org}/branding",
                   headers=auth(platform_owner),
                   json={"logo_data_url": LOGO, "brand_color": "#16a34a"})
    assert r.status_code == 200, r.text
    org = _org(klant_org)
    assert org.logo_data_url == LOGO
    assert org.brand_color == "#16a34a"


def test_gewone_org_admin_mag_dat_niet(client, admin_user):
    r = client.put(f"/api/admin/organizations/{admin_user.organization_id}/branding",
                   headers=auth(admin_user),
                   json={"brand_color": "#000000"})
    assert r.status_code == 403


def test_eigenaar_krijgt_dezelfde_validatie(client, platform_owner, admin_user):
    r = client.put(f"/api/admin/organizations/{admin_user.organization_id}/branding",
                   headers=auth(platform_owner), json={"brand_color": "paars"})
    assert r.status_code == 400


def test_huisstijl_wissen_kan(client, platform_owner, admin_user):
    org_id = admin_user.organization_id
    client.put(f"/api/admin/organizations/{org_id}/branding",
               headers=auth(platform_owner),
               json={"logo_data_url": LOGO, "brand_color": "#16a34a"})
    r = client.put(f"/api/admin/organizations/{org_id}/branding",
                   headers=auth(platform_owner),
                   json={"logo_data_url": None, "brand_color": None})
    assert r.status_code == 200
    org = _org(org_id)
    assert org.logo_data_url is None
    assert org.brand_color is None


def test_onbekende_organisatie_geeft_404(client, platform_owner):
    r = client.put("/api/admin/organizations/bestaat-niet/branding",
                   headers=auth(platform_owner), json={"brand_color": "#000000"})
    assert r.status_code == 404
