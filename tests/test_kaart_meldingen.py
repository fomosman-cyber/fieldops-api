"""Tests voor /api/meldingen/map — het lichte endpoint achter de kaart.

De kaart heeft per melding acht velden nodig om een stip te zetten. De gewone
lijst stuurt er ruim dertig, inclusief de CROW-classificatie en de norm-JSON.
Bij drieduizend meldingen scheelt dat 2,2 MB tegen 0,9 MB, over hetzelfde
netwerk als waar de inspecteur buiten op staat te wachten.

Wat hier bewaakt wordt:
  - Het endpoint bestaat vóór /{melding_id} in de routetabel. Staat het erna,
    dan vangt die route "map" op als id en krijgt de kaart een 404 -- precies
    wat er gebeurde toen de dev-server nog de oude code draaide.
  - Geen foto-blobs, alleen de vlag.
  - Meldingen zonder coordinaten horen er niet in: die kun je niet tekenen.
  - Het projectfilter werkt serverkant, zodat een filter op een klein project
    niet alsnog de hele organisatie over de lijn trekt.
"""

import json

from database import SessionLocal
from models import Melding
from tests.conftest import auth


FOTO = "data:image/png;base64," + ("A" * 4000)


def _melding(org_id, user_id, project_id=None, **kw):
    db = SessionLocal()
    try:
        m = Melding(organization_id=org_id, created_by=user_id,
                    project_id=project_id,
                    title=kw.pop("title", "Testmelding"),
                    priority=kw.pop("priority", "normaal"),
                    status=kw.pop("status", "open"), **kw)
        db.add(m)
        db.commit()
        db.refresh(m)
        return m.id
    finally:
        db.close()


def _project(client, user, naam="Kaartproject"):
    r = client.post("/api/projects/", json={"name": naam}, headers=auth(user))
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


# ─────────────────────────────────────────────────────────────────────────────
# De route zelf
# ─────────────────────────────────────────────────────────────────────────────

