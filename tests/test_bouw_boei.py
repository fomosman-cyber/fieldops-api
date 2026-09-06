"""BOEI-gebouwinspectie: vragenlijst, opname per gebouw én per straat, scores.

De twee dingen die hier echt bewaakt worden:

1. **Een opname hangt aan precies één object.** Een gebouw of een straat, nooit
   allebei en nooit geen van beide. Zonder die regel krijg je opnames waarvan
   niemand weet waar ze bij horen, en dubbeltellingen in de rapportage.
2. **De conditiescore komt uit de NEN 2767-rekenkern**, niet uit een cijfer dat
   iemand zelf kiest. Dat is het hele punt van conditiemeting.
"""

from database import SessionLocal
from models import Asset, BouwInspectie

import bouw_boei as bb

from .conftest import auth


def _asset(org_id, user_id, code="GEB-001", naam="Gemeentehuis"):
    db = SessionLocal()
    try:
        a = Asset(code=code, name=naam, asset_type="gebouw",
                  organization_id=org_id, created_by=user_id)
        db.add(a)
        db.commit()
        return a.id
    finally:
        db.close()


def _start(client, admin_user, **payload):
    return client.post("/api/bouw/", json=payload, headers=auth(admin_user))


# ---------------------------------------------------------------------------
# De vragenlijst zelf
# ---------------------------------------------------------------------------

