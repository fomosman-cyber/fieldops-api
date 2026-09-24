"""Werk begroten: je zegt wat je gaat doen, het materieel rolt eruit.

De kern is dat dit een *voorstel* is dat pas iets wordt als de gebruiker het
overneemt, en dat er zonder AI niets wordt verzonnen.
"""

import json
from datetime import date

import pytest

import materieel_begroting as mb
from database import SessionLocal
from models import Materieel, MaterieelInzet, Project
from tests.conftest import auth


def _stuk(client, user, naam, **kw):
    body = {"naam": naam, "soort": "graafmachine", "energiedrager": "diesel",
            "verbruik_per_uur": 12.0, "leverancier": "Boels"}
    body.update(kw)
    r = client.post("/api/materieel", json=body, headers=auth(user))
    assert r.status_code == 201, r.text
    return r.json()


def _project(user):
    db = SessionLocal()
    try:
        p = Project(name="N201 groot onderhoud", organization_id=user.organization_id,
                    created_by=user.id)
        db.add(p); db.commit(); db.refresh(p)
        return p.id
    finally:
        db.close()


# ── De vraag die eruit gaat ──────────────────────────────────────────

def test_werk_wordt_leesbaar_opgeschreven():
    tekst = mb.werk_als_tekst([
        {"werk": "Asfalt frezen", "aantal": 450, "eenheid": "m2"},
        {"werk": "Band zetten", "aantal": 120, "eenheid": "m1"},
        {"werk": "Opruimen", "aantal": None, "eenheid": None},
    ])
    assert "Asfalt frezen: 450 m²" in tekst
    assert "Band zetten: 120 m¹" in tekst
    assert "- Opruimen" in tekst


def test_het_eigen_materieel_gaat_mee_in_de_vraag():
    """Anders komt er 'een graafmachine' uit in plaats van 'Rupskraan 8t'."""
    tekst = mb.register_als_tekst([
        {"id": "abc", "naam": "Rupskraan 8t", "soort_naam": "Graafmachine / kraan",
         "energiedrager_naam": "Diesel (B7)", "verbruik_per_uur": 12.0, "eenheid": "liter"},
        {"id": "def", "naam": "Trilplaat", "verbruik_per_uur": None},
    ])
    assert "id=abc" in tekst and "Rupskraan 8t" in tekst and "12 liter/draaiuur" in tekst
    assert "verbruik onbekend" in tekst


def test_zonder_register_zegt_de_vraag_dat_ook():
    assert "nog geen materieel" in mb.register_als_tekst([])


# ── Zonder AI verzinnen we niets ─────────────────────────────────────

