"""Tests voor CAR2023 beeldkwaliteit — wegmeubilair-schouw (F3)."""
import car2023
from tests.conftest import auth


def test_overall_klasse_worst_aspect():
    assert car2023.overall_klasse({"heelheid": "A", "reinheid": "C"}) == "C"
    assert car2023.overall_klasse({"heelheid": "A+", "reinheid": "A"}) == "A"
    assert car2023.overall_klasse({}) is None
    # Onbekende aspect-codes/klassen worden genegeerd.
    assert car2023.overall_klasse({"onbekend": "A", "heelheid": "B"}) == "B"
    assert car2023.overall_klasse({"heelheid": "X"}) is None


def test_voldoet_aan_ambitie():
    assert car2023.voldoet_aan_ambitie("A", "B") is True
    assert car2023.voldoet_aan_ambitie("B", "B") is True
    assert car2023.voldoet_aan_ambitie("C", "B") is False
    assert car2023.voldoet_aan_ambitie(None, "B") is False


def test_aspecten_voor_type():
    codes = {a["code"] for a in car2023.aspecten_voor("afvalbak")}
    assert codes == {"heelheid", "reinheid", "stabiliteit"}
    assert car2023.aspecten_voor("onbekend") == []


def test_beoordeel_meldt_ontbrekende_aspecten():
    r = car2023.beoordeel("verkeersbord", {"heelheid": "A", "reinheid": "B"}, "B")
    assert r["overall_klasse"] == "B"
    assert r["voldoet"] is True
    assert set(r["ontbrekende_aspecten"]) == {"stabiliteit", "functie"}


def test_catalogus_endpoint(client, admin_user):
    r = client.get("/api/car2023/catalogus", headers=auth(admin_user))
    assert r.status_code == 200
    body = r.json()
    assert [k["klasse"] for k in body["klassen"]] == ["A+", "A", "B", "C", "D"]
    keys = {t["key"] for t in body["types"]}
    assert {"verkeersbord", "lichtmast", "afvalbak"} <= keys


def test_score_endpoint(client, admin_user):
    r = client.post(
        "/api/car2023/score",
        json={"objecttype": "zitbank",
              "aspect_klassen": {"heelheid": "A", "reinheid": "C"}, "ambitie": "B"},
        headers=auth(admin_user),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["overall_klasse"] == "C"
    assert body["voldoet"] is False


def test_score_endpoint_ongeldige_ambitie(client, admin_user):
    r = client.post("/api/car2023/score",
                    json={"aspect_klassen": {}, "ambitie": "Z"}, headers=auth(admin_user))
    assert r.status_code == 400


def test_endpoints_vereisen_auth(client):
    assert client.get("/api/car2023/catalogus").status_code in (401, 403)
