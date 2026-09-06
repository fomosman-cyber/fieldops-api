"""Incidenten — ongeval, bijna-ongeval en gevaarlijke situatie.

De twee regels van dit rechtenmodel wijzen bewust verschillende kanten op:

  melden  -> mag IEDEREEN, ook een viewer. Een bijna-ongeval wordt alleen
             gemeld als het makkelijk gaat en niemand bang hoeft te zijn.
  inzien  -> alleen je eigen meldingen, tenzij je beheerder of manager bent.
             Hier staan gezondheidsgegevens van met naam genoemde mensen in.

Beide kanten worden hier getest, want een fout in de ene richting maakt de
registratie waardeloos en in de andere richting lekt het persoonsgegevens.
"""
import pytest

from database import SessionLocal
from models import AccountStatus, Incident, Organization, Project, SubscriptionPlan

from .conftest import _make_user, auth


@pytest.fixture
def other_org_admin():
    """Beheerder van een TWEEDE organisatie, voor de multi-tenant-checks.

    Alleen kolom-attributen gebruiken (.id, .organization_id): de sessie is
    gesloten voordat de test draait, dus een lazy relation gooit hier
    DetachedInstanceError.
    """
    db = SessionLocal()
    try:
        andere = Organization(name="AndereOrg", plan=SubscriptionPlan.PROFESSIONAL,
                              status=AccountStatus.ACTIVE, max_users=10)
        db.add(andere)
        db.commit()
        db.refresh(andere)
        return _make_user(db, "andere-incident@test.nl", org=andere)
    finally:
        db.close()


def _project(org_id, maker_id, naam="N207 Alphen"):
    db = SessionLocal()
    try:
        p = Project(name=naam, organization_id=org_id, status="active",
                    created_by=maker_id)
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id
    finally:
        db.close()


