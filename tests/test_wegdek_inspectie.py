"""Tests voor het wegdek-inspectietype (CROW 146a/b) — B2."""
import kunstwerken_taxonomy as kt
from crow_146 import CROW_146A_SCHADEBEELDEN, CROW_146B_SCHADEBEELDEN
from tests.conftest import auth


def test_wegdek_types_geregistreerd():
    assert "wegdek_asfalt" in kt.KUNSTWERK_TYPES
    assert "wegdek_elementen" in kt.KUNSTWERK_TYPES


def test_wegvak_assettypes_normaliseren_naar_wegdek():
    # Geïmporteerde wegvak-assets (IMBOR) sluiten zo aan op het inspectietype.
    assert kt.normalize_type("wegvak_asfalt") == "wegdek_asfalt"
    assert kt.normalize_type("asfalt") == "wegdek_asfalt"
    assert kt.normalize_type("fietspad") == "wegdek_asfalt"
    assert kt.normalize_type("wegvak_elementen") == "wegdek_elementen"
    assert kt.normalize_type("klinkers") == "wegdek_elementen"
    assert kt.normalize_type("trottoir") == "wegdek_elementen"


def test_wegdek_elementen_met_crow146_gebreken():
    asf = kt.elementen_voor("wegdek_asfalt")
    assert asf and asf[0]["code"] == "WEGDEK.DEKLAAG"
    asf_gebreken = {g["code"] for g in kt.gebreken_voor("wegdek_asfalt", "WEGDEK.DEKLAAG")}
    assert {s["code"] for s in CROW_146A_SCHADEBEELDEN} <= asf_gebreken

    elem = kt.elementen_voor("wegdek_elementen")
    assert elem and elem[0]["code"] == "WEGDEK.ELEMENTENVERHARDING"
    elem_gebreken = {g["code"] for g in kt.gebreken_voor("wegdek_elementen", "WEGDEK.ELEMENTENVERHARDING")}
    assert {s["code"] for s in CROW_146B_SCHADEBEELDEN} <= elem_gebreken


def test_wegdek_vragen_bevatten_generieke():
    gen = {q["code"] for q in kt.GENERIEKE_VRAGEN}
    codes = {q["code"] for q in kt.vragen_voor("wegdek_asfalt", "WEGDEK.DEKLAAG")}
    assert gen <= codes


def test_types_endpoint_bevat_wegdek(client, admin_user):
    r = client.get("/api/kunstwerken-inspecties/taxonomy/types", headers=auth(admin_user))
    assert r.status_code == 200
    keys = {t["key"] for t in r.json()["types"]}
    assert {"wegdek_asfalt", "wegdek_elementen"} <= keys
