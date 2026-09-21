"""Instellingen van de schouwcamera en de herkenning.

Wat hier vastligt:

1. **Niets instellen = het gedrag van vóór de instellingen.** Zelfde prompt,
   zelfde drempels.
2. **Wat uit staat, bestaat niet.** Een objecttype of verharding die de
   organisatie niet laat herkennen, komt niet terug -- ook niet als het model
   hem toch noemt.
3. **Grenzen zijn grenzen.** Verpixelen van mensen kan alleen strenger dan de
   standaard; meetellen kan niet onder 70%. Waarden buiten de grenzen worden
   geweigerd, niet stilletjes bijgeknipt.
4. **Alleen een beheerder stelt de herkenning in**; iedereen die schouwt mag
   zien hoe hij staat.
5. **Een proefbeeld slaat niets op.**
"""

import io
import json
import sys
import types

import pytest

import schouw_instellingen as si
import schouw_vision as sv
from database import SessionLocal
from models import AuditLog, Organization, SchouwBeeld, Schouwwaarneming

from .conftest import auth

BEELD = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="


# ---------------------------------------------------------------------------
# De module
# ---------------------------------------------------------------------------

def test_niets_ingesteld_is_het_oude_gedrag():
    assert si.lees(None) == si.STANDAARD
    assert sv._systeem_prompt(si.STANDAARD) == sv._systeem_prompt()
    assert si.STANDAARD["drempel_automatisch"] == sv.DREMPEL_AUTOMATISCH


@pytest.mark.parametrize("fout", [
    {"onzin": 1},
    {"objecttypen": ["ufo"]},
    {"verhardingen": ["grind"]},
    {"grondigheid": "heel grondig"},
    {"drempel_automatisch": 0.6},
    {"drempel_automatisch": True},
    {"max_objecten": 13},
    {"verpixel_mens": 0.4},          # soepeler dan de standaard: nooit
    {"verpixel_voertuig": 0.9},
])
def test_ongeldige_instelling_wordt_geweigerd(fout):
    with pytest.raises(si.OngeldigeInstelling):
        si.valideer(fout)


def test_strenger_verpixelen_mag_wel():
    assert si.valideer({"verpixel_mens": 0.15})["verpixel_mens"] == 0.15


def test_volgorde_is_altijd_dezelfde():
    # De prompt wordt gecachet; dezelfde keuze in een andere volgorde mag geen
    # andere prompt opleveren.
    a = si.valideer({"objecttypen": ["kolk", "verkeersbord"], "verhardingen": ["beton", "asfalt"]})
    b = si.valideer({"objecttypen": ["verkeersbord", "kolk"], "verhardingen": ["asfalt", "beton"]})
    assert a == b
    assert sv._systeem_prompt(a) == sv._systeem_prompt(b)


def test_kapotte_opgeslagen_instelling_valt_terug_op_standaard():
    org = types.SimpleNamespace(schouw_instellingen="{kapot")
    assert si.lees(org) == si.STANDAARD
    org.schouw_instellingen = json.dumps({"verpixel_mens": 0.9})
    assert si.lees(org) == si.STANDAARD


def test_prompt_volgt_de_instellingen():
    klein = sv._systeem_prompt(si.valideer({"verhardingen": ["asfalt"],
                                            "objecttypen": ["verkeersbord"],
                                            "max_objecten": 4}))
    assert "rafeling" in klein and "kit-gebreken" not in klein and "opdrukking" not in klein
    objecten = klein.split("OBJECTEN:")[1].split("\n")[1]
    assert "verkeersbord" in objecten and "lichtmast" not in objecten
    assert "hooguit 4 objecten" in klein

    leeg = sv._systeem_prompt(si.valideer({"verhardingen": [], "objecttypen": []}))
    assert 'laat "wegschade" leeg' in leeg and 'laat "objecten" leeg' in leeg


def _antwoord(monkeypatch, payload, instellingen):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(sv, "_roep_aan", lambda *a, **k: (json.dumps(payload), "model-x"))
    return sv.analyseer_frame(image_bytes=b"\xff\xd8nep", privacy_gecontroleerd=True,
                              instellingen=instellingen)


