"""LMRA — de Laatste Minuut Risico Analyse vlak voor de start van een taak.

Wat hier bewaakt wordt:
  - de vragenlijst is intern consistent (integriteitstest onderaan)
  - iedereen mag er een starten, ook zonder project en zonder leidinggevende
  - een LMRA hoort bij wie hem gestart is; een ander vult hem niet in
  - afronden weigert zolang er vragen open staan
  - een "nee" zonder maatregel mag niet als "veilig" worden afgesloten
  - "niet starten" mag altijd — daar zit geen drempel op
  - een afgesloten LMRA wijzigt niet meer
  - de vraagtekst wordt gesnapshot, zodat een oude LMRA blijft kloppen
"""
import pytest

import lmra_checklist as lc
from database import SessionLocal
from models import (AccountStatus, Lmra, LmraAntwoord, Organization, Project,
                    SubscriptionPlan)

from .conftest import _make_user, auth


@pytest.fixture
def andere_org_gebruiker():
    db = SessionLocal()
    try:
        andere = Organization(name="AndereOrgLmra", plan=SubscriptionPlan.PROFESSIONAL,
                              status=AccountStatus.ACTIVE, max_users=10)
        db.add(andere)
        db.commit()
        db.refresh(andere)
        return _make_user(db, "andere-lmra@test.nl", org=andere)
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


