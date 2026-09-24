"""Materieel en CO2: rekenen, registreren, en niet verzinnen.

De kern van deze module is niet de CRUD maar het verschil tussen gemeten,
geschat en onbekend. Die drie uitkomsten worden hier stuk voor stuk
vastgelegd, want zodra "onbekend" ooit als 0 kg in een rapport belandt, staat
er een bewering in die niemand heeft gedaan.
"""

import json
from datetime import timedelta

import pytest

import materieel as mt
from database import SessionLocal
from models import Materieel, Organization, Project
from tests.conftest import auth


def _vandaag():
    """De Nederlandse dag, zoals de server hem rekent voor een regel zonder
    datum. `date.today()` is op de CI-machine de UTC-dag: tussen middernacht
    en 02:00 Nederlandse tijd een dag te vroeg."""
    from datetime import datetime, timezone
    from export_huisstijl import naar_nl
    return naar_nl(datetime.now(timezone.utc)).date()


# ── De factorenlijst ─────────────────────────────────────────────────

def test_factorenlijst_draagt_bron_en_versie():
    """Zonder bron is een emissiefactor een verzonnen getal met decimalen."""
    lijst = mt.factorenlijst()
    assert lijst["versie"], "de lijst moet een versiedatum hebben"
    assert lijst["bron"].startswith("http")
    assert lijst["factoren"], "er moet minstens één factor in staan"
    for rij in lijst["factoren"]:
        assert rij.get("bron", "").startswith("http"), f"{rij['code']} mist een bron"
        assert rij.get("eenheid") in ("liter", "kg", "kWh")


def test_iedere_energiedrager_heeft_een_bestaande_factor():
    """Een keuzelijst die naar een niet-bestaande factor wijst levert stilletjes
    nooit een CO2-getal op. Beter hier stuk dan in het rapport."""
    codes = {f["code"] for f in mt.factorenlijst()["factoren"]}
    for drager, gegevens in mt.ENERGIEDRAGERS.items():
        if gegevens["factor_code"] is None:
            continue
        assert gegevens["factor_code"] in codes, f"{drager} wijst naar een onbekende factor"


# ── De berekening ────────────────────────────────────────────────────

def test_gemeten_is_liters_maal_factor():
    diesel = mt.factor_voor("diesel")
    uit = mt.bereken(energiedrager="diesel", brandstof_hoeveelheid=100)
    assert uit.methode == "gemeten"
    assert uit.kg == pytest.approx(100 * diesel.kg_co2_per_eenheid, abs=0.01)
    assert not uit.is_schatting


def test_geschat_gebruikt_draaiuren_maal_verbruik():
    diesel = mt.factor_voor("diesel")
    uit = mt.bereken(energiedrager="diesel", draaiuren=8, verbruik_per_uur=12)
    assert uit.methode == "geschat"
    assert uit.is_schatting
    assert uit.kg == pytest.approx(8 * 12 * diesel.kg_co2_per_eenheid, abs=0.01)


def test_gemeten_wint_van_geschat():
    """De tank liegt niet, het kengetal wel eens."""
    uit = mt.bereken(energiedrager="diesel", brandstof_hoeveelheid=50,
                     draaiuren=8, verbruik_per_uur=12)
    assert uit.methode == "gemeten"
    assert uit.hoeveelheid == 50


def test_zonder_getal_geen_uitstoot_en_zeker_geen_nul():
    uit = mt.bereken(energiedrager="diesel")
    assert uit.kg is None
    assert uit.methode == "onbekend"
    assert uit.kg != 0

    alleen_uren = mt.bereken(energiedrager="diesel", draaiuren=8)
    assert alleen_uren.kg is None
    assert "verbruik per uur" in alleen_uren.reden


def test_materieel_zonder_aandrijving_levert_geen_getal():
    uit = mt.bereken(energiedrager="geen", draaiuren=8, verbruik_per_uur=12)
    assert uit.kg is None
    assert uit.methode == "onbekend"


def test_eigen_factor_van_de_organisatie_wint():
    """Wat de auditor voorschrijft gaat voor wat wij in het bestand hebben."""
    uit = mt.bereken(energiedrager="diesel", brandstof_hoeveelheid=10,
                     eigen_factoren={"diesel": 2.0})
    assert uit.kg == pytest.approx(20.0)
    assert uit.factor.eigen is True
    assert "organisatie" in uit.factor.herkomst.lower()


