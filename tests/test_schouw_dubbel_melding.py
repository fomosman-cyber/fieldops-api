"""Schouw: dezelfde schade één keer, en van schade naar melding in één tik.

Wat hier vastligt:

1. **Dezelfde kuil in drie beelden is één kuil.** Zelfde schadebeeld, nog in
   beeld, op dezelfde plek: de bestaande waarneming telt mee in plaats van een
   nieuwe regel.
2. **Liever dubbel dan kwijt.** Twee schades in hetzelfde beeld, of tien meter
   uit elkaar, of een minuut later: dat blijven er twee. Een samengevoegde
   schade die er twee had moeten zijn, is een schade die niemand herstelt.
3. **Het zekerste beeld is het bewijs.** Ernst, kader en foto komen uit
   hetzelfde beeld, zodat ze bij elkaar passen.
4. **Wat een mens besliste, blijft staan.** Een bevestigde of afgewezen
   schade verandert niet meer door wat de camera daarna ziet.
5. **Eén schade, één melding.** Twee keer drukken maakt geen tweede melding.
6. **De keuzelijst bij meldingen kent elk schadebeeld dat de schouw kent.**
"""

import io
import re
from datetime import datetime, timedelta, timezone

import pytest

import crow_kosten as ck
import crow_schouw as cs
import schouw_vision as sv
from database import SessionLocal
from models import Melding, Schouwwaarneming

from .conftest import auth

BEELD = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="
LAT, LNG = 52.0000, 4.2000
METER_LAT = 1 / 111_320          # één meter noord, in graden


def _schade(schadebeeld="kuilen", groep="vlakheid", ernst="M", zekerheid=0.9,
            kader=(0.4, 0.5, 0.6, 0.8), verharding="asfalt", **extra):
    w = {"klasse": "verharding", "drager": "gesloten_verharding",
         "meetlat": cs.meetlat_voor("verharding", "gesloten_verharding"),
         "naam": schadebeeld, "eenheid": None, "waarde": None,
         "klasse_niveau": {"L": "B", "M": "C", "E": "D"}.get(ernst),
         "verharding": verharding, "schadegroep": groep, "schadebeeld": schadebeeld,
         "ernst": ernst, "omvang": "1", "kader": list(kader) if kader else None,
         "zekerheid": zekerheid, "toelichting": f"{schadebeeld} {ernst}",
         "beoordeling_nodig": zekerheid < sv.DREMPEL_AUTOMATISCH}
    w.update(extra)
    return w


@pytest.fixture
def beelden(monkeypatch):
    """Vision geeft per aanroep het volgende antwoord uit de rij."""
    rij: list[list[dict]] = []

    def nep(**kwargs):
        return {"bruikbaar": True, "reden_onbruikbaar": None, "gebied": [],
                "objecten": [], "wegschade": rij.pop(0) if rij else [],
                "_versie": sv.SCHOUW_VISION_VERSION, "_model_id": "test-model"}
    monkeypatch.setattr(sv, "analyseer_frame", nep)
    return rij


