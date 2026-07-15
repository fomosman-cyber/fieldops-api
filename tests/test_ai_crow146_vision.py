"""CROW 146-classificatie: echte Claude-vision-pad + heuristiek-fallback (I35).

Verifieert dat /api/ai/classify-crow146 de werkelijke foto-pixels door
`inspections.analyze_image` haalt zodra er een foto + ANTHROPIC_API_KEY is, en
anders deterministisch terugvalt op de CROW 146-heuristiek. Geen netwerk-calls:
de vision-functie wordt gemonkeypatcht.
"""

import base64

import inspections
from tests.conftest import auth


def _data_url() -> str:
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 256
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _fake_vision(**kwargs):
    """Doet alsof Claude vision een langsscheur M2 op de foto zag."""
    assert kwargs.get("image_bytes"), "vision moet de gedecodeerde pixels krijgen"
    return {
        "schade_aanwezig": True,
        "schade_type": "scheur",
        "crow_schadegroep": "samenhang",
        "crow_schadebeeld": "scheurvorming-langs",
        "crow_ernst": "M",
        "crow_omvang": "2",
        "crow_klasse": "M2",
        "nen_2767_conditie": 3,
        "confidence": 0.82,
        "asset_zichtbaar": True,
        "gw_maatregel": "Vullen polymeer",
        "gw_term": "Vullen polymeer (cold-pour)",
        "onderhoud_categorie": "KO",
        "aanbevolen_actie": "Repareer binnen 4-6 weken; monitor uitbreiding",
        "bevindingen": ["Langsscheur ~2m in rechter rijspoor"],
        "_model_id": "claude-sonnet-4-6",
    }


# ── decode-helper ────────────────────────────────────────────────────────────

def test_decode_data_url_ok():
    raw, media = inspections.decode_data_url(_data_url())
    assert media == "image/png"
    assert raw.startswith(b"\x89PNG")


def test_decode_data_url_rejects_garbage():
    for bad in ("", "niet-een-data-url", "data:image/png;base64,"):
        try:
            inspections.decode_data_url(bad)
            assert False, f"verwachtte ValueError voor {bad!r}"
        except ValueError:
            pass


# ── endpoint: heuristiek-fallback (geen API-key in tests) ──────────────────────

def test_crow146_heuristic_without_key(client, admin_user):
    """Zonder ANTHROPIC_API_KEY (default in tests) blijft het de heuristiek."""
    r = client.post("/api/ai/classify-crow146",
                    json={"asset_type": "wegvak", "photo_data_url": _data_url()},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["method"] == "heuristic-crow146"
    assert res["detection_method"] in ("auto", "explicit")
    assert len(res["suggestions"]) > 0


# ── endpoint: echte vision-pad (gemonkeypatcht) ────────────────────────────────

def test_crow146_uses_vision_when_key_and_photo(client, admin_user, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("inspections.analyze_image", _fake_vision)

    r = client.post("/api/ai/classify-crow146",
                    json={"asset_type": "wegvak", "photo_data_url": _data_url()},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["method"] == "claude-vision-crow146"
    assert res["detection_method"] == "vision"
    assert res["model_id"] == "claude-sonnet-4-6"
    assert res["nen_2767_conditie"] == 3
    assert res["bevindingen"]
    top = res["suggestions"][0]
    assert top["code"] == "scheurvorming-langs"
    assert top["klasse"] == "M2"
    assert top["confidence_pct"] == 82
    assert top["maatregel"] == "Vullen polymeer"


def test_crow146_no_photo_stays_heuristic_even_with_key(client, admin_user, monkeypatch):
    """Geen foto → geen vision-call, ook al staat de key gezet."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("inspections.analyze_image",
                        lambda **k: (_ for _ in ()).throw(AssertionError("niet aanroepen zonder foto")))
    r = client.post("/api/ai/classify-crow146",
                    json={"asset_type": "wegvak"},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["method"] == "heuristic-crow146"


def test_crow146_falls_back_on_vision_error(client, admin_user, monkeypatch):
    """Een vision-fout mag de inspecteur niet blokkeren → terugval op heuristiek."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _boom(**kwargs):
        raise RuntimeError("Anthropic API down")

    monkeypatch.setattr("inspections.analyze_image", _boom)
    r = client.post("/api/ai/classify-crow146",
                    json={"asset_type": "wegvak", "photo_data_url": _data_url()},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["method"] == "heuristic-crow146"
    assert len(res["suggestions"]) > 0