def test_totaal_houdt_gemeten_en_geschat_uit_elkaar():
    regels = [
        mt.bereken(energiedrager="diesel", brandstof_hoeveelheid=10),
        mt.bereken(energiedrager="diesel", draaiuren=2, verbruik_per_uur=5),
        mt.bereken(energiedrager="diesel"),
    ]
    t = mt.totaal(regels)
    assert t["kg_gemeten"] > 0 and t["kg_geschat"] > 0
    assert t["kg_totaal"] == pytest.approx(t["kg_gemeten"] + t["kg_geschat"], abs=0.01)
    assert t["regels_zonder_getal"] == 1
    assert t["volledig"] is False


def test_uitleg_noemt_de_bron():
    uit = mt.bereken(energiedrager="diesel", brandstof_hoeveelheid=10)
    tekst = uit.uitleg()
    assert "gemeten" in tekst and "co2emissiefactoren.nl" in tekst


# ── De materieellijst inlezen ────────────────────────────────────────

def test_csv_leest_puntkomma_en_nederlandse_komma():
    rijen, waarschuwingen = mt.lees_csv(
        "naam;soort;brandstof;verbruik per uur;leverancier\n"
        "Rupskraan 8t;graafmachine;diesel;12,5;Boels\n")
    assert len(rijen) == 1
    assert rijen[0]["verbruik_per_uur"] == 12.5
    assert rijen[0]["soort"] == "graafmachine"
    assert rijen[0]["leverancier"] == "Boels"
    assert not waarschuwingen


def test_csv_raadt_niet_maar_waarschuwt():
    rijen, waarschuwingen = mt.lees_csv("naam;soort\nIets;ruimteschip\n")
    assert rijen[0]["soort"] == ""
    assert any("ruimteschip" in w for w in waarschuwingen)


def test_csv_zonder_naamkolom_wordt_geweigerd():
    rijen, waarschuwingen = mt.lees_csv("kenteken;soort\nAB-12-CD;shovel\n")
    assert rijen == []
    assert waarschuwingen


# ── Het register via de API ──────────────────────────────────────────

def _project(user):
    db = SessionLocal()
    try:
        p = Project(name="Onderhoud N201", organization_id=user.organization_id,
                    created_by=user.id)
        db.add(p); db.commit(); db.refresh(p)
        return p
    finally:
        db.close()


def _maak_stuk(client, user, **kwargs):
    body = {"naam": "Rupskraan 8t", "soort": "graafmachine", "energiedrager": "diesel",
            "leverancier": "Boels", "verbruik_per_uur": 12.0}
    body.update(kwargs)
    r = client.post("/api/materieel", json=body, headers=auth(user))
    assert r.status_code == 201, r.text
    return r.json()


def test_register_aanmaken_en_teruglezen(client, admin_user):
    stuk = _maak_stuk(client, admin_user)
    assert stuk["soort_naam"] == "Graafmachine / kraan"
    assert stuk["eenheid"] == "liter"

    r = client.get("/api/materieel", headers=auth(admin_user))
    assert r.status_code == 200
    assert [m["naam"] for m in r.json()["materieel"]] == ["Rupskraan 8t"]


def test_viewer_mag_het_register_niet_wijzigen(client, viewer_user):
    r = client.post("/api/materieel", json={"naam": "Kraan"}, headers=auth(viewer_user))
    assert r.status_code == 403


def test_onbekende_keuze_wordt_geweigerd(client, admin_user):
    r = client.post("/api/materieel", json={"naam": "X", "energiedrager": "kernfusie"},
                    headers=auth(admin_user))
    assert r.status_code == 400
    assert "energiedrager" in r.json()["detail"]


