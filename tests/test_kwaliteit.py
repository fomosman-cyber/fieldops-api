"""Tests voor de module Kwaliteit & keuringen.

De kern van wat hier bewaakt wordt:

  - Een keuring is niet een registratie. Velden en eisen hangen aan de keuring,
    antwoorden en bewijs aan de registratie.
  - Zonder norm geen oordeel. Een meetwaarde zonder grenzen komt terug als
    `niet_beoordeeld`, nooit als akkoord -- dat is de regel waar het hele
    dossier op rust.
  - Indienen kan niet met een gat erin: verplichte velden ingevuld, en bewijs
    aanwezig bij eisen die erom vragen.
  - Wat is ingediend hoort bij het dossier en gaat er niet meer uit.
"""

import kwaliteit as kw
from tests.conftest import auth


# ─────────────────────────────────────────────────────────────────────────────
# De rekenregels los
# ─────────────────────────────────────────────────────────────────────────────

def test_zonder_norm_geen_oordeel():
    """De belangrijkste regel van de module, direct op de motor getest."""
    assert kw.beoordeel_meetwaarde(97.0) == "niet_beoordeeld"
    assert kw.beoordeel_meetwaarde(None, norm_min=98) == "niet_beoordeeld"


def test_meetwaarde_ondergrens():
    assert kw.beoordeel_meetwaarde(99.2, norm_min=98) == "akkoord"
    assert kw.beoordeel_meetwaarde(94.0, norm_min=98) == "niet_akkoord"
    assert kw.beoordeel_meetwaarde(98.0, norm_min=98) == "akkoord"


def test_meetwaarde_bovengrens_en_tolerantie():
    assert kw.beoordeel_meetwaarde(170, norm_max=165) == "niet_akkoord"
    # Met 5 speling mag 170 wel.
    assert kw.beoordeel_meetwaarde(170, norm_max=165, tolerantie=5) == "akkoord"
    # Speling werkt aan beide kanten.
    assert kw.beoordeel_meetwaarde(97, norm_min=98, tolerantie=1) == "akkoord"
    assert kw.beoordeel_meetwaarde(96.9, norm_min=98, tolerantie=1) == "niet_akkoord"


def test_meetwaarde_bandbreedte():
    assert kw.beoordeel_meetwaarde(160, norm_min=150, norm_max=180) == "akkoord"
    assert kw.beoordeel_meetwaarde(145, norm_min=150, norm_max=180) == "niet_akkoord"
    assert kw.beoordeel_meetwaarde(190, norm_min=150, norm_max=180) == "niet_akkoord"


def test_registratie_rollup():
    assert kw.beoordeel_registratie([]) == "niet_beoordeeld"
    assert kw.beoordeel_registratie(["akkoord", "akkoord"]) == "akkoord"
    # Een afkeur wint altijd -- daar valt niet over te middelen.
    assert kw.beoordeel_registratie(["akkoord", "niet_akkoord"]) == "niet_akkoord"
    assert kw.beoordeel_registratie(["akkoord", "niet_beoordeeld"]) == "deels_akkoord"
    assert kw.beoordeel_registratie(["niet_beoordeeld"]) == "niet_beoordeeld"


def test_voortgang_zonder_noemer():
    """Geen verwacht aantal betekent geen percentage, geen 0%."""
    assert kw.voortgang(3, None) is None
    assert kw.voortgang(3, 0) is None
    assert kw.voortgang(5, 10) == 50
    assert kw.voortgang(12, 10) == 100      # afgekapt


def test_sjablonen_zijn_consistent():
    """Elk sjabloon moet bruikbaar zijn zodra je het kopieert."""
    codes = set()
    for t in kw.TEMPLATES:
        assert t["code"] not in codes, f"dubbele sjablooncode {t['code']}"
        codes.add(t["code"])
        assert t["werksoort"] in kw.WERKSOORTEN
        assert t["frequentie"] in kw.FREQUENTIES
        assert t["velden"], f"{t['code']} heeft geen velden"

        veldcodes = set()
        for v in t["velden"]:
            assert v["veldtype"] in kw.VELDTYPES, f"{t['code']}.{v['code']}: {v['veldtype']}"
            assert v["code"] not in veldcodes, f"dubbele veldcode in {t['code']}"
            veldcodes.add(v["code"])
            # Een keuzeveld zonder opties is buiten onbruikbaar.
            if v["veldtype"] in kw.OPTIE_VELDTYPES:
                assert v.get("opties"), f"{t['code']}.{v['code']} mist opties"

        eisnummers = [e["eisnummer"] for e in t["eisen"]]
        assert len(eisnummers) == len(set(eisnummers)), f"dubbel eisnummer in {t['code']}"


