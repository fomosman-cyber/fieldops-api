"""EN1176 + NEN3140 classify-endpoints: echte Claude-vision-pad + heuristiek-fallback.

Vervolg op de CROW-146-vision-fix (#101): ook de speeltoestellen- (NEN-EN 1176)
en elektra- (NEN 3140) knoppen kijken nu naar de werkelijke foto-pixels zodra er
een foto + ANTHROPIC_API_KEY is. Geen netwerk: analyze_photo_json wordt
gemonkeypatcht.
"""

import base64

from tests.conftest import auth

_DATA_URL = "data:image/png;base64," + base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 256).decode("ascii")


def _fake_en1176(**kwargs):
    assert kwargs.get("image_bytes"), "vision moet de pixels krijgen"
    return {
        "asset_zichtbaar": True,
        "categorie": "C",
        "faaltypes": [
            {"naam": "Versleten kettingschalm schommel", "ernst": "hoog",
             "maatregel": "Vervang ketting", "confidence_pct": 88},
        ],
        "bevindingen": ["Roest op bevestigingspunt"],
        "_model_id": "claude-sonnet-4-6",
    }


def _fake_nen3140(**kwargs):
    assert kwargs.get("image_bytes"), "vision moet de pixels krijgen"
    return {
        "asset_zichtbaar": True,
        "defects": [
            {"naam": "Loshangende kabel onderin mast", "ernst": "kritiek",
             "maatregel": "Direct afzetten", "confidence_pct": 91},
        ],
        "bevindingen": ["Kastdeurtje staat open"],
        "_model_id": "claude-sonnet-4-6",
    }


# ─── NEN-EN 1176 ───────────────────────────────────────────────────────────────

def test_en1176_heuristic_without_key(client, admin_user):
    r = client.post("/api/ai/classify-en1176",
                    json={"asset_type": "speeltoestel", "photo_data_url": _DATA_URL},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["method"] == "heuristic-nen-en-1176"


def test_en1176_uses_vision_when_key_and_photo(client, admin_user, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("inspections.analyze_photo_json", _fake_en1176)
    r = client.post("/api/ai/classify-en1176",
                    json={"asset_type": "schommel", "photo_data_url": _DATA_URL},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["method"] == "claude-vision-nen-en-1176"
    assert res["detection_method"] == "vision"
    assert res["categorie"] == "C"
    assert res["bevindingen"]
    top = res["suggestions"][0]
    assert top["naam"] == "Versleten kettingschalm schommel"
    assert top["klasse_advies"] == "hoog"
    assert top["confidence_pct"] == 88


# ─── NEN 3140 ──────────────────────────────────────────────────────────────────

def test_nen3140_heuristic_without_key(client, admin_user):
    r = client.post("/api/ai/classify-nen3140",
                    json={"asset_type": "lichtmast", "photo_data_url": _DATA_URL},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["method"] == "grens-check + heuristic-nen-3140"
    assert "grens_breaches" in body


def test_nen3140_uses_vision_when_key_and_photo(client, admin_user, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("inspections.analyze_photo_json", _fake_nen3140)
    r = client.post("/api/ai/classify-nen3140",
                    json={"asset_type": "lichtmast", "photo_data_url": _DATA_URL},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["method"] == "grens-check + claude-vision-nen-3140"
    assert res["detection_method"] == "vision"
    assert "grens_breaches" in res          # meetwaarde-check blijft los bestaan
    assert res["bevindingen"]
    top = res["suggestions"][0]
    assert top["naam"] == "Loshangende kabel onderin mast"
    assert top["klasse_advies"] == "kritiek"


def test_nen3140_no_photo_stays_heuristic_with_key(client, admin_user, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("inspections.analyze_photo_json",
                        lambda **k: (_ for _ in ()).throw(AssertionError("niet aanroepen zonder foto")))
    r = client.post("/api/ai/classify-nen3140",
                    json={"asset_type": "lichtmast"},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["method"] == "grens-check + heuristic-nen-3140"