def test_verwijderen_archiveert_en_gooit_niet_weg(client, admin_user):
    """Aan een machine hangen dagen uit het verleden; die onderbouwing blijft."""
    stuk = _maak_stuk(client, admin_user)
    r = client.delete(f"/api/materieel/{stuk['id']}", headers=auth(admin_user))
    assert r.status_code == 200

    db = SessionLocal()
    try:
        bewaard = db.query(Materieel).filter(Materieel.id == stuk["id"]).first()
        assert bewaard is not None and bewaard.actief is False
    finally:
        db.close()
    assert client.get("/api/materieel", headers=auth(admin_user)).json()["aantal"] == 0
    assert client.get("/api/materieel?alleen_actief=false",
                      headers=auth(admin_user)).json()["aantal"] == 1


def test_import_slaat_niets_op_zonder_bevestiging(client, admin_user):
    csv = "naam;soort;brandstof\nShovel;shovel;diesel\n"
    r = client.post("/api/materieel/import", json={"inhoud": csv}, headers=auth(admin_user))
    assert r.json()["gelezen"] == 1 and r.json()["opgeslagen"] == 0
    assert client.get("/api/materieel", headers=auth(admin_user)).json()["aantal"] == 0

    r = client.post("/api/materieel/import", json={"inhoud": csv, "bevestigen": True},
                    headers=auth(admin_user))
    assert r.json()["opgeslagen"] == 1
    # Tweede keer dezelfde lijst maakt geen dubbele regels
    r = client.post("/api/materieel/import", json={"inhoud": csv, "bevestigen": True},
                    headers=auth(admin_user))
    assert r.json()["opgeslagen"] == 0 and r.json()["overgeslagen_want_bestond_al"] == 1


# ── Inzet per dag ────────────────────────────────────────────────────

def test_inzet_verschijnt_in_het_werkdagboek(client, admin_user):
    stuk = _maak_stuk(client, admin_user)
    vandaag = _vandaag().isoformat()
    r = client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "datum": vandaag, "materieel_id": stuk["id"], "draaiuren": 6,
        "brandstof_hoeveelheid": 70})
    assert r.status_code == 201, r.text
    regel = r.json()
    assert regel["co2_methode"] == "gemeten"
    assert regel["co2_kg"] > 0
    assert regel["leverancier"] == "Boels"   # overgenomen uit het register

    dag = client.get(f"/api/daybook/day?date={vandaag}", headers=auth(admin_user)).json()
    materieelregels = [e for e in dag["entries"] if e["entry_type"] == "materieel_inzet"]
    assert len(materieelregels) == 1
    assert "Rupskraan 8t" in materieelregels[0]["title"]
    assert "Boels" in materieelregels[0]["description"]


def test_draaiuren_tellen_niet_als_gewerkte_uren(client, admin_user):
    """Een kraan stuurt geen factuur voor zichzelf. Zes draaiuren mogen niet
    als zes uur in de urenregistratie belanden."""
    stuk = _maak_stuk(client, admin_user)
    vandaag = _vandaag().isoformat()
    client.post("/api/materieel/inzet", headers=auth(admin_user),
                json={"datum": vandaag, "materieel_id": stuk["id"], "draaiuren": 6})

    dag = client.get(f"/api/daybook/day?date={vandaag}", headers=auth(admin_user)).json()
    assert dag["total_minutes"] == 0
    assert all(e["duration_minutes"] in (None, 0) for e in dag["entries"])


def test_losse_huur_zonder_registerregel(client, admin_user):
    r = client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "materieel_naam": "Aggregaat 20 kVA", "leverancier": "Riwal",
        "energiedrager": "diesel", "brandstof_hoeveelheid": 40})
    assert r.status_code == 201
    assert r.json()["materieel_id"] is None
    assert r.json()["co2_kg"] > 0


def test_inzet_zonder_naam_wordt_geweigerd(client, admin_user):
    r = client.post("/api/materieel/inzet", headers=auth(admin_user),
                    json={"draaiuren": 4})
    assert r.status_code == 400


def test_inzet_zonder_verbruik_levert_geen_kilogrammen(client, admin_user):
    """Leeg is niet nul: de regel bestaat, het getal niet."""
    stuk = _maak_stuk(client, admin_user, verbruik_per_uur=None)
    r = client.post("/api/materieel/inzet", headers=auth(admin_user),
                    json={"materieel_id": stuk["id"], "draaiuren": 5})
    assert r.status_code == 201
    assert r.json()["co2_kg"] is None
    assert r.json()["co2_methode"] == "onbekend"


