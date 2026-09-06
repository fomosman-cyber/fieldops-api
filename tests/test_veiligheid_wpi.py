"""Werkplekinspectie (WPI) — de rondgang langs de controlevragen.

Wat hier bewaakt wordt:
  - de vragenlijst zelf is intern consistent (eigen integriteitstest onderaan)
  - een rondgang wordt compleet aangemaakt, niet gaandeweg opgebouwd
  - "niet in orde" zonder toelichting wordt geweigerd
  - afronden weigert zolang er vragen open staan
  - n.v.t. telt niet mee in de score
  - een actie mag ook na afronden nog worden afgevinkt
  - de vraagtekst wordt gesnapshot, zodat een oude rondgang blijft kloppen
"""
import pytest

from database import SessionLocal
from models import (AccountStatus, Organization, Project, SubscriptionPlan,
                    Werkplekinspectie, WerkplekinspectieAntwoord)

import wpi_checklist as wc

from .conftest import _make_user, auth


@pytest.fixture
def other_org_admin():
    db = SessionLocal()
    try:
        andere = Organization(name="AndereOrg", plan=SubscriptionPlan.PROFESSIONAL,
                              status=AccountStatus.ACTIVE, max_users=10)
        db.add(andere)
        db.commit()
        db.refresh(andere)
        return _make_user(db, "andere-wpi@test.nl", org=andere)
    finally:
        db.close()


def _project(org_id, maker_id, naam="N207 Alphen"):
    db = SessionLocal()
    try:
        p = Project(name=naam, organization_id=org_id, status="active", created_by=maker_id)
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id
    finally:
        db.close()


