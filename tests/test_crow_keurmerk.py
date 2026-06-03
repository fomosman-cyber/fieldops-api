"""Integriteit van de CROW-keurmerk conformiteits-catalogus.

Borgt dat het dossier EERLIJK blijft: elk geciteerd bewijs (module + test) moet
echt in de repo bestaan, en het /norm-conformance endpoint moet de catalogus
correct teruggeven. Zo kan een auditor elke claim verifiëren.
"""
import os

import crow_keurmerk
from tests.conftest import auth

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_catalogus_structuur():
    assert crow_keurmerk.CONFORMANCE
    geldige_status = {crow_keurmerk.STATUS_VOLLEDIG, crow_keurmerk.STATUS_GEDEELTELIJK}
    for c in crow_keurmerk.CONFORMANCE:
        for key in ("domein", "norm", "criterium", "status", "modules", "endpoints", "tests"):
            assert c.get(key), f"{c.get('domein')} mist veld {key}"
        assert c["status"] in geldige_status
        assert isinstance(c["modules"], list) and c["modules"]
        assert isinstance(c["tests"], list) and c["tests"]


def test_bewijs_bestaat_echt():
    """Elke geciteerde module + test moet daadwerkelijk in de repo staan."""
    ontbreekt = []
    for c in crow_keurmerk.CONFORMANCE:
        for path in c["modules"] + c["tests"]:
            if not os.path.exists(os.path.join(_ROOT, path)):
                ontbreekt.append(f"{c['domein']}: {path}")
    assert not ontbreekt, "Niet-bestaand bewijs geciteerd: " + "; ".join(ontbreekt)


def test_summary_telt_klopt():
    s = crow_keurmerk.summary()
    assert s["criteria_totaal"] == len(crow_keurmerk.CONFORMANCE)
    assert s["volledig"] + s["gedeeltelijk"] == s["criteria_totaal"]
    assert 0 <= s["dekkingsgraad_pct"] <= 100


def test_endpoint_requires_auth(client):
    assert client.get("/api/compliance/norm-conformance").status_code in (401, 403)


def test_endpoint_returns_dossier(client, admin_user):
    r = client.get("/api/compliance/norm-conformance", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["criteria"] and body["normen"]
    assert body["samenvatting"]["criteria_totaal"] == len(crow_keurmerk.CONFORMANCE)
    assert "generated_at" in body
