"""Regressietest voor de import van 'SOK Amsterdam - Asfalt 2026'.

Borgt dat de dataset uit de drie PDF-rapporten compleet en gekoppeld in de
database landt, dat een tweede run niets dubbel aanmaakt, en dat de gegevens
die de klant op de kaart ziet — coordinaat, foto, weg, werksoort — kloppen.
"""
import json
import re
from collections import Counter

import pytest

import import_sok_amsterdam as imp
from database import SessionLocal
from models import Asset, Melding, Organization, Project, User, UserRole
from werksoorten import (CLUSTER_SKILLS, ONBEPAALD, WERKSOORT_KLEUREN,
                         WERKSOORT_MAATREGEL)

ORG_NAAM = "SOK Testorganisatie"


@pytest.fixture
def org_en_user():
    from auth import hash_password
    db = SessionLocal()
    try:
        org = Organization(name=ORG_NAAM)
        db.add(org)
        db.flush()
        user = User(email="sok-test@example.org", hashed_password=hash_password("Sok12345!"),
                    first_name="Sok", last_name="Test", role=UserRole.ADMIN,
                    organization_id=org.id, is_active=True)
        db.add(user)
        db.commit()
        yield org.id, user.id
    finally:
        db.close()


def _importeer(org_id, user_id, overschrijf=False):
    db = SessionLocal()
    try:
        data = imp.laad_data()
        org = db.get(Organization, org_id)
        user = db.get(User, user_id)
        project = imp.upsert_project(db, data, org, user)
        assets = imp.upsert_wegen(db, data, org, user, project)
        telling = imp.upsert_meldingen(db, data, org, user, project, assets,
                                       overschrijf=overschrijf)
        db.commit()
        return project.id, telling
    finally:
        db.close()


def test_dataset_is_compleet_en_consistent():
    """De dataset zelf — los van de database — moet kloppen met de rapporten."""
    data = imp.laad_data()
    meldingen = data["meldingen"]
    assert len(meldingen) == 156
    assert len(data["wegen"]) == 48

    # Elke melding hoort bij een weg die ook echt in de dataset staat.
    codes = {w["code"] for w in data["wegen"]}
    assert {m["asset_code"] for m in meldingen} <= codes

    # Bronreferenties zijn uniek — daarop matcht de importer.
    refs = [m["ref"] for m in meldingen]
    assert len(set(refs)) == len(refs)

    # Op één punt na staat overal een coordinaat uit het rapport, en die ligt
    # in Amsterdam-Noord.
    zonder = [m for m in meldingen if m["lat"] is None]
    assert len(zonder) == 1, "alleen Baanakkerspad mist GPS in de bron"
    assert "Baanakkerspad" in zonder[0]["titel"]
    for m in meldingen:
        if m["lat"] is not None:
            assert 52.20 <= m["lat"] <= 52.50, m["titel"]
            assert 4.60 <= m["lng"] <= 5.10, m["titel"]


def test_elke_melding_heeft_de_schouwfoto():
    data = imp.laad_data()
    for m in data["meldingen"]:
        assert m["foto"], f"geen foto bij {m['titel']}"
        assert (imp.FOTO_MAP / m["foto"]).exists(), m["foto"]
        url = imp.foto_data_url(m["foto"])
        assert url and url.startswith("data:image/jpeg;base64,")


def test_projectgebied_omsluit_alle_punten():
    """Het projectgebied op de kaart moet de meldingen echt omvatten."""
    data = imp.laad_data()
    ring = data["project"]["boundary_geojson"]["geometry"]["coordinates"][0]
    lngs = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    for m in data["meldingen"]:
        if m["lat"] is None:
            continue
        assert min(lats) <= m["lat"] <= max(lats), m["titel"]
        assert min(lngs) <= m["lng"] <= max(lngs), m["titel"]


