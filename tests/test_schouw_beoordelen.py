"""Schouw na de rit: beoordelen, leren van de oordelen, en de dekking.

Wat hier vastligt:

1. **Een rijdende schouw beoordeel je na de rit**, ook als hij al is
   afgerond; de uitslag rekent dan mee. Een lopende schouw blijft dicht na
   afronden.
2. **Afwijzen met een reden, verbeteren met behoud van wat het model zei.**
   Dat zijn de lessen.
3. **De oordelen gaan mee naar het model** als voorbeelden vóór het beeld,
   afgewezen voorbeelden eerst, en ze worden opnieuw gemaakt na elk oordeel.
4. **Dekking: niet bekeken is iets anders dan geen schade.** Een gat tussen
   twee beelden staat als "niet bekeken" op de kaart.
"""

import pytest

import schouw_leren
from database import SessionLocal
from models import Schouwrit, Schouwwaarneming

from .conftest import auth
from .test_schouw_rijstand import KUIL, _opname, _rit, _schades, nep_model


@pytest.fixture
def model(monkeypatch):
    return nep_model(monkeypatch)


@pytest.fixture(autouse=True)
def _schone_voorbeelden():
    schouw_leren._cache.clear()
    yield
    schouw_leren._cache.clear()


def _schade_via_rit(client, user, model, **kuil):
    rit_id = _rit(client, user)
    model["antwoorden"].append({"bruikbaar": True, "wegschade": [dict(KUIL, **kuil)]})
    _opname(client, user, rit_id, 1)
    [w] = _schades(rit_id)
    return rit_id, w.id


def _patch(client, user, wid, **body):
    return client.patch(f"/api/schouw/waarnemingen/{wid}", headers=auth(user), json=body)


# ---------------------------------------------------------------------------
# Beoordelen
# ---------------------------------------------------------------------------

def test_afwijzen_met_reden_en_bevestigen_wist_de_reden(client, admin_user, model):
    _, wid = _schade_via_rit(client, admin_user, model)
    d = _patch(client, admin_user, wid, afgewezen=True, afwijs_reden="naad").json()
    assert d["afgewezen"] and d["afwijs_reden"] == "naad"
    d = _patch(client, admin_user, wid, bevestigd=True).json()
    assert d["bevestigd"] and not d["afgewezen"] and d["afwijs_reden"] is None
    assert _patch(client, admin_user, wid, afgewezen=True, afwijs_reden="onzin").status_code == 400


def test_ander_schadebeeld_bewaart_wat_het_model_zei(client, admin_user, model):
    _, wid = _schade_via_rit(client, admin_user, model)
    d = _patch(client, admin_user, wid, schadebeeld="scheurvorming-langs", ernst="E",
               bevestigd=True).json()
    assert (d["schadebeeld"], d["ernst"], d["klasse_niveau"]) == ("scheurvorming-langs", "E", "D")
    assert d["oorspronkelijk_schadebeeld"] == "kuilen"
    # Een tweede verbetering overschrijft het origineel niet.
    d = _patch(client, admin_user, wid, schadebeeld="scheurvorming-dwars").json()
    assert d["oorspronkelijk_schadebeeld"] == "kuilen"
    # Een schadebeeld van een andere verharding past niet.
    assert _patch(client, admin_user, wid, verharding="beton",
                  schadebeeld="kuilen").status_code == 400


def test_rijdend_na_afronden_nog_te_beoordelen_en_uitslag_rekent_mee(client, admin_user, model):
    rit_id, wid = _schade_via_rit(client, admin_user, model, zekerheid=0.5)
    assert client.post(f"/api/schouw/ritten/{rit_id}/afronden",
                       headers=auth(admin_user)).status_code == 200
    assert _patch(client, admin_user, wid, bevestigd=True).status_code == 200
    db = SessionLocal()
    try:
        w = db.get(Schouwwaarneming, wid)
        assert w.bevestigd and w.beoordeeld_op is not None
        assert db.get(Schouwrit, rit_id).status == "afgerond"
    finally:
        db.close()


def test_lopende_schouw_blijft_dicht_na_afronden(client, admin_user, model):
    r = client.post("/api/schouw/ritten", headers=auth(admin_user), json={"gebied": "Centrum"}).json()
    w = client.post(f"/api/schouw/ritten/{r['id']}/waarneming", headers=auth(admin_user),
                    json={"detectieklasse": "afval_los", "drager": "elementenverharding",
                          "waarde": 2}).json()
    client.post(f"/api/schouw/ritten/{r['id']}/afronden", headers=auth(admin_user))
    assert _patch(client, admin_user, w["id"], afgewezen=True).status_code == 409


def test_catalogus_noemt_de_redenen():
    from routers.schouw_router import catalogus
    assert catalogus()["afwijs_redenen"]["wegmarkering"] == "Wegmarkering of belijning"


# ---------------------------------------------------------------------------
# Leren
# ---------------------------------------------------------------------------

