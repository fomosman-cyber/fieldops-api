"""Inspectie-soort: grote CROW-inspectie vs klein onderhoud.

Grote CROW = volledige formele decompositie (auto_elements). Klein onderhoud =
snel/licht (start zonder bouwdelen; voeg zelf toe). De soort wordt opgeslagen +
teruggegeven in de respons.
"""

from database import SessionLocal
from tests.conftest import auth
from tests.test_kunstwerken_inspecties import _make_asset


def _create(client, admin_user, code, **extra):
    db = SessionLocal()
    try:
        a_id = _make_asset(db, user=admin_user, asset_type="kademuur", code=code).id
    finally:
        db.close()
    payload = {"asset_id": a_id, "title": "T", "kunstwerk_type": "kademuur"}
    payload.update(extra)
    return client.post("/api/kunstwerken-inspecties/", json=payload, headers=auth(admin_user))


def test_grote_crow_volledige_decompositie(client, admin_user):
    r = _create(client, admin_user, "KW-GROOT",
                inspectie_soort="crow_groot", auto_elements=True)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["inspectie_soort"] == "crow_groot"
    assert len(d["elementen"]) >= 5  # standaard kademuur-decompositie


def test_klein_onderhoud_start_leeg(client, admin_user):
    r = _create(client, admin_user, "KW-KLEIN",
                inspectie_soort="klein_onderhoud", auto_elements=False)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["inspectie_soort"] == "klein_onderhoud"
    assert len(d["elementen"]) == 0


def test_ongeldige_soort_valt_terug_op_groot(client, admin_user):
    r = _create(client, admin_user, "KW-FOUT",
                inspectie_soort="onzin", auto_elements=False)
    assert r.status_code == 200, r.text
    assert r.json()["inspectie_soort"] == "crow_groot"


def test_default_soort_is_groot(client, admin_user):
    r = _create(client, admin_user, "KW-DEF", auto_elements=False)
    assert r.status_code == 200, r.text
    assert r.json()["inspectie_soort"] == "crow_groot"