def test_wijzigen_werkt_co2_en_dagboekregel_bij(client, admin_user):
    stuk = _maak_stuk(client, admin_user)
    vandaag = _vandaag().isoformat()
    regel = client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "datum": vandaag, "materieel_id": stuk["id"], "brandstof_hoeveelheid": 10}).json()
    eerste = regel["co2_kg"]

    r = client.patch(f"/api/materieel/inzet/{regel['id']}", headers=auth(admin_user),
                     json={"brandstof_hoeveelheid": 20})
    assert r.status_code == 200
    assert r.json()["co2_kg"] == pytest.approx(eerste * 2, abs=0.01)

    dag = client.get(f"/api/daybook/day?date={vandaag}", headers=auth(admin_user)).json()
    tekst = [e for e in dag["entries"] if e["entry_type"] == "materieel_inzet"][0]["description"]
    assert "20 liter" in tekst


def test_verwijderen_haalt_ook_de_dagboekregel_weg(client, admin_user):
    stuk = _maak_stuk(client, admin_user)
    vandaag = _vandaag().isoformat()
    regel = client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "datum": vandaag, "materieel_id": stuk["id"], "brandstof_hoeveelheid": 10}).json()

    assert client.delete(f"/api/materieel/inzet/{regel['id']}",
                         headers=auth(admin_user)).status_code == 200

    dag = client.get(f"/api/daybook/day?date={vandaag}", headers=auth(admin_user)).json()
    assert [e for e in dag["entries"] if e["entry_type"] == "materieel_inzet"] == []
    assert client.get(f"/api/materieel/inzet?datum={vandaag}",
                      headers=auth(admin_user)).json()["aantal"] == 0


def test_andere_gebruiker_komt_niet_aan_jouw_regel(client, admin_user, technician_user):
    stuk = _maak_stuk(client, admin_user)
    regel = client.post("/api/materieel/inzet", headers=auth(technician_user), json={
        "materieel_id": stuk["id"], "brandstof_hoeveelheid": 10}).json()

    # De technicus mag zijn eigen regel wél wijzigen
    assert client.patch(f"/api/materieel/inzet/{regel['id']}", headers=auth(technician_user),
                        json={"draaiuren": 3}).status_code == 200


def test_organisaties_zien_elkaars_materieel_niet(client, admin_user):
    _maak_stuk(client, admin_user)
    db = SessionLocal()
    try:
        from models import AccountStatus, SubscriptionPlan, User, UserRole
        from auth import hash_password
        andere = Organization(name="Andere BV", plan=SubscriptionPlan.PROFESSIONAL,
                              status=AccountStatus.ACTIVE, max_users=5)
        db.add(andere); db.commit(); db.refresh(andere)
        vreemde = User(email="vreemd@andere.nl", hashed_password=hash_password("test1234"),
                       first_name="V", last_name="B", role=UserRole.ADMIN,
                       is_org_admin=True, organization_id=andere.id)
        db.add(vreemde); db.commit(); db.refresh(vreemde)
    finally:
        db.close()

    assert client.get("/api/materieel", headers=auth(vreemde)).json()["aantal"] == 0


# ── Het rapport ──────────────────────────────────────────────────────

def test_rapport_splitst_gemeten_geschat_en_leeg(client, admin_user):
    project = _project(admin_user)
    gemeten = _maak_stuk(client, admin_user, naam="Kraan gemeten")
    geschat = _maak_stuk(client, admin_user, naam="Kraan geschat", verbruik_per_uur=10)
    leeg = _maak_stuk(client, admin_user, naam="Kraan leeg", verbruik_per_uur=None)
    vandaag = _vandaag().isoformat()

    client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "datum": vandaag, "materieel_id": gemeten["id"], "brandstof_hoeveelheid": 10,
        "project_id": project.id})
    client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "datum": vandaag, "materieel_id": geschat["id"], "draaiuren": 4,
        "project_id": project.id})
    client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "datum": vandaag, "materieel_id": leeg["id"], "draaiuren": 4})

    r = client.get(f"/api/materieel/co2?from={vandaag}&to={vandaag}", headers=auth(admin_user))
    assert r.status_code == 200
    rapport = r.json()
    t = rapport["totaal"]
    assert t["kg_gemeten"] > 0
    assert t["kg_geschat"] > 0
    assert t["regels_zonder_getal"] == 1
    assert t["volledig"] is False
    assert t["kg_totaal"] == pytest.approx(t["kg_gemeten"] + t["kg_geschat"], abs=0.01)

    namen = {g["naam"] for g in rapport["per_materieel"]}
    assert {"Kraan gemeten", "Kraan geschat", "Kraan leeg"} <= namen
    assert rapport["factorenlijst"]["versie"]
    projecten = {g["naam"] for g in rapport["per_project"]}
    assert "Onderhoud N201" in projecten and "(niet ingevuld)" in projecten


