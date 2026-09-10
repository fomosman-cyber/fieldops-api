"""Tests voor de opleverronde met AI-camera.

Waar dit over gaat: bij een oplevering loopt iemand het werk af en noteert wat
er nog niet klaar of niet goed is. Die restpuntenlijst is een contractstuk waar
geld aan hangt. De camera helpt bij het vinden; hij beslist niets.

Wat hier hard bewaakt wordt:

  1. **Niets gaat automatisch de lijst in.** Alles wat uit een frame komt is
     'voorgesteld' tot een mens het bevestigt. Een lijst die zichzelf afvinkt
     is geen oplevering, en bij een geschil is "de computer zei het" geen
     onderbouwing.
  2. **De privacy-poort is een poort.** Zonder expliciete bevestiging gaat er
     geen beeld naar een verwerker buiten de EU.
  3. **Herstel zonder foto bestaat niet**, en hersteld is niet hetzelfde als
     afgetekend.
  4. **Wat het model zag blijft bewaard**, ook nadat een mens het heeft
     bijgesteld.
"""

import base64

import oplever_vision as ov
from database import SessionLocal
from models import OpleveringPunt
from tests.conftest import auth

# Een geldige, piepkleine PNG als data-URL. Genoeg om de route te doorlopen;
# zonder ANTHROPIC_API_KEY komt er toch geen echte analyse aan te pas.
BEELD = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
         "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _oplevering(client, user, titel="Oplevering N213 fase 2"):
    r = client.post("/api/opleveringen/", json={"title": titel}, headers=auth(user))
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _ronde(client, user, opl_id, **kw):
    payload = {"privacy_bevestigd": True}
    payload.update(kw)
    r = client.post(f"/api/opleveringen/{opl_id}/rondes", json=payload, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


def _punt(client, user, ronde_id, **kw):
    payload = {"omschrijving": "Twee tegels liggen los", "ernst": "matig",
               "plek": "trottoir voor de inrit"}
    payload.update(kw)
    r = client.post(f"/api/opleveringen/rondes/{ronde_id}/punt",
                    json=payload, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


def _maak_voorstel(org_id, opl_id, ronde_id, **kw):
    """Een AI-voorstel rechtstreeks in de database.

    De vision-aanroep zelf draait in de tests niet (geen sleutel), dus we zetten
    het resultaat neer zoals de router het zou opslaan.
    """
    db = SessionLocal()
    try:
        p = OpleveringPunt(
            oplevering_id=opl_id, organization_id=org_id, ronde_id=ronde_id,
            code=kw.pop("code", "RP-001"),
            omschrijving=kw.pop("omschrijving", "twee tegels liggen los en wiebelen"),
            restpunt_klasse=kw.pop("restpunt_klasse", "verharding_los"),
            ernst=kw.pop("ernst", "matig"),
            plek=kw.pop("plek", "trottoir voor de inrit"),
            zekerheid=kw.pop("zekerheid", 0.83),
            bron="ai", status="voorgesteld",
            model_id="claude-sonnet-5", vision_versie=ov.OPLEVER_VISION_VERSIE,
            photo_url=BEELD, order_index=1, **kw)
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# De rekenregels van het domein
# ─────────────────────────────────────────────────────────────────────────────

def test_klassen_zijn_bruikbaar():
    codes = set()
    for k in ov.klassen():
        assert k["code"] not in codes, f"dubbele code {k['code']}"
        codes.add(k["code"])
        assert k["naam"] and k["waar"], f"{k['code']} is niet ingevuld"
    # De dingen waar een opleverronde in de GWW echt over gaat.
    for verwacht in ("verharding_los", "voeg_open", "markering_ontbreekt",
                     "opruimen", "beschadiging"):
        assert verwacht in codes


def test_lage_zekerheid_moet_nagekeken():
    assert ov.moet_nagekeken(0.30) is True
    assert ov.moet_nagekeken(None) is True
    assert ov.moet_nagekeken(0.95) is False


def test_verzonnen_klasse_haalt_de_lijst_niet():
    """Een model dat een klasse verzint mag de lijst niet vervuilen."""
    uit = ov._schoon({"bruikbaar": True, "punten": [
        {"klasse": "bestaat_niet", "omschrijving": "iets", "zekerheid": 0.9},
        {"klasse": "opruimen", "omschrijving": "zand op de weg", "zekerheid": 0.8},
    ]})
    assert len(uit["punten"]) == 1
    assert uit["punten"][0]["klasse"] == "opruimen"


def test_punt_zonder_omschrijving_valt_af():
    uit = ov._schoon({"bruikbaar": True, "punten": [
        {"klasse": "opruimen", "omschrijving": "  ", "zekerheid": 0.9}]})
    assert uit["punten"] == []


def test_onbekende_ernst_wordt_de_lichtste():
    """Te zwaar inschatten kost de aannemer geld dat hij misschien niet hoort
    te betalen; bij twijfel dus de lichtste."""
    uit = ov._schoon({"bruikbaar": True, "punten": [
        {"klasse": "opruimen", "omschrijving": "zand", "ernst": "catastrofaal",
         "zekerheid": 0.8}]})
    assert uit["punten"][0]["ernst"] == "licht"


def test_zonder_sleutel_geen_schone_lijst():
    """Niets bekeken is niet hetzelfde als niets gevonden.

    Dat verschil hoort zichtbaar te zijn voordat iemand een oplevering tekent.
    """
    uit = ov.analyseer_frame(image_bytes=b"x", privacy_gecontroleerd=True)
    assert uit["bruikbaar"] is False
    assert uit["reden_onbruikbaar"]
    assert uit["punten"] == []


def test_privacy_is_een_poort():
    import pytest
    with pytest.raises(ov.NietGecontroleerd):
        ov.analyseer_frame(image_bytes=b"x", privacy_gecontroleerd=False)


# ─────────────────────────────────────────────────────────────────────────────
# De ronde
# ─────────────────────────────────────────────────────────────────────────────

def test_ronde_starten_en_nummeren(client, admin_user):
    opl = _oplevering(client, admin_user)
    eerste = _ronde(client, admin_user, opl)
    assert eerste["nummer"] == 1
    assert eerste["soort"] == "eerste"
    assert eerste["status"] == "bezig"

    tweede = _ronde(client, admin_user, opl, soort="herkeuring")
    assert tweede["nummer"] == 2
    assert tweede["soort"] == "herkeuring"


def test_frame_zonder_privacybevestiging_wordt_geweigerd(client, admin_user):
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl, privacy_bevestigd=False)
    resp = client.post(f"/api/opleveringen/rondes/{r['id']}/frame",
                       json={"image_data_url": BEELD}, headers=auth(admin_user))
    assert resp.status_code == 400
    assert "privacy" in resp.json()["detail"].lower()


def test_onbruikbaar_frame_wordt_apart_geteld(client, admin_user):
    """Zonder AI-sleutel is elk frame onbruikbaar -- en dat hoort te tellen."""
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    resp = client.post(f"/api/opleveringen/rondes/{r['id']}/frame",
                       json={"image_data_url": BEELD}, headers=auth(admin_user))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bruikbaar"] is False
    assert body["gevonden"] == []
    assert body["ronde"]["frames"] == 1
    assert body["ronde"]["frames_onbruikbaar"] == 1


def test_rommel_in_plaats_van_een_afbeelding(client, admin_user):
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    resp = client.post(f"/api/opleveringen/rondes/{r['id']}/frame",
                       json={"image_data_url": "dit is geen plaatje maar wel lang genoeg"},
                       headers=auth(admin_user))
    assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Voorstellen: niets gaat automatisch de lijst in
# ─────────────────────────────────────────────────────────────────────────────

def test_voorstel_staat_niet_in_de_restpuntenlijst(client, admin_user):
    """De kern van de module."""
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    _maak_voorstel(admin_user.organization_id, opl, r["id"])

    body = client.get(f"/api/opleveringen/{opl}/restpunten",
                      headers=auth(admin_user)).json()
    assert body["restpunten"] == []
    assert len(body["voorstellen"]) == 1
    assert body["tellingen"]["nog_te_bevestigen"] == 1
    assert body["tellingen"]["totaal"] == 0


def test_bevestigen_zet_het_in_de_lijst_en_bewaart_wat_de_camera_zag(client, admin_user):
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    pid = _maak_voorstel(admin_user.organization_id, opl, r["id"], zekerheid=0.83)

    resp = client.patch(f"/api/opleveringen/punten/{pid}/bevestigen",
                        json={"besluit": "bevestigen", "ernst": "zwaar",
                              "omschrijving": "drie tegels los, struikelgevaar"},
                        headers=auth(admin_user))
    assert resp.status_code == 200, resp.text
    p = resp.json()
    assert p["status"] == "restpunt"
    assert p["ernst"] == "zwaar"
    assert p["omschrijving"] == "drie tegels los, struikelgevaar"
    # Wat het model dacht blijft staan, ook nu een mens het heeft bijgesteld.
    assert p["zekerheid"] == 0.83
    assert p["bron"] == "ai"

    body = client.get(f"/api/opleveringen/{opl}/restpunten",
                      headers=auth(admin_user)).json()
    assert body["tellingen"]["totaal"] == 1
    assert body["tellingen"]["nog_te_bevestigen"] == 0
    assert body["tellingen"]["per_ernst_open"]["zwaar"] == 1


def test_verwerpen_gooit_het_voorstel_weg(client, admin_user):
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    pid = _maak_voorstel(admin_user.organization_id, opl, r["id"])

    resp = client.patch(f"/api/opleveringen/punten/{pid}/bevestigen",
                        json={"besluit": "verwerpen"}, headers=auth(admin_user))
    assert resp.status_code == 200
    assert resp.json()["verworpen"] is True

    body = client.get(f"/api/opleveringen/{opl}/restpunten",
                      headers=auth(admin_user)).json()
    assert body["voorstellen"] == []
    assert body["restpunten"] == []


def test_een_punt_kan_niet_twee_keer_bevestigd(client, admin_user):
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    pid = _maak_voorstel(admin_user.organization_id, opl, r["id"])
    client.patch(f"/api/opleveringen/punten/{pid}/bevestigen",
                 json={"besluit": "bevestigen"}, headers=auth(admin_user))
    tweede = client.patch(f"/api/opleveringen/punten/{pid}/bevestigen",
                          json={"besluit": "bevestigen"}, headers=auth(admin_user))
    assert tweede.status_code == 409


def test_ronde_afronden_kan_niet_met_open_voorstellen(client, admin_user):
    """Anders verdwijnt wat de camera zag in het niets."""
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    _maak_voorstel(admin_user.organization_id, opl, r["id"])

    resp = client.post(f"/api/opleveringen/rondes/{r['id']}/afronden",
                       headers=auth(admin_user))
    assert resp.status_code == 400
    assert "voorstellen" in resp.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# Punt voor punt blijft kunnen
# ─────────────────────────────────────────────────────────────────────────────

def test_handmatig_punt_komt_direct_in_de_lijst(client, admin_user):
    """Een mens heeft het al gezien; dat hoeft niet nog eens bevestigd."""
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    p = _punt(client, admin_user, r["id"], restpunt_klasse="voeg_open")
    assert p["status"] == "restpunt"
    assert p["bron"] == "handmatig"
    assert p["moet_nagekeken"] is False
    assert p["restpunt_klasse_naam"]


def test_onbekende_klasse_bij_handmatig_punt(client, admin_user):
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    resp = client.post(f"/api/opleveringen/rondes/{r['id']}/punt",
                       json={"omschrijving": "iets", "restpunt_klasse": "onzin"},
                       headers=auth(admin_user))
    assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Bewijslast dat het hersteld is
# ─────────────────────────────────────────────────────────────────────────────

def test_herstel_vraagt_een_foto(client, admin_user):
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    p = _punt(client, admin_user, r["id"])

    zonder = client.post(f"/api/opleveringen/punten/{p['id']}/herstel",
                         json={"toelichting": "gemaakt"}, headers=auth(admin_user))
    assert zonder.status_code == 422, "foto hoort verplicht te zijn"

    met = client.post(f"/api/opleveringen/punten/{p['id']}/herstel",
                      json={"photo_url_after": BEELD, "toelichting": "opnieuw gestraat"},
                      headers=auth(admin_user))
    assert met.status_code == 200, met.text
    assert met.json()["status"] == "hersteld"
    assert met.json()["heeft_herstelfoto"] is True


def test_hersteld_is_niet_hetzelfde_als_afgetekend(client, admin_user):
    """Dicht is het pas als iemand het heeft nagekeken."""
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    p = _punt(client, admin_user, r["id"])
    client.post(f"/api/opleveringen/punten/{p['id']}/herstel",
                json={"photo_url_after": BEELD}, headers=auth(admin_user))

    body = client.get(f"/api/opleveringen/{opl}/restpunten?alleen_open=true",
                      headers=auth(admin_user)).json()
    assert body["tellingen"]["hersteld"] == 1
    assert body["tellingen"]["geverifieerd"] == 0
    assert body["tellingen"]["open"] == 1, "een gemeld herstel telt nog als open"


def test_afwijzen_vraagt_een_reden_en_zet_het_punt_terug(client, admin_user):
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    p = _punt(client, admin_user, r["id"])
    client.post(f"/api/opleveringen/punten/{p['id']}/herstel",
                json={"photo_url_after": BEELD}, headers=auth(admin_user))

    zonder = client.post(f"/api/opleveringen/punten/{p['id']}/verifieren",
                         json={"besluit": "afwijzen"}, headers=auth(admin_user))
    assert zonder.status_code == 400

    met = client.post(f"/api/opleveringen/punten/{p['id']}/verifieren",
                      json={"besluit": "afwijzen", "reden": "voeg nog steeds open"},
                      headers=auth(admin_user))
    assert met.status_code == 200
    terug = met.json()
    assert terug["status"] == "restpunt"
    assert terug["afgewezen_reden"] == "voeg nog steeds open"
    assert terug["hersteld_op"] is None


def test_akkoord_tekent_het_punt_af(client, admin_user):
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    p = _punt(client, admin_user, r["id"])
    client.post(f"/api/opleveringen/punten/{p['id']}/herstel",
                json={"photo_url_after": BEELD}, headers=auth(admin_user))
    resp = client.post(f"/api/opleveringen/punten/{p['id']}/verifieren",
                       json={"besluit": "akkoord"}, headers=auth(admin_user))
    assert resp.status_code == 200
    assert resp.json()["status"] == "geverifieerd"
    assert resp.json()["geverifieerd_op"]


def test_voorstel_kan_niet_hersteld_worden(client, admin_user):
    """Eerst bevestigen dat het een punt is, dan pas herstellen."""
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    pid = _maak_voorstel(admin_user.organization_id, opl, r["id"])
    resp = client.post(f"/api/opleveringen/punten/{pid}/herstel",
                       json={"photo_url_after": BEELD}, headers=auth(admin_user))
    assert resp.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# Geen blobs in lijsten, en afscherming
# ─────────────────────────────────────────────────────────────────────────────

def test_restpuntenlijst_stuurt_geen_fotos_mee(client, admin_user):
    """Dertig punten met een voor- en nafoto is zestig blobs."""
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    groot = "data:image/png;base64," + base64.b64encode(b"x" * 4000).decode()
    p = _punt(client, admin_user, r["id"], photo_url=groot)

    lijst = client.get(f"/api/opleveringen/{opl}/restpunten", headers=auth(admin_user))
    assert "eHh4" not in lijst.text, "de foto zit in de lijst"
    rij = lijst.json()["restpunten"][0]
    assert rij["heeft_foto"] is True
    assert "photo_url" not in rij

    los = client.get(f"/api/opleveringen/punten/{p['id']}/fotos",
                     headers=auth(admin_user)).json()
    assert los["photo_url"]


def test_ronde_van_andere_organisatie_is_onzichtbaar(client, admin_user, platform_owner):
    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    assert client.get(f"/api/opleveringen/rondes/{r['id']}",
                      headers=auth(platform_owner)).status_code == 404


def test_zonder_login_geen_toegang(client):
    assert client.get("/api/opleveringen/restpunt-klassen").status_code in (401, 403)


def test_audit_legt_de_keten_vast(client, admin_user):
    from models import AuditLog

    opl = _oplevering(client, admin_user)
    r = _ronde(client, admin_user, opl)
    p = _punt(client, admin_user, r["id"])
    client.post(f"/api/opleveringen/punten/{p['id']}/herstel",
                json={"photo_url_after": BEELD}, headers=auth(admin_user))
    client.post(f"/api/opleveringen/punten/{p['id']}/verifieren",
                json={"besluit": "akkoord"}, headers=auth(admin_user))
    client.post(f"/api/opleveringen/rondes/{r['id']}/afronden", headers=auth(admin_user))

    db = SessionLocal()
    try:
        acties = {a.action for a in db.query(AuditLog).all()}
    finally:
        db.close()
    assert {"oplevering.ronde.start", "oplevering.restpunt.handmatig",
            "oplevering.restpunt.hersteld", "oplevering.herstel.beoordeeld",
            "oplevering.ronde.afgerond"} <= acties