def test_import_koppelt_project_wegen_en_meldingen(org_en_user):
    org_id, user_id = org_en_user
    project_id, (nieuw, bijgewerkt, met_foto) = _importeer(org_id, user_id)
    assert (nieuw, bijgewerkt, met_foto) == (156, 0, 156)

    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        assert project.name == "SOK Amsterdam - Asfalt 2026"
        assert project.gemeente == "Amsterdam"
        assert json.loads(project.categories)[0] == "Hotbox werkzaamheden"
        assert json.loads(project.boundary_geojson)["geometry"]["type"] == "Polygon"

        assets = db.query(Asset).filter(Asset.project_id == project_id).all()
        assert len(assets) == 48
        assert all(a.asset_type == "wegvak_asfalt" for a in assets)
        # Een weg wordt op het zwaartepunt van zijn meldingen gezet. Alleen
        # Baanakkerspad blijft zonder coordinaat: zijn enige melding heeft er
        # in het bronrapport ook geen.
        zonder_coord = [a.name for a in assets if not (a.lat and a.lng)]
        assert zonder_coord == ["Baanakkerspad"]

        meldingen = db.query(Melding).filter(Melding.project_id == project_id).all()
        assert len(meldingen) == 156
        # Alles hangt aan een weg en heeft de schouwfoto.
        assert all(m.asset_id for m in meldingen)
        # De werksoort komt uit de handgeschreven code in de kantlijn. Wat geen
        # code had blijft "nog in te delen"; dat mag niet stilletjes groeien.
        per_soort = Counter(m.category for m in meldingen)
        assert per_soort["Hotbox werkzaamheden"] == 86
        assert per_soort["Asfalt machinaal"] == 23
        assert per_soort[ONBEPAALD] == 47
        assert set(per_soort) == {"Hotbox werkzaamheden", "Asfalt machinaal", ONBEPAALD}
        # Eén melding is in het rapport met de hand als voorrang aangemerkt.
        assert sum(1 for m in meldingen if m.priority == "kritiek") == 1
        assert all((m.photo_url or "").startswith("data:image/") for m in meldingen)
        # De veldfoto is nog niet gemaakt — die plek moet vrij blijven.
        assert all(m.photo_after_url is None for m in meldingen)
        assert sum(1 for m in meldingen if m.lat is not None) == 155
    finally:
        db.close()


def test_tweede_run_maakt_geen_duplicaten(org_en_user):
    org_id, user_id = org_en_user
    _importeer(org_id, user_id)
    project_id, (nieuw, bijgewerkt, _) = _importeer(org_id, user_id)
    assert (nieuw, bijgewerkt) == (0, 156)

    db = SessionLocal()
    try:
        assert db.query(Melding).filter(Melding.project_id == project_id).count() == 156
        assert db.query(Asset).filter(Asset.project_id == project_id).count() == 48
        assert db.query(Project).filter(Project.organization_id == org_id).count() == 1
    finally:
        db.close()


def test_hernoemde_melding_blijft_dezelfde_melding(org_en_user):
    """De importer matcht op bronreferentie, niet op titel — anders levert een
    bijgeschaafde titel een duplicaat op."""
    org_id, user_id = org_en_user
    project_id, _ = _importeer(org_id, user_id)

    db = SessionLocal()
    try:
        m = db.query(Melding).filter(Melding.project_id == project_id).first()
        ref = re.search(r"(SOK-AMS-2026-\d+)", m.description).group(1)
        m.title = "Door de gebruiker aangepaste titel"
        db.commit()
    finally:
        db.close()

    _importeer(org_id, user_id)
    db = SessionLocal()
    try:
        assert db.query(Melding).filter(Melding.project_id == project_id).count() == 156
        hits = [x for x in db.query(Melding).filter(Melding.project_id == project_id).all()
                if ref in (x.description or "")]
        assert len(hits) == 1
    finally:
        db.close()


def test_csv_uitvoer_past_op_de_bestaande_import(tmp_path):
    """De CSV's moeten door /api/meldingen/import/csv en de asset-import heen."""
    import csv as csvmod
    paden = imp.schrijf_csv(imp.laad_data(), str(tmp_path))
    assert len(paden) == 3

    with open(tmp_path / "sok-amsterdam-meldingen.csv", encoding="utf-8-sig") as f:
        rijen = list(csvmod.DictReader(f, delimiter=";"))
    assert len(rijen) == 156
    assert {"title", "description", "category", "priority", "lat", "lng",
            "project", "asset_code", "foto"} <= set(rijen[0])
    assert rijen[0]["project"] == "SOK Amsterdam - Asfalt 2026"

    with open(tmp_path / "sok-amsterdam-wegen.csv", encoding="utf-8-sig") as f:
        wegen = list(csvmod.DictReader(f, delimiter=";"))
    assert len(wegen) == 48
    assert {"code", "asset_type", "name", "lat", "lng"} <= set(wegen[0])

    import zipfile
    with zipfile.ZipFile(tmp_path / "sok-amsterdam-fotos.zip") as z:
        assert len(z.namelist()) == 156