def _start(client, user, project_id=None):
    pid = project_id or _project(user.organization_id, user.id)
    r = client.post("/api/wpi/", json={"project_id": pid, "locatie": "Bij de inrit"},
                    headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


def _alles_beantwoorden(client, user, wpi, antwoord="ja"):
    for a in wpi["antwoorden"]:
        body = {"antwoord": antwoord}
        if antwoord == "nee":
            body["toelichting"] = "Toelichting"
        r = client.patch(f"/api/wpi/{wpi['id']}/antwoorden/{a['id']}", json=body,
                         headers=auth(user))
        assert r.status_code == 200, r.text


# ── Starten ──────────────────────────────────────────────────────────

def test_rondgang_start_met_de_volledige_lijst(client, manager_user):
    """Vooraf aanmaken, niet gaandeweg: anders sla je makkelijk iets over."""
    w = _start(client, manager_user)
    assert w["status"] == "concept"
    assert len(w["antwoorden"]) == len(wc.VRAGEN)
    assert w["checklist_versie"] == wc.WPI_VERSION
    assert all(a["antwoord"] is None for a in w["antwoorden"])
    assert w["inspecteur_naam"], "inspecteur hoort gedenormaliseerd te worden vastgelegd"


def test_vraagtekst_wordt_gesnapshot(client, admin_user):
    """Wijzigt de checklist later, dan moet een oude rondgang blijven tonen wat
    er destijds gevraagd is."""
    w = _start(client, admin_user)
    codes = {a["question_code"]: a["vraag"] for a in w["antwoorden"]}
    for v in wc.VRAGEN:
        assert codes[v["code"]] == v["vraag"]


def test_technicus_mag_geen_rondgang_starten(client, technician_user):
    pid = _project(technician_user.organization_id, technician_user.id)
    r = client.post("/api/wpi/", json={"project_id": pid}, headers=auth(technician_user))
    assert r.status_code == 403, r.text


def test_project_is_verplicht(client, admin_user):
    r = client.post("/api/wpi/", json={"locatie": "Ergens"}, headers=auth(admin_user))
    assert r.status_code == 422, r.text


def test_project_van_andere_org_geeft_404(client, admin_user, other_org_admin):
    vreemd = _project(other_org_admin.organization_id, other_org_admin.id, "Andermans")
    r = client.post("/api/wpi/", json={"project_id": vreemd}, headers=auth(admin_user))
    assert r.status_code == 404, r.text


def test_rondgang_van_andere_org_is_onzichtbaar(client, admin_user, other_org_admin):
    w = _start(client, admin_user)
    assert client.get(f"/api/wpi/{w['id']}", headers=auth(other_org_admin)).status_code == 404
    assert client.get("/api/wpi/", headers=auth(other_org_admin)).json() == []


# ── Beantwoorden ─────────────────────────────────────────────────────

def test_niet_in_orde_zonder_toelichting_wordt_geweigerd(client, admin_user):
    """Zonder uitleg kan degene die het moet oplossen er niets mee."""
    w = _start(client, admin_user)
    aid = w["antwoorden"][0]["id"]
    r = client.patch(f"/api/wpi/{w['id']}/antwoorden/{aid}", json={"antwoord": "nee"},
                     headers=auth(admin_user))
    assert r.status_code == 400, r.text
    assert "toelichting" in r.json()["detail"].lower()


def test_niet_in_orde_met_toelichting_en_actie(client, admin_user, technician_user):
    w = _start(client, admin_user)
    aid = w["antwoorden"][0]["id"]
    r = client.patch(f"/api/wpi/{w['id']}/antwoorden/{aid}", json={
        "antwoord": "nee",
        "toelichting": "V&G-plan lag in de bus, niet op de werkplek",
        "actie": "Plan uitprinten en ophangen in de keet",
        "actiehouder_id": technician_user.id,
    }, headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["actiehouder_naam"], "naam hoort uit het account te komen"
    assert r.json()["actie_gereed"] is False


def test_in_orde_heeft_geen_toelichting_nodig(client, admin_user):
    w = _start(client, admin_user)
    aid = w["antwoorden"][0]["id"]
    r = client.patch(f"/api/wpi/{w['id']}/antwoorden/{aid}", json={"antwoord": "ja"},
                     headers=auth(admin_user))
    assert r.status_code == 200, r.text


def test_ongeldig_antwoord_wordt_geweigerd(client, admin_user):
    w = _start(client, admin_user)
    aid = w["antwoorden"][0]["id"]
    r = client.patch(f"/api/wpi/{w['id']}/antwoorden/{aid}", json={"antwoord": "misschien"},
                     headers=auth(admin_user))
    assert r.status_code == 422, r.text


# ── Afronden ─────────────────────────────────────────────────────────

def test_afronden_weigert_bij_open_vragen(client, admin_user):
    """Een halve rondgang met een mooie score is misleidend."""
    w = _start(client, admin_user)
    aid = w["antwoorden"][0]["id"]
    client.patch(f"/api/wpi/{w['id']}/antwoorden/{aid}", json={"antwoord": "ja"},
                 headers=auth(admin_user))
    r = client.post(f"/api/wpi/{w['id']}/afronden", headers=auth(admin_user))
    assert r.status_code == 400, r.text
    assert "open" in r.json()["detail"].lower()


def test_afronden_berekent_score(client, admin_user):
    w = _start(client, admin_user)
    _alles_beantwoorden(client, admin_user, w, "ja")
    r = client.post(f"/api/wpi/{w['id']}/afronden", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["score_pct"] == 100
    assert r.json()["aantal_niet_in_orde"] == 0
    assert r.json()["afgerond_op"]


def test_nvt_telt_niet_mee_in_de_score(client, admin_user):
    """Anders zakt de score van een klein werk waar de helft niet speelt, en
    dan gaan mensen n.v.t. vermijden en lukraak 'in orde' invullen."""
    w = _start(client, admin_user)
    for i, a in enumerate(w["antwoorden"]):
        antwoord = "nvt" if i % 2 else "ja"
        client.patch(f"/api/wpi/{w['id']}/antwoorden/{a['id']}", json={"antwoord": antwoord},
                     headers=auth(admin_user))
    r = client.post(f"/api/wpi/{w['id']}/afronden", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["score_pct"] == 100, "n.v.t. hoort de score niet te drukken"


def test_afgeronde_rondgang_is_bevroren(client, admin_user):
    w = _start(client, admin_user)
    _alles_beantwoorden(client, admin_user, w, "ja")
    client.post(f"/api/wpi/{w['id']}/afronden", headers=auth(admin_user))

    aid = w["antwoorden"][0]["id"]
    assert client.patch(f"/api/wpi/{w['id']}/antwoorden/{aid}", json={"antwoord": "nvt"},
                        headers=auth(admin_user)).status_code == 409
    assert client.patch(f"/api/wpi/{w['id']}", json={"locatie": "Anders"},
                        headers=auth(admin_user)).status_code == 409
    assert client.delete(f"/api/wpi/{w['id']}", headers=auth(admin_user)).status_code == 409
    assert client.post(f"/api/wpi/{w['id']}/afronden",
                       headers=auth(admin_user)).status_code == 409


def test_actie_afvinken_mag_ook_na_afronden(client, admin_user):
    """Het punt blijft staan zoals het geconstateerd is, maar het werk eraan
    loopt door."""
    w = _start(client, admin_user)
    _alles_beantwoorden(client, admin_user, w, "nee")
    client.post(f"/api/wpi/{w['id']}/afronden", headers=auth(admin_user))

    aid = w["antwoorden"][0]["id"]
    r = client.patch(f"/api/wpi/{w['id']}/antwoorden/{aid}", json={"actie_gereed": True},
                     headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["actie_gereed"] is True
    # Maar het antwoord zelf blijft bevroren.
    assert client.patch(f"/api/wpi/{w['id']}/antwoorden/{aid}",
                        json={"actie_gereed": True, "antwoord": "ja"},
                        headers=auth(admin_user)).status_code == 409


# ── Openstaande acties ───────────────────────────────────────────────

def test_open_acties_worden_verzameld(client, admin_user):
    w = _start(client, admin_user)
    for a in w["antwoorden"][:3]:
        client.patch(f"/api/wpi/{w['id']}/antwoorden/{a['id']}",
                     json={"antwoord": "nee", "toelichting": "Stuk", "actie": "Repareren"},
                     headers=auth(admin_user))

    r = client.get("/api/wpi/acties/open", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert len(r.json()) == 3
    assert r.json()[0]["project_name"] == "N207 Alphen"

    # Afvinken haalt hem uit de lijst.
    client.patch(f"/api/wpi/{w['id']}/antwoorden/{w['antwoorden'][0]['id']}",
                 json={"actie_gereed": True}, headers=auth(admin_user))
    assert len(client.get("/api/wpi/acties/open", headers=auth(admin_user)).json()) == 2


def test_acties_route_wordt_niet_opgeslokt(client, admin_user):
    """/acties/open mag niet als een wpi-id worden gelezen."""
    r = client.get("/api/wpi/acties/open", headers=auth(admin_user))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_checklist_route_wordt_niet_opgeslokt(client, admin_user):
    r = client.get("/api/wpi/checklist", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["versie"] == wc.WPI_VERSION
    assert len(r.json()["vragen"]) == len(wc.VRAGEN)


# ── PDF ──────────────────────────────────────────────────────────────

def test_export_pdf(client, admin_user):
    w = _start(client, admin_user)
    _alles_beantwoorden(client, admin_user, w, "ja")
    client.post(f"/api/wpi/{w['id']}/afronden", headers=auth(admin_user))

    r = client.get(f"/api/wpi/{w['id']}/export.pdf", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:4] == b"%PDF"
    assert len(r.content) > 1000


def test_export_pdf_van_concept_met_bevindingen(client, admin_user):
    """Ook een lopende rondgang moet een leesbaar rapport geven."""
    w = _start(client, admin_user)
    client.patch(f"/api/wpi/{w['id']}/antwoorden/{w['antwoorden'][0]['id']}",
                 json={"antwoord": "nee", "toelichting": "Ontbreekt — zie foto"},
                 headers=auth(admin_user))
    r = client.get(f"/api/wpi/{w['id']}/export.pdf", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"


def test_export_pdf_van_andere_org_geeft_404(client, admin_user, other_org_admin):
    w = _start(client, admin_user)
    r = client.get(f"/api/wpi/{w['id']}/export.pdf", headers=auth(other_org_admin))
    assert r.status_code == 404


# ── Opruimen ─────────────────────────────────────────────────────────

def test_verwijderen_ruimt_antwoorden_op(client, admin_user):
    w = _start(client, admin_user)
    r = client.delete(f"/api/wpi/{w['id']}", headers=auth(admin_user))
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        assert db.query(Werkplekinspectie).filter(
            Werkplekinspectie.id == w["id"]).first() is None
        assert db.query(WerkplekinspectieAntwoord).filter(
            WerkplekinspectieAntwoord.wpi_id == w["id"]).count() == 0
    finally:
        db.close()


# ── Integriteit van de vragenlijst ───────────────────────────────────

def test_vraagcodes_zijn_uniek():
    codes = [v["code"] for v in wc.VRAGEN]
    dubbel = sorted({c for c in codes if codes.count(c) > 1})
    assert not dubbel, f"Dubbele vraagcodes: {dubbel}"


def test_elke_vraag_heeft_de_kernvelden():
    problemen = []
    for v in wc.VRAGEN:
        code = v.get("code") or "<geen code>"
        if not v.get("vraag"):
            problemen.append(f"{code}: lege vraagtekst")
        if not v.get("uitleg"):
            problemen.append(f"{code}: geen uitleg")
        if not v.get("norm_ref"):
            problemen.append(f"{code}: geen norm_ref")
        if v.get("categorie") not in wc.CATEGORIEEN:
            problemen.append(f"{code}: onbekende categorie {v.get('categorie')!r}")
    assert not problemen, "Checklist-problemen:\n" + "\n".join(problemen)


def test_alle_vragen_zijn_positief_geformuleerd():
    """Een lijst waarin de ene vraag omgekeerd werkt dan de andere levert
    fouten op bij iemand die in de regen staat af te vinken."""
    for v in wc.VRAGEN:
        assert v["type"] == "ja_nee_nvt", f"{v['code']}: type {v['type']}"
        assert v["attention_when"] is False, (
            f"{v['code']}: attention_when moet False zijn — een NEE is het aandachtspunt")


def test_norm_ref_verwijst_naar_een_bekende_regeling():
    import re
    toegestaan = re.compile(r"VCA|Arbobesluit|CROW|NEN|WION|Arbowet", re.I)
    fout = [v["code"] for v in wc.VRAGEN if not toegestaan.search(v["norm_ref"])]
    assert not fout, f"Onbekende norm_ref bij: {fout}"


def test_elke_categorie_heeft_vragen():
    for cat in wc.CATEGORIEEN:
        assert wc.vragen_voor(cat), f"Categorie {cat} heeft geen vragen"


def test_score_negeert_nvt_en_onbeantwoord():
    telling = wc.bereken_score([
        {"antwoord": "ja"}, {"antwoord": "ja"}, {"antwoord": "nee"},
        {"antwoord": "nvt"}, {"antwoord": None},
    ])
    assert telling["beoordeeld"] == 3
    assert telling["score_pct"] == 67
    assert telling["nvt"] == 1


def test_score_zonder_beoordeelde_vragen_is_none():
    """Een rondgang waar alles n.v.t. is krijgt geen score van 0% — dat zou
    onterecht als een slechte inspectie lezen."""
    assert wc.bereken_score([{"antwoord": "nvt"}])["score_pct"] is None
    assert wc.bereken_score([])["score_pct"] is None
