"""Wegschade in de schouw: CROW-schadebeelden, aangewezen met een rood vlak.

Wat hier vastligt:

1. **De camera kent alle schadebeelden die we hebben.** De lijst komt uit
   `crow_kosten`; komt daar een schadebeeld bij zonder herkenning in
   `crow_wegschade`, dan faalt deze test. Anders zou "alle CROW-schades" stil
   ophouden waar te zijn.
2. **Een schade die niet bij de verharding hoort bestaat niet.** Rafeling op
   klinkers is een vergissing van het model, geen waarneming.
3. **Een kapot kader gooit de schade niet weg.** Dan is hij er wel, maar tekenen
   we geen rood vlak op een verkeerde plek.
4. **Een beeld met schade wordt bewaard.** Het is het bewijs, en zonder beeld
   kan het scherm het rode vak later niet meer laten zien.
5. **De catalogus in de prompt is bij elk beeld gelijk.** Anders valt hij uit de
   promptcache en betaalt elk beeld de volle prijs.
"""

import io
import sys
import types

import pytest

import crow_kosten as ck
import crow_schouw as cs
import crow_wegschade as cw
import schouw_vision as sv

from .conftest import auth

BEELD_BYTES = b"\xff\xd8\xff\xe0nep-jpeg"
BEELD_URL = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="


# ---------------------------------------------------------------------------
# De catalogus
# ---------------------------------------------------------------------------

def _alle_uit_crow_kosten():
    paren = set()
    for vcode, groepen in (("asfalt", ck.SCHADEGROEPEN_ASFALT),
                           ("elementen", ck.SCHADEGROEPEN_ELEMENTEN),
                           ("beton", ck.SCHADEGROEPEN_BETON)):
        for beelden in groepen.values():
            paren.update((vcode, b) for b in beelden)
    return paren


def test_elk_schadebeeld_uit_crow_kosten_heeft_herkenning():
    ontbreekt = _alle_uit_crow_kosten() - set(cw.HERKENNING)
    assert not ontbreekt, f"schadebeelden zonder herkenning: {sorted(ontbreekt)}"


def test_geen_herkenning_voor_schadebeelden_die_niet_bestaan():
    over = set(cw.HERKENNING) - _alle_uit_crow_kosten()
    assert not over, f"herkenning zonder schadebeeld in crow_kosten: {sorted(over)}"


def test_elk_schadebeeld_beschrijft_alle_drie_de_ernstklassen():
    for sb in cw.schadebeelden():
        assert sb["kenmerken"] and sb["niet_verwarren_met"], sb["schadebeeld"]
        for e in ("L", "M", "E"):
            assert sb["ernst"][e].strip(), f"{sb['schadebeeld']} mist ernst {e}"


def test_de_prompt_noemt_elk_schadebeeld():
    tekst = sv._systeem_prompt()
    for sb in cw.schadebeelden():
        assert sb["schadebeeld"] in tekst, sb["schadebeeld"]
    for vcode in cw.VERHARDINGEN:
        assert f'"{vcode}"' in tekst


def test_de_prompt_is_bij_elk_beeld_gelijk():
    # Promptcache is een prefix-match: één verschil en de hele catalogus
    # wordt opnieuw betaald.
    assert sv._systeem_prompt() == sv._systeem_prompt()


def test_ernst_valt_op_een_bestaand_beeldkwaliteitsniveau():
    for niveau in cw.ERNST_NAAR_NIVEAU.values():
        assert niveau in cs.KLASSE_CODES


def test_schadebeeld_moet_bij_de_verharding_horen():
    assert cw.zoek("asfalt", "rafeling")
    assert cw.zoek("elementen", "rafeling") is None
    assert cw.zoek("beton", "kuilen") is None


def test_klasse_is_alleen_een_indicatie_als_beide_delen_er_zijn():
    assert cw.klasse_indicatie("M", "2") == "M2"
    assert cw.klasse_indicatie("M", None) is None
    assert cw.klasse_indicatie("X", "2") is None


# ---------------------------------------------------------------------------
# Wat de herkenning teruggeeft
# ---------------------------------------------------------------------------

