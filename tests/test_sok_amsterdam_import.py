"""Regressietest voor de import van 'SOK Amsterdam - Asfalt 2026'.

Borgt dat de dataset uit de drie PDF-rapporten compleet en gekoppeld in de
database landt, dat een tweede run niets dubbel aanmaakt, en dat de gegevens
die de klant op de kaart ziet — coordinaat, foto, weg, werksoort — kloppen.
"""
import json
import re

import pytest

import import_sok_amsterdam as imp
from database import SessionLocal
from models import Asset, Melding, Organization, Project, User, UserRole
from werksoorten import ONBEPAALD

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
        # Alles hangt aan een weg, heeft de schouwfoto en de nog-in-te-delen werksoort.
        assert all(m.asset_id for m in meldingen)
        assert all(m.category == ONBEPAALD for m in meldingen)
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
