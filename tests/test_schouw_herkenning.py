"""Schouw: alles in beeld omkaderd, verpixeld versturen, en lesmateriaal.

Wat hier vastligt:

1. **Elk herkend object krijgt een kader en een niveau**, ook als het in orde
   is. Het scherm tekent ze allemaal; schade blijft rood.
2. **Lesmateriaal is alleen geanonimiseerd materiaal.** Een beeld dat niet op
   het toestel is verpixeld, komt niet in de leerset -- ook niet als het als
   bewijs van een schade wel wordt bewaard.
3. **De leerset is een standaardformaat** (COCO), met per kader of een mens het
   bevestigde of afwees. Afgewezen kaders zijn ook lesmateriaal.
4. **Alleen een beheerder haalt de leerset op.**
5. **Het verpixelmodel staat op onze eigen server** en wordt met een
   integriteitscontrole geladen.
"""

import io
import json
import os

import pytest

import crow_schouw as cs
import schouw_vision as sv
from database import SessionLocal
from models import SchouwBeeld, Schouwwaarneming

from .conftest import auth

BEELD = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="


# ---------------------------------------------------------------------------
# Wat de herkenning teruggeeft
# ---------------------------------------------------------------------------

def _antwoord(monkeypatch, payload: dict):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(sv, "_roep_aan", lambda *a, **k: (json.dumps(payload), "model-x"))
    return sv.analyseer_frame(image_bytes=b"\xff\xd8nep", privacy_gecontroleerd=True)


def test_objecten_krijgen_naam_niveau_en_kader(monkeypatch):
    uit = _antwoord(monkeypatch, {"bruikbaar": True, "gebied": [], "wegschade": [], "objecten": [
        {"type": "verkeerslicht", "niveau": "A", "kader": [0.5, 0.1, 0.55, 0.4], "zekerheid": 0.9},
        {"type": "afvalbak", "niveau": "Z", "kader": [0.1, 0.5, 0.2, 0.7], "zekerheid": 0.8},
        {"type": "ufo", "niveau": "A", "kader": [0.1, 0.1, 0.2, 0.2]},
    ]})
    assert [(o["naam"], o["niveau"], o["kader"]) for o in uit["objecten"]] == [
        ("Verkeerslicht", "A", [0.5, 0.1, 0.55, 0.4]),
        ("Afvalbak", None, [0.1, 0.5, 0.2, 0.7]),       # onbekend niveau: geen niveau
    ]


def test_hooguit_twaalf_objecten(monkeypatch):
    objecten = [{"type": "paal_poller", "niveau": "A", "kader": [0.1, 0.1, 0.2, 0.2]}] * 20
    uit = _antwoord(monkeypatch, {"bruikbaar": True, "objecten": objecten})
    assert len(uit["objecten"]) == sv.MAX_OBJECTEN_PER_BEELD


def test_gebied_krijgt_ook_een_kader(monkeypatch):
    uit = _antwoord(monkeypatch, {"bruikbaar": True, "objecten": [], "gebied": [
        {"klasse": "afval_los", "drager": "elementenverharding", "waarde": 3,
         "kader": [0.2, 0.6, 0.35, 0.8], "zekerheid": 0.9}]})
    assert uit["gebied"][0]["kader"] == [0.2, 0.6, 0.35, 0.8]


def test_de_prompt_vraagt_om_alle_objecten_met_kader():
    tekst = sv._systeem_prompt()
    assert "ELK object" in tekst
    for t in sv.OBJECT_TYPES:
        assert t in tekst and t in sv.OBJECT_NAMEN


# ---------------------------------------------------------------------------
# Via de schouwroute
# ---------------------------------------------------------------------------

def _schade(**extra):
    w = {"klasse": "verharding", "drager": "gesloten_verharding",
         "meetlat": cs.meetlat_voor("verharding", "gesloten_verharding"),
         "naam": "Kuil", "eenheid": None, "waarde": None, "klasse_niveau": "C",
         "verharding": "asfalt", "schadegroep": "vlakheid", "schadebeeld": "kuilen",
         "ernst": "M", "omvang": "1", "kader": [0.4, 0.5, 0.6, 0.8],
         "zekerheid": 0.9, "toelichting": None, "beoordeling_nodig": False}
    w.update(extra)
    return w


def _afval():
    return {"klasse": "afval_los", "drager": "elementenverharding",
            "meetlat": cs.meetlat_voor("afval_los", "elementenverharding"),
            "waarde": 3.0, "klasse_niveau": None, "zekerheid": 0.95,
            "toelichting": None, "kader": [0.1, 0.7, 0.2, 0.85],
            "beoordeling_nodig": False}


