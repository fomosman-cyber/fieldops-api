"""Live schouw: camera aan, beeld voor beeld, waarnemingen die binnenlopen.

Vier regels die hier vastliggen:

1. **Doorlopend opnemen vanuit een voertuig kan niet.** Dat vraagt automatisch
   blurren van gezichten en kentekens en dat is niet gebouwd. Een duidelijke
   weigering is beter dan straatbeelden met omstanders naar een verwerker
   buiten de EU.
2. **De AI kijkt, jij beslist.** Onzekere waarnemingen tellen pas mee na een
   bevestiging.
3. **Afwijzen verwijdert niets.** Het spoor van een gewijzigde score moet
   navolgbaar blijven.
4. **Een ontbrekende drager is te herstellen.** Vult iemand hem alsnog in, dan
   wordt de meetlat opnieuw bepaald -- daar is de beoordeellijst voor.
"""

import pytest

import crow_schouw as cs
import schouw_vision as sv

from .conftest import auth

# Een geldige, minimale data-URL. De vision-aanroep wordt gestubd, dus de
# inhoud doet er niet toe -- alleen dat hij te decoderen valt.
BEELD = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="


@pytest.fixture
def stub_vision(monkeypatch):
    """Vision vervangen door een vast antwoord, zodat de test niets aanroept."""
    def zet(gebied=None, bruikbaar=True, reden=None):
        def nep(**kwargs):
            if not kwargs.get("privacy_gecontroleerd"):
                raise sv.NietGeblurd("niet geblurd")
            return {"bruikbaar": bruikbaar, "reden_onbruikbaar": reden,
                    "gebied": gebied or [], "objecten": [],
                    "_versie": sv.SCHOUW_VISION_VERSION, "_model_id": "test-model"}
        monkeypatch.setattr(sv, "analyseer_frame", nep)
    return zet


def _rit(client, user, **body):
    body.setdefault("gebied", "Coolsingel")
    body.setdefault("gebiedstype", "centrum")
    return client.post("/api/schouw/ritten", json=body, headers=auth(user))


def _zeker(klasse="afval_los", drager="elementenverharding", waarde=3.0,
           zekerheid=0.95, **extra):
    return dict({
        "klasse": klasse, "drager": drager,
        "meetlat": cs.meetlat_voor(klasse, drager),
        "waarde": waarde, "klasse_niveau": None, "zekerheid": zekerheid,
        "toelichting": None,
        "beoordeling_nodig": zekerheid < sv.DREMPEL_AUTOMATISCH,
    }, **extra)


# ---------------------------------------------------------------------------
# De privacy-poort
# ---------------------------------------------------------------------------

def test_rijdend_schouwen_wordt_geweigerd(client, admin_user):
    """Zolang er niet geblurd wordt, gaat deze modus er niet in."""
    r = _rit(client, admin_user, privacy_modus="rijdend")
    assert r.status_code == 400
    assert "blurren" in r.text


def test_gericht_schouwen_mag(client, admin_user):
    r = _rit(client, admin_user, privacy_modus="gericht")
    assert r.status_code == 200, r.text
    assert r.json()["privacy_modus"] == "gericht"


def test_catalogus_meldt_welke_modi_bestaan(client, admin_user):
    d = client.get("/api/schouw/catalogus", headers=auth(admin_user)).json()
    assert d["privacy_modi"] == ["gericht"]
    assert d["zekerheidsdrempel"] == sv.DREMPEL_AUTOMATISCH
    assert len(d["detectieklassen"]) >= 10


# ---------------------------------------------------------------------------
# Een rit starten
# ---------------------------------------------------------------------------

def test_gebiedstype_bepaalt_de_ambitie(client, admin_user):
    centrum = _rit(client, admin_user, gebiedstype="centrum").json()
    terrein = _rit(client, admin_user, gebiedstype="bedrijventerrein").json()
    assert centrum["ambitie"] == "A"
    assert terrein["ambitie"] == "C"


def test_eigen_ambitie_wint(client, admin_user):
    r = _rit(client, admin_user, gebiedstype="bedrijventerrein", ambitie="A+")
    assert r.json()["ambitie"] == "A+"