def _meld(client, user, **kwargs):
    body = {"soort": "bijna_ongeval", "omschrijving": "Bijna geraakt door een graafmachine"}
    body.update(kwargs)
    r = client.post("/api/incidenten/", json=body, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


# ── Melden mag iedereen ──────────────────────────────────────────────

def test_viewer_mag_melden(client, viewer_user):
    """De laagste rol moet kunnen melden, anders komt er niets binnen."""
    i = _meld(client, viewer_user)
    assert i["soort"] == "bijna_ongeval"
    assert i["status"] == "gemeld"
    assert i["gebeurd_op"], "zonder opgave hoort 'nu' te worden ingevuld"


def test_technicus_mag_melden(client, technician_user):
    i = _meld(client, technician_user, soort="gevaarlijke_situatie",
              omschrijving="Losliggende stelconplaat bij de inrit")
    assert i["soort"] == "gevaarlijke_situatie"


def test_onbekende_soort_wordt_geweigerd(client, admin_user):
    r = client.post("/api/incidenten/",
                    json={"soort": "verkeersongeluk", "omschrijving": "x"},
                    headers=auth(admin_user))
    assert r.status_code == 422, r.text


def test_omschrijving_is_verplicht(client, admin_user):
    r = client.post("/api/incidenten/", json={"soort": "ongeval", "omschrijving": ""},
                    headers=auth(admin_user))
    assert r.status_code == 422, r.text


# ── Inzage is beperkt ────────────────────────────────────────────────

def test_viewer_ziet_alleen_eigen_meldingen(client, viewer_user, technician_user):
    _meld(client, viewer_user, omschrijving="Van mij")
    _meld(client, technician_user, omschrijving="Van iemand anders")

    r = client.get("/api/incidenten/", headers=auth(viewer_user))
    assert r.status_code == 200, r.text
    lijst = r.json()
    assert len(lijst) == 1
    assert lijst[0]["omschrijving"] == "Van mij"


def test_manager_ziet_alles(client, viewer_user, technician_user, manager_user):
    _meld(client, viewer_user, omschrijving="Van de viewer")
    _meld(client, technician_user, omschrijving="Van de technicus")

    r = client.get("/api/incidenten/", headers=auth(manager_user))
    assert r.status_code == 200, r.text
    assert len(r.json()) == 2


def test_viewer_kan_andermans_incident_niet_openen(client, viewer_user, technician_user):
    """404 en niet 403 — anders kun je aftasten of een incident bestaat."""
    ander = _meld(client, technician_user, omschrijving="Niet van jou")
    r = client.get("/api/incidenten/" + ander["id"], headers=auth(viewer_user))
    assert r.status_code == 404, r.text


def test_eigen_melding_is_wel_volledig_zichtbaar(client, viewer_user):
    i = _meld(client, viewer_user, soort="ongeval", letsel="ehbo",
              betrokkene_naam="Jan de Vries", omschrijving="Snijwond aan de hand")
    r = client.get("/api/incidenten/" + i["id"], headers=auth(viewer_user))
    assert r.status_code == 200, r.text
    assert r.json()["letsel"] == "ehbo"
    assert r.json()["betrokkene_naam"] == "Jan de Vries"


def test_incident_van_andere_org_is_onzichtbaar(client, admin_user, other_org_admin):
    i = _meld(client, admin_user)
    r = client.get("/api/incidenten/" + i["id"], headers=auth(other_org_admin))
    assert r.status_code == 404, r.text
    assert client.get("/api/incidenten/", headers=auth(other_org_admin)).json() == []


def test_project_van_andere_org_geeft_404(client, admin_user, other_org_admin):
    vreemd = _project(other_org_admin.organization_id, other_org_admin.id, "Andermans")
    r = client.post("/api/incidenten/",
                    json={"soort": "ongeval", "omschrijving": "x", "project_id": vreemd},
                    headers=auth(admin_user))
    assert r.status_code == 404, r.text


# ── Afhandelen ───────────────────────────────────────────────────────

def test_viewer_mag_niet_afhandelen(client, viewer_user):
    i = _meld(client, viewer_user)
    r = client.post(f"/api/incidenten/{i['id']}/afhandelen",
                    json={"vervolgmaatregelen": "Opgelost"}, headers=auth(viewer_user))
    assert r.status_code == 403, r.text


def test_afhandelen_zonder_maatregel_wordt_geweigerd(client, admin_user):
    """Een incident zonder vastgelegde maatregel is voor een auditor hetzelfde
    als een incident dat niet is opgepakt."""
    i = _meld(client, admin_user)
    r = client.post(f"/api/incidenten/{i['id']}/afhandelen", json={"oorzaak": "Onoplettendheid"},
                    headers=auth(admin_user))
    assert r.status_code == 400, r.text
    assert "maatregel" in r.json()["detail"].lower()


def test_afhandelen_met_maatregel(client, manager_user):
    i = _meld(client, manager_user)
    r = client.post(f"/api/incidenten/{i['id']}/afhandelen",
                    json={"oorzaak": "Geen oogcontact met de machinist",
                          "vervolgmaatregelen": "Toolbox over machineveiligheid ingepland"},
                    headers=auth(manager_user))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "afgehandeld"
    assert r.json()["afgehandeld_op"]
    assert r.json()["afgehandeld_door"] == manager_user.id


def test_afgehandeld_incident_is_bevroren(client, admin_user):
    i = _meld(client, admin_user)
    client.post(f"/api/incidenten/{i['id']}/afhandelen",
                json={"vervolgmaatregelen": "Gedaan"}, headers=auth(admin_user))

    assert client.patch(f"/api/incidenten/{i['id']}", json={"omschrijving": "Anders"},
                        headers=auth(admin_user)).status_code == 409
    assert client.post(f"/api/incidenten/{i['id']}/afhandelen",
                       json={"vervolgmaatregelen": "Nogmaals"},
                       headers=auth(admin_user)).status_code == 409


# ── Arbeidsinspectie ─────────────────────────────────────────────────

def test_ziekenhuisopname_geeft_waarschuwing(client, admin_user):
    """Bij ziekenhuisopname of overlijden moet de Arbeidsinspectie worden
    ingelicht. Wij beslissen dat niet, maar we wijzen erop."""
    i = _meld(client, admin_user, soort="ongeval", letsel="ziekenhuis",
              omschrijving="Val van steiger")
    assert i["inspectie_verplicht"] is True
    assert "Arbeidsinspectie" in i.get("waarschuwing", "")


def test_licht_letsel_geeft_geen_waarschuwing(client, admin_user):
    i = _meld(client, admin_user, soort="ongeval", letsel="ehbo",
              omschrijving="Schaafwond")
    assert i["inspectie_verplicht"] is False
    assert "waarschuwing" not in i


def test_melding_bij_inspectie_kan_worden_vastgelegd(client, admin_user):
    i = _meld(client, admin_user, soort="ongeval", letsel="ziekenhuis",
              omschrijving="Val van steiger")
    r = client.patch(f"/api/incidenten/{i['id']}", json={"gemeld_bij_inspectie": True},
                     headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["gemeld_bij_inspectie"] is True


# ── Statistiek ───────────────────────────────────────────────────────

def test_statistiek_telt_zonder_persoonsgegevens(client, viewer_user, admin_user):
    """Cijfers mag iedereen zien — een ploeg die ziet dat er gemeld wordt,
    meldt zelf ook eerder."""
    _meld(client, admin_user, soort="ongeval", letsel="ehbo",
          betrokkene_naam="Jan de Vries", omschrijving="Snijwond")
    _meld(client, admin_user, soort="bijna_ongeval", omschrijving="Bijna geraakt")
    _meld(client, admin_user, soort="bijna_ongeval", omschrijving="Struikelen")

    r = client.get("/api/incidenten/statistiek/samenvatting", headers=auth(viewer_user))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["totaal"] == 3
    assert s["per_soort"]["bijna_ongeval"] == 2
    assert s["per_soort"]["ongeval"] == 1
    assert s["per_status"]["gemeld"] == 3
    # Geen enkel persoonsgegeven in de samenvatting
    assert "Jan de Vries" not in str(s)
    assert "letsel" not in str(s)


# ── Verwijderen ──────────────────────────────────────────────────────

def test_viewer_mag_eigen_melding_niet_verwijderen(client, viewer_user):
    """Bewust: een registratie die de melder zelf kan wissen is geen registratie."""
    i = _meld(client, viewer_user)
    r = client.delete("/api/incidenten/" + i["id"], headers=auth(viewer_user))
    assert r.status_code == 403, r.text


def test_admin_kan_verwijderen(client, admin_user):
    i = _meld(client, admin_user)
    r = client.delete("/api/incidenten/" + i["id"], headers=auth(admin_user))
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        assert db.query(Incident).filter(Incident.id == i["id"]).first() is None
    finally:
        db.close()