def _rit(client, user):
    r = client.post("/api/schouw/ritten", headers=auth(user),
                    json={"gebied": "Dijkweg", "gebiedstype": "woonwijk"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _frame(client, user, rit_id, meter_noord=0.0, gps=True, nauwkeurig=4.0):
    body = {"image_data_url": BEELD}
    if gps:
        body.update(lat=LAT + meter_noord * METER_LAT, lng=LNG, nauwkeurigheid_m=nauwkeurig)
    r = client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(user), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _schades(rit_id):
    db = SessionLocal()
    try:
        return (db.query(Schouwwaarneming)
                  .filter(Schouwwaarneming.schouwrit_id == rit_id,
                          Schouwwaarneming.crow_schadebeeld.isnot(None))
                  .order_by(Schouwwaarneming.created_at).all())
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Samenvoegen
# ---------------------------------------------------------------------------

def test_zelfde_kuil_in_twee_beelden_is_een_kuil(client, admin_user, beelden):
    beelden += [[_schade(kader=(0.4, 0.5, 0.6, 0.8))],
                [_schade(kader=(0.3, 0.6, 0.5, 0.9), zekerheid=0.85)]]
    rit = _rit(client, admin_user)
    eerste = _frame(client, admin_user, rit)
    tweede = _frame(client, admin_user, rit, meter_noord=2)

    assert len(_schades(rit)) == 1
    [w] = tweede["gevonden"]
    assert w["id"] == eerste["gevonden"][0]["id"]
    assert w["samengevoegd"] is True and w["keer_gezien"] == 2
    # Rood in dit beeld hoort op de plek in dít beeld.
    [rood] = tweede["in_beeld"]
    assert (rood["soort"], rood["id"], rood["naam"], rood["ernst"], rood["kader"]) == (
        "schade", w["id"], "Kuil", "M", [0.3, 0.6, 0.5, 0.9])


def test_zekerder_beeld_wordt_het_bewijs(client, admin_user, beelden):
    beelden += [[_schade(ernst="M", zekerheid=0.7, kader=(0.4, 0.5, 0.6, 0.8))],
                [_schade(ernst="E", zekerheid=0.95, kader=(0.1, 0.1, 0.3, 0.3))]]
    rit = _rit(client, admin_user)
    _frame(client, admin_user, rit)
    [w] = _frame(client, admin_user, rit, meter_noord=1)["gevonden"]
    assert w["beeld_vervangen"] is True
    assert (w["ernst"], w["klasse_niveau"], w["kader"]) == ("E", "D", [0.1, 0.1, 0.3, 0.3])
    assert w["zekerheid"] == 0.95


def test_minder_zeker_beeld_laat_het_bewijs_staan(client, admin_user, beelden):
    beelden += [[_schade(ernst="M", zekerheid=0.95, kader=(0.4, 0.5, 0.6, 0.8))],
                [_schade(ernst="E", zekerheid=0.6, kader=(0.1, 0.1, 0.3, 0.3))]]
    rit = _rit(client, admin_user)
    _frame(client, admin_user, rit)
    [w] = _frame(client, admin_user, rit, meter_noord=1)["gevonden"]
    assert w["beeld_vervangen"] is False and w["keer_gezien"] == 2
    assert (w["ernst"], w["kader"]) == ("M", [0.4, 0.5, 0.6, 0.8])


def test_twee_kuilen_in_een_beeld_blijven_er_twee(client, admin_user, beelden):
    beelden += [[_schade(kader=(0.1, 0.5, 0.3, 0.7)), _schade(kader=(0.6, 0.5, 0.8, 0.7))]]
    rit = _rit(client, admin_user)
    _frame(client, admin_user, rit)
    assert len(_schades(rit)) == 2


def test_twee_kuilen_in_twee_beelden_blijven_er_twee(client, admin_user, beelden):
    beelden += [[_schade(), _schade(zekerheid=0.85)],
                [_schade(), _schade(zekerheid=0.85)]]
    rit = _rit(client, admin_user)
    _frame(client, admin_user, rit)
    tweede = _frame(client, admin_user, rit, meter_noord=1)
    assert len(_schades(rit)) == 2
    assert all(w["samengevoegd"] for w in tweede["gevonden"])


def test_tien_meter_verder_is_een_andere_kuil(client, admin_user, beelden):
    beelden += [[_schade()], [_schade()]]
    rit = _rit(client, admin_user)
    _frame(client, admin_user, rit)
    _frame(client, admin_user, rit, meter_noord=10)
    assert len(_schades(rit)) == 2


def test_ander_schadebeeld_is_een_andere_schade(client, admin_user, beelden):
    beelden += [[_schade()], [_schade(schadebeeld="rafeling", groep="textuur")]]
    rit = _rit(client, admin_user)
    _frame(client, admin_user, rit)
    _frame(client, admin_user, rit)
    assert len(_schades(rit)) == 2


def test_later_terugkomen_is_een_nieuwe_waarneming(client, admin_user, beelden):
    beelden += [[_schade()], [_schade()]]
    rit = _rit(client, admin_user)
    _frame(client, admin_user, rit)
    db = SessionLocal()
    try:
        w = db.query(Schouwwaarneming).filter(Schouwwaarneming.schouwrit_id == rit).first()
        w.laatst_gezien_op = datetime.now(timezone.utc) - timedelta(minutes=2)
        w.created_at = w.laatst_gezien_op
        db.commit()
    finally:
        db.close()
    _frame(client, admin_user, rit)
    assert len(_schades(rit)) == 2


def test_zonder_gps_telt_alleen_dat_hij_nog_in_beeld_was(client, admin_user, beelden):
    beelden += [[_schade()], [_schade()]]
    rit = _rit(client, admin_user)
    _frame(client, admin_user, rit, gps=False)
    _frame(client, admin_user, rit, gps=False)
    assert len(_schades(rit)) == 1


def test_grove_gps_mag_niet_ver_samenvoegen(client, admin_user, beelden):
    # Nauwkeurigheid 50 m: de straal blijft op 12 m, anders wordt een hele
    # straat één kuil.
    beelden += [[_schade()], [_schade()]]
    rit = _rit(client, admin_user)
    _frame(client, admin_user, rit, nauwkeurig=50)
    _frame(client, admin_user, rit, meter_noord=20, nauwkeurig=50)
    assert len(_schades(rit)) == 2


def test_bevestigde_schade_verandert_niet_meer(client, admin_user, beelden):
    beelden += [[_schade(ernst="M", zekerheid=0.7)],
                [_schade(ernst="E", zekerheid=0.99, kader=(0.1, 0.1, 0.2, 0.2))]]
    rit = _rit(client, admin_user)
    [w] = _frame(client, admin_user, rit)["gevonden"]
    client.patch(f"/api/schouw/waarnemingen/{w['id']}", headers=auth(admin_user),
                 json={"bevestigd": True})
    [w2] = _frame(client, admin_user, rit)["gevonden"]
    assert (w2["ernst"], w2["keer_gezien"], w2["beeld_vervangen"]) == ("M", 2, False)


def test_afgewezen_schade_komt_niet_terug_als_nieuwe(client, admin_user, beelden):
    beelden += [[_schade()], [_schade()]]
    rit = _rit(client, admin_user)
    [w] = _frame(client, admin_user, rit)["gevonden"]
    client.patch(f"/api/schouw/waarnemingen/{w['id']}", headers=auth(admin_user),
                 json={"afgewezen": True})
    [w2] = _frame(client, admin_user, rit)["gevonden"]
    assert w2["id"] == w["id"] and w2["afgewezen"] is True
    assert len(_schades(rit)) == 1


# ---------------------------------------------------------------------------
# Van schade naar melding
# ---------------------------------------------------------------------------

def _melding_van(client, user, waarneming_id):
    return client.post(f"/api/schouw/waarnemingen/{waarneming_id}/melding",
                       headers=auth(user))


def test_schade_wordt_melding_met_alles_erbij(client, admin_user, beelden):
    beelden += [[_schade(ernst="E")]]
    rit = _rit(client, admin_user)
    [w] = _frame(client, admin_user, rit)["gevonden"]

    r = _melding_van(client, admin_user, w["id"])
    assert r.status_code == 200, r.text
    uit = r.json()
    assert uit["bestond_al"] is False
    assert uit["waarneming"]["melding_id"] == uit["melding_id"]
    assert uit["waarneming"]["bevestigd"] is True

    db = SessionLocal()
    try:
        m = db.query(Melding).filter(Melding.id == uit["melding_id"]).first()
        assert m.title.startswith("Kuil")
        assert (m.crow_schadegroep, m.crow_schadebeeld, m.crow_ernst) == ("vlakheid", "kuilen", "E")
        assert m.crow_omvang is None and m.crow_klasse is None   # omvang is aan de inspecteur
        assert (m.priority, m.category) == ("hoog", "Wegdek")
        assert m.photo_url == BEELD
        assert (round(m.lat, 4), round(m.lng, 4)) == (LAT, LNG)
        assert "schouwcamera" in m.description
    finally:
        db.close()


def test_twee_keer_drukken_geeft_een_melding(client, admin_user, beelden):
    beelden += [[_schade()]]
    rit = _rit(client, admin_user)
    [w] = _frame(client, admin_user, rit)["gevonden"]
    eerste = _melding_van(client, admin_user, w["id"]).json()
    tweede = _melding_van(client, admin_user, w["id"]).json()
    assert tweede["bestond_al"] is True
    assert tweede["melding_id"] == eerste["melding_id"]


def test_klinkers_worden_bestrating(client, admin_user, beelden):
    beelden += [[_schade(verharding="elementen", schadebeeld="los-liggend", groep="stenen",
                         ernst="L", drager="elementenverharding")]]
    rit = _rit(client, admin_user)
    [w] = _frame(client, admin_user, rit)["gevonden"]
    uit = _melding_van(client, admin_user, w["id"]).json()
    db = SessionLocal()
    try:
        m = db.query(Melding).filter(Melding.id == uit["melding_id"]).first()
        assert (m.category, m.priority) == ("Bestrating", "laag")
    finally:
        db.close()


def test_afgewezen_schade_wordt_geen_melding(client, admin_user, beelden):
    beelden += [[_schade()]]
    rit = _rit(client, admin_user)
    [w] = _frame(client, admin_user, rit)["gevonden"]
    client.patch(f"/api/schouw/waarnemingen/{w['id']}", headers=auth(admin_user),
                 json={"afgewezen": True})
    assert _melding_van(client, admin_user, w["id"]).status_code == 409


def test_melding_kan_ook_na_afronden(client, admin_user, beelden):
    beelden += [[_schade()]]
    rit = _rit(client, admin_user)
    [w] = _frame(client, admin_user, rit)["gevonden"]
    client.post(f"/api/schouw/ritten/{rit}/afronden", headers=auth(admin_user))
    assert _melding_van(client, admin_user, w["id"]).status_code == 200


def test_alleen_wegschade_wordt_hier_een_melding(client, admin_user, monkeypatch):
    def nep(**kwargs):
        return {"bruikbaar": True, "reden_onbruikbaar": None, "objecten": [], "wegschade": [],
                "gebied": [{"klasse": "afval_los", "drager": "elementenverharding",
                            "meetlat": cs.meetlat_voor("afval_los", "elementenverharding"),
                            "waarde": 3.0, "klasse_niveau": None, "zekerheid": 0.95,
                            "toelichting": None, "beoordeling_nodig": False}],
                "_versie": sv.SCHOUW_VISION_VERSION, "_model_id": "test-model"}
    monkeypatch.setattr(sv, "analyseer_frame", nep)
    rit = _rit(client, admin_user)
    [w] = _frame(client, admin_user, rit)["gevonden"]
    assert _melding_van(client, admin_user, w["id"]).status_code == 400


def test_onbekende_waarneming_geeft_404(client, admin_user):
    assert _melding_van(client, admin_user, "bestaat-niet").status_code == 404


# ---------------------------------------------------------------------------
# De keuzelijst bij meldingen
# ---------------------------------------------------------------------------

def test_keuzelijst_meldingen_kent_elk_schadebeeld():
    with io.open("templates/portaal.html", encoding="utf-8") as f:
        html = f.read()
    blok = re.search(r"var CROW_SCHADEBEELDEN = \{(.*?)\};", html, re.S).group(1)
    in_portaal = set(re.findall(r"'([a-z-]+)'", blok))
    alle = {b for groepen in (ck.SCHADEGROEPEN_ASFALT, ck.SCHADEGROEPEN_ELEMENTEN,
                              ck.SCHADEGROEPEN_BETON)
            for beelden in groepen.values() for b in beelden}
    assert alle - in_portaal == set(), "schadebeelden die je bij een melding niet kunt kiezen"