def _antwoord(monkeypatch, payload: str):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(sv, "_roep_aan", lambda *a, **k: (payload, "model-x"))
    return sv.analyseer_frame(image_bytes=BEELD_BYTES, privacy_gecontroleerd=True)


def _schade(**extra):
    w = {"verharding": "asfalt", "schadebeeld": "scheurvorming-langs",
         "ernst": "M", "omvang": "1", "kader": [0.4, 0.5, 0.6, 0.95],
         "zekerheid": 0.9, "toelichting": "open scheur"}
    w.update(extra)
    return w


def _met(monkeypatch, *schades, gebied=None):
    import json
    return _antwoord(monkeypatch, json.dumps({
        "bruikbaar": True, "gebied": gebied or [], "objecten": [],
        "wegschade": list(schades)}))


def test_wegschade_komt_terug_met_kader_en_niveau(monkeypatch):
    uit = _met(monkeypatch, _schade())
    [w] = uit["wegschade"]
    assert w["schadebeeld"] == "scheurvorming-langs"
    assert w["schadegroep"] == "samenhang"
    assert w["kader"] == [0.4, 0.5, 0.6, 0.95]
    assert w["klasse_niveau"] == "C"                      # M -> C
    assert w["drager"] == "gesloten_verharding"
    assert w["meetlat"] == cs.meetlat_voor("verharding", "gesloten_verharding")
    assert w["beoordeling_nodig"] is False


def test_klinkers_tellen_op_elementenverharding(monkeypatch):
    uit = _met(monkeypatch, _schade(verharding="elementen", schadebeeld="los-liggend", ernst="E"))
    [w] = uit["wegschade"]
    assert w["drager"] == "elementenverharding"
    assert w["klasse_niveau"] == "D"


def test_schade_die_niet_bij_de_verharding_hoort_verdwijnt(monkeypatch):
    uit = _met(monkeypatch, _schade(verharding="elementen", schadebeeld="rafeling"),
               _schade(schadebeeld="bestaat-niet"))
    assert uit["wegschade"] == []


@pytest.mark.parametrize("kader", [
    [0.6, 0.5, 0.4, 0.9],          # omgedraaid
    [0.4, 0.5, 0.405, 0.9],        # te smal om iets aan te wijzen
    [0.1, 0.2, 0.3],               # te kort
    ["links", 0.2, 0.3, 0.4],      # geen getal
    None,
])
def test_kapot_kader_houdt_de_schade_maar_zonder_vlak(monkeypatch, kader):
    uit = _met(monkeypatch, _schade(kader=kader))
    [w] = uit["wegschade"]
    assert w["kader"] is None
    assert w["schadebeeld"] == "scheurvorming-langs"


def test_kader_buiten_het_beeld_wordt_bijgeknipt(monkeypatch):
    uit = _met(monkeypatch, _schade(kader=[-0.2, 0.3, 1.4, 0.9]))
    assert uit["wegschade"][0]["kader"] == [0.0, 0.3, 1.0, 0.9]


def test_zonder_ernst_moet_een_mens_kijken(monkeypatch):
    uit = _met(monkeypatch, _schade(ernst="zwaar"))
    [w] = uit["wegschade"]
    assert w["ernst"] is None and w["klasse_niveau"] is None
    assert w["beoordeling_nodig"] is True


def test_onzekere_schade_moet_bevestigd_worden(monkeypatch):
    uit = _met(monkeypatch, _schade(zekerheid=0.5))
    assert uit["wegschade"][0]["beoordeling_nodig"] is True


def test_platte_verhardingsmelding_telt_niet_dubbel(monkeypatch):
    plat = {"klasse": "verharding", "drager": "gesloten_verharding",
            "klasse_niveau": "C", "zekerheid": 0.9}
    met_schade = _met(monkeypatch, _schade(), gebied=[plat])
    assert [w["klasse"] for w in met_schade["gebied"]] == []
    zonder_schade = _met(monkeypatch, gebied=[plat])
    assert [w["klasse"] for w in zonder_schade["gebied"]] == ["verharding"]


def test_hooguit_acht_schades_per_beeld(monkeypatch):
    uit = _met(monkeypatch, *[_schade() for _ in range(12)])
    assert len(uit["wegschade"]) == sv.MAX_WEGSCHADE_PER_BEELD