def test_wat_uit_staat_komt_niet_terug(monkeypatch):
    inst = si.valideer({"verhardingen": ["elementen"], "objecttypen": ["verkeersbord"],
                        "max_objecten": 1})
    uit = _antwoord(monkeypatch, {"bruikbaar": True, "gebied": [], "objecten": [
        {"type": "lichtmast", "niveau": "A", "kader": [0.1, 0.1, 0.2, 0.5]},
        {"type": "verkeersbord", "niveau": "B", "kader": [0.3, 0.1, 0.4, 0.3]},
        {"type": "verkeersbord", "niveau": "A", "kader": [0.6, 0.1, 0.7, 0.3]},
    ], "wegschade": [
        {"verharding": "asfalt", "schadebeeld": "kuilen", "ernst": "M", "zekerheid": 0.9},
        {"verharding": "elementen", "schadebeeld": "los-liggend", "ernst": "L", "zekerheid": 0.9},
    ]}, inst)
    assert [(o["type"], o["niveau"]) for o in uit["objecten"]] == [("verkeersbord", "B")]
    assert [w["schadebeeld"] for w in uit["wegschade"]] == ["los-liggend"]


def test_drempel_bepaalt_wat_bevestigd_moet_worden(monkeypatch):
    schade = {"bruikbaar": True, "wegschade": [
        {"verharding": "asfalt", "schadebeeld": "kuilen", "ernst": "M", "zekerheid": 0.85}]}
    streng = _antwoord(monkeypatch, schade, si.valideer({"drempel_automatisch": 0.9}))
    soepel = _antwoord(monkeypatch, schade, si.valideer({"drempel_automatisch": 0.8}))
    assert streng["wegschade"][0]["beoordeling_nodig"] is True
    assert soepel["wegschade"][0]["beoordeling_nodig"] is False


def test_grondig_vraagt_meer_denkwerk(monkeypatch):
    verstuurd = {}

    class Client:
        def __init__(self, api_key=None):
            self.messages = self

        def create(self, **kwargs):
            verstuurd.update(kwargs)
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="{}")],
                                         model=kwargs["model"], stop_reason="end_turn")

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Client))
    monkeypatch.delenv("SCHOUW_MODEL", raising=False)
    sv._roep_aan("sleutel", [], si.valideer({"grondigheid": "grondig"}))
    assert verstuurd["extra_body"]["output_config"] == {"effort": "medium"}
    sv._roep_aan("sleutel", [], si.valideer({"grondigheid": "snel"}))
    assert verstuurd["extra_body"]["output_config"] == {"effort": "low"}


# ---------------------------------------------------------------------------
# De routes
# ---------------------------------------------------------------------------

def test_iedereen_die_schouwt_ziet_de_instellingen(client, manager_user, admin_user):
    r = client.get("/api/schouw/instellingen", headers=auth(manager_user))
    assert r.status_code == 200
    assert r.json()["kan_wijzigen"] is False
    assert r.json()["instellingen"] == si.STANDAARD
    assert client.get("/api/schouw/instellingen", headers=auth(admin_user)).json()["kan_wijzigen"] is True


def test_alleen_een_beheerder_stelt_in(client, manager_user):
    r = client.put("/api/schouw/instellingen", headers=auth(manager_user),
                   json={"grondigheid": "grondig"})
    assert r.status_code == 403


def test_beheerder_stelt_in_en_het_wordt_bewaard(client, admin_user):
    r = client.put("/api/schouw/instellingen", headers=auth(admin_user),
                   json={"grondigheid": "grondig", "verhardingen": ["asfalt"],
                         "drempel_automatisch": 0.9})
    assert r.status_code == 200, r.text
    assert r.json()["instellingen"]["grondigheid"] == "grondig"
    again = client.get("/api/schouw/instellingen", headers=auth(admin_user)).json()
    assert again["instellingen"]["verhardingen"] == ["asfalt"]
    db = SessionLocal()
    try:
        assert db.query(AuditLog).filter(AuditLog.action == "schouw.instellingen").count() == 1
    finally:
        db.close()


def test_buiten_de_grenzen_geeft_een_uitleg(client, admin_user):
    r = client.put("/api/schouw/instellingen", headers=auth(admin_user),
                   json={"verpixel_mens": 0.5})
    assert r.status_code == 400
    assert "verpixel_mens" in r.json()["detail"]


def test_schouw_gebruikt_de_instellingen_van_de_organisatie(client, admin_user, monkeypatch):
    client.put("/api/schouw/instellingen", headers=auth(admin_user),
               json={"objecttypen": ["kolk"], "grondigheid": "grondig"})
    gezien = {}

    def nep(**kwargs):
        gezien.update(kwargs)
        return {"bruikbaar": True, "gebied": [], "objecten": [], "wegschade": [],
                "_versie": sv.SCHOUW_VISION_VERSION, "_model_id": "test"}
    monkeypatch.setattr(sv, "analyseer_frame", nep)
    rit = client.post("/api/schouw/ritten", headers=auth(admin_user),
                      json={"gebied": "Markt", "gebiedstype": "centrum"}).json()["id"]
    client.post(f"/api/schouw/ritten/{rit}/frame", headers=auth(admin_user),
                json={"image_data_url": BEELD})
    assert gezien["instellingen"]["objecttypen"] == ["kolk"]
    assert gezien["instellingen"]["grondigheid"] == "grondig"


