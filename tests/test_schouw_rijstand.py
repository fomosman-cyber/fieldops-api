"""Schouw rijdend: opnemen tijdens het rijden, analyseren daarna.

Wat hier vastligt:

1. **Opnemen wacht nergens op.** Een opname is meteen binnen; de analyse volgt
   (in de tests direct, op de server in een wachtrij).
2. **Alleen het wegdek gaat naar het model**: het wegdek vooruit plus een
   scherpe uitsnede van het stuk vlak voor de auto, en de rode vakken komen
   terug op de goede plek in het hele beeld.
3. **Een beeld telt één keer**, ook als de upload na een haperende verbinding
   opnieuw binnenkomt.
4. **Een haperend model is geen schoon stuk weg.** Het beeld gaat terug in de
   wachtrij en wordt pas na herhaald falen als mislukt geboekt.
5. **Een schade ligt voor de auto, niet eronder.** Met koers en snelheid wordt
   de plek vooruit geschat, en twee beelden van dezelfde kuil worden één.
6. **Wat na het afronden binnenkomt, telt mee.**
"""

import io
import json
import math

import pytest
from PIL import Image

import schouw_analyse
import schouw_vision as sv
from database import SessionLocal
from models import SchouwOpname, Schouwrit, Schouwwaarneming
from routers.schouw_router import _naar_heel_beeld, _projecteer, _wegdek_uitsnede

from .conftest import auth


def _jpeg(b=1280, h=720) -> bytes:
    im = Image.new("RGB", (b, h), (90, 90, 95))
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


def _data_url() -> str:
    import base64
    return "data:image/jpeg;base64," + base64.b64encode(_jpeg()).decode()


KUIL = {"verharding": "asfalt", "schadebeeld": "kuilen", "ernst": "M", "omvang": "1",
        "kader": [0.40, 0.50, 0.60, 0.70], "zekerheid": 0.9, "toelichting": "gat rechts"}