def test_leeg_antwoord_heeft_ook_een_lege_wegschadelijst(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    uit = sv.analyseer_frame(image_bytes=BEELD_BYTES, privacy_gecontroleerd=True)
    assert uit["wegschade"] == [] and uit["bruikbaar"] is False


def test_slechtste_schade_bepaalt_de_score_van_het_vak():
    code = cs.meetlat_voor("verharding", "gesloten_verharding")
    frames = [
        {"bruikbaar": True, "gebied": [], "wegschade": [
            {"meetlat": code, "waarde": None, "klasse_niveau": "C", "beoordeling_nodig": False}]},
        {"bruikbaar": True, "gebied": [], "wegschade": [
            {"meetlat": code, "waarde": None, "klasse_niveau": "D", "beoordeling_nodig": False}]},
    ]
    assert sv.bundel_tot_waarnemingen(frames)["directe_klassen"][code] == "D"


# ---------------------------------------------------------------------------
# De aanroep zelf
# ---------------------------------------------------------------------------

def test_extra_velden_per_model():
    opus = sv._verzoek_extra("claude-opus-5")
    assert opus["extra_body"]["output_config"] == {"effort": "low"}
    assert opus["extra_body"]["fallbacks"] == "default"
    assert opus["extra_headers"]["anthropic-beta"] == "server-side-fallback-2026-07-01"

    sonnet = sv._verzoek_extra("claude-sonnet-4-6")
    assert sonnet["extra_body"] == {"output_config": {"effort": "low"}}
    assert "extra_headers" not in sonnet

    assert sv._verzoek_extra("claude-haiku-4-5") == {}


class _NepAnthropic:
    """Staat in voor de SDK: legt vast wat er verstuurd wordt."""

    def __init__(self, stop_reason="end_turn"):
        self.verstuurd = {}
        self.stop_reason = stop_reason

    def module(self):
        nep = self

        class Client:
            def __init__(self, api_key=None):
                self.messages = self

            def create(self, **kwargs):
                nep.verstuurd = kwargs
                blok = types.SimpleNamespace(type="text", text='{"bruikbaar": true}')
                denk = types.SimpleNamespace(type="thinking", thinking="")
                return types.SimpleNamespace(content=[denk, blok], model=kwargs["model"],
                                             stop_reason=nep.stop_reason)

        return types.SimpleNamespace(Anthropic=Client)


def test_standaardmodel_en_cache_op_de_catalogus(monkeypatch):
    nep = _NepAnthropic()
    monkeypatch.setitem(sys.modules, "anthropic", nep.module())
    monkeypatch.delenv("SCHOUW_MODEL", raising=False)
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-4-6")   # geldt voor inspecties, niet hier

    tekst, model = sv._roep_aan("sleutel", [{"type": "text", "text": "x"}])
    assert model == sv.MODEL_STANDAARD == "claude-opus-5"
    assert tekst == '{"bruikbaar": true}'                      # denkblok niet in de tekst
    assert nep.verstuurd["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert nep.verstuurd["extra_body"]["output_config"] == {"effort": "low"}


def test_schouw_model_is_in_te_stellen(monkeypatch):
    nep = _NepAnthropic()
    monkeypatch.setitem(sys.modules, "anthropic", nep.module())
    monkeypatch.setenv("SCHOUW_MODEL", "claude-sonnet-5")
    sv._roep_aan("sleutel", [])
    assert nep.verstuurd["model"] == "claude-sonnet-5"
    assert "fallbacks" not in nep.verstuurd["extra_body"]


def test_weigering_levert_een_onbruikbaar_beeld(monkeypatch):
    nep = _NepAnthropic(stop_reason="refusal")
    monkeypatch.setitem(sys.modules, "anthropic", nep.module())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    uit = sv.analyseer_frame(image_bytes=BEELD_BYTES, privacy_gecontroleerd=True)
    assert uit["bruikbaar"] is False
    assert "weigerde" in uit["reden_onbruikbaar"]


# ---------------------------------------------------------------------------
# Via de schouwroute
# ---------------------------------------------------------------------------

@pytest.fixture
def stub_schade(monkeypatch):
    def zet(wegschade=None, gebied=None):
        def nep(**kwargs):
            if not kwargs.get("privacy_gecontroleerd"):
                raise sv.NietGeblurd("niet geblurd")
            return {"bruikbaar": True, "reden_onbruikbaar": None,
                    "gebied": gebied or [], "objecten": [],
                    "wegschade": wegschade or [],
                    "_versie": sv.SCHOUW_VISION_VERSION, "_model_id": "test-model"}
        monkeypatch.setattr(sv, "analyseer_frame", nep)
    return zet


def _rit(client, user):
    r = client.post("/api/schouw/ritten", headers=auth(user),
                    json={"gebied": "Dorpsstraat", "gebiedstype": "woonwijk"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _schone_schade(**extra):
    """Zoals _schoon_wegschade hem teruggeeft."""
    w = {"klasse": "verharding", "drager": "gesloten_verharding",
         "meetlat": cs.meetlat_voor("verharding", "gesloten_verharding"),
         "naam": "Langsscheur", "eenheid": None, "waarde": None,
         "klasse_niveau": "C", "verharding": "asfalt", "schadegroep": "samenhang",
         "schadebeeld": "scheurvorming-langs", "ernst": "M", "omvang": "1",
         "kader": [0.4, 0.5, 0.6, 0.95], "zekerheid": 0.92,
         "toelichting": "open scheur", "beoordeling_nodig": False}
    w.update(extra)
    return w


def test_frame_met_schade_geeft_kader_en_crow_velden(client, admin_user, stub_schade):
    stub_schade(wegschade=[_schone_schade()])
    rit_id = _rit(client, admin_user)
    r = client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(admin_user),
                    json={"image_data_url": BEELD_URL})
    assert r.status_code == 200, r.text
    [w] = r.json()["gevonden"]
    assert w["wegschade"] is True
    assert w["naam"] == "Langsscheur"
    assert w["kader"] == [0.4, 0.5, 0.6, 0.95]
    assert (w["schadegroep"], w["schadebeeld"], w["ernst"]) == ("samenhang", "scheurvorming-langs", "M")
    assert w["klasse_indicatie"] == "M1"
    assert w["klasse_niveau"] == "C"
    assert w["telt_mee"] is True


def test_beeld_met_schade_wordt_altijd_bewaard(client, admin_user, stub_schade):
    stub_schade(wegschade=[_schone_schade(), _schone_schade(schadebeeld="rafeling", naam="Rafeling")])
    rit_id = _rit(client, admin_user)
    gevonden = client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(admin_user),
                           json={"image_data_url": BEELD_URL}).json()["gevonden"]
    assert len(gevonden) == 2
    assert all(w["photo_url"] == BEELD_URL for w in gevonden)


def test_schade_blijft_na_herladen_aanwijsbaar(client, admin_user, stub_schade):
    stub_schade(wegschade=[_schone_schade()])
    rit_id = _rit(client, admin_user)
    client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(admin_user),
                json={"image_data_url": BEELD_URL})
    detail = client.get(f"/api/schouw/ritten/{rit_id}", headers=auth(admin_user)).json()
    [w] = detail["waarnemingen"]
    assert w["kader"] == [0.4, 0.5, 0.6, 0.95] and w["photo_url"]


def test_catalogus_bevat_alle_schadebeelden(client, admin_user):
    r = client.get("/api/schouw/catalogus", headers=auth(admin_user))
    assert r.status_code == 200
    ws = r.json()["wegschade"]
    assert len(ws["schadebeelden"]) == len(_alle_uit_crow_kosten())
    assert set(ws["ernst"]) == {"L", "M", "E"}


# ---------------------------------------------------------------------------
# Het scherm
# ---------------------------------------------------------------------------

def test_het_rode_vlak_is_een_kwart_dekkend():
    with io.open("templates/portaal.html", encoding="utf-8") as f:
        html = f.read()
    assert 'id="schouwSchadeLaag"' in html
    assert 'fill="#FF0000" fill-opacity="0.25"' in html
    assert "function schouwToonInBeeld(" in html