def test_strengere_drempel_telt_minder_mee(client, admin_user, monkeypatch):
    def nep(**kwargs):
        return {"bruikbaar": True, "objecten": [], "wegschade": [], "gebied": [
            {"klasse": "afval_los", "drager": "elementenverharding",
             "meetlat": "zwerfafval.elementenverharding", "waarde": 2.0,
             "klasse_niveau": None, "zekerheid": 0.85, "toelichting": None,
             "beoordeling_nodig": False}],
            "_versie": sv.SCHOUW_VISION_VERSION, "_model_id": "test"}
    monkeypatch.setattr(sv, "analyseer_frame", nep)
    rit = client.post("/api/schouw/ritten", headers=auth(admin_user),
                      json={"gebied": "Markt", "gebiedstype": "centrum"}).json()["id"]
    [w] = client.post(f"/api/schouw/ritten/{rit}/frame", headers=auth(admin_user),
                      json={"image_data_url": BEELD}).json()["gevonden"]
    assert w["telt_mee"] is True                                  # 0.85 >= 0.80
    client.put("/api/schouw/instellingen", headers=auth(admin_user),
               json={"drempel_automatisch": 0.9})
    detail = client.get(f"/api/schouw/ritten/{rit}", headers=auth(admin_user)).json()
    assert detail["waarnemingen"][0]["telt_mee"] is False         # 0.85 < 0.90


def test_proefbeeld_meldt_duur_en_slaat_niets_op(client, admin_user, monkeypatch):
    def nep(**kwargs):
        assert kwargs["instellingen"] == si.STANDAARD
        return {"bruikbaar": True, "reden_onbruikbaar": None, "gebied": [], "objecten": [
            {"type": "kolk", "naam": "Kolk", "niveau": "A", "kader": [0.4, 0.8, 0.5, 0.9],
             "zekerheid": 0.9}], "wegschade": [
            {"naam": "Kuil", "ernst": "E", "kader": [0.2, 0.6, 0.3, 0.7], "zekerheid": 0.9}],
            "_versie": sv.SCHOUW_VISION_VERSION, "_model_id": "test-model"}
    monkeypatch.setattr(sv, "analyseer_frame", nep)
    r = client.post("/api/schouw/proefbeeld", headers=auth(admin_user),
                    json={"image_data_url": BEELD})
    assert r.status_code == 200, r.text
    uit = r.json()
    assert isinstance(uit["duur_ms"], int) and uit["model_id"] == "test-model"
    assert {i["soort"] for i in uit["items"]} == {"schade", "object"}
    db = SessionLocal()
    try:
        assert db.query(Schouwwaarneming).count() == 0
        assert db.query(SchouwBeeld).count() == 0
    finally:
        db.close()


def test_instellingen_horen_bij_de_eigen_organisatie(client, admin_user):
    client.put("/api/schouw/instellingen", headers=auth(admin_user), json={"max_objecten": 3})
    db = SessionLocal()
    try:
        org = db.get(Organization, admin_user.organization_id)
        assert json.loads(org.schouw_instellingen)["max_objecten"] == 3
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Het scherm
# ---------------------------------------------------------------------------

def test_scherm_heeft_camera_en_herkenning():
    with io.open("templates/portaal.html", encoding="utf-8") as f:
        html = f.read()
    for stuk in ('id="schouwInstellingen"', 'id="schouwCamLens"', 'id="schouwCamGrootte"',
                 'id="schouwCamRitme"', 'value="afstand-10"', 'id="schouwCamScherp"',
                 'id="schouwZoom"', 'id="schouwZaklampBtn"', 'id="schouwProefBtn"',
                 "function _schouwCameraMogelijkheden(", "function _schouwMeetBeeld(",
                 "function _schouwOverslaanReden(", "function schouwHkOpslaan(",
                 "function schouwProef(", "function _schouwKaderSvg("):
        assert stuk in html, stuk
    # "Nu vastleggen" slaat nooit over; de klok roept de automatische variant aan.
    assert 'onclick="schouwNu(true)"' in html
    assert "setTimeout(function () { schouwNu(false); }, ms)" in html