def test_herimport_laat_veldwerk_staan(org_en_user):
    """Als er later betere foto's komen, mag een tweede import de indeling en
    de verplaatste pin uit het portaal niet terugdraaien."""
    org_id, user_id = org_en_user
    project_id, _ = _importeer(org_id, user_id)

    db = SessionLocal()
    try:
        m = db.query(Melding).filter(Melding.project_id == project_id,
                                     Melding.lat.isnot(None)).first()
        melding_id, oude_foto = m.id, m.photo_url
        m.category = "Hotbox werkzaamheden"
        m.title = "Eigen titel van de uitvoerder"
        m.lat, m.lng = 52.40000, 4.90000
        m.photo_after_url = "data:image/jpeg;base64,AAAA"
        db.commit()
    finally:
        db.close()

    _importeer(org_id, user_id)
    db = SessionLocal()
    try:
        m = db.get(Melding, melding_id)
        assert m.category == "Hotbox werkzaamheden"
        assert m.title == "Eigen titel van de uitvoerder"
        assert (m.lat, m.lng) == (52.40000, 4.90000)
        assert m.photo_after_url == "data:image/jpeg;base64,AAAA"
        assert m.photo_url == oude_foto      # schouwfoto wel ververst
    finally:
        db.close()


def test_overschrijf_zet_de_brongegevens_terug(org_en_user):
    org_id, user_id = org_en_user
    project_id, _ = _importeer(org_id, user_id)

    db = SessionLocal()
    try:
        m = db.query(Melding).filter(Melding.project_id == project_id).first()
        melding_id = m.id
        m.category = "Hotbox werkzaamheden"
        m.title = "Eigen titel"
        db.commit()
    finally:
        db.close()

    _importeer(org_id, user_id, overschrijf=True)
    db = SessionLocal()
    try:
        m = db.get(Melding, melding_id)
        assert m.category == ONBEPAALD
        assert m.title != "Eigen titel"
    finally:
        db.close()


# ── Inladen vanuit het portaal ────────────────────────────────────────────
# Niet iedereen heeft shell-toegang tot de server; de knop op de projectpagina
# laadt dezelfde dataset in de organisatie van de ingelogde beheerder.

def _token(user_id):
    from auth import create_access_token
    return {"Authorization": f"Bearer {create_access_token({'sub': user_id})}"}


def test_endpoint_laadt_in_eigen_organisatie(client, org_en_user):
    org_id, user_id = org_en_user
    db = SessionLocal()
    try:
        db.get(User, user_id).is_org_admin = True
        db.commit()
    finally:
        db.close()

    r = client.post("/api/imports/sok-amsterdam", json={"met_fotos": True},
                    headers=_token(user_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project"] == "SOK Amsterdam - Asfalt 2026"
    assert (body["meldingen_nieuw"], body["wegen"], body["met_foto"]) == (156, 48, 156)

    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == body["project_id"]).first()
        assert project.organization_id == org_id
        assert db.query(Melding).filter(Melding.project_id == project.id).count() == 156
    finally:
        db.close()

    # Tweede keer: geen duplicaten.
    r2 = client.post("/api/imports/sok-amsterdam", json={"met_fotos": True},
                     headers=_token(user_id))
    assert r2.json()["meldingen_nieuw"] == 0
    assert r2.json()["meldingen_bijgewerkt"] == 156


def test_endpoint_dry_run_schrijft_niets_weg(client, org_en_user):
    org_id, user_id = org_en_user
    db = SessionLocal()
    try:
        db.get(User, user_id).is_org_admin = True
        db.commit()
    finally:
        db.close()

    r = client.post("/api/imports/sok-amsterdam", json={"dry_run": True},
                    headers=_token(user_id))
    assert r.status_code == 200
    assert r.json()["meldingen_nieuw"] == 156
    assert r.json()["project_id"] is None

    db = SessionLocal()
    try:
        assert db.query(Project).filter(Project.organization_id == org_id).count() == 0
    finally:
        db.close()


def test_endpoint_alleen_voor_beheerder(client, org_en_user):
    _, user_id = org_en_user           # is_org_admin blijft False
    r = client.post("/api/imports/sok-amsterdam", json={}, headers=_token(user_id))
    assert r.status_code == 403


def test_knop_staat_op_de_projectpagina():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "templates" / "portaal.html").read_text(encoding="utf-8")
    assert 'id="sokImportBtn"' in html
    assert "/api/imports/sok-amsterdam" in html
    # Alleen zichtbaar voor een org-beheerder.
    assert "sokBtn.style.display = (currentUser && currentUser.is_org_admin)" in html


