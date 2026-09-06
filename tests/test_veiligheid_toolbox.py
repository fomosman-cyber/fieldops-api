"""Toolbox — de veiligheidsbespreking op de bouwplaats.

Wat hier bewaakt wordt:
  - alleen admin/manager stelt op; tekenen mag iedereen
  - een toolbox hangt verplicht aan een project uit je EIGEN organisatie
  - externen zonder account kunnen op de presentielijst en kunnen tekenen
  - afgesloten is afgesloten: daarna wijzigt er niets meer
  - de AI-generatie doet nooit een netwerkcall in tests en valt netjes terug
  - de PDF genereert ook met een handtekening erin, en ook zonder deelnemers
"""
import base64

import pytest

from database import SessionLocal
from models import AccountStatus, Melding, Organization, Project, SubscriptionPlan, Toolbox

from .conftest import _make_user, auth


@pytest.fixture
def other_admin():
    """Beheerder van een TWEEDE organisatie — voor de multi-tenant-checks.

    Geeft het object terug nadat de sessie is gesloten, dus gebruik alleen
    kolom-attributen (.id, .organization_id); een lazy relation als
    .organization gooit hier DetachedInstanceError.
    """
    db = SessionLocal()
    try:
        andere_org = Organization(name="AndereOrg",
                                  plan=SubscriptionPlan.PROFESSIONAL,
                                  status=AccountStatus.ACTIVE, max_users=10)
        db.add(andere_org)
        db.commit()
        db.refresh(andere_org)
        return _make_user(db, "andere@test.nl", org=andere_org)
    finally:
        db.close()

