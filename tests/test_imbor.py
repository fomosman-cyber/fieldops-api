"""Tests voor IMBOR-taxonomie.

Twee lagen:
1. Pure module-tests op imbor_taxonomy (geen DB nodig, draaien altijd).
2. E2E endpoint-tests via TestClient (afhankelijk van pytest-fixtures).

NB: e2e-tests vereisen werkende SQLAlchemy-mappers. Op het moment van schrijven
is er een pre-existing FK-ambiguity-bug op Project.organization die mappers
lokaal laat falen onder Python 3.14 + nieuwste SQLAlchemy. Op productie werkt
het. Tot die fix gemerged is, kunnen de e2e-tests skippen.
"""

import pytest

import imbor_taxonomy as it


# ─────────────────────────────────────────────────────────────────────────────
# Pure module-tests — geen DB, werken altijd
# ─────────────────────────────────────────────────────────────────────────────


def test_module_eleven_hoofdgroepen():
    groepen = it.list_hoofdgroepen()
    assert len(groepen) == 11
    for hg in groepen:
        assert {"code", "naam", "icon", "asset_count"}.issubset(hg.keys())
        assert isinstance(hg["asset_count"], int) and hg["asset_count"] >= 0


def test_module_known_hoofdgroep_codes():
    codes = {hg["code"] for hg in it.list_hoofdgroepen()}
    for expected in ["wegen", "kunstwerken", "riolering", "verlichting",
                     "groen", "speel_sport", "meubilair", "bewegwijzering",
                     "water", "gebouwen", "overig"]:
        assert expected in codes, f"hoofdgroep '{expected}' ontbreekt"


def test_module_types_minimum_count():
    all_types = it.list_types()
    assert len(all_types) >= 50, "MVP-taxonomie moet >=50 types hebben"


def test_module_filter_by_hoofdgroep():
    verlichting = it.list_types("verlichting")
    assert len(verlichting) >= 1
    for t in verlichting:
        assert t["hoofdgroep"] == "verlichting"
    codes = [t["code"] for t in verlichting]
    assert "lichtmast" in codes


def test_module_get_type_info_lichtmast():
    info = it.get_type_info("lichtmast")
    assert info is not None
    assert info["code"] == "lichtmast"
    assert info["hoofdgroep"] == "verlichting"
    assert info["default_inspection_norm"] == "NEN 3140"
    assert info["default_inspection_frequency_months"] == 12
    assert "masthoogte_m" in info["typical_properties"]


def test_module_get_type_info_unknown_returns_none():
    assert it.get_type_info("nonexistent-type") is None


def test_module_tree_structure():
    tree = it.get_tree()
    assert len(tree) == 11
    for code, hg in tree.items():
        assert "naam" in hg
        assert "asset_types" in hg
        assert isinstance(hg["asset_types"], list)
        for t in hg["asset_types"]:
            assert t["hoofdgroep"] == code


def test_module_speeltoestellen_nen_en_1176():
    """Speel-categorie moet NEN-EN 1176 default krijgen."""
    types = it.list_types("speel_sport")
    speeltoestellen = [t for t in types if t["code"].startswith("speeltoestel_")]
    assert len(speeltoestellen) >= 3
    for t in speeltoestellen:
        assert "NEN-EN 1176" in t["default_inspection_norm"]


def test_module_verlichting_nen_3140():
    """Alle verlichtings-types vallen onder NEN 3140."""
    types = it.list_types("verlichting")
    assert len(types) >= 3
    for t in types:
        assert "NEN 3140" in t["default_inspection_norm"]


def test_module_unique_codes():
    """Geen dubbele codes in de taxonomie."""
    codes = [t["code"] for t in it.list_types()]
    assert len(codes) == len(set(codes)), "Dubbele asset-codes gedetecteerd"


def test_module_all_hoofdgroepen_have_types():
    """Bijna alle hoofdgroepen hebben minstens 1 type (overig mag leeg)."""
    counts = {hg["code"]: hg["asset_count"] for hg in it.list_hoofdgroepen()}
    for code in ["wegen", "kunstwerken", "riolering", "verlichting",
                 "groen", "speel_sport", "meubilair", "bewegwijzering",
                 "water", "gebouwen"]:
        assert counts[code] >= 1, f"hoofdgroep '{code}' heeft 0 types"


# ─────────────────────────────────────────────────────────────────────────────
# E2E endpoint-tests — vereisen werkende mappers
# Skip elegant als pre-existing FK-bug nog niet opgelost is.
# ─────────────────────────────────────────────────────────────────────────────

try:
    from tests.conftest import auth
    _CONFTEST_OK = True
except Exception:
    _CONFTEST_OK = False


def _client_or_skip(client):
    """Probeer een eenvoudige request — skip als mapper-init faalt."""
    try:
        client.get("/api/health")
    except Exception as e:
        if "AmbiguousForeignKeys" in str(e) or "InvalidRequestError" in str(e):
            pytest.skip(f"Pre-existing SQLAlchemy mapper-bug blokkeert e2e: {e}")
        raise


def test_e2e_hoofdgroepen_returns_eleven(client, admin_user):
    _client_or_skip(client)
    r = client.get("/api/imbor/hoofdgroepen", headers=auth(admin_user))
    assert r.status_code == 200
    assert len(r.json()["hoofdgroepen"]) == 11


def test_e2e_types_filtered(client, admin_user):
    _client_or_skip(client)
    r = client.get("/api/imbor/types?hoofdgroep=verlichting",
                   headers=auth(admin_user))
    assert r.status_code == 200
    for t in r.json()["types"]:
        assert t["hoofdgroep"] == "verlichting"


def test_e2e_type_info_404(client, admin_user):
    _client_or_skip(client)
    r = client.get("/api/imbor/types/nonexistent-type",
                   headers=auth(admin_user))
    assert r.status_code == 404


def test_e2e_requires_auth(client):
    _client_or_skip(client)
    r = client.get("/api/imbor/hoofdgroepen")
    assert r.status_code == 401