# ── Clusteren ─────────────────────────────────────────────────────────────
# De job-orchestratie pakt alleen meldingen op met `gw_term` gezet. Zonder die
# maatregel leverde "genereer clusters" nul clusters op zonder te zeggen
# waarom — precies wat er misging toen dit project net ingeladen was.

def test_werksoort_draagt_de_crow_maatregel(org_en_user):
    org_id, user_id = org_en_user
    project_id, _ = _importeer(org_id, user_id)
    db = SessionLocal()
    try:
        meldingen = db.query(Melding).filter(Melding.project_id == project_id).all()
        met_werksoort = [m for m in meldingen if m.category != ONBEPAALD]
        assert len(met_werksoort) == 109
        assert all(m.gw_term and m.gw_maatregel for m in met_werksoort)
        # Zonder werksoort hoort er ook geen maatregel te staan.
        assert all(not m.gw_term for m in meldingen if m.category == ONBEPAALD)
    finally:
        db.close()


def test_clusteren_levert_clusters_op(org_en_user):
    from orchestration import generate_clusters
    org_id, user_id = org_en_user
    _importeer(org_id, user_id)
    db = SessionLocal()
    try:
        r = generate_clusters(db, org_id, radius_km=1.0)
    finally:
        db.close()
    assert r["clusters_created"] >= 5, r
    assert r["meldingen_clustered"] >= 90, r
    assert r["savings_percentage"] > 0


def test_clusters_volgen_de_werksoort_indeling(org_en_user):
    """Een cluster bevat één werksoort — hotbox-werk plan je niet samen met
    machinaal werk, ook al liggen de plekken naast elkaar."""
    from orchestration import generate_clusters
    from models import JobCluster
    org_id, user_id = org_en_user
    _importeer(org_id, user_id)
    db = SessionLocal()
    try:
        generate_clusters(db, org_id, radius_km=1.0)
        clusters = db.query(JobCluster).filter(
            JobCluster.organization_id == org_id).all()
        assert clusters
        for c in clusters:
            in_cluster = db.query(Melding).filter(
                Melding.job_cluster_id == c.id).all()
            soorten = {m.category for m in in_cluster}
            assert len(soorten) == 1, f"cluster {c.gw_term} mengt {soorten}"
            assert c.gw_term == WERKSOORT_MAATREGEL[soorten.pop()]["gw_term"]
            assert c.skill_code in CLUSTER_SKILLS
            assert c.estimated_hours and c.estimated_hours > 0
        # Alleen de twee vastgestelde werksoorten clusteren; wat nog ingedeeld
        # moet worden hoort er bewust buiten te vallen.
        assert {c.gw_term for c in clusters} == {"Hotbox werkzaamheden", "Asfalt machinaal"}
        onbepaald = db.query(Melding).filter(
            Melding.organization_id == org_id, Melding.category == ONBEPAALD).all()
        assert len(onbepaald) == 47
        assert all(m.job_cluster_id is None for m in onbepaald)
    finally:
        db.close()