def test_rapport_groepeert_per_leverancier(client, admin_user):
    _maak_stuk(client, admin_user, naam="Kraan A", leverancier="Boels")
    _maak_stuk(client, admin_user, naam="Kraan B", leverancier="Riwal")
    vandaag = _vandaag().isoformat()
    for naam in ("Kraan A", "Kraan B"):
        stuk = [m for m in client.get("/api/materieel", headers=auth(admin_user)).json()["materieel"]
                if m["naam"] == naam][0]
        client.post("/api/materieel/inzet", headers=auth(admin_user), json={
            "datum": vandaag, "materieel_id": stuk["id"], "brandstof_hoeveelheid": 10})

    rapport = client.get(f"/api/materieel/co2?from={vandaag}&to={vandaag}",
                         headers=auth(admin_user)).json()
    assert {g["naam"] for g in rapport["per_leverancier"]} == {"Boels", "Riwal"}


def test_uitkomst_bevriest_als_de_factor_later_verandert(client, admin_user):
    """Een rapport over vorig jaar hoort niet te wijzigen omdat de lijst is
    bijgewerkt. Daarom staat de uitkomst in de regel, niet in een formule."""
    stuk = _maak_stuk(client, admin_user)
    vandaag = _vandaag().isoformat()
    regel = client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "datum": vandaag, "materieel_id": stuk["id"], "brandstof_hoeveelheid": 10}).json()
    oorspronkelijk = regel["co2_kg"]

    r = client.put("/api/materieel/factoren", headers=auth(admin_user),
                   json={"factoren": {"diesel": 99.0}})
    assert r.status_code == 200

    opnieuw = client.get(f"/api/materieel/inzet?datum={vandaag}",
                         headers=auth(admin_user)).json()["regels"][0]
    assert opnieuw["co2_kg"] == pytest.approx(oorspronkelijk, abs=0.01)

    # Maar een nieuwe regel rekent wél met de nieuwe factor
    nieuw = client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "datum": vandaag, "materieel_id": stuk["id"], "brandstof_hoeveelheid": 10}).json()
    assert nieuw["co2_kg"] == pytest.approx(990.0, abs=0.01)


def test_eigen_factor_moet_een_bestaande_drager_zijn(client, admin_user):
    r = client.put("/api/materieel/factoren", headers=auth(admin_user),
                   json={"factoren": {"waterstofplasma": 1.0}})
    assert r.status_code == 400


def test_te_lange_periode_wordt_geweigerd(client, admin_user):
    vroeg = (_vandaag() - timedelta(days=500)).isoformat()
    r = client.get(f"/api/materieel/co2?from={vroeg}&to={_vandaag().isoformat()}",
                   headers=auth(admin_user))
    assert r.status_code == 400


def test_leveranciers_worden_voorgesteld(client, admin_user):
    _maak_stuk(client, admin_user, leverancier="Boels")
    client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "materieel_naam": "Losse pomp", "leverancier": "Van der Spek",
        "energiedrager": "diesel", "brandstof_hoeveelheid": 5})
    namen = client.get("/api/materieel/leveranciers", headers=auth(admin_user)).json()["leveranciers"]
    assert namen == ["Boels", "Van der Spek"]