# Echte 1x1 PNG — een verzonnen base64-blob laat fpdf2 klappen zodra hij als
# handtekening in de PDF wordt gezet.
_PNG_1x1 = ("data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk"
            "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


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


def _melding(org_id, project_id, maker_id, titel="Afzetting omgereden"):
    db = SessionLocal()
    try:
        m = Melding(title=titel, organization_id=org_id, project_id=project_id,
                    status="open", priority="hoog", category="schade",
                    created_by=maker_id)
        db.add(m)
        db.commit()
        db.refresh(m)
        return m.id
    finally:
        db.close()


def _maak_toolbox(client, user, project_id, onderwerp="Werken langs de rijbaan"):
    r = client.post("/api/toolbox/", json={
        "project_id": project_id,
        "onderwerp": onderwerp,
        "inleiding": "Vandaag werken we langs de rijbaan.",
        "risicos": ["Passerend verkeer"],
        "maatregelen": ["Afzetting volgens CROW 96b"],
        "bespreekpunten": ["Wie bewaakt de afzetting?"],
    }, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


# ── Aanmaken en rollen ───────────────────────────────────────────────

def test_manager_mag_toolbox_aanmaken(client, manager_user):
    pid = _project(manager_user.organization_id, manager_user.id)
    t = _maak_toolbox(client, manager_user, pid)
    assert t["onderwerp"] == "Werken langs de rijbaan"
    assert t["status"] == "concept"
    assert t["risicos"] == ["Passerend verkeer"]
    assert t["houder_naam"], "houder moet gedenormaliseerd worden vastgelegd voor de PDF"


def test_technicus_mag_geen_toolbox_aanmaken(client, technician_user):
    """Opstellen is admin/manager-only; tekenen mag hij straks wel."""
    pid = _project(technician_user.organization_id, technician_user.id)
    r = client.post("/api/toolbox/", json={"project_id": pid, "onderwerp": "Test"},
                    headers=auth(technician_user))
    assert r.status_code == 403, r.text


def test_toolbox_zonder_project_wordt_geweigerd(client, admin_user):
    r = client.post("/api/toolbox/", json={"onderwerp": "Los onderwerp"},
                    headers=auth(admin_user))
    assert r.status_code == 422, r.text


def test_project_van_andere_org_geeft_404(client, admin_user, other_admin):
    """Zonder org-check op het project kon je je toolbox aan andermans project hangen."""
    vreemd_pid = _project(other_admin.organization_id, other_admin.id, "Andermans project")
    r = client.post("/api/toolbox/", json={"project_id": vreemd_pid, "onderwerp": "Test"},
                    headers=auth(admin_user))
    assert r.status_code == 404, r.text


def test_toolbox_van_andere_org_is_onzichtbaar(client, admin_user, other_admin):
    pid = _project(admin_user.organization_id, admin_user.id)
    t = _maak_toolbox(client, admin_user, pid)

    r = client.get(f"/api/toolbox/{t['id']}", headers=auth(other_admin))
    assert r.status_code == 404, r.text

    r2 = client.get("/api/toolbox/", headers=auth(other_admin))
    assert r2.status_code == 200, r2.text
    assert r2.json() == []


# ── Presentielijst ───────────────────────────────────────────────────

def test_externe_deelnemer_zonder_account_kan_tekenen(client, admin_user):
    pid = _project(admin_user.organization_id, admin_user.id)
    t = _maak_toolbox(client, admin_user, pid)

    r = client.post(f"/api/toolbox/{t['id']}/deelnemers",
                    json={"naam": "Jan de Vries", "bedrijf": "De Vries Infra BV"},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["extern"] is True
    assert d["user_id"] is None
    assert d["getekend"] is False

    r2 = client.post(f"/api/toolbox/{t['id']}/deelnemers/{d['id']}/sign",
                     json={"signature_data_url": _PNG_1x1},
                     headers=auth(admin_user))
    assert r2.status_code == 200, r2.text
    assert r2.json()["getekend"] is True
    assert r2.json()["signed_at"]


def test_deelnemer_uit_account_krijgt_naam_automatisch(client, admin_user, technician_user):
    pid = _project(admin_user.organization_id, admin_user.id)
    t = _maak_toolbox(client, admin_user, pid)

    r = client.post(f"/api/toolbox/{t['id']}/deelnemers",
                    json={"user_id": technician_user.id}, headers=auth(admin_user))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["extern"] is False
    assert d["naam"], "naam hoort uit het account te komen"


def test_deelnemer_zonder_naam_en_zonder_account_geeft_400(client, admin_user):
    pid = _project(admin_user.organization_id, admin_user.id)
    t = _maak_toolbox(client, admin_user, pid)
    r = client.post(f"/api/toolbox/{t['id']}/deelnemers", json={},
                    headers=auth(admin_user))
    assert r.status_code == 400, r.text


def test_technicus_mag_wel_tekenen(client, admin_user, technician_user):
    """De ploeg tekent op het toestel van de uitvoerder — geen beheerrecht nodig."""
    pid = _project(admin_user.organization_id, admin_user.id)
    t = _maak_toolbox(client, admin_user, pid)
    d = client.post(f"/api/toolbox/{t['id']}/deelnemers", json={"naam": "Piet"},
                    headers=auth(admin_user)).json()

    r = client.post(f"/api/toolbox/{t['id']}/deelnemers/{d['id']}/sign",
                    json={"signature_data_url": _PNG_1x1},
                    headers=auth(technician_user))
    assert r.status_code == 200, r.text


def test_tekenen_zonder_data_url_geeft_400(client, admin_user):
    pid = _project(admin_user.organization_id, admin_user.id)
    t = _maak_toolbox(client, admin_user, pid)
    d = client.post(f"/api/toolbox/{t['id']}/deelnemers", json={"naam": "Piet"},
                    headers=auth(admin_user)).json()

    r = client.post(f"/api/toolbox/{t['id']}/deelnemers/{d['id']}/sign",
                    json={"signature_data_url": "not-a-data-url"},
                    headers=auth(admin_user))
    assert r.status_code == 400, r.text


def test_eerste_handtekening_zet_status_op_gehouden(client, admin_user):
    pid = _project(admin_user.organization_id, admin_user.id)
    t = _maak_toolbox(client, admin_user, pid)
    d = client.post(f"/api/toolbox/{t['id']}/deelnemers", json={"naam": "Piet"},
                    headers=auth(admin_user)).json()
    client.post(f"/api/toolbox/{t['id']}/deelnemers/{d['id']}/sign",
                json={"signature_data_url": _PNG_1x1}, headers=auth(admin_user))

    r = client.get(f"/api/toolbox/{t['id']}", headers=auth(admin_user))
    assert r.json()["status"] == "gehouden"


# ── Afsluiten bevriest de registratie ────────────────────────────────

def test_afgesloten_toolbox_is_bevroren(client, admin_user):
    pid = _project(admin_user.organization_id, admin_user.id)
    t = _maak_toolbox(client, admin_user, pid)
    d = client.post(f"/api/toolbox/{t['id']}/deelnemers", json={"naam": "Piet"},
                    headers=auth(admin_user)).json()

    r = client.post(f"/api/toolbox/{t['id']}/afsluiten", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "afgesloten"

    # Niets mag hierna nog wijzigen
    assert client.patch(f"/api/toolbox/{t['id']}", json={"onderwerp": "Anders"},
                        headers=auth(admin_user)).status_code == 409
    assert client.post(f"/api/toolbox/{t['id']}/deelnemers", json={"naam": "Laatkomer"},
                       headers=auth(admin_user)).status_code == 409
    assert client.post(f"/api/toolbox/{t['id']}/deelnemers/{d['id']}/sign",
                       json={"signature_data_url": _PNG_1x1},
                       headers=auth(admin_user)).status_code == 409
    assert client.delete(f"/api/toolbox/{t['id']}",
                         headers=auth(admin_user)).status_code == 409
    assert client.post(f"/api/toolbox/{t['id']}/afsluiten",
                       headers=auth(admin_user)).status_code == 409


# ── AI-generatie ─────────────────────────────────────────────────────

def test_genereren_zonder_sleutel_geeft_sjabloon(client, admin_user):
    """Geen ANTHROPIC_API_KEY (de standaard in tests) -> bruikbaar sjabloon, geen 500."""
    pid = _project(admin_user.organization_id, admin_user.id)
    r = client.post("/api/toolbox/genereer",
                    json={"project_id": pid, "onderwerp": "Werken langs de rijbaan"},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["bron"] == "sjabloon"
    assert data["risicos"] and data["maatregelen"] and data["bespreekpunten"]
    assert "rijbaan" in data["inleiding"].lower()


def test_genereren_gebruikt_projectcontext(client, admin_user, monkeypatch):
    """De AI moet de assets en openstaande meldingen van dit project meekrijgen."""
    pid = _project(admin_user.organization_id, admin_user.id)
    _melding(admin_user.organization_id, pid, admin_user.id)

    gezien = {}

    def _fake(**kwargs):
        gezien.update(kwargs)
        return {"inleiding": "Nagemaakt", "risicos": ["r"], "maatregelen": ["m"],
                "bespreekpunten": ["b"], "bron": "claude",
                "model_id": "claude-opus-5", "prompt_versie": "v1.0-toolbox"}

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("toolbox_ai.genereer_toolbox", _fake)

    r = client.post("/api/toolbox/genereer",
                    json={"project_id": pid, "onderwerp": "Werken langs de rijbaan"},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert gezien.get("project_naam") == "N207 Alphen"
    assert gezien.get("meldingen"), "openstaande meldingen horen mee te gaan"
    assert gezien["meldingen"][0]["titel"] == "Afzetting omgereden"
    assert r.json()["context_gebruikt"]["open_meldingen"] == 1


def test_genereren_valt_terug_bij_ai_fout(client, admin_user, monkeypatch):
    """Een storing bij Anthropic mag de uitvoerder niet blokkeren."""
    pid = _project(admin_user.organization_id, admin_user.id)

    def _boom(**kwargs):
        raise RuntimeError("Anthropic API down")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("toolbox_ai._roep_claude", _boom)

    r = client.post("/api/toolbox/genereer",
                    json={"project_id": pid, "onderwerp": "Werken in de kou"},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["bron"] == "sjabloon"


def test_afgeronde_meldingen_tellen_niet_mee(client, admin_user, monkeypatch):
    """Statussen zijn open/in_behandeling/afgerond — afgerond hoort er niet bij."""
    pid = _project(admin_user.organization_id, admin_user.id)
    db = SessionLocal()
    try:
        db.add(Melding(title="Al opgelost", organization_id=admin_user.organization_id,
                       project_id=pid, status="afgerond", created_by=admin_user.id))
        db.commit()
    finally:
        db.close()

    r = client.post("/api/toolbox/genereer",
                    json={"project_id": pid, "onderwerp": "Test"},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["context_gebruikt"]["open_meldingen"] == 0


def test_technicus_mag_niet_genereren(client, technician_user):
    pid = _project(technician_user.organization_id, technician_user.id)
    r = client.post("/api/toolbox/genereer", json={"project_id": pid, "onderwerp": "X"},
                    headers=auth(technician_user))
    assert r.status_code == 403, r.text


# ── PDF ──────────────────────────────────────────────────────────────

def test_export_pdf_met_handtekening(client, admin_user):
    pid = _project(admin_user.organization_id, admin_user.id)
    t = _maak_toolbox(client, admin_user, pid)
    d = client.post(f"/api/toolbox/{t['id']}/deelnemers",
                    json={"naam": "Jan de Vries", "bedrijf": "De Vries Infra BV"},
                    headers=auth(admin_user)).json()
    client.post(f"/api/toolbox/{t['id']}/deelnemers/{d['id']}/sign",
                json={"signature_data_url": _PNG_1x1}, headers=auth(admin_user))

    r = client.get(f"/api/toolbox/{t['id']}/export.pdf", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:4] == b"%PDF"
    assert len(r.content) > 1000


def test_export_pdf_zonder_deelnemers(client, admin_user):
    """Een toolbox zonder presentielijst moet nog steeds een PDF opleveren."""
    pid = _project(admin_user.organization_id, admin_user.id)
    t = _maak_toolbox(client, admin_user, pid)
    r = client.get(f"/api/toolbox/{t['id']}/export.pdf", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"


def test_export_pdf_met_typografische_tekens(client, admin_user):
    """Claude schrijft en-dashes en krulaanhalingstekens; Helvetica is latin-1."""
    pid = _project(admin_user.organization_id, admin_user.id)
    r = client.post("/api/toolbox/", json={
        "project_id": pid,
        "onderwerp": "Werken langs de weg — met “aandacht” voor het verkeer…",
        "inleiding": "Let op: 2 m breed → smalle strook · €500 boete",
        "risicos": ["Passerend verkeer – zeer dichtbij"],
        "maatregelen": ["Afzetting — CROW 96b"],
        "bespreekpunten": ["Wie let er op?"],
    }, headers=auth(admin_user))
    assert r.status_code == 200, r.text
    tid = r.json()["id"]

    r2 = client.get(f"/api/toolbox/{tid}/export.pdf", headers=auth(admin_user))
    assert r2.status_code == 200, r2.text
    assert r2.content[:4] == b"%PDF"


def test_export_pdf_onbekende_id_geeft_404(client, admin_user):
    r = client.get("/api/toolbox/bestaat-niet/export.pdf", headers=auth(admin_user))
    assert r.status_code == 404


def test_export_pdf_van_andere_org_geeft_404(client, admin_user, other_admin):
    pid = _project(admin_user.organization_id, admin_user.id)
    t = _maak_toolbox(client, admin_user, pid)
    r = client.get(f"/api/toolbox/{t['id']}/export.pdf", headers=auth(other_admin))
    assert r.status_code == 404


# ── Opruimen ─────────────────────────────────────────────────────────

def test_verwijderen_ruimt_deelnemers_op(client, admin_user):
    pid = _project(admin_user.organization_id, admin_user.id)
    t = _maak_toolbox(client, admin_user, pid)
    client.post(f"/api/toolbox/{t['id']}/deelnemers", json={"naam": "Piet"},
                headers=auth(admin_user))

    r = client.delete(f"/api/toolbox/{t['id']}", headers=auth(admin_user))
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        assert db.query(Toolbox).filter(Toolbox.id == t["id"]).first() is None
    finally:
        db.close()


def test_handtekening_wordt_volledig_bewaard(client, admin_user):
    """Text-kolom, geen VARCHAR(500): een echte handtekening is fors groter."""
    pid = _project(admin_user.organization_id, admin_user.id)
    t = _maak_toolbox(client, admin_user, pid)
    d = client.post(f"/api/toolbox/{t['id']}/deelnemers", json={"naam": "Piet"},
                    headers=auth(admin_user)).json()

    lange_sig = "data:image/png;base64," + base64.b64encode(b"x" * 4000).decode()
    r = client.post(f"/api/toolbox/{t['id']}/deelnemers/{d['id']}/sign",
                    json={"signature_data_url": lange_sig}, headers=auth(admin_user))
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        tb = db.query(Toolbox).filter(Toolbox.id == t["id"]).first()
        bewaard = tb.deelnemers[0].signature_data_url
        assert bewaard == lange_sig, "handtekening werd afgekapt"
    finally:
        db.close()