def test_checklist_bevat_alle_vier_de_pijlers(client, admin_user):
    r = client.get("/api/bouw/checklist", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    d = r.json()
    assert [p["code"] for p in d["pijlers"]] == ["B", "O", "E", "I"]
    assert d["versie"] == bb.BOEI_VERSION
    assert len(d["vragen"]) > 20
    assert len(d["elementen"]) > 20


def test_elke_vraag_heeft_een_normverwijzing():
    """Zonder norm_ref is een bevinding niet te verdedigen tegenover een auditor."""
    zonder = [v["code"] for v in bb.VRAGEN if not v.get("norm_ref")]
    assert zonder == [], f"vragen zonder normverwijzing: {zonder}"


def test_ja_nee_vragen_zijn_allemaal_positief_geformuleerd():
    """Een lijst waarin de ene vraag omgekeerd werkt dan de andere levert
    fouten op bij iemand die met een tablet door een ketelhuis loopt."""
    afwijkend = [v["code"] for v in bb.VRAGEN
                 if v.get("type") == "ja_nee_nvt" and v.get("attention_when") is not False]
    assert afwijkend == []


def test_gebouwtype_filtert_niet_toepasselijke_vragen(client, admin_user):
    kantoor = client.get("/api/bouw/checklist?gebouw_type=kantoor",
                         headers=auth(admin_user)).json()["vragen"]
    werkplaats = client.get("/api/bouw/checklist?gebouw_type=werkplaats",
                            headers=auth(admin_user)).json()["vragen"]
    codes_kantoor = {v["code"] for v in kantoor}
    codes_werkplaats = {v["code"] for v in werkplaats}

    # De labelplicht geldt voor kantoren, niet voor een gemeentewerf.
    assert "E.02" in codes_kantoor
    assert "E.02" not in codes_werkplaats
    # De monumentenvraag hoort bij geen van beide.
    assert "I.10" not in codes_kantoor and "I.10" not in codes_werkplaats


def test_bewijsstukken_zijn_uniek_en_verwijzen_naar_een_vraag(client, admin_user):
    stukken = client.get("/api/bouw/checklist",
                         headers=auth(admin_user)).json()["bewijsstukken"]
    namen = [s["bewijs"] for s in stukken]
    assert len(namen) == len(set(namen))
    assert all(bb.vraag(s["code"]) is not None for s in stukken)


# ---------------------------------------------------------------------------
# Per gebouw of per straat -- precies één
# ---------------------------------------------------------------------------

def test_opname_per_gebouw(client, admin_user, org):
    asset_id = _asset(org.id, admin_user.id)
    r = _start(client, admin_user, asset_id=asset_id, gebouw_type="publiek")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["asset_id"] == asset_id
    assert d["straatnaam"] is None
    assert d["status"] == "concept"
    assert len(d["antwoorden"]) > 20


def test_opname_per_straat(client, admin_user):
    """Een rij portiekwoningen loop je per straat, niet per pand."""
    r = _start(client, admin_user, straatnaam="Prins Hendrikkade", plaats="Rotterdam")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["straatnaam"] == "Prins Hendrikkade"
    assert d["asset_id"] is None


def test_gebouw_en_straat_tegelijk_wordt_geweigerd(client, admin_user, org):
    r = _start(client, admin_user, asset_id=_asset(org.id, admin_user.id, code="GEB-002"),
               straatnaam="Coolsingel")
    assert r.status_code == 400
    assert "niet allebei" in r.text


def test_zonder_object_wordt_geweigerd(client, admin_user):
    r = _start(client, admin_user, gebouw_type="school")
    assert r.status_code == 400


def test_asset_van_andere_organisatie_is_onvindbaar(client, admin_user):
    """Tenant-isolatie: een asset-id uit een andere organisatie geeft 404,
    geen opname die stilletjes aan het verkeerde gebouw hangt."""
    r = _start(client, admin_user, asset_id="bestaat-niet-of-andere-org")
    assert r.status_code == 404


def test_alleen_gekozen_pijlers_worden_aangemaakt(client, admin_user):
    r = _start(client, admin_user, straatnaam="Westblaak", pijlers=["B"])
    d = r.json()
    assert {a["pijler"] for a in d["antwoorden"]} == {"B"}


# ---------------------------------------------------------------------------
# Conditiescore uit de NEN 2767-rekenkern
# ---------------------------------------------------------------------------

def test_conditie_wordt_berekend_niet_ingevoerd(client, admin_user, org):
    """Ernst, intensiteit en omvang gaan erin; de conditie komt eruit."""
    bouw_id = _start(client, admin_user, asset_id=_asset(org.id, admin_user.id, code="GEB-003"),
                     gebouw_type="school").json()["id"]

    r = client.post(f"/api/bouw/{bouw_id}/condities", headers=auth(admin_user), json={
        "element_code": "GEV.01",
        "gebrek": "voegwerk uitgesleten",
        "ernst": 2, "intensiteit": 2, "omvang_klasse": 3,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["element"] == "Metselwerk buitengevel"
    assert d["groep"] == "gevel"
    assert 1 <= d["conditie"] <= 6
    assert d["conditie_label"]


def test_onbekend_element_wordt_geweigerd(client, admin_user):
    bouw_id = _start(client, admin_user, straatnaam="Blaak").json()["id"]
    r = client.post(f"/api/bouw/{bouw_id}/condities", headers=auth(admin_user),
                    json={"element_code": "BESTAAT.NIET", "ernst": 1})
    assert r.status_code == 404


def test_zelfde_element_tweemaal_werkt_bij_in_plaats_van_dubbel(client, admin_user):
    bouw_id = _start(client, admin_user, straatnaam="Schiedamsedijk").json()["id"]
    for omvang in (1, 5):
        client.post(f"/api/bouw/{bouw_id}/condities", headers=auth(admin_user), json={
            "element_code": "DAK.01", "ernst": 3, "intensiteit": 3,
            "omvang_klasse": omvang,
        })
    condities = client.get(f"/api/bouw/{bouw_id}",
                           headers=auth(admin_user)).json()["condities"]
    assert len(condities) == 1
    assert condities[0]["omvang_klasse"] == 5


# ---------------------------------------------------------------------------
# Afronden
# ---------------------------------------------------------------------------

def _beantwoord_alles(client, admin_user, bouw_id, antwoord="ja"):
    d = client.get(f"/api/bouw/{bouw_id}", headers=auth(admin_user)).json()
    for a in d["antwoorden"]:
        client.patch(f"/api/bouw/{bouw_id}/antwoorden/{a['id']}",
                     headers=auth(admin_user), json={"antwoord": antwoord})


def test_afronden_vereist_dat_alles_beantwoord_is(client, admin_user):
    bouw_id = _start(client, admin_user, straatnaam="Wilhelminakade",
                     pijlers=["E"]).json()["id"]
    r = client.post(f"/api/bouw/{bouw_id}/afronden", headers=auth(admin_user))
    assert r.status_code == 400
    assert "onbeantwoord" in r.text


def test_afronden_zet_score_en_hoogste_conditie_vast(client, admin_user, org):
    bouw_id = _start(client, admin_user, asset_id=_asset(org.id, admin_user.id, code="GEB-004"),
                     gebouw_type="zorg", pijlers=["B"]).json()["id"]
    client.post(f"/api/bouw/{bouw_id}/condities", headers=auth(admin_user), json={
        "element_code": "DAK.01", "ernst": 3, "intensiteit": 3, "omvang_klasse": 5,
    })
    _beantwoord_alles(client, admin_user, bouw_id, "ja")

    r = client.post(f"/api/bouw/{bouw_id}/afronden", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "afgerond"
    assert d["score_pct"] == 100
    assert d["aantal_aandachtspunten"] == 0
    assert d["conditie_hoogste"] is not None


def test_nee_telt_als_aandachtspunt(client, admin_user):
    bouw_id = _start(client, admin_user, straatnaam="Boompjes",
                     pijlers=["E"]).json()["id"]
    _beantwoord_alles(client, admin_user, bouw_id, "nee")
    d = client.post(f"/api/bouw/{bouw_id}/afronden", headers=auth(admin_user)).json()
    assert d["score_pct"] == 0
    assert d["aantal_aandachtspunten"] > 0

    open_acties = client.get("/api/bouw/acties/open", headers=auth(admin_user)).json()
    assert any(a["bouw_inspectie_id"] == bouw_id for a in open_acties)


def test_nvt_verlaagt_de_score_niet(client, admin_user):
    """Een gebouw zonder lift hoort niet lager te scoren omdat de liftvraag
    niet van toepassing is."""
    bouw_id = _start(client, admin_user, straatnaam="Weena", pijlers=["I"]).json()["id"]
    d = client.get(f"/api/bouw/{bouw_id}", headers=auth(admin_user)).json()
    for i, a in enumerate(d["antwoorden"]):
        client.patch(f"/api/bouw/{bouw_id}/antwoorden/{a['id']}",
                     headers=auth(admin_user),
                     json={"antwoord": "nvt" if i % 2 else "ja"})
    uit = client.post(f"/api/bouw/{bouw_id}/afronden", headers=auth(admin_user)).json()
    assert uit["score_pct"] == 100


def test_afgeronde_opname_is_niet_meer_te_wijzigen(client, admin_user):
    bouw_id = _start(client, admin_user, straatnaam="Erasmusbrug",
                     pijlers=["E"]).json()["id"]
    _beantwoord_alles(client, admin_user, bouw_id, "ja")
    client.post(f"/api/bouw/{bouw_id}/afronden", headers=auth(admin_user))

    r = client.patch(f"/api/bouw/{bouw_id}", headers=auth(admin_user),
                     json={"algemene_indruk": "toch nog iets"})
    assert r.status_code == 409
    r = client.post(f"/api/bouw/{bouw_id}/condities", headers=auth(admin_user),
                    json={"element_code": "DAK.01", "ernst": 1})
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Rollen en isolatie
# ---------------------------------------------------------------------------

def test_viewer_mag_lezen_maar_niet_opnemen(client, viewer_user):
    assert client.get("/api/bouw/", headers=auth(viewer_user)).status_code == 200
    r = client.post("/api/bouw/", headers=auth(viewer_user),
                    json={"straatnaam": "Coolsingel"})
    assert r.status_code == 403


def test_opname_van_andere_organisatie_geeft_404(client, admin_user, org):
    """Rechtstreeks een id opvragen mag nooit langs de organisatiegrens."""
    db = SessionLocal()
    try:
        vreemde = BouwInspectie(organization_id="een-andere-org",
                                straatnaam="Elders", status="concept",
                                created_by=admin_user.id)
        db.add(vreemde)
        db.commit()
        vreemd_id = vreemde.id
    finally:
        db.close()

    assert client.get(f"/api/bouw/{vreemd_id}",
                      headers=auth(admin_user)).status_code == 404