@pytest.mark.parametrize("formaat", ["xlsx", "pdf"])
def test_export_levert_een_bestand(client, admin_user, formaat):
    stuk = _maak_stuk(client, admin_user)
    vandaag = _vandaag().isoformat()
    client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "datum": vandaag, "materieel_id": stuk["id"], "brandstof_hoeveelheid": 10})

    r = client.get(f"/api/materieel/co2.{formaat}?from={vandaag}&to={vandaag}",
                   headers=auth(admin_user))
    assert r.status_code == 200
    assert len(r.content) > 1000
    assert "CO2" in r.headers["content-disposition"]


# ── De module-poort ──────────────────────────────────────────────────

def test_zonder_dagboekmodule_geen_materieel(client, admin_user):
    """Materieel hangt onder het werkdagboek. Een organisatie die dat niet
    afneemt hoort ook deze endpoints niet te kunnen aanroepen."""
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(
            Organization.id == admin_user.organization_id).first()
        org.enabled_modules = json.dumps(["kunstwerken"])
        db.commit()
    finally:
        db.close()

    for pad in ("/api/materieel", "/api/materieel/config", "/api/materieel/inzet"):
        assert client.get(pad, headers=auth(admin_user)).status_code == 403, pad


# ── Het scherm ───────────────────────────────────────────────────────

def test_portaal_kent_de_materieelknop():
    """Een backend zonder knop is een backend die niemand gebruikt."""
    from pathlib import Path
    portaal = Path(__file__).resolve().parent.parent / "templates" / "portaal.html"
    inhoud = portaal.read_text(encoding="utf-8")
    assert "openMaterieelModal" in inhoud
    assert "/api/materieel/inzet" in inhoud
    assert "dbStatCo2" in inhoud


def test_naam_van_een_losse_regel_is_te_herstellen(client, admin_user):
    """Bij een eenmalige huur is de naam het enige aanknopingspunt; een
    typefout daarin moet je kunnen rechtzetten."""
    regel = client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "materieel_naam": "Aggregat 20 kVA", "energiedrager": "diesel",
        "brandstof_hoeveelheid": 5}).json()
    r = client.patch(f"/api/materieel/inzet/{regel['id']}", headers=auth(admin_user),
                     json={"materieel_naam": "Aggregaat 20 kVA"})
    assert r.status_code == 200
    assert r.json()["materieel_naam"] == "Aggregaat 20 kVA"


def test_naam_uit_het_register_wijzig_je_in_het_register(client, admin_user):
    stuk = _maak_stuk(client, admin_user)
    regel = client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "materieel_id": stuk["id"], "brandstof_hoeveelheid": 5}).json()
    r = client.patch(f"/api/materieel/inzet/{regel['id']}", headers=auth(admin_user),
                     json={"materieel_naam": "Iets anders"})
    assert r.status_code == 400


# ── Wie ziet wiens regels ────────────────────────────────────────────

def _twee_regels(client, admin_user, technician_user):
    """Eén regel van de beheerder, één van de technicus, zelfde dag."""
    client.post("/api/materieel/inzet", headers=auth(technician_user), json={
        "materieel_naam": "Kraan van de technicus", "energiedrager": "diesel",
        "brandstof_hoeveelheid": 10})
    client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "materieel_naam": "Kraan van de beheerder", "energiedrager": "diesel",
        "brandstof_hoeveelheid": 10})
    return _vandaag().isoformat()


def test_het_werkdagboek_toont_je_eigen_regels(client, admin_user, technician_user):
    """Ook een beheerder ziet in zijn eigen dagboek alleen zijn eigen machines.

    Anders stond in zijn werkdagboek het materieel van de hele ploeg naast een
    tijdlijn met alleen zijn eigen regels, en telde de CO2-teller het
    bedrijfstotaal.
    """
    vandaag = _twee_regels(client, admin_user, technician_user)
    uit = client.get(f"/api/materieel/inzet?datum={vandaag}", headers=auth(admin_user)).json()
    assert [r["materieel_naam"] for r in uit["regels"]] == ["Kraan van de beheerder"]

    dag = client.get(f"/api/daybook/day?date={vandaag}", headers=auth(admin_user)).json()
    tijdlijn = [e for e in dag["entries"] if e["entry_type"] == "materieel_inzet"]
    assert len(tijdlijn) == len(uit["regels"]), "blok en tijdlijn horen hetzelfde te tellen"