def test_zonder_oordelen_geen_voorbeelden(client, admin_user, model):
    _schade_via_rit(client, admin_user, model)
    inhoud = model["inhoud"][-1]
    # Alleen de twee beelden van het wegdek zelf, met hun labels.
    assert inhoud[0]["text"] == "BEELD 1:"
    assert len([b for b in inhoud if b["type"] == "image"]) == 2


def test_oordelen_gaan_als_voorbeelden_mee_naar_het_volgende_beeld(client, admin_user, model):
    rit_id, wid = _schade_via_rit(client, admin_user, model)
    _patch(client, admin_user, wid, afgewezen=True, afwijs_reden="wegmarkering")

    model["antwoorden"].append({"bruikbaar": True, "wegschade": []})
    _opname(client, admin_user, rit_id, 2)
    inhoud = model["inhoud"][-1]
    teksten = [b["text"] for b in inhoud if b["type"] == "text"]
    assert inhoud[0]["type"] == "text" and "VOORBEELDEN" in inhoud[0]["text"]
    assert any("AFGEWEZEN" in t and "wegmarkering" in t for t in teksten)
    assert "Vaak ten onrechte gemeld" in inhoud[0]["text"]
    # Eerst het voorbeeld, daarna de twee beelden van dit stuk weg.
    beelden = [i for i, b in enumerate(inhoud) if b["type"] == "image"]
    assert len(beelden) == 3 and beelden[-1] > max(i for i, b in enumerate(inhoud) if "cache_control" in b)


def test_nieuw_oordeel_ververst_de_voorbeelden(client, admin_user, model):
    rit_id, wid = _schade_via_rit(client, admin_user, model)
    _patch(client, admin_user, wid, afgewezen=True, afwijs_reden="schaduw")
    _opname(client, admin_user, rit_id, 2)
    assert any("schaduw" in (b.get("text") or "") for b in model["inhoud"][-1])
    _patch(client, admin_user, wid, bevestigd=True)
    _opname(client, admin_user, rit_id, 3)
    teksten = [b.get("text") or "" for b in model["inhoud"][-1]]
    assert any("BEVESTIGD" in t for t in teksten) and not any("AFGEWEZEN" in t for t in teksten)


# ---------------------------------------------------------------------------
# Dekking
# ---------------------------------------------------------------------------

def test_dekking_toont_bekeken_onbruikbaar_en_gaten(client, admin_user, model):
    rit_id = _rit(client, admin_user)
    stap = 10 / 111_320
    model["antwoorden"] += [{"bruikbaar": True, "wegschade": [KUIL]},
                            {"bruikbaar": True, "wegschade": []},
                            {"bruikbaar": False, "reden_onbruikbaar": "te donker", "wegschade": []},
                            {"bruikbaar": True, "wegschade": []}]
    for i, noord in enumerate((0, 1, 2, 12)):          # na het derde beeld 100 m niets
        _opname(client, admin_user, rit_id, i + 1, lat=52.37 + noord * stap, lng=4.63,
                nauwkeurigheid_m=4.0)
    d = client.get(f"/api/schouw/ritten/{rit_id}/dekking", headers=auth(admin_user)).json()
    assert [p["status"] for p in d["punten"]] == ["schade", "schoon", "onbruikbaar", "schoon"]
    assert [s["status"] for s in d["stukken"]] == ["beoordeeld", "beoordeeld", "niet_bekeken"]
    assert d["km"]["beoordeeld"] == pytest.approx(0.02, abs=0.001)
    assert d["km"]["niet_bekeken"] == pytest.approx(0.1, abs=0.001)
    assert d["km_gereden"] == pytest.approx(0.12, abs=0.001)
    assert len(d["schades"]) == 1 and d["schades"][0]["naam"]


def test_dekking_van_een_lopende_schouw_is_leeg(client, admin_user):
    r = client.post("/api/schouw/ritten", headers=auth(admin_user), json={"gebied": "Centrum"}).json()
    d = client.get(f"/api/schouw/ritten/{r['id']}/dekking", headers=auth(admin_user)).json()
    assert d["punten"] == [] and d["stukken"] == [] and d["km_gereden"] == 0


# ---------------------------------------------------------------------------
# Het portaal
# ---------------------------------------------------------------------------

def test_portaal_heeft_beoordelen_en_dekking():
    import io
    with io.open("templates/portaal.html", encoding="utf-8") as f:
        html = f.read()
    assert 'id="schouwBeoordeelModal"' in html and "function schouwBeoordeelOpen()" in html
    # Twijfelgevallen eerst, redenen uit de catalogus, verbeteren met ernst.
    assert "return (a.zekerheid || 0) - (b.zekerheid || 0);" in html
    assert "_schouwCatalogus.afwijs_redenen" in html
    assert "verharding: delen[0], schadebeeld: delen[1]" in html
    assert "'/api/schouw/ritten/' + id + '/dekking'" in html
    assert "niet_bekeken: { color: '#EA580C'" in html