def nep_model(monkeypatch):
    """Nep-model: antwoordt met wat de test in `antwoorden` zet, en onthoudt
    met welke stand en inhoud het werd aangeroepen."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    staat = {"antwoorden": [], "standen": [], "beelden": [], "inhoud": []}

    def roep_aan(sleutel, inhoud, instellingen=None, stand="alles"):
        staat["standen"].append(stand)
        staat["inhoud"].append(inhoud)
        # Het te beoordelen beeld is het laatste beeld; ervoor kunnen voorbeelden staan.
        staat["beelden"].append([b for b in inhoud if b["type"] == "image"][-1]["source"]["data"])
        antwoord = staat["antwoorden"].pop(0) if staat["antwoorden"] else {"bruikbaar": True, "wegschade": []}
        if isinstance(antwoord, Exception):
            raise antwoord
        return json.dumps(antwoord), "claude-opus-5"

    monkeypatch.setattr(sv, "_roep_aan", roep_aan)
    return staat


@pytest.fixture
def model(monkeypatch):
    return nep_model(monkeypatch)


def _rit(client, user):
    r = client.post("/api/schouw/ritten", headers=auth(user),
                    json={"naam": "Haarlem Zuid", "privacy_modus": "rijdend"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _opname(client, user, rit_id, volgnummer, **kw):
    body = {"image_data_url": _data_url(), "volgnummer": volgnummer, "geanonimiseerd": True,
            "verpixeld": 2, "breedte": 1280, "hoogte": 720, "wegdek_boven": 0.4,
            "gemaakt_op": "2026-09-22T10:00:00+00:00", **kw}
    return client.post(f"/api/schouw/ritten/{rit_id}/opnames", headers=auth(user), json=body)


def _opnames(rit_id):
    db = SessionLocal()
    try:
        return db.query(SchouwOpname).filter(SchouwOpname.schouwrit_id == rit_id).all()
    finally:
        db.close()


def _schades(rit_id):
    db = SessionLocal()
    try:
        return (db.query(Schouwwaarneming)
                  .filter(Schouwwaarneming.schouwrit_id == rit_id,
                          Schouwwaarneming.crow_schadebeeld.isnot(None)).all())
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Opnemen en analyseren
# ---------------------------------------------------------------------------

def test_opname_wordt_als_wegdek_geanalyseerd_met_vak_op_de_goede_plek(client, admin_user, model):
    rit_id = _rit(client, admin_user)
    model["antwoorden"].append({"bruikbaar": True, "wegschade": [KUIL],
                                "gebied": [{"klasse": "afval_los", "waarde": 3}],
                                "objecten": [{"type": "afvalbak", "niveau": "A"}]})
    r = _opname(client, admin_user, rit_id, 1)
    assert r.status_code == 200, r.text and r.json()["dubbel"] is False
    assert model["standen"] == ["wegdek"]

    [o] = _opnames(rit_id)
    assert (o.status, o.schades, o.model_id) == ("klaar", 1, "claude-opus-5")
    assert o.duur_ms is not None and o.photo_url
    [w] = _schades(rit_id)
    # Kader in de uitsnede (onderste 60%) terug naar het hele beeld.
    assert json.loads(w.kader) == [0.4, 0.7, 0.6, 0.82]
    assert w.photo_url == o.photo_url
    # Buiten het wegdek wordt in deze stand niets vastgelegd.
    db = SessionLocal()
    try:
        assert db.query(Schouwwaarneming).filter(
            Schouwwaarneming.schouwrit_id == rit_id).count() == 1
    finally:
        db.close()


def test_alleen_het_wegdek_gaat_naar_het_model(model):
    uitsnede, extra, boven = _wegdek_uitsnede(_jpeg(1920, 1080), 0.4)
    with Image.open(io.BytesIO(uitsnede)) as im:
        assert im.size[0] <= 1568
        assert abs(im.size[1] / im.size[0] - (1080 * 0.6) / 1920) < 0.01
    # Het stuk vlak voor de auto gaat er scherp naast: daar is een haarscheur
    # nog een paar pixels breed.
    assert len(extra) == 1
    with Image.open(io.BytesIO(extra[0])) as im:
        assert abs(im.size[1] / im.size[0] - (1080 * 0.3) / 1920) < 0.02
    assert _naar_heel_beeld([0.1, 0.0, 0.2, 1.0], 0.4) == [0.1, 0.4, 0.2, 1.0]


def test_zelfde_beeld_telt_een_keer(client, admin_user, model):
    rit_id = _rit(client, admin_user)
    assert _opname(client, admin_user, rit_id, 7).json()["dubbel"] is False
    assert _opname(client, admin_user, rit_id, 7).json()["dubbel"] is True
    assert len(_opnames(rit_id)) == 1


def test_haperend_model_komt_terug_in_de_wachtrij(client, admin_user, model):
    rit_id = _rit(client, admin_user)
    model["antwoorden"] += [RuntimeError("overbelast")] * 3
    _opname(client, admin_user, rit_id, 1)
    [o] = _opnames(rit_id)
    assert (o.status, o.pogingen) == ("wacht", 1) and "overbelast" in o.fout
    schouw_analyse.verwerk(o.id)
    schouw_analyse.verwerk(o.id)
    [o] = _opnames(rit_id)
    assert (o.status, o.pogingen) == ("mislukt", 3)
    # Niet als "bekeken, niets gezien" geboekt.
    db = SessionLocal()
    try:
        assert db.get(Schouwrit, rit_id).frames == 0
    finally:
        db.close()


def test_onbruikbaar_beeld_is_klaar_met_reden(client, admin_user, model):
    rit_id = _rit(client, admin_user)
    model["antwoorden"].append({"bruikbaar": False, "reden_onbruikbaar": "voorligger vult het beeld",
                                "wegschade": []})
    _opname(client, admin_user, rit_id, 1)
    [o] = _opnames(rit_id)
    assert o.status == "klaar" and o.fout == "voorligger vult het beeld"


def test_een_beeld_wordt_maar_een_keer_beoordeeld(client, admin_user, model):
    rit_id = _rit(client, admin_user)
    _opname(client, admin_user, rit_id, 1)
    [o] = _opnames(rit_id)
    schouw_analyse.verwerk(o.id)          # al klaar: niets meer doen
    assert len(model["standen"]) == 1


def test_herstart_zet_blijven_liggen_opnames_weer_in_de_wachtrij(client, admin_user, model):
    rit_id = _rit(client, admin_user)
    model["antwoorden"].append(RuntimeError("stroomstoring"))
    _opname(client, admin_user, rit_id, 1)
    db = SessionLocal()
    try:
        o = db.query(SchouwOpname).filter(SchouwOpname.schouwrit_id == rit_id).one()
        o.status = "bezig"                 # de server stopte midden in de analyse
        db.commit()
    finally:
        db.close()
    assert schouw_analyse.herstel_wachtrij() == 1
    [o] = _opnames(rit_id)
    assert o.status == "klaar"


# ---------------------------------------------------------------------------
# Plek van de schade
# ---------------------------------------------------------------------------

def test_schade_ligt_voor_de_auto():
    lat, lng = 52.37, 4.63
    # Laag in beeld: dichtbij. Hoger: verder weg. Richting noord: lat stijgt.
    dichtbij = _projecteer(lat, lng, 0.0, 11.0, [0.4, 0.85, 0.6, 0.95], 0.4)
    veraf = _projecteer(lat, lng, 0.0, 11.0, [0.4, 0.5, 0.6, 0.6], 0.4)
    assert lat < dichtbij[0] < veraf[0] and dichtbij[1] == pytest.approx(lng)
    # Stilstaand zegt de koers niets: dan de plek van het toestel.
    assert _projecteer(lat, lng, 0.0, 0.3, [0.4, 0.5, 0.6, 0.6], 0.4) == (lat, lng)
    assert _projecteer(lat, lng, None, 11.0, [0.4, 0.5, 0.6, 0.6], 0.4) == (lat, lng)


def test_dezelfde_kuil_uit_twee_beelden_is_een_schade(client, admin_user, model):
    """Beeld 1 ziet de kuil verder weg, beeld 2 tien meter later vlak voor de
    auto. Vooruit geschat komen ze op dezelfde plek uit."""
    rit_id = _rit(client, admin_user)
    lat0, lng0, boven = 52.37, 4.63, 0.4

    def kader_voor(afstand):
        hoek = math.degrees(math.atan(1.3 / afstand))
        midden = boven + hoek / 50.0
        return [0.45, (midden - boven) / (1 - boven) - 0.02,
                0.55, (midden - boven) / (1 - boven) + 0.02]

    model["antwoorden"] += [{"bruikbaar": True, "wegschade": [dict(KUIL, kader=kader_voor(14))]},
                            {"bruikbaar": True, "wegschade": [dict(KUIL, kader=kader_voor(4),
                                                                   zekerheid=0.95)]}]
    _opname(client, admin_user, rit_id, 1, lat=lat0, lng=lng0, koers=0.0, snelheid_ms=11.0,
            nauwkeurigheid_m=4.0)
    _opname(client, admin_user, rit_id, 2, lat=lat0 + 10 / 111_320, lng=lng0, koers=0.0,
            snelheid_ms=11.0, nauwkeurigheid_m=4.0,
            gemaakt_op="2026-09-22T10:00:01+00:00")
    [w] = _schades(rit_id)
    assert w.keer_gezien == 2
    assert w.lat == pytest.approx(lat0 + 14 / 111_320, abs=2 / 111_320)


# ---------------------------------------------------------------------------
# Stand, afronden, toegang
# ---------------------------------------------------------------------------

def test_stand_van_de_analyse(client, admin_user, model):
    rit_id = _rit(client, admin_user)
    model["antwoorden"].append({"bruikbaar": True, "wegschade": [KUIL]})
    _opname(client, admin_user, rit_id, 1)
    _opname(client, admin_user, rit_id, 2)
    d = client.get(f"/api/schouw/ritten/{rit_id}/opnames", headers=auth(admin_user)).json()
    assert (d["totaal"], d["klaar"], d["wacht"], d["schades"]) == (2, 2, 0, 1)
    assert d["laatste_schades"][0]["schadebeeld"] == "kuilen"
    assert d["rit"]["opnames"]["klaar"] == 2


def test_wat_na_afronden_binnenkomt_telt_mee(client, admin_user, model):
    rit_id = _rit(client, admin_user)
    assert client.post(f"/api/schouw/ritten/{rit_id}/afronden",
                       headers=auth(admin_user)).status_code == 200
    model["antwoorden"].append({"bruikbaar": True, "wegschade": [KUIL]})
    assert _opname(client, admin_user, rit_id, 1).status_code == 200
    assert len(_schades(rit_id)) == 1


def test_een_andere_organisatie_kan_niet_opnemen(client, admin_user, model):
    from models import AccountStatus, Organization, SubscriptionPlan

    from .conftest import _make_user
    rit_id = _rit(client, admin_user)
    db = SessionLocal()
    try:
        ander = Organization(name="Andere Gemeente", plan=SubscriptionPlan.PROFESSIONAL,
                             status=AccountStatus.ACTIVE, max_users=5)
        db.add(ander)
        db.commit()
        vreemde = _make_user(db, "vreemd@andere.nl", org=ander)
    finally:
        db.close()
    assert _opname(client, vreemde, rit_id, 1).status_code == 404
    assert _opnames(rit_id) == []


def test_lopende_schouw_neemt_geen_opnames(client, admin_user, model):
    r = client.post("/api/schouw/ritten", headers=auth(admin_user), json={"naam": "Lopend"})
    assert _opname(client, admin_user, r.json()["id"], 1).status_code == 400


def test_verwijderen_neemt_de_opnames_mee(client, admin_user, model):
    rit_id = _rit(client, admin_user)
    _opname(client, admin_user, rit_id, 1)
    assert client.delete(f"/api/schouw/ritten/{rit_id}", headers=auth(admin_user)).status_code == 200
    assert _opnames(rit_id) == []


def test_de_wegdek_prompt_noemt_wat_op_schade_lijkt():
    tekst = sv._systeem_prompt_wegdek()
    for valkuil in ("wegmarkering", "schaduw", "reparatievlak", "putdeksels", "naad"):
        assert valkuil in tekst
    assert "kuilen" in tekst and "scheurvorming-langs" in tekst


# ---------------------------------------------------------------------------
# Het portaal
# ---------------------------------------------------------------------------

def _portaal():
    with io.open("templates/portaal.html", encoding="utf-8") as f:
        return f.read()


def test_portaal_kan_rijdend_starten_en_stuurt_alleen_verpixeld():
    html = _portaal()
    assert '<option value="rijdend">' in html
    assert "privacy_modus: (document.getElementById('schouwStand') || {}).value === 'rijdend'" in html
    assert "'/api/schouw/ritten/' + item.rit + '/opnames'" in html
    # Zonder verpixelen gaat er niets de deur uit.
    assert "if (!model) throw new Error('verpixelen');" in html
    assert "if (n === null) throw new Error('verpixelen');" in html


def test_portaal_bewaart_niet_verstuurde_beelden_en_houdt_het_scherm_aan():
    html = _portaal()
    assert "indexedDB.open('fieldops-schouw', 1)" in html
    assert "navigator.wakeLock.request('screen')" in html
    assert "function _schouwRijHervat()" in html and "_schouwRijHervat();" in html


def test_verpixelen_rekent_de_vakken_terug_naar_het_hele_beeld():
    html = _portaal()
    assert "var b = p.bbox.map(function (v) { return v * factor; });" in html


def test_camera_mag_van_het_portaal_zelf():
    import main
    assert "camera=(self)" in main._PERMISSIONS_POLICY


def test_de_wegdek_prompt_legt_uit_hoe_schade_er_vanuit_een_auto_uitziet():
    tekst = sv._systeem_prompt_wegdek()
    for stuk in ("BEELD 2", "wielsporen", "LICHTE SCHADE IS OOK SCHADE", "haarscheur", "rafeling:"):
        assert stuk in tekst, stuk
    # Niemand wacht op deze beoordeling, dus liever beter kijken.
    assert sv._EFFORT_WEGDEK["snel"] == "high"


def test_proefbeeld_kan_een_foto_als_wegdek_beoordelen(client, admin_user, model):
    """Een foto van een eerdere rit door dezelfde molen: zo zie je wat de
    herkenning ervan maakt, en welk deel als wegdek is beoordeeld."""
    model["antwoorden"].append({"bruikbaar": True, "wegschade": [KUIL]})
    r = client.post("/api/schouw/proefbeeld", headers=auth(admin_user),
                    json={"image_data_url": _data_url(), "stand": "wegdek", "wegdek_boven": 0.4})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["stand"] == "wegdek" and model["standen"][-1] == "wegdek"
    assert d["beeld"].startswith("data:image/jpeg;base64,") and d["beeld"] != _data_url()
    assert len([b for b in model["inhoud"][-1] if b["type"] == "image"]) == 2
    assert [i["soort"] for i in d["items"]] == ["schade"]