def test_beheerder_kan_gericht_over_de_schouder_kijken(client, admin_user, technician_user):
    vandaag = _twee_regels(client, admin_user, technician_user)
    uit = client.get(f"/api/materieel/inzet?datum={vandaag}&user_id={technician_user.id}",
                     headers=auth(admin_user)).json()
    assert [r["materieel_naam"] for r in uit["regels"]] == ["Kraan van de technicus"]

    alles = client.get(f"/api/materieel/inzet?datum={vandaag}&iedereen=true",
                       headers=auth(admin_user)).json()
    assert alles["aantal"] == 2


def test_zonder_beheerdersrol_kom_je_niet_bij_een_ander(client, admin_user, technician_user):
    vandaag = _twee_regels(client, admin_user, technician_user)
    r = client.get(f"/api/materieel/inzet?datum={vandaag}&user_id={admin_user.id}",
                   headers=auth(technician_user))
    assert r.status_code == 403

    # En 'iedereen' levert stilletjes alleen de eigen regels op, geen 403:
    # het is een verzoek om een overzicht, geen poging tot inbraak.
    eigen = client.get(f"/api/materieel/inzet?datum={vandaag}&iedereen=true",
                       headers=auth(technician_user)).json()
    assert [r2["materieel_naam"] for r2 in eigen["regels"]] == ["Kraan van de technicus"]


def test_het_rapport_gaat_over_het_bedrijf(client, admin_user, technician_user):
    """Een CO2-rapportage telt de hele organisatie op -- dat is waar hij voor
    dient. Wie geen beheerder is, ziet zijn eigen regels."""
    vandaag = _twee_regels(client, admin_user, technician_user)
    rapport = client.get(f"/api/materieel/co2?from={vandaag}&to={vandaag}",
                         headers=auth(admin_user)).json()
    assert len(rapport["per_materieel"]) == 2

    eigen = client.get(f"/api/materieel/co2?from={vandaag}&to={vandaag}",
                       headers=auth(technician_user)).json()
    assert [g["naam"] for g in eigen["per_materieel"]] == ["Kraan van de technicus"]


def test_het_bevroren_verbruik_komt_mee_terug(client, admin_user):
    """Het scherm rekent zijn voorbeeld hiermee door; zonder dit veld toont het
    bij het bewerken een ander getal dan de server opslaat."""
    stuk = _maak_stuk(client, admin_user, verbruik_per_uur=12.0)
    regel = client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "materieel_id": stuk["id"], "draaiuren": 4}).json()
    assert regel["verbruik_per_uur"] == 12.0

    client.patch(f"/api/materieel/{stuk['id']}", headers=auth(admin_user),
                 json={"verbruik_per_uur": 8.0})
    opnieuw = client.get(f"/api/materieel/inzet?datum={regel['datum']}",
                         headers=auth(admin_user)).json()["regels"][0]
    assert opnieuw["verbruik_per_uur"] == 12.0, "de regel houdt het kengetal van toen"


def test_het_scherm_haalt_de_projecten_op_als_de_lijst_leeg_is():
    """Wie rechtstreeks naar het werkdagboek gaat heeft de projectenpagina
    nooit geopend; zonder dit blijft de projectkeuze leeg."""
    from pathlib import Path
    portaal = (Path(__file__).resolve().parent.parent / "templates" / "portaal.html"
               ).read_text(encoding="utf-8")
    assert "_zorgVoorProjecten" in portaal
    # Beide keuzelijsten gebruiken hem: de materieelregel en het CO2-rapport.
    assert portaal.count("_zorgVoorProjecten().then") >= 2


def test_een_regel_zonder_datum_krijgt_de_nederlandse_dag(client, admin_user):
    """Een werkdagboek vul je 's avonds in. Met de UTC-datum belandt alles wat
    na middernacht Nederlandse tijd wordt ingevuld op de dag ervoor."""
    from export_huisstijl import naar_nl
    from datetime import datetime as dt, timezone as tz
    r = client.post("/api/materieel/inzet", headers=auth(admin_user), json={
        "materieel_naam": "Losse pomp", "energiedrager": "diesel",
        "brandstof_hoeveelheid": 5})
    assert r.json()["datum"] == naar_nl(dt.now(tz.utc)).date().isoformat()