def _bord():
    return {"type": "verkeersbord", "naam": "Verkeersbord", "niveau": "B",
            "kader": [0.6, 0.2, 0.66, 0.35], "aspect": "reinheid",
            "waarneming": "vuil", "zekerheid": 0.85, "beoordeling_nodig": False}


@pytest.fixture
def herkenning(monkeypatch):
    rij: list[dict] = []

    def nep(**kwargs):
        r = rij.pop(0) if rij else {}
        return {"bruikbaar": True, "reden_onbruikbaar": None,
                "gebied": r.get("gebied", []), "objecten": r.get("objecten", []),
                "wegschade": r.get("wegschade", []),
                "_versie": sv.SCHOUW_VISION_VERSION, "_model_id": "test-model"}
    monkeypatch.setattr(sv, "analyseer_frame", nep)
    return rij


def _rit(client, user):
    r = client.post("/api/schouw/ritten", headers=auth(user),
                    json={"gebied": "Coolsingel", "gebiedstype": "centrum"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _frame(client, user, rit_id, **extra):
    body = {"image_data_url": BEELD, "breedte": 1280, "hoogte": 720}
    body.update(extra)
    r = client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(user), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _beelden(rit_id):
    db = SessionLocal()
    try:
        return db.query(SchouwBeeld).filter(SchouwBeeld.schouwrit_id == rit_id).all()
    finally:
        db.close()


def test_alles_in_beeld_komt_terug_met_soort_en_kader(client, admin_user, herkenning):
    herkenning.append({"gebied": [_afval()], "objecten": [_bord()], "wegschade": [_schade()]})
    rit = _rit(client, admin_user)
    uit = _frame(client, admin_user, rit)
    soorten = {i["soort"]: i for i in uit["in_beeld"]}
    assert set(soorten) == {"schade", "gebied", "object"}
    assert soorten["object"]["naam"] == "Verkeersbord" and soorten["object"]["niveau"] == "B"
    assert soorten["gebied"]["naam"] == cs.DETECTIEKLASSEN["afval_los"]["naam"]
    assert soorten["gebied"]["waarde"] == 3.0
    assert soorten["schade"]["kader"] == [0.4, 0.5, 0.6, 0.8]


def test_verpixelde_beelden_worden_geteld(client, admin_user, herkenning):
    rit = _rit(client, admin_user)
    _frame(client, admin_user, rit, geanonimiseerd=True, verpixeld=2)
    uit = _frame(client, admin_user, rit)
    assert uit["rit"]["frames"] == 2
    assert uit["rit"]["frames_geanonimiseerd"] == 1


def test_verpixeld_beeld_met_schade_wordt_lesmateriaal(client, admin_user, herkenning):
    herkenning.append({"objecten": [_bord()], "wegschade": [_schade()]})
    rit = _rit(client, admin_user)
    uit = _frame(client, admin_user, rit, geanonimiseerd=True, verpixeld=3)
    [b] = _beelden(rit)
    assert (b.breedte, b.hoogte, b.verpixeld, b.photo_url) == (1280, 720, 3, BEELD)
    kaders = json.loads(b.kaders)
    assert {k["soort"] for k in kaders} == {"schade", "object"}
    schade_id = [g for g in uit["gevonden"] if g["wegschade"]][0]["id"]
    assert [k["waarneming_id"] for k in kaders if k["soort"] == "schade"] == [schade_id]
    db = SessionLocal()
    try:
        assert db.get(Schouwwaarneming, schade_id).beeld_id == b.id
    finally:
        db.close()


def test_niet_verpixeld_beeld_wordt_nooit_lesmateriaal(client, admin_user, herkenning):
    herkenning.append({"objecten": [_bord()], "wegschade": [_schade()]})
    rit = _rit(client, admin_user)
    uit = _frame(client, admin_user, rit, bewaar_beeld=True)
    assert _beelden(rit) == []
    # Als bewijs bij de schade wordt hij wel bewaard.
    assert [g for g in uit["gevonden"] if g["wegschade"]][0]["photo_url"] == BEELD


def test_steekproef_van_beelden_zonder_schade(client, admin_user, herkenning):
    from routers import schouw_router
    rit = _rit(client, admin_user)
    for _ in range(schouw_router.LEERBEELD_ELKE):
        herkenning.append({"objecten": [_bord()]})
        _frame(client, admin_user, rit, geanonimiseerd=True, verpixeld=0)
    assert len(_beelden(rit)) == 1


def test_leeg_beeld_wordt_geen_lesmateriaal(client, admin_user, herkenning):
    rit = _rit(client, admin_user)
    _frame(client, admin_user, rit, geanonimiseerd=True, bewaar_beeld=True)
    assert _beelden(rit) == []


def test_ronde_verwijderen_neemt_lesbeelden_mee(client, admin_user, herkenning):
    herkenning.append({"wegschade": [_schade()]})
    rit = _rit(client, admin_user)
    _frame(client, admin_user, rit, geanonimiseerd=True)
    assert len(_beelden(rit)) == 1
    assert client.delete(f"/api/schouw/ritten/{rit}", headers=auth(admin_user)).status_code == 200
    assert _beelden(rit) == []


# ---------------------------------------------------------------------------
# De leerset
# ---------------------------------------------------------------------------

def test_leerset_is_coco_met_oordeel_per_kader(client, admin_user, herkenning):
    herkenning += [{"objecten": [_bord()], "wegschade": [_schade()]},
                   {"wegschade": [_schade(schadebeeld="rafeling", schadegroep="textuur",
                                          naam="Rafeling", kader=[0.1, 0.1, 0.3, 0.3])]}]
    rit = _rit(client, admin_user)
    a = _frame(client, admin_user, rit, geanonimiseerd=True)
    b = _frame(client, admin_user, rit, geanonimiseerd=True)
    kuil = [g for g in a["gevonden"] if g["wegschade"]][0]["id"]
    rafeling = [g for g in b["gevonden"] if g["wegschade"]][0]["id"]
    client.patch(f"/api/schouw/waarnemingen/{kuil}", headers=auth(admin_user), json={"bevestigd": True})
    client.patch(f"/api/schouw/waarnemingen/{rafeling}", headers=auth(admin_user), json={"afgewezen": True})

    r = client.get("/api/schouw/leerset", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    set_ = r.json()
    assert len(set_["images"]) == 2
    namen = {c["id"]: c["name"] for c in set_["categories"]}
    per_label = {namen[a["category_id"]]: a for a in set_["annotations"]}
    assert per_label["kuilen"]["status"] == "bevestigd"
    assert per_label["rafeling"]["status"] == "afgewezen"
    assert per_label["verkeersbord"]["status"] == "voorstel"
    # [0.4, 0.5, 0.6, 0.8] op 1280 x 720 -> x 512, y 360, b 256, h 216
    assert per_label["kuilen"]["bbox"] == [512.0, 360.0, 256.0, 216.0]
    # Een base64-beeld staat niet in de lijst zelf, maar achter een eigen adres.
    assert all(i["file_name"].startswith("/api/schouw/leerset/beeld/") for i in set_["images"])


def test_leerset_beeld_ophalen(client, admin_user, herkenning):
    herkenning.append({"wegschade": [_schade()]})
    rit = _rit(client, admin_user)
    _frame(client, admin_user, rit, geanonimiseerd=True)
    [b] = _beelden(rit)
    r = client.get(f"/api/schouw/leerset/beeld/{b.id}", headers=auth(admin_user))
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


def test_alleen_een_beheerder_haalt_de_leerset_op(client, manager_user):
    assert client.get("/api/schouw/leerset", headers=auth(manager_user)).status_code == 403


# ---------------------------------------------------------------------------
# Het scherm en het verpixelmodel
# ---------------------------------------------------------------------------

def test_verpixelmodel_staat_op_onze_eigen_server():
    map_ = "static/models/coco-ssd-lite"
    with open(os.path.join(map_, "model.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    for groep in manifest["weightsManifest"]:
        for pad in groep["paths"]:
            assert os.path.getsize(os.path.join(map_, pad)) > 0, pad


def test_scherm_verpixelt_voor_het_versturen():
    with io.open("templates/portaal.html", encoding="utf-8") as f:
        html = f.read()
    assert "modelUrl: '/static/models/coco-ssd-lite/model.json'" in html
    assert "var SCHOUW_PRIVACY_KLASSEN = { person: 0.30, car: 0.50" in html
    assert "SCHOUW_VOERTUIG_MAX_OPPERVLAK" in html
    # Beide CDN-scripts met integriteitscontrole.
    assert html.count("'sha384-") >= 2 and "el.integrity = integriteit;" in html
    assert "function _schouwTekenBeeld(" in html and "function schouwWisselWeergave(" in html


def test_modellen_hebben_een_eigen_cache():
    with io.open("static/service-worker.js", encoding="utf-8") as f:
        sw = f.read()
    assert "const MODEL_CACHE = 'fieldops-modellen-v1';" in sw
    assert "MODEL_CACHE].includes(k)" in sw