def test_onbekend_gebiedstype_wordt_geweigerd(client, admin_user):
    assert _rit(client, admin_user, gebiedstype="maanlandschap").status_code == 400


# ---------------------------------------------------------------------------
# Live frames
# ---------------------------------------------------------------------------

def test_frame_levert_waarnemingen_en_een_tussenstand(client, admin_user, stub_vision):
    stub_vision(gebied=[_zeker()])
    rit_id = _rit(client, admin_user).json()["id"]

    r = client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(admin_user),
                    json={"image_data_url": BEELD, "lat": 51.92, "lng": 4.47})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["bruikbaar"] is True
    assert len(d["gevonden"]) == 1
    assert d["gevonden"][0]["meetlat"] == "zwerfafval.elementenverharding"
    assert d["gevonden"][0]["lat"] == 51.92
    assert d["rit"]["frames"] == 1
    assert "tussenstand" in d["rit"]


def test_onbruikbaar_frame_wordt_geteld_niet_genegeerd(client, admin_user, stub_vision):
    """Anders leest een rit door de regen als een schone wijk."""
    stub_vision(bruikbaar=False, reden="te donker")
    rit_id = _rit(client, admin_user).json()["id"]

    r = client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(admin_user),
                    json={"image_data_url": BEELD})
    assert r.json()["rit"]["frames_onbruikbaar"] == 1


def test_beeld_wordt_alleen_bewaard_als_je_dat_vraagt(client, admin_user, stub_vision):
    """Bij een lange rit is elk frame bewaren veel data en zelden nodig."""
    stub_vision(gebied=[_zeker()])
    rit_id = _rit(client, admin_user).json()["id"]

    zonder = client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(admin_user),
                         json={"image_data_url": BEELD}).json()
    met = client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(admin_user),
                      json={"image_data_url": BEELD, "bewaar_beeld": True}).json()
    assert zonder["gevonden"][0]["photo_url"] is None
    assert met["gevonden"][0]["photo_url"] == BEELD


def test_onleesbaar_beeld_geeft_400(client, admin_user, stub_vision):
    stub_vision()
    rit_id = _rit(client, admin_user).json()["id"]
    r = client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(admin_user),
                    json={"image_data_url": "dit is geen data-url maar wel lang genoeg"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# De AI kijkt, jij beslist
# ---------------------------------------------------------------------------

def test_onzekere_waarneming_telt_pas_na_bevestiging(client, admin_user, stub_vision):
    stub_vision(gebied=[_zeker(zekerheid=0.40)])
    rit_id = _rit(client, admin_user).json()["id"]
    gevonden = client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(admin_user),
                           json={"image_data_url": BEELD}).json()["gevonden"][0]
    assert gevonden["telt_mee"] is False

    bevestigd = client.patch(f"/api/schouw/waarnemingen/{gevonden['id']}",
                             headers=auth(admin_user),
                             json={"bevestigd": True}).json()
    assert bevestigd["telt_mee"] is True


def test_afwijzen_verwijdert_niets(client, admin_user, stub_vision):
    """Het spoor van een gewijzigde score moet navolgbaar blijven."""
    stub_vision(gebied=[_zeker()])
    rit_id = _rit(client, admin_user).json()["id"]
    w = client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(admin_user),
                    json={"image_data_url": BEELD}).json()["gevonden"][0]

    uit = client.patch(f"/api/schouw/waarnemingen/{w['id']}", headers=auth(admin_user),
                       json={"afgewezen": True}).json()
    assert uit["afgewezen"] is True and uit["telt_mee"] is False

    detail = client.get(f"/api/schouw/ritten/{rit_id}", headers=auth(admin_user)).json()
    assert len(detail["waarnemingen"]) == 1