def test_elke_werksoort_heeft_een_sjabloon():
    """Wegen, constructie, graafwerk en riool zijn waar de vraag zit."""
    aanwezig = {t["werksoort"] for t in kw.TEMPLATES}
    for verwacht in ("wegen", "constructie", "graafwerk", "riool"):
        assert verwacht in aanwezig


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _maak_keuring(client, user, **kw_args):
    payload = {"naam": "Controle fundering", "werksoort": "wegen"}
    payload.update(kw_args)
    r = client.post("/api/kwaliteit/keuringen", json=payload, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


def _veld(client, user, keuring_id, **kw_args):
    payload = {"label": "Verdichtingsgraad", "veldtype": "meetwaarde"}
    payload.update(kw_args)
    r = client.post(f"/api/kwaliteit/keuringen/{keuring_id}/velden",
                    json=payload, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


def _start(client, user, keuring_id, **kw_args):
    r = client.post(f"/api/kwaliteit/keuringen/{keuring_id}/registraties",
                    json=kw_args, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


def _antwoord_id(reg, veld_code):
    for a in reg["antwoorden"]:
        if a["veld_code"] == veld_code:
            return a["id"]
    raise AssertionError(f"veld {veld_code} niet gevonden in registratie")


# ─────────────────────────────────────────────────────────────────────────────
# Config en sjablonen via de API
# ─────────────────────────────────────────────────────────────────────────────

def test_config_geeft_de_builder_wat_hij_nodig_heeft(client, admin_user):
    r = client.get("/api/kwaliteit/config", headers=auth(admin_user))
    assert r.status_code == 200
    body = r.json()
    codes = {v["code"] for v in body["veldtypes"]}
    assert {"meetwaarde", "foto", "checklist", "handtekening"} <= codes
    meet = next(v for v in body["veldtypes"] if v["code"] == "meetwaarde")
    assert meet["heeft_norm"] is True
    foto = next(v for v in body["veldtypes"] if v["code"] == "foto")
    assert foto["is_bewijs"] is True


def test_templates_filteren_op_werksoort(client, admin_user):
    r = client.get("/api/kwaliteit/templates?werksoort=riool", headers=auth(admin_user))
    assert r.status_code == 200
    body = r.json()
    assert body and all(t["werksoort"] == "riool" for t in body)

    r = client.get("/api/kwaliteit/templates?werksoort=bestaatniet",
                   headers=auth(admin_user))
    assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Keuring opstellen
# ─────────────────────────────────────────────────────────────────────────────

def test_keuring_vanaf_sjabloon_neemt_velden_en_eisen_mee(client, admin_user):
    r = client.post("/api/kwaliteit/keuringen",
                    json={"template_code": "weg.asfalt"}, headers=auth(admin_user))
    assert r.status_code == 200, r.text
    k = r.json()
    assert k["naam"] == "Aanbrengen asfaltverharding"
    assert k["werksoort"] == "wegen"
    assert k["aantal_velden"] > 5
    assert k["aantal_eisen"] >= 3
    # De temperatuurmeting komt zonder ingevulde grenzen mee -- die staan in
    # het bestek en horen niet door ons verzonnen te worden.
    temp = next(v for v in k["velden"] if v["code"] == "temp_aanvoer")
    assert temp["veldtype"] == "meetwaarde"
    assert temp["eenheid"] == "°C"
    assert temp["norm_min"] is None and temp["norm_max"] is None


def test_keuring_zonder_naam_wordt_geweigerd(client, admin_user):
    r = client.post("/api/kwaliteit/keuringen", json={}, headers=auth(admin_user))
    assert r.status_code == 400


def test_onbekend_sjabloon_werksoort_frequentie(client, admin_user):
    for payload, veld in (
        ({"template_code": "bestaat.niet"}, "sjabloon"),
        ({"naam": "X", "werksoort": "ruimtevaart"}, "werksoort"),
        ({"naam": "X", "frequentie": "elke_schrikkeldag"}, "frequentie"),
    ):
        r = client.post("/api/kwaliteit/keuringen", json=payload, headers=auth(admin_user))
        assert r.status_code == 400, f"{veld}: {r.text}"


def test_vrije_frequentie_vraagt_om_een_omschrijving(client, admin_user):
    r = client.post("/api/kwaliteit/keuringen",
                    json={"naam": "X", "frequentie": "vrij"}, headers=auth(admin_user))
    assert r.status_code == 400

    r = client.post("/api/kwaliteit/keuringen",
                    json={"naam": "X", "frequentie": "vrij",
                          "frequentie_vrij": "Per stortdag"},
                    headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["frequentie_label"] == "Per stortdag"


def test_viewer_mag_geen_keuring_opstellen(client, viewer_user):
    r = client.post("/api/kwaliteit/keuringen", json={"naam": "X"},
                    headers=auth(viewer_user))
    assert r.status_code == 403


def test_technicus_mag_geen_keuring_opstellen(client, technician_user):
    """Opstellen is beheerwerk; uitvoeren mag hij wel (zie verderop)."""
    r = client.post("/api/kwaliteit/keuringen", json={"naam": "X"},
                    headers=auth(technician_user))
    assert r.status_code == 403


def test_keuze_veld_zonder_opties_wordt_geweigerd(client, admin_user):
    k = _maak_keuring(client, admin_user)
    r = client.post(f"/api/kwaliteit/keuringen/{k['id']}/velden",
                    json={"label": "Verband", "veldtype": "keuze"},
                    headers=auth(admin_user))
    assert r.status_code == 400
    assert "opties" in r.json()["detail"]


def test_onbekend_veldtype_wordt_geweigerd(client, admin_user):
    k = _maak_keuring(client, admin_user)
    r = client.post(f"/api/kwaliteit/keuringen/{k['id']}/velden",
                    json={"label": "Iets", "veldtype": "telepathie"},
                    headers=auth(admin_user))
    assert r.status_code == 400


def test_dubbele_veldcode_wordt_uniek_gemaakt(client, admin_user):
    """Twee kolommen met dezelfde code lopen in een export door elkaar."""
    k = _maak_keuring(client, admin_user)
    a = _veld(client, admin_user, k["id"], label="Laagdikte", veldtype="meetwaarde")
    b = _veld(client, admin_user, k["id"], label="Laagdikte", veldtype="meetwaarde")
    assert a["code"] != b["code"]


def test_eis_krijgt_automatisch_een_nummer(client, admin_user):
    k = _maak_keuring(client, admin_user)
    e1 = client.post(f"/api/kwaliteit/keuringen/{k['id']}/eisen",
                     json={"titel": "Verdichting voldoet", "bewijs_vereist": True},
                     headers=auth(admin_user)).json()
    e2 = client.post(f"/api/kwaliteit/keuringen/{k['id']}/eisen",
                     json={"titel": "Laagdikte volgens bestek"},
                     headers=auth(admin_user)).json()
    assert e1["eisnummer"] == "01"
    assert e2["eisnummer"] == "02"
    assert e1["bewijs_vereist"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Registreren
# ─────────────────────────────────────────────────────────────────────────────

def test_registratie_krijgt_alle_antwoordrijen_vooraf(client, admin_user):
    """De complete lijst staat meteen op je scherm, zoals bij de LMRA."""
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"], label="Verdichting", veldtype="meetwaarde")
    _veld(client, admin_user, k["id"], label="Foto", veldtype="foto")

    reg = _start(client, admin_user, k["id"], werkvak="Werkvak 03")
    assert reg["status"] == "concept"
    assert reg["volgnummer"] == 1
    assert reg["aantal_velden"] == 2
    assert reg["aantal_ingevuld"] == 0
    assert reg["werkvak"] == "Werkvak 03"

    tweede = _start(client, admin_user, k["id"])
    assert tweede["volgnummer"] == 2


def test_registratie_zonder_velden_kan_niet(client, admin_user):
    k = _maak_keuring(client, admin_user)
    r = client.post(f"/api/kwaliteit/keuringen/{k['id']}/registraties",
                    json={}, headers=auth(admin_user))
    assert r.status_code == 409


def test_technicus_mag_wel_registreren(client, admin_user, technician_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"])
    r = client.post(f"/api/kwaliteit/keuringen/{k['id']}/registraties",
                    json={}, headers=auth(technician_user))
    assert r.status_code == 200


def test_viewer_mag_niet_registreren(client, admin_user, viewer_user):
    """De opdrachtgever kijkt mee; hij vult het dossier niet."""
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"])
    r = client.post(f"/api/kwaliteit/keuringen/{k['id']}/registraties",
                    json={}, headers=auth(viewer_user))
    assert r.status_code == 403


def test_meting_buiten_de_norm_markeert_de_registratie_als_afwijkend(client, admin_user):
    """Het voorbeeld uit het bestek: eis 98%, gemeten 94%."""
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"], label="Verdichting", veldtype="meetwaarde",
          eenheid="%", norm_min=98)
    reg = _start(client, admin_user, k["id"])
    aid = _antwoord_id(reg, "verdichting")

    r = client.patch(f"/api/kwaliteit/registraties/{reg['id']}/antwoorden/{aid}",
                     json={"waarde": 94}, headers=auth(admin_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["antwoord"]["oordeel"] == "niet_akkoord"
    assert body["registratie"]["afwijkend"] is True
    assert body["registratie"]["resultaat"] == "niet_akkoord"
    # Antwoorden zet de registratie in uitvoering.
    assert body["registratie"]["status"] == "in_uitvoering"


def test_meting_binnen_de_norm_is_akkoord(client, admin_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"], label="Verdichting", veldtype="meetwaarde",
          norm_min=98)
    reg = _start(client, admin_user, k["id"])
    aid = _antwoord_id(reg, "verdichting")

    body = client.patch(f"/api/kwaliteit/registraties/{reg['id']}/antwoorden/{aid}",
                        json={"waarde": 99.2}, headers=auth(admin_user)).json()
    assert body["antwoord"]["oordeel"] == "akkoord"
    assert body["registratie"]["afwijkend"] is False
    assert body["registratie"]["resultaat"] == "akkoord"


def test_meting_zonder_ingevulde_norm_is_niet_akkoord_maar_ook_niet_goed(client, admin_user):
    """De regel waar het hele dossier op rust, nu via de API.

    Een meting zonder norm mag nooit stilzwijgend een goedkeuring worden.
    """
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"], label="Temperatuur", veldtype="meetwaarde",
          eenheid="°C")
    reg = _start(client, admin_user, k["id"])
    aid = _antwoord_id(reg, "temperatuur")

    body = client.patch(f"/api/kwaliteit/registraties/{reg['id']}/antwoorden/{aid}",
                        json={"waarde": 165}, headers=auth(admin_user)).json()
    assert body["antwoord"]["oordeel"] == "niet_beoordeeld"
    assert body["registratie"]["resultaat"] == "niet_beoordeeld"
    assert body["registratie"]["afwijkend"] is False


def test_norm_wordt_gesnapshot_bij_het_antwoord(client, admin_user):
    """Een later gewijzigde norm herschrijft het dossier niet met terugwerkende kracht."""
    k = _maak_keuring(client, admin_user)
    v = _veld(client, admin_user, k["id"], label="Verdichting",
              veldtype="meetwaarde", norm_min=98)
    reg = _start(client, admin_user, k["id"])
    aid = _antwoord_id(reg, "verdichting")
    client.patch(f"/api/kwaliteit/registraties/{reg['id']}/antwoorden/{aid}",
                 json={"waarde": 94}, headers=auth(admin_user))

    # Beheerder verlaagt de eis achteraf naar 90.
    client.patch(f"/api/kwaliteit/keuringen/{k['id']}/velden/{v['id']}",
                 json={"norm_min": 90}, headers=auth(admin_user))

    detail = client.get(f"/api/kwaliteit/registraties/{reg['id']}",
                        headers=auth(admin_user)).json()
    antwoord = detail["antwoorden"][0]
    assert antwoord["norm_min"] == 98          # de norm van toen
    assert antwoord["oordeel"] == "niet_akkoord"


def test_akkoord_en_ja_nee_velden_geven_een_oordeel(client, admin_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"], label="Ondergrond schoon", veldtype="ja_nee")
    _veld(client, admin_user, k["id"], label="Naden", veldtype="akkoord")
    _veld(client, admin_user, k["id"], label="Bovenlaag", veldtype="keuring")
    reg = _start(client, admin_user, k["id"])

    def zet(code, waarde):
        aid = _antwoord_id(reg, code)
        return client.patch(f"/api/kwaliteit/registraties/{reg['id']}/antwoorden/{aid}",
                            json={"waarde": waarde}, headers=auth(admin_user)).json()

    assert zet("ondergrond_schoon", False)["antwoord"]["oordeel"] == "niet_akkoord"
    assert zet("naden", "akkoord")["antwoord"]["oordeel"] == "akkoord"
    assert zet("bovenlaag", "afgekeurd")["antwoord"]["oordeel"] == "niet_akkoord"


def test_nvt_telt_niet_mee_in_het_resultaat(client, admin_user):
    """N.v.t. is geen goedkeuring en geen afkeur -- het valt buiten de telling."""
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"], label="Kleeflaag", veldtype="ja_nee_nvt")
    _veld(client, admin_user, k["id"], label="Naden", veldtype="akkoord")
    reg = _start(client, admin_user, k["id"])

    client.patch(f"/api/kwaliteit/registraties/{reg['id']}/antwoorden/{_antwoord_id(reg, 'kleeflaag')}",
                 json={"waarde": "nvt"}, headers=auth(admin_user))
    body = client.patch(
        f"/api/kwaliteit/registraties/{reg['id']}/antwoorden/{_antwoord_id(reg, 'naden')}",
        json={"waarde": "akkoord"}, headers=auth(admin_user)).json()
    assert body["registratie"]["resultaat"] == "akkoord"


def test_checklist_slaat_een_lijst_op(client, admin_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"], label="Controlepunten", veldtype="checklist",
          opties=["Ondergrond schoon", "Hoogte gecontroleerd", "Verdichting gecontroleerd"])
    reg = _start(client, admin_user, k["id"])
    aid = _antwoord_id(reg, "controlepunten")

    body = client.patch(f"/api/kwaliteit/registraties/{reg['id']}/antwoorden/{aid}",
                        json={"waarde": ["Ondergrond schoon", "Hoogte gecontroleerd"]},
                        headers=auth(admin_user)).json()
    assert body["antwoord"]["waarde"] == ["Ondergrond schoon", "Hoogte gecontroleerd"]


def test_getal_in_een_tekstveld_van_het_verkeerde_type_faalt_netjes(client, admin_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"], label="Laagdikte", veldtype="meetwaarde")
    reg = _start(client, admin_user, k["id"])
    aid = _antwoord_id(reg, "laagdikte")
    r = client.patch(f"/api/kwaliteit/registraties/{reg['id']}/antwoorden/{aid}",
                     json={"waarde": "dik genoeg"}, headers=auth(admin_user))
    assert r.status_code == 400
    assert "getal" in r.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# Bewijs
# ─────────────────────────────────────────────────────────────────────────────

def test_bewijs_hangt_aan_eis_en_registratie(client, admin_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"])
    eis = client.post(f"/api/kwaliteit/keuringen/{k['id']}/eisen",
                      json={"titel": "Verdichting aangetoond", "bewijs_vereist": True},
                      headers=auth(admin_user)).json()
    reg = _start(client, admin_user, k["id"])

    r = client.post(f"/api/kwaliteit/registraties/{reg['id']}/bewijs",
                    json={"soort": "meetrapport", "titel": "Meetrapport.pdf",
                          "url": "data:application/pdf;base64,JVBERi0=",
                          "eis_id": eis["id"], "bestandsnaam": "Meetrapport.pdf"},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    bewijs = r.json()
    assert bewijs["soort"] == "meetrapport"
    assert bewijs["eis_id"] == eis["id"]
    # De inhoud zit bewust niet in het antwoord van het aanmaken.
    assert "url" not in bewijs


def test_lijst_stuurt_geen_bewijsinhoud_mee(client, admin_user):
    """Dit is precies wat de API eerder omvertrok."""
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"])
    reg = _start(client, admin_user, k["id"])
    grote_foto = "data:image/jpeg;base64," + ("A" * 5000)
    client.post(f"/api/kwaliteit/registraties/{reg['id']}/bewijs",
                json={"url": grote_foto}, headers=auth(admin_user))

    lijst = client.get("/api/kwaliteit/registraties", headers=auth(admin_user)).text
    assert "A" * 500 not in lijst

    detail = client.get(f"/api/kwaliteit/registraties/{reg['id']}",
                        headers=auth(admin_user)).json()
    assert detail["bewijs"][0].get("url") is None
    assert detail["aantal_bewijs"] == 1

    # Per stuk opvragen geeft de inhoud wel.
    bewijs_id = detail["bewijs"][0]["id"]
    stuk = client.get(f"/api/kwaliteit/registraties/{reg['id']}/bewijs/{bewijs_id}",
                      headers=auth(admin_user)).json()
    assert stuk["url"] == grote_foto


def test_onbekende_bewijssoort_wordt_geweigerd(client, admin_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"])
    reg = _start(client, admin_user, k["id"])
    r = client.post(f"/api/kwaliteit/registraties/{reg['id']}/bewijs",
                    json={"soort": "kruiswoordpuzzel", "url": "x"},
                    headers=auth(admin_user))
    assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Indienen en beoordelen
# ─────────────────────────────────────────────────────────────────────────────

def test_indienen_kan_niet_met_een_leeg_verplicht_veld(client, admin_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"], label="Laagdikte", veldtype="meetwaarde",
          verplicht=True)
    reg = _start(client, admin_user, k["id"])

    r = client.post(f"/api/kwaliteit/registraties/{reg['id']}/indienen",
                    headers=auth(admin_user))
    assert r.status_code == 400
    assert "Laagdikte" in r.json()["detail"]


def test_indienen_kan_niet_zonder_verplicht_bewijs(client, admin_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"])
    client.post(f"/api/kwaliteit/keuringen/{k['id']}/eisen",
                json={"titel": "Verdichting aangetoond", "bewijs_vereist": True},
                headers=auth(admin_user))
    reg = _start(client, admin_user, k["id"])

    r = client.post(f"/api/kwaliteit/registraties/{reg['id']}/indienen",
                    headers=auth(admin_user))
    assert r.status_code == 400
    assert "Bewijs ontbreekt" in r.json()["detail"]


def test_afwijkende_registratie_mag_juist_wel_worden_ingediend(client, admin_user):
    """Die moet langs de controleur -- tegenhouden zou het gat verbergen."""
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"], label="Verdichting", veldtype="meetwaarde",
          norm_min=98, verplicht=True)
    reg = _start(client, admin_user, k["id"])
    client.patch(f"/api/kwaliteit/registraties/{reg['id']}/antwoorden/{_antwoord_id(reg, 'verdichting')}",
                 json={"waarde": 94}, headers=auth(admin_user))

    r = client.post(f"/api/kwaliteit/registraties/{reg['id']}/indienen",
                    headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["status"] == "ingediend"
    assert r.json()["resultaat"] == "niet_akkoord"


def test_beoordelen_afkeuren_vraagt_een_reden(client, admin_user, inspector_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"])
    reg = _start(client, admin_user, k["id"])
    client.post(f"/api/kwaliteit/registraties/{reg['id']}/indienen", headers=auth(admin_user))

    r = client.post(f"/api/kwaliteit/registraties/{reg['id']}/beoordelen",
                    json={"besluit": "afkeuren"}, headers=auth(inspector_user))
    assert r.status_code == 400

    r = client.post(f"/api/kwaliteit/registraties/{reg['id']}/beoordelen",
                    json={"besluit": "afkeuren", "reden": "Verdichting opnieuw uitvoeren"},
                    headers=auth(inspector_user))
    assert r.status_code == 200
    assert r.json()["status"] == "afgekeurd"
    assert r.json()["beoordeling_reden"] == "Verdichting opnieuw uitvoeren"


def test_toezichthouder_mag_beoordelen_technicus_niet(client, admin_user,
                                                      inspector_user, technician_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"])
    reg = _start(client, admin_user, k["id"])
    client.post(f"/api/kwaliteit/registraties/{reg['id']}/indienen", headers=auth(admin_user))

    r = client.post(f"/api/kwaliteit/registraties/{reg['id']}/beoordelen",
                    json={"besluit": "goedkeuren"}, headers=auth(technician_user))
    assert r.status_code == 403

    r = client.post(f"/api/kwaliteit/registraties/{reg['id']}/beoordelen",
                    json={"besluit": "goedkeuren"}, headers=auth(inspector_user))
    assert r.status_code == 200
    assert r.json()["status"] == "goedgekeurd"


def test_beoordeelde_registratie_kan_niet_meer_wijzigen(client, admin_user, inspector_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"], label="Verdichting", veldtype="meetwaarde")
    reg = _start(client, admin_user, k["id"])
    client.post(f"/api/kwaliteit/registraties/{reg['id']}/indienen", headers=auth(admin_user))
    client.post(f"/api/kwaliteit/registraties/{reg['id']}/beoordelen",
                json={"besluit": "goedkeuren"}, headers=auth(inspector_user))

    aid = _antwoord_id(reg, "verdichting")
    r = client.patch(f"/api/kwaliteit/registraties/{reg['id']}/antwoorden/{aid}",
                     json={"waarde": 99}, headers=auth(admin_user))
    assert r.status_code == 409


def test_alleen_ingediende_registratie_kan_beoordeeld_worden(client, admin_user,
                                                             inspector_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"])
    reg = _start(client, admin_user, k["id"])
    r = client.post(f"/api/kwaliteit/registraties/{reg['id']}/beoordelen",
                    json={"besluit": "goedkeuren"}, headers=auth(inspector_user))
    assert r.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# Het dossier blijft staan
# ─────────────────────────────────────────────────────────────────────────────

def test_ingediende_registratie_kan_niet_verwijderd_worden(client, admin_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"])
    reg = _start(client, admin_user, k["id"])
    client.post(f"/api/kwaliteit/registraties/{reg['id']}/indienen", headers=auth(admin_user))

    r = client.delete(f"/api/kwaliteit/registraties/{reg['id']}", headers=auth(admin_user))
    assert r.status_code == 409


def test_eigen_concept_mag_wel_weg(client, admin_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"])
    reg = _start(client, admin_user, k["id"])
    r = client.delete(f"/api/kwaliteit/registraties/{reg['id']}", headers=auth(admin_user))
    assert r.status_code == 200


def test_keuring_met_ingediende_registratie_kan_niet_weg(client, admin_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"])
    reg = _start(client, admin_user, k["id"])
    client.post(f"/api/kwaliteit/registraties/{reg['id']}/indienen", headers=auth(admin_user))

    r = client.delete(f"/api/kwaliteit/keuringen/{k['id']}", headers=auth(admin_user))
    assert r.status_code == 409
    assert "gearchiveerd" in r.json()["detail"]


def test_veld_verwijderen_kan_niet_na_indienen(client, admin_user):
    """Anders heeft een goedgekeurde registratie achteraf een vraag minder."""
    k = _maak_keuring(client, admin_user)
    v = _veld(client, admin_user, k["id"])
    reg = _start(client, admin_user, k["id"])
    client.post(f"/api/kwaliteit/registraties/{reg['id']}/indienen", headers=auth(admin_user))

    r = client.delete(f"/api/kwaliteit/keuringen/{k['id']}/velden/{v['id']}",
                      headers=auth(admin_user))
    assert r.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# Voortgang, filters en afscherming
# ─────────────────────────────────────────────────────────────────────────────

def test_voortgang_telt_ingediende_registraties(client, admin_user):
    k = _maak_keuring(client, admin_user, verwacht_aantal=4)
    _veld(client, admin_user, k["id"])
    for _ in range(2):
        reg = _start(client, admin_user, k["id"])
        client.post(f"/api/kwaliteit/registraties/{reg['id']}/indienen",
                    headers=auth(admin_user))
    # Een concept telt niet mee -- er is nog niets gecontroleerd.
    _start(client, admin_user, k["id"])

    detail = client.get(f"/api/kwaliteit/keuringen/{k['id']}", headers=auth(admin_user)).json()
    assert detail["registraties"]["uitgevoerd"] == 2
    assert detail["registraties"]["totaal"] == 3
    assert detail["voortgang"] == 50


def test_filter_op_afwijkend(client, admin_user):
    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"], label="Verdichting", veldtype="meetwaarde",
          norm_min=98)
    goed = _start(client, admin_user, k["id"])
    client.patch(f"/api/kwaliteit/registraties/{goed['id']}/antwoorden/{_antwoord_id(goed, 'verdichting')}",
                 json={"waarde": 99}, headers=auth(admin_user))
    fout = _start(client, admin_user, k["id"])
    client.patch(f"/api/kwaliteit/registraties/{fout['id']}/antwoorden/{_antwoord_id(fout, 'verdichting')}",
                 json={"waarde": 90}, headers=auth(admin_user))

    body = client.get("/api/kwaliteit/registraties?alleen_afwijkend=true",
                      headers=auth(admin_user)).json()
    assert body["totaal"] == 1
    assert body["registraties"][0]["id"] == fout["id"]


def test_keuring_van_een_andere_organisatie_is_onzichtbaar(client, admin_user,
                                                           platform_owner):
    k = _maak_keuring(client, admin_user)
    r = client.get(f"/api/kwaliteit/keuringen/{k['id']}", headers=auth(platform_owner))
    assert r.status_code == 404

    lijst = client.get("/api/kwaliteit/keuringen", headers=auth(platform_owner)).json()
    assert lijst["totaal"] == 0


def test_zonder_login_geen_toegang(client):
    assert client.get("/api/kwaliteit/keuringen").status_code in (401, 403)
    assert client.get("/api/kwaliteit/config").status_code in (401, 403)


def test_audit_legt_de_keten_vast(client, admin_user, inspector_user):
    """Wie, wat, wanneer -- van aanmaken tot goedkeuren."""
    from database import SessionLocal
    from models import AuditLog

    k = _maak_keuring(client, admin_user)
    _veld(client, admin_user, k["id"], label="Verdichting", veldtype="meetwaarde",
          norm_min=98)
    reg = _start(client, admin_user, k["id"])
    client.patch(f"/api/kwaliteit/registraties/{reg['id']}/antwoorden/{_antwoord_id(reg, 'verdichting')}",
                 json={"waarde": 94}, headers=auth(admin_user))
    client.post(f"/api/kwaliteit/registraties/{reg['id']}/indienen", headers=auth(admin_user))
    client.post(f"/api/kwaliteit/registraties/{reg['id']}/beoordelen",
                json={"besluit": "afkeuren", "reden": "Opnieuw verdichten"},
                headers=auth(inspector_user))

    db = SessionLocal()
    try:
        acties = {a.action for a in db.query(AuditLog).all()}
    finally:
        db.close()
    assert {"kwaliteit.keuring.create", "kwaliteit.veld.create",
            "kwaliteit.registratie.create", "kwaliteit.registratie.antwoord",
            "kwaliteit.registratie.indienen",
            "kwaliteit.registratie.beoordeeld"} <= acties


def test_module_uit_geeft_403(client, admin_user):
    """De module hoort per organisatie uitgezet te kunnen worden."""
    import json as _json
    from database import SessionLocal
    from models import Organization

    db = SessionLocal()
    try:
        o = db.query(Organization).filter(Organization.id == admin_user.organization_id).first()
        o.enabled_modules = _json.dumps(["kunstwerken"])
        db.commit()
    finally:
        db.close()

    r = client.get("/api/kwaliteit/keuringen", headers=auth(admin_user))
    assert r.status_code == 403


def test_keuring_overleeft_het_verwijderen_van_haar_project(client, admin_user):
    """Een kwaliteitsdossier verdwijnt niet omdat het project wordt opgeruimd.

    Zelfde keuze als bij toolboxen en werkplekinspecties: de koppeling gaat los,
    de registratie blijft staan. Wie het project weggooit, gooit niet het bewijs weg.
    """
    project = client.post("/api/projects/", json={"name": "N201 herinrichting"},
                          headers=auth(admin_user))
    assert project.status_code in (200, 201), project.text
    project_id = project.json()["id"]

    k = _maak_keuring(client, admin_user, project_id=project_id)
    _veld(client, admin_user, k["id"])
    reg = _start(client, admin_user, k["id"])
    client.post(f"/api/kwaliteit/registraties/{reg['id']}/indienen", headers=auth(admin_user))

    r = client.delete(f"/api/projects/{project_id}?hard=true", headers=auth(admin_user))
    assert r.status_code == 200, r.text

    detail = client.get(f"/api/kwaliteit/keuringen/{k['id']}", headers=auth(admin_user))
    assert detail.status_code == 200
    assert detail.json()["project_id"] is None
    assert detail.json()["registraties"]["ingediend"] == 1