def test_map_is_een_eigen_route_en_geen_melding_id(client, admin_user):
    """/map mag niet als melding-id worden opgevat.

    In FastAPI wint de eerst geregistreerde route. Zou /{melding_id} eerder
    staan, dan komt "map" daar binnen als id en krijgt de kaart 404 "Melding
    niet gevonden" -- zonder dat iets anders stukgaat, dus dat merk je pas als
    de kaart leeg blijft.
    """
    r = client.get("/api/meldingen/map", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_zonder_login_geen_kaartdata(client):
    assert client.get("/api/meldingen/map").status_code in (401, 403)


# ─────────────────────────────────────────────────────────────────────────────
# Wat erin zit, en vooral wat niet
# ─────────────────────────────────────────────────────────────────────────────

def test_geen_fotoblobs_alleen_de_vlag(client, admin_user):
    """De hele reden dat dit endpoint bestaat."""
    _melding(admin_user.organization_id, admin_user.id,
             title="Met foto", lat=52.1, lng=4.3, photo_url=FOTO)
    _melding(admin_user.organization_id, admin_user.id,
             title="Zonder foto", lat=52.2, lng=4.4)

    r = client.get("/api/meldingen/map", headers=auth(admin_user))
    assert r.status_code == 200
    assert "A" * 500 not in r.text, "de base64-foto zit in het antwoord"

    op_titel = {m["title"]: m for m in r.json()}
    assert op_titel["Met foto"]["has_photo"] is True
    assert op_titel["Zonder foto"]["has_photo"] is False
    assert "photo_url" not in op_titel["Met foto"]


def test_meldingen_zonder_coordinaten_vallen_af(client, admin_user):
    """Een melding zonder plek kun je niet tekenen."""
    _melding(admin_user.organization_id, admin_user.id, title="Op de kaart",
             lat=52.1, lng=4.3)
    _melding(admin_user.organization_id, admin_user.id, title="Geen plek")

    titels = {m["title"] for m in client.get("/api/meldingen/map",
                                             headers=auth(admin_user)).json()}
    assert "Op de kaart" in titels
    assert "Geen plek" not in titels


def test_de_popup_krijgt_wat_hij_toont(client, admin_user):
    _melding(admin_user.organization_id, admin_user.id, title="Scheur N213",
             lat=52.1, lng=4.3, category="Scheuren vullen", priority="hoog",
             description="Langsscheur over ongeveer veertig meter.",
             norm_data_json=json.dumps({"asfaltsoort": "rood",
                                        "vta_risicoklasse": 3}))
    m = client.get("/api/meldingen/map", headers=auth(admin_user)).json()[0]

    for veld in ("id", "title", "category", "priority", "status", "lat", "lng",
                 "project_id", "has_photo", "description", "norm_data"):
        assert veld in m, f"{veld} ontbreekt en de popup toont hem wel"

    # Alleen de asfaltsoort: dat is het enige norm-veld dat de kaart labelt.
    assert m["norm_data"] == {"asfaltsoort": "rood"}
    assert "vta_risicoklasse" not in json.dumps(m["norm_data"])


def test_lange_omschrijving_wordt_afgekapt(client, admin_user):
    """De popup toont een regel, geen roman -- en duizend romans is een payload."""
    _melding(admin_user.organization_id, admin_user.id, title="Lang",
             lat=52.1, lng=4.3, description="x" * 5000)
    m = client.get("/api/meldingen/map", headers=auth(admin_user)).json()[0]
    assert len(m["description"]) <= 240


# ─────────────────────────────────────────────────────────────────────────────
# Filteren en afscherming
# ─────────────────────────────────────────────────────────────────────────────

def test_projectfilter_werkt_serverkant(client, admin_user):
    """Anders trekt een filter op een klein project alsnog alles over de lijn."""
    p1 = _project(client, admin_user, "Project een")
    p2 = _project(client, admin_user, "Project twee")
    _melding(admin_user.organization_id, admin_user.id, p1, title="In een",
             lat=52.1, lng=4.3)
    _melding(admin_user.organization_id, admin_user.id, p2, title="In twee",
             lat=52.2, lng=4.4)

    alles = client.get("/api/meldingen/map", headers=auth(admin_user)).json()
    assert len(alles) == 2

    gefilterd = client.get(f"/api/meldingen/map?project_id={p1}",
                           headers=auth(admin_user)).json()
    assert len(gefilterd) == 1
    assert gefilterd[0]["title"] == "In een"


def test_andere_organisatie_ziet_niets(client, admin_user, platform_owner):
    _melding(admin_user.organization_id, admin_user.id, title="Van de klant",
             lat=52.1, lng=4.3)
    assert client.get("/api/meldingen/map", headers=auth(platform_owner)).json() == []


def test_kaart_payload_is_flink_kleiner_dan_de_volle_lijst(client, admin_user):
    """Meetbaar, niet op gevoel."""
    for i in range(25):
        _melding(admin_user.organization_id, admin_user.id,
                 title=f"Melding {i}", lat=52.0 + i / 100, lng=4.0 + i / 100,
                 description="y" * 400, photo_url=FOTO,
                 crow_schadegroep="samenhang", crow_schadebeeld="rafeling",
                 crow_ernst="M", crow_omvang="2", crow_klasse="M2",
                 gw_maatregel="Vullen polymeer")

    vol = client.get("/api/meldingen/", headers=auth(admin_user))
    kaart = client.get("/api/meldingen/map", headers=auth(admin_user))
    assert vol.status_code == 200 and kaart.status_code == 200
    assert len(kaart.content) < len(vol.content) * 0.75, (
        f"kaart {len(kaart.content)} bytes tegen lijst {len(vol.content)} bytes "
        "-- te weinig verschil, dan is het endpoint zijn bestaan niet waard")