def _start(client, user, **body):
    r = client.post("/api/lmra/", json=body or {"taak": "Hotbox op de Kadoelenweg"},
                    headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


def _alles(client, user, lmra, antwoord="ja"):
    for a in lmra["antwoorden"]:
        r = client.patch(f"/api/lmra/{lmra['id']}/antwoorden/{a['id']}",
                         json={"antwoord": antwoord}, headers=auth(user))
        assert r.status_code == 200, r.text


# ── Starten ──────────────────────────────────────────────────────────

def test_lmra_start_met_de_volledige_lijst(client, technician_user):
    """Vooraf aanmaken, niet gaandeweg: anders sla je makkelijk iets over."""
    l = _start(client, technician_user)
    assert len(l["antwoorden"]) == len(lc.VRAGEN)
    assert l["status"] == "concept"
    assert l["checklist_versie"] == lc.LMRA_VERSION
    assert l["uitvoerder_naam"]
    assert all(a["antwoord"] is None for a in l["antwoorden"])
    # De vragen staan in de looproute-volgorde van de lijst.
    assert [a["question_code"] for a in l["antwoorden"]] == [v["code"] for v in lc.VRAGEN]


def test_iedereen_mag_een_lmra_starten(client, viewer_user, technician_user, contractor_user):
    """Dit is het verschil met de werkplekinspectie: de LMRA doet degene die
    zo begint. Wachten op een leidinggevende is precies het gedrag dat je hier
    niet wilt."""
    for gebruiker in (viewer_user, technician_user, contractor_user):
        r = client.post("/api/lmra/", json={"taak": "Scheuren vullen"},
                        headers=auth(gebruiker))
        assert r.status_code == 200, r.text


def test_lmra_mag_zonder_project(client, technician_user):
    """Een storing in de berm heeft geen projectnummer, en hoort wel
    vastgelegd te worden."""
    l = _start(client, technician_user, taak="Gat dichten na melding")
    assert l["project_id"] is None
    assert l["taak"] == "Gat dichten na melding"


def test_lmra_koppelt_aan_project_en_melding(client, admin_user):
    pid = _project(admin_user.organization_id, admin_user.id)
    r = client.post("/api/lmra/", json={"project_id": pid, "taak": "Frezen"},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == pid
    assert r.json()["project_naam"] == "N207 Alphen"


def test_onbekend_project_wordt_geweigerd(client, technician_user):
    r = client.post("/api/lmra/", json={"project_id": "bestaat-niet"},
                    headers=auth(technician_user))
    assert r.status_code == 404


# ── Invullen ─────────────────────────────────────────────────────────

def test_alleen_de_eigenaar_vult_zijn_eigen_lmra_in(client, technician_user, contractor_user):
    """Een LMRA is een persoonlijke verantwoording. Iemand anders die er
    antwoorden in zet maakt er een papieren werkelijkheid van."""
    l = _start(client, technician_user)
    r = client.patch(f"/api/lmra/{l['id']}/antwoorden/{l['antwoorden'][0]['id']}",
                     json={"antwoord": "ja"}, headers=auth(contractor_user))
    assert r.status_code == 403


def test_beheerder_mag_wel_bij_een_lmra_van_een_ander(client, technician_user, admin_user):
    l = _start(client, technician_user)
    r = client.patch(f"/api/lmra/{l['id']}/antwoorden/{l['antwoorden'][0]['id']}",
                     json={"antwoord": "ja"}, headers=auth(admin_user))
    assert r.status_code == 200, r.text


def test_een_andere_organisatie_ziet_de_lmra_niet(client, technician_user, andere_org_gebruiker):
    l = _start(client, technician_user)
    r = client.get(f"/api/lmra/{l['id']}", headers=auth(andere_org_gebruiker))
    assert r.status_code == 404


# ── Afronden ─────────────────────────────────────────────────────────

def test_afronden_weigert_met_open_vragen(client, technician_user):
    """Een half ingevulde LMRA is geen LMRA."""
    l = _start(client, technician_user)
    client.patch(f"/api/lmra/{l['id']}/antwoorden/{l['antwoorden'][0]['id']}",
                 json={"antwoord": "ja"}, headers=auth(technician_user))
    r = client.post(f"/api/lmra/{l['id']}/afronden", json={"oordeel": "veilig"},
                    headers=auth(technician_user))
    assert r.status_code == 400
    assert "niet beantwoord" in r.json()["detail"]


def test_alles_in_orde_sluit_af_als_veilig(client, technician_user):
    l = _start(client, technician_user)
    _alles(client, technician_user, l)
    r = client.post(f"/api/lmra/{l['id']}/afronden", json={"oordeel": "veilig"},
                    headers=auth(technician_user))
    assert r.status_code == 200, r.text
    uit = r.json()
    assert uit["status"] == "veilig"
    assert uit["oordeel"] == "veilig"
    assert uit["aantal_niet_in_orde"] == 0
    assert uit["afgerond_op"]


def test_nee_zonder_maatregel_mag_niet_veilig_worden_afgesloten(client, technician_user):
    """Dit is de hele bedoeling van het formulier: wat heb je eraan gedaan
    voordat je begon?"""
    l = _start(client, technician_user)
    _alles(client, technician_user, l, antwoord="nee")
    r = client.post(f"/api/lmra/{l['id']}/afronden", json={"oordeel": "veilig"},
                    headers=auth(technician_user))
    assert r.status_code == 400
    assert "maatregel" in r.json()["detail"]


def test_nee_met_maatregel_mag_wel_veilig(client, technician_user):
    l = _start(client, technician_user)
    _alles(client, technician_user, l)
    eerste = l["antwoorden"][0]["id"]
    client.patch(f"/api/lmra/{l['id']}/antwoorden/{eerste}",
                 json={"antwoord": "nee", "maatregel": "Afzetting verplaatst en opnieuw gekeken"},
                 headers=auth(technician_user))
    r = client.post(f"/api/lmra/{l['id']}/afronden", json={"oordeel": "veilig"},
                    headers=auth(technician_user))
    assert r.status_code == 200, r.text
    assert r.json()["aantal_niet_in_orde"] == 1


def test_niet_starten_kent_geen_drempel(client, technician_user):
    """Stoppen moet altijd kunnen. Een drempel op 'niet starten' zou precies
    het verkeerde gedrag belonen."""
    l = _start(client, technician_user)
    _alles(client, technician_user, l, antwoord="nee")
    r = client.post(f"/api/lmra/{l['id']}/afronden", json={"oordeel": "niet_starten"},
                    headers=auth(technician_user))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "niet_gestart"
    assert r.json()["aantal_niet_in_orde"] == len(lc.VRAGEN)


def test_afgesloten_lmra_wijzigt_niet_meer(client, technician_user):
    l = _start(client, technician_user)
    _alles(client, technician_user, l)
    client.post(f"/api/lmra/{l['id']}/afronden", json={"oordeel": "veilig"},
                headers=auth(technician_user))
    r = client.patch(f"/api/lmra/{l['id']}/antwoorden/{l['antwoorden'][0]['id']}",
                     json={"antwoord": "nee"}, headers=auth(technician_user))
    assert r.status_code == 409
    r = client.patch(f"/api/lmra/{l['id']}", json={"taak": "Iets anders"},
                     headers=auth(technician_user))
    assert r.status_code == 409


def test_nvt_telt_niet_als_aandachtspunt(client, technician_user):
    l = _start(client, technician_user)
    _alles(client, technician_user, l, antwoord="nvt")
    r = client.post(f"/api/lmra/{l['id']}/afronden", json={"oordeel": "veilig"},
                    headers=auth(technician_user))
    assert r.status_code == 200, r.text
    assert r.json()["aantal_niet_in_orde"] == 0


# ── Lijst, statistiek en verwijderen ─────────────────────────────────

def test_statistiek_telt_hoe_vaak_er_niet_gestart_is(client, technician_user):
    """Het getal waar het om draait is niet hoeveel LMRA's er zijn, maar hoe
    vaak er is gestopt."""
    veilig = _start(client, technician_user)
    _alles(client, technician_user, veilig)
    client.post(f"/api/lmra/{veilig['id']}/afronden", json={"oordeel": "veilig"},
                headers=auth(technician_user))
    gestopt = _start(client, technician_user)
    _alles(client, technician_user, gestopt, antwoord="nee")
    client.post(f"/api/lmra/{gestopt['id']}/afronden", json={"oordeel": "niet_starten"},
                headers=auth(technician_user))
    _start(client, technician_user)          # blijft concept

    r = client.get("/api/lmra/statistiek", headers=auth(technician_user))
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["totaal"] == 3
    assert st["afgerond"] == 2
    assert st["concept"] == 1
    assert st["veilig"] == 1
    assert st["niet_gestart"] == 1
    assert st["met_aandachtspunt"] == 1


def test_lijst_filtert_op_eigen_lmra(client, technician_user, contractor_user):
    _start(client, technician_user)
    _start(client, contractor_user)
    r = client.get("/api/lmra/?alleen_eigen=true", headers=auth(technician_user))
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1
    assert r.json()[0]["uitvoerder_id"] == technician_user.id
    # Zonder filter zie je die van je ploeg ook — daar leer je van.
    assert len(client.get("/api/lmra/", headers=auth(technician_user)).json()) == 2


def test_verwijderen_is_voor_beheer(client, technician_user, admin_user):
    """Juist een 'niet starten' is het bewijs dat het systeem werkt; die hoort
    niet te verdwijnen omdat iemand hem niet mooi vindt."""
    l = _start(client, technician_user)
    assert client.delete(f"/api/lmra/{l['id']}",
                         headers=auth(technician_user)).status_code == 403
    assert client.delete(f"/api/lmra/{l['id']}",
                         headers=auth(admin_user)).status_code == 200


def test_antwoorden_verdwijnen_met_de_lmra(client, technician_user, admin_user):
    l = _start(client, technician_user)
    client.delete(f"/api/lmra/{l['id']}", headers=auth(admin_user))
    db = SessionLocal()
    try:
        assert db.query(Lmra).filter(Lmra.id == l["id"]).count() == 0
        assert db.query(LmraAntwoord).filter(LmraAntwoord.lmra_id == l["id"]).count() == 0
    finally:
        db.close()


def test_vraagtekst_wordt_gesnapshot(client, technician_user):
    """Verandert de lijst later, dan blijft een oude LMRA tonen wat er destijds
    gevraagd is. Zonder dat is een audit-trail niets waard."""
    l = _start(client, technician_user)
    db = SessionLocal()
    try:
        rijen = db.query(LmraAntwoord).filter(LmraAntwoord.lmra_id == l["id"]).all()
        assert len(rijen) == len(lc.VRAGEN)
        for a in rijen:
            assert a.question_text_snapshot == lc.VRAGEN_PER_CODE[a.question_code]["vraag"]
            assert a.question_version == lc.LMRA_VERSION
    finally:
        db.close()


# ── De vragenlijst zelf ──────────────────────────────────────────────

def test_checklist_endpoint_geeft_de_hele_lijst(client, viewer_user):
    r = client.get("/api/lmra/checklist", headers=auth(viewer_user))
    assert r.status_code == 200, r.text
    lijst = r.json()
    assert lijst["versie"] == lc.LMRA_VERSION
    assert lijst["aantal_vragen"] == len(lc.VRAGEN)
    assert sum(len(g["vragen"]) for g in lijst["categorieen"]) == len(lc.VRAGEN)
    assert {o["key"] for o in lijst["oordelen"]} == {"veilig", "niet_starten"}


def test_vragenlijst_is_consistent():
    """Alle vragen positief geformuleerd en van hetzelfde type: een lijst waarin
    de ene vraag andersom werkt dan de andere levert fouten op bij iemand die in
    de regen langs de weg staat af te vinken."""
    codes = [v["code"] for v in lc.VRAGEN]
    assert len(codes) == len(set(codes)), "dubbele vraagcode"
    for v in lc.VRAGEN:
        assert v["categorie"] in lc.CATEGORIEEN, v["code"]
        assert v["type"] == "ja_nee_nvt", v["code"]
        assert v["attention_when"] is False, v["code"]
        assert v["vraag"].endswith("?"), v["code"]
        assert v["uitleg"] and v["norm_ref"], v["code"]
        assert len(v["vraag"]) <= 500, v["code"]
    # Elke categorie wordt ook echt gebruikt; een lege kop is verwarrend.
    gebruikt = {v["categorie"] for v in lc.VRAGEN}
    assert gebruikt == set(lc.CATEGORIEEN)


def test_lmra_staat_in_het_portaal():
    """Zonder tabblad en scherm is de API voor de mensen buiten onzichtbaar."""
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "templates" / "portaal.html"
            ).read_text(encoding="utf-8")
    assert 'data-tab="lmra"' in html
    assert 'id="vg-tab-lmra"' in html
    assert 'id="lmraModal"' in html
    for functie in ("loadLmra", "startLmra", "toonLmra", "lmraAfronden"):
        assert html.count("function " + functie) == 1, functie
    # Beide uitkomsten moeten op het scherm staan; alleen "veilig" aanbieden
    # maakt van de LMRA een aftekenformulier.
    assert "lmraNietStartenKnop" in html and "lmraVeiligKnop" in html