def test_ontbrekende_drager_alsnog_invullen_bepaalt_de_meetlat(
        client, admin_user, stub_vision):
    """Graffiti zonder drager is niet te scoren; met drager wel."""
    stub_vision(gebied=[{
        "klasse": "graffiti", "drager": None, "meetlat": None,
        "waarde": 20, "klasse_niveau": None, "zekerheid": 0.95,
        "toelichting": None, "beoordeling_nodig": True,
    }])
    rit_id = _rit(client, admin_user).json()["id"]
    w = client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(admin_user),
                    json={"image_data_url": BEELD}).json()["gevonden"][0]
    assert w["meetlat"] is None

    hersteld = client.patch(f"/api/schouw/waarnemingen/{w['id']}",
                            headers=auth(admin_user),
                            json={"drager": "nutskast", "bevestigd": True}).json()
    assert hersteld["meetlat"] == "bekladding.nutskast"
    assert hersteld["telt_mee"] is True


def test_drager_die_niet_bij_de_klasse_past_geeft_400(client, admin_user, stub_vision):
    stub_vision(gebied=[_zeker(klasse="graffiti", drager="afvalbak", waarde=10)])
    rit_id = _rit(client, admin_user).json()["id"]
    w = client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(admin_user),
                    json={"image_data_url": BEELD}).json()["gevonden"][0]
    r = client.patch(f"/api/schouw/waarnemingen/{w['id']}", headers=auth(admin_user),
                     json={"drager": "gras"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Handmatig
# ---------------------------------------------------------------------------

def test_zelf_iets_vastleggen_telt_meteen(client, admin_user):
    """Uitwerpselen ziet de camera niet; die tikt de inspecteur in."""
    rit_id = _rit(client, admin_user).json()["id"]
    r = client.post(f"/api/schouw/ritten/{rit_id}/waarneming", headers=auth(admin_user),
                    json={"detectieklasse": "afval_grof", "drager": "groen",
                          "waarde": 2, "toelichting": "matras in het plantsoen"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["bron"] == "mens" and d["bevestigd"] is True and d["telt_mee"] is True
    assert d["meetlat"] == "grofvuil.groen"


def test_handmatig_met_onbekende_klasse_geeft_400(client, admin_user):
    rit_id = _rit(client, admin_user).json()["id"]
    r = client.post(f"/api/schouw/ritten/{rit_id}/waarneming", headers=auth(admin_user),
                    json={"detectieklasse": "verzonnen", "waarde": 1})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Afronden en isolatie
# ---------------------------------------------------------------------------

def test_afgeronde_rit_neemt_geen_frames_meer_aan(client, admin_user, stub_vision):
    stub_vision(gebied=[_zeker()])
    rit_id = _rit(client, admin_user).json()["id"]
    client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(admin_user),
                json={"image_data_url": BEELD})

    klaar = client.post(f"/api/schouw/ritten/{rit_id}/afronden", headers=auth(admin_user))
    assert klaar.status_code == 200, klaar.text
    assert klaar.json()["status"] == "afgerond"

    weer = client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(admin_user),
                       json={"image_data_url": BEELD})
    assert weer.status_code == 409


def test_zonder_drempels_geen_verzonnen_score(client, admin_user, stub_vision):
    """Geen score is beter dan een score die niemand heeft afgesproken.

    De organisatie kan haar grenswaarden nog niet instellen; tot die tijd komt
    elke meetlat terug onder niet_beoordeeld in plaats van als een cijfer.
    """
    stub_vision(gebied=[_zeker()])
    rit_id = _rit(client, admin_user).json()["id"]
    client.post(f"/api/schouw/ritten/{rit_id}/frame", headers=auth(admin_user),
                json={"image_data_url": BEELD})
    uit = client.post(f"/api/schouw/ritten/{rit_id}/afronden",
                      headers=auth(admin_user)).json()
    assert uit["beeldkwaliteit"] is None
    assert "zwerfafval.elementenverharding" in uit["uitslag"]["niet_beoordeeld"]


def test_viewer_mag_lezen_maar_niet_schouwen(client, viewer_user):
    assert client.get("/api/schouw/ritten", headers=auth(viewer_user)).status_code == 200
    assert _rit(client, viewer_user).status_code == 403


def test_rit_van_andere_organisatie_geeft_404(client, admin_user):
    assert client.get("/api/schouw/ritten/bestaat-niet",
                      headers=auth(admin_user)).status_code == 404