def test_zonder_sleutel_komen_er_geen_uren_uit(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    uit = mb.begroot(omschrijving="450 m2 frezen", werkregels=[],
                     register=[{"id": "a", "naam": "Rupskraan 8t",
                                "energiedrager": "diesel", "verbruik_per_uur": 12.0}])
    assert uit["bron"] == "leeg"
    assert all(r["draaiuren"] is None for r in uit["regels"])
    assert all(r["co2_kg"] is None for r in uit["regels"])
    assert uit["totaal"]["kg_totaal"] == 0
    assert "geen AI" in uit["samenvatting"]


# ── Met AI ───────────────────────────────────────────────────────────

def _antwoord(regels, samenvatting="Gerekend op een ploeg van twee."):
    return json.dumps({"regels": regels, "samenvatting": samenvatting}), "claude-opus-5"


def test_een_voorstel_wordt_doorgerekend_naar_liters_en_kilos(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-sleutel")
    register = [{"id": "a", "naam": "Rupskraan 8t", "energiedrager": "diesel",
                 "verbruik_per_uur": 12.0}]
    monkeypatch.setattr(mb, "_roep_claude", lambda k, v: _antwoord([
        {"materieel_id": "a", "naam": "Rupskraan 8t", "draaiuren": 6,
         "toelichting": "Ontgraven en laden."}]))

    uit = mb.begroot(omschrijving="grondwerk", werkregels=[], register=register)
    assert uit["bron"] == "claude"
    regel = uit["regels"][0]
    assert regel["draaiuren"] == 6
    assert regel["brandstof_hoeveelheid"] == pytest.approx(72)
    assert regel["co2_methode"] == "geschat", "een begroting is nooit gemeten"
    assert regel["co2_kg"] == pytest.approx(72 * 3.251, abs=0.1)
    assert uit["totaal_draaiuren"] == 6


def test_een_machine_die_niet_bestaat_verliest_zijn_id(monkeypatch):
    """Anders hangt een regel aan materieel dat er niet is, en klopt het
    rapport erover ook niet."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-sleutel")
    monkeypatch.setattr(mb, "_roep_claude", lambda k, v: _antwoord([
        {"materieel_id": "bestaat-niet", "naam": "Shovel", "draaiuren": 4,
         "toelichting": ""}]))
    uit = mb.begroot(omschrijving="x", werkregels=[], register=[])
    assert uit["regels"][0]["materieel_id"] is None
    assert uit["regels"][0]["naam"] == "Shovel"


def test_onzinnige_uren_worden_geweigerd(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-sleutel")
    monkeypatch.setattr(mb, "_roep_claude", lambda k, v: _antwoord([
        {"materieel_id": None, "naam": "Kraan", "draaiuren": 9000, "toelichting": ""},
        {"materieel_id": None, "naam": "Shovel", "draaiuren": -3, "toelichting": ""}]))
    uit = mb.begroot(omschrijving="x", werkregels=[], register=[])
    assert all(r["draaiuren"] is None for r in uit["regels"])


def test_een_storing_blokkeert_niemand(monkeypatch):
    """Zelfde ontwerpregel als bij de toolbox: een AI-fout mag een uitvoerder
    die buiten staat niet tegenhouden."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-sleutel")

    def stuk(k, v):
        raise RuntimeError("netwerk weg")
    monkeypatch.setattr(mb, "_roep_claude", stuk)

    uit = mb.begroot(omschrijving="x", werkregels=[], register=[])
    assert uit["bron"] == "leeg"
    assert "RuntimeError" in uit["reden"]


def test_onleesbaar_antwoord_levert_geen_halve_begroting(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-sleutel")
    monkeypatch.setattr(mb, "_roep_claude", lambda k, v: ("dit is geen json", None))
    uit = mb.begroot(omschrijving="x", werkregels=[], register=[])
    assert uit["bron"] == "leeg"


# ── Via de API ───────────────────────────────────────────────────────

def test_begroting_slaat_niets_op(client, admin_user, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _stuk(client, admin_user, "Rupskraan 8t")
    r = client.post("/api/materieel/begroting", headers=auth(admin_user), json={
        "omschrijving": "450 m2 asfalt frezen",
        "werkregels": [{"werk": "Asfalt frezen", "aantal": 450, "eenheid": "m2"}]})
    assert r.status_code == 200
    assert r.json()["bron"] == "leeg"

    db = SessionLocal()
    try:
        assert db.query(MaterieelInzet).count() == 0
    finally:
        db.close()


def test_overnemen_maakt_dagregels_met_een_dagboekregel(client, admin_user):
    stuk = _stuk(client, admin_user, "Rupskraan 8t")
    project_id = _project(admin_user)
    vandaag = date.today().isoformat()

    r = client.post("/api/materieel/begroting/overnemen", headers=auth(admin_user), json={
        "datum": vandaag, "project_id": project_id, "opmerking": "Uit de begroting",
        "regels": [
            {"materieel_id": stuk["id"], "naam": "Rupskraan 8t", "draaiuren": 6},
            {"materieel_id": None, "naam": "Gehuurde trilplaat", "draaiuren": 2,
             "leverancier": "Aduco"},
            {"materieel_id": None, "naam": "Niets ingevuld", "draaiuren": None},
        ]})
    assert r.status_code == 201, r.text
    uit = r.json()
    assert uit["aangemaakt"] == 2
    assert uit["overgeslagen_zonder_uren"] == 1

    namen = {x["materieel_naam"]: x for x in uit["regels"]}
    assert namen["Rupskraan 8t"]["co2_methode"] == "geschat"
    assert namen["Gehuurde trilplaat"]["leverancier"] == "Aduco"
    assert namen["Gehuurde trilplaat"]["co2_kg"] is None, "geen verbruik bekend"

    dag = client.get(f"/api/daybook/day?date={vandaag}", headers=auth(admin_user)).json()
    assert len([e for e in dag["entries"] if e["entry_type"] == "materieel_inzet"]) == 2


def test_overnemen_zonder_enige_uren_wordt_geweigerd(client, admin_user):
    r = client.post("/api/materieel/begroting/overnemen", headers=auth(admin_user), json={
        "regels": [{"materieel_id": None, "naam": "Kraan", "draaiuren": None}]})
    assert r.status_code == 400


def test_overnemen_kent_geen_materieel_van_een_ander(client, admin_user):
    r = client.post("/api/materieel/begroting/overnemen", headers=auth(admin_user), json={
        "regels": [{"materieel_id": "niet-van-mij", "naam": "Kraan", "draaiuren": 3}]})
    assert r.status_code == 404


def test_eenheden_worden_aangeboden(client, admin_user):
    codes = {e["code"] for e in client.get("/api/materieel/eenheden",
                                           headers=auth(admin_user)).json()["eenheden"]}
    assert {"m2", "m1", "stuks"} <= codes


# ── Standaardbedrijven ───────────────────────────────────────────────

def test_de_keuzelijst_biedt_bekende_bedrijven_aan(client, admin_user):
    uit = client.get("/api/materieel/leveranciers", headers=auth(admin_user)).json()
    namen = {b["naam"] for b in uit["suggesties"]}
    assert {"P.C. van der Wiel", "Aduco", "Vrijbloed"} <= namen
    assert all(b.get("soort") for b in uit["suggesties"]), "elke suggestie heeft een soort"


def test_eigen_namen_verdringen_de_suggestie(client, admin_user):
    """Wat je zelf gebruikt hoort niet nog een keer als suggestie terug te komen."""
    _stuk(client, admin_user, "Kraan", leverancier="Aduco")
    uit = client.get("/api/materieel/leveranciers", headers=auth(admin_user)).json()
    assert "Aduco" in uit["leveranciers"]
    assert "Aduco" not in {b["naam"] for b in uit["suggesties"]}


def test_de_bedrijvenlijst_is_data_en_geen_administratie():
    """Namen als invulhulp, met een toelichting die dat zegt -- geen
    geverifieerde bedrijfsgegevens."""
    from pathlib import Path
    pad = Path(__file__).resolve().parent.parent / "data" / "bedrijven.json"
    lijst = json.loads(pad.read_text(encoding="utf-8"))
    assert "invulhulp" in lijst["toelichting"]
    assert all({"naam", "soort"} <= set(b) for b in lijst["bedrijven"])


def test_doorrekenen_gebruikt_dezelfde_rekenregel_als_opslaan(client, admin_user):
    """Het scherm zou het zelf kunnen, maar twee rekenregels in één codebase
    lopen op een dag uit elkaar -- in een getal dat naar een opdrachtgever gaat."""
    stuk = _stuk(client, admin_user, "Rupskraan 8t")
    doorgerekend = client.post("/api/materieel/begroting/doorrekenen",
                               headers=auth(admin_user), json={"regels": [
                                   {"materieel_id": stuk["id"], "naam": "Rupskraan 8t",
                                    "draaiuren": 5}]}).json()
    opgeslagen = client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "materieel_id": stuk["id"], "draaiuren": 5}).json()
    assert doorgerekend["regels"][0]["co2_kg"] == pytest.approx(opgeslagen["co2_kg"])


def test_het_scherm_heeft_een_begrotingsknop():
    from pathlib import Path
    portaal = (Path(__file__).resolve().parent.parent / "templates" / "portaal.html"
               ).read_text(encoding="utf-8")
    assert "openBegrotingModal" in portaal
    assert "/api/materieel/begroting" in portaal
    # De herkomst hoort zichtbaar te zijn: uitgerekend leest anders dan zelf invullen.
    assert "uitgerekend" in portaal and "zelf invullen" in portaal