def test_werksoort_wijzigen_verplaatst_de_melding_naar_het_juiste_cluster(org_en_user):
    """De indeling in het portaal is leidend: wie een melding indeelt, ziet hem
    bij de eerstvolgende generatie in het cluster van die werksoort staan."""
    from orchestration import generate_clusters
    from models import JobCluster
    org_id, user_id = org_en_user
    _importeer(org_id, user_id)
    db = SessionLocal()
    try:
        onbepaald = (db.query(Melding)
                       .filter(Melding.organization_id == org_id,
                               Melding.category == ONBEPAALD,
                               Melding.lat.isnot(None))
                       .limit(4).all())
        assert len(onbepaald) == 4
        for m in onbepaald:
            m.category = "Scheuren vullen"
        db.commit()
        ids = [m.id for m in onbepaald]

        generate_clusters(db, org_id, radius_km=50.0)
        verplaatst = db.query(Melding).filter(Melding.id.in_(ids)).all()
        assert all(m.gw_term == "Scheuren herstellen" for m in verplaatst)
        clusters = {c.id: c for c in db.query(JobCluster).filter(
            JobCluster.organization_id == org_id).all()}
        scheuren = {clusters[m.job_cluster_id].gw_term for m in verplaatst
                    if m.job_cluster_id}
        assert scheuren == {"Scheuren herstellen"}
        assert all(clusters[m.job_cluster_id].skill_code == "VULLEN_POLYMEER"
                   for m in verplaatst if m.job_cluster_id)
    finally:
        db.close()


def test_asfaltsoort_staat_los_van_de_werksoort(org_en_user):
    """Rood of zwart zegt waar het werk in zit, niet hoe het wordt uitgevoerd.
    Het rapport noemt het maar bij negen locaties; de rest blijft leeg in plaats
    van stilzwijgend zwart te worden."""
    org_id, user_id = org_en_user
    project_id, _ = _importeer(org_id, user_id)
    db = SessionLocal()
    try:
        meldingen = db.query(Melding).filter(Melding.project_id == project_id).all()
        soorten = Counter(json.loads(m.norm_data_json or "{}").get("asfaltsoort")
                          for m in meldingen)
        assert soorten["zwart"] == 8
        assert soorten["rood"] == 1
        assert soorten[None] == 147
        assert sum(soorten.values()) == 156
        # De asfaltsoort is geen werksoort — anders zou hij de kaartkleur en het
        # cluster sturen, en dat hoort de uitvoering te doen.
        assert not any(s in WERKSOORT_KLEUREN for s in
                       ("Asfalt zwart (rijweg)", "Asfalt rood (fiets-/voetpad)"))
        # En hij staat in de omschrijving, zodat de uitvoerder buiten weet welk
        # asfalt er op de wagen moet.
        zwart = [m for m in meldingen
                 if json.loads(m.norm_data_json or "{}").get("asfaltsoort") == "zwart"]
        assert all("Asfaltsoort: zwart asfalt (rijweg)" in m.description for m in zwart)
    finally:
        db.close()


def test_bewerken_van_een_melding_wist_de_maatvoering_niet(client, org_en_user):
    """Het portaal stuurt bij het opslaan alleen de norm-formuliervelden mee.
    De gemeten oppervlakken komen uit de import en moeten dat overleven —
    anders zakt de MJOP in zodra iemand een tekstje corrigeert."""
    org_id, user_id = org_en_user
    project_id, _ = _importeer(org_id, user_id)
    db = SessionLocal()
    try:
        m = (db.query(Melding)
               .filter(Melding.project_id == project_id,
                       Melding.norm_data_json.isnot(None))
               .first())
        melding_id, voor = m.id, json.loads(m.norm_data_json)
        assert voor.get("oppervlakte_m2")
    finally:
        db.close()

    r = client.put(f"/api/meldingen/{melding_id}",
                   json={"title": "Door de uitvoerder verduidelijkt",
                         "norm_data": {}},
                   headers=_token(user_id))
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        na = json.loads(db.get(Melding, melding_id).norm_data_json)
        assert na["oppervlakte_m2"] == voor["oppervlakte_m2"]
        assert na.get("maatvoering") == voor.get("maatvoering")
    finally:
        db.close()


def test_maatvoering_staat_bij_de_melding(org_en_user):
    """De uitvoerder moet de maat zien, en de calculatie moet erop kunnen rekenen."""
    org_id, user_id = org_en_user
    project_id, _ = _importeer(org_id, user_id)
    db = SessionLocal()
    try:
        meldingen = db.query(Melding).filter(Melding.project_id == project_id).all()
        met_maat = [m for m in meldingen if m.norm_data_json
                    and json.loads(m.norm_data_json).get("oppervlakte_m2")]
        assert len(met_maat) == 146          # 10 meldingen noemen geen enkele maat
        totaal = sum(json.loads(m.norm_data_json)["oppervlakte_m2"] for m in met_maat)
        assert 2000 < totaal < 2200, totaal
    finally:
        db.close()


def test_wegen_hebben_conditie_en_hoeveelheid_voor_de_mjop(org_en_user):
    """De MJOP rekent per asset met conditie-score en hoeveelheid; zonder die
    twee valt een weg volledig buiten de begroting."""
    org_id, user_id = org_en_user
    project_id, _ = _importeer(org_id, user_id)
    db = SessionLocal()
    try:
        assets = db.query(Asset).filter(Asset.project_id == project_id).all()
        assert all(1 <= a.condition_score <= 6 for a in assets)
        met_opp = 0
        for a in assets:
            props = json.loads(a.properties_json)
            assert "conditie_herkomst" in props
            if props.get("oppervlakte_m2"):
                met_opp += 1
        assert met_opp >= 45
    finally:
        db.close()


def test_elke_melding_heeft_een_crow_klasse(org_en_user):
    """De classificatie is een schatting van de schouwfoto. Hij mag onzeker
    zijn, maar niet ontbreken — anders vallen meldingen buiten de voorspeller
    en de prioritering, en dat is precies waar de klant naar kijkt."""
    from crow_kosten import ALL_KLASSEN, klasse_to_categorie
    org_id, user_id = org_en_user
    project_id, _ = _importeer(org_id, user_id)
    db = SessionLocal()
    try:
        meldingen = db.query(Melding).filter(Melding.project_id == project_id).all()
        assert len(meldingen) == 156
        for m in meldingen:
            assert m.crow_klasse in ALL_KLASSEN, m.title
            assert m.crow_ernst == m.crow_klasse[0]
            assert m.crow_omvang == m.crow_klasse[1]
            assert m.crow_schadegroep and m.crow_schadebeeld
            assert 1 <= m.nen_2767_conditie <= 6
            assert m.onderhoud_categorie == klasse_to_categorie(m.crow_klasse)
            # De onderbouwing moet mee — een klasse zonder herkomst is een getal
            # waar niemand op durft te bouwen.
            assert "Classificatie:" in m.description
            assert "controleren op locatie" in m.description
        # De classificatie stuurt de maatregel niet: die volgt uit de werksoort,
        # want die bepaalt welke ploeg gaat.
        hotbox = [m for m in meldingen if m.category == "Hotbox werkzaamheden"]
        assert all(m.gw_term == "Hotbox werkzaamheden" for m in hotbox)
    finally:
        db.close()


def test_schadebeeld_past_bij_wat_er_ligt(org_en_user):
    """Het meeste werk is klinkerstrook in het asfalt, geen scheurvorming —
    het schadebeeld hoort dat te zeggen."""
    org_id, user_id = org_en_user
    project_id, _ = _importeer(org_id, user_id)
    db = SessionLocal()
    try:
        meldingen = db.query(Melding).filter(Melding.project_id == project_id).all()
        beelden = Counter(m.crow_schadebeeld for m in meldingen)
        assert beelden["verzakking"] > 100
        # En de echte asfaltschade is wel apart benoemd.
        assert beelden["kuilen"] >= 4
        assert sum(v for k, v in beelden.items() if k.startswith("scheurvorming")) >= 4
        # Elk schadebeeld hoort in de CROW-catalogus thuis.
        from crow_kosten import ALL_SCHADEBEELDEN
        bekend = {b for _, b in ALL_SCHADEBEELDEN}
        assert set(beelden) <= bekend, set(beelden) - bekend
    finally:
        db.close()
