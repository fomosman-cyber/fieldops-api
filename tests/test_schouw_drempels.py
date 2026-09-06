"""Grenswaarden voor de beeldkwaliteitsschouw.

Zonder deze getallen levert een schouw wel waarnemingen op maar geen A-D-score.
Ze staan in het bestek van de opdrachtgever en horen daar vandaan te komen --
niet uit een aanname van ons.

Wat hier vastligt:

1. **Leeg blijft leeg.** Een nieuwe organisatie krijgt geen verzonnen grenzen.
2. **Een omgekeerde reeks wordt geweigerd.** Ligt de grens voor B onder die van
   A, dan is er geen enkele waarde die B oplevert en valt een heel niveau
   stilzwijgend weg. Dat merkt niemand tot er een rapport uitgaat.
3. **Alleen de beheerder legt ze vast.** Deze getallen bepalen waar een
   aannemer op wordt afgerekend.
"""

import crow_schouw as cs

from .conftest import auth

AFVAL = "zwerfafval.elementenverharding"
OPLOPEND = {"A+": 0, "A": 2, "B": 5, "C": 10}


def _zet(client, user, **body):
    body.setdefault("per_verschijnsel", {})
    body.setdefault("per_meetlat", {})
    return client.put("/api/schouw/drempels", json=body, headers=auth(user))


# ---------------------------------------------------------------------------
# Leeg blijft leeg
# ---------------------------------------------------------------------------

def test_nieuwe_organisatie_heeft_geen_grenzen(client, admin_user):
    d = client.get("/api/schouw/drempels", headers=auth(admin_user)).json()
    assert d["per_verschijnsel"] == {} and d["per_meetlat"] == {}
    # Alles staat op de lijst van wat nog geen score kan opleveren.
    assert len(d["zonder_grenzen"]) == d["meetlatten_totaal"] == len(cs.MEETLATTEN)


def test_zonder_grenzen_geen_score_maar_wel_waarnemingen(client, admin_user):
    """De schouw blijft bruikbaar; alleen het cijfer ontbreekt."""
    rit = client.post("/api/schouw/ritten", headers=auth(admin_user),
                      json={"gebied": "Coolsingel", "gebiedstype": "centrum"}).json()
    client.post(f"/api/schouw/ritten/{rit['id']}/waarneming", headers=auth(admin_user),
                json={"detectieklasse": "afval_los", "waarde": 8})
    uit = client.post(f"/api/schouw/ritten/{rit['id']}/afronden",
                      headers=auth(admin_user)).json()
    assert uit["beeldkwaliteit"] is None
    assert AFVAL in uit["uitslag"]["niet_beoordeeld"]


# ---------------------------------------------------------------------------
# Vastleggen
# ---------------------------------------------------------------------------

def test_grenzen_per_verschijnsel_gelden_voor_alle_dragers(client, admin_user):
    r = _zet(client, admin_user, per_verschijnsel={"zwerfafval": OPLOPEND})
    assert r.status_code == 200, r.text
    zonder = r.json()["zonder_grenzen"]
    # Alle zwerfafval-meetlatten zijn nu gedekt, ongeacht de ondergrond.
    assert not [c for c in zonder if c.startswith("zwerfafval.")]
    assert any(c.startswith("bekladding.") for c in zonder)


def test_grenzen_maken_een_score_mogelijk(client, admin_user):
    _zet(client, admin_user, per_verschijnsel={"zwerfafval": OPLOPEND})
    rit = client.post("/api/schouw/ritten", headers=auth(admin_user),
                      json={"gebied": "Coolsingel", "gebiedstype": "woonwijk"}).json()
    client.post(f"/api/schouw/ritten/{rit['id']}/waarneming", headers=auth(admin_user),
                json={"detectieklasse": "afval_los", "waarde": 8})
    uit = client.post(f"/api/schouw/ritten/{rit['id']}/afronden",
                      headers=auth(admin_user)).json()
    assert uit["beeldkwaliteit"] == "C"      # 8 valt tussen 5 en 10
    assert uit["voldoet"] is False           # ambitie van een woonwijk is B


def test_meetlat_specifieke_grens_wint(client, admin_user):
    """Een bestek dat viaducten anders behandelt zet die ene meetlat apart."""
    r = _zet(client, admin_user,
             per_verschijnsel={"bekladding": {"A+": 0, "A": 1, "B": 3}},
             per_meetlat={"bekladding.viaduct_brug": {"A+": 0, "A": 10, "B": 40}})
    assert r.status_code == 200, r.text

    rit = client.post("/api/schouw/ritten", headers=auth(admin_user),
                      json={"gebied": "Maastunnel", "gebiedstype": "hoofdweg"}).json()
    client.post(f"/api/schouw/ritten/{rit['id']}/waarneming", headers=auth(admin_user),
                json={"detectieklasse": "graffiti", "drager": "viaduct_brug",
                      "waarde": 5})
    uit = client.post(f"/api/schouw/ritten/{rit['id']}/afronden",
                      headers=auth(admin_user)).json()
    # Met de algemene regel was 5 een D geweest; met de eigen regel is het A.
    assert uit["beeldkwaliteit"] == "A"


# ---------------------------------------------------------------------------
# Weigeren wat niet klopt
# ---------------------------------------------------------------------------

def test_omgekeerde_reeks_wordt_geweigerd(client, admin_user):
    """Ligt B onder A, dan bestaat er geen waarde die B oplevert."""
    r = _zet(client, admin_user,
             per_verschijnsel={"zwerfafval": {"A+": 0, "A": 8, "B": 3, "C": 10}})
    assert r.status_code == 400
    assert "strengere niveau" in r.text


def test_negatieve_grens_wordt_geweigerd(client, admin_user):
    r = _zet(client, admin_user, per_verschijnsel={"zwerfafval": {"A": -2}})
    assert r.status_code == 400


def test_onbekend_verschijnsel_wordt_geweigerd(client, admin_user):
    r = _zet(client, admin_user, per_verschijnsel={"kapotte_sfeer": OPLOPEND})
    assert r.status_code == 400
    assert "verschijnsel" in r.text


def test_onbekende_meetlat_wordt_geweigerd(client, admin_user):
    r = _zet(client, admin_user, per_meetlat={"bekladding.maan": OPLOPEND})
    assert r.status_code == 400


def test_gaten_in_de_reeks_mogen(client, admin_user):
    """Niet elk bestek kent alle vijf de niveaus; A en C alleen mag ook."""
    r = _zet(client, admin_user, per_verschijnsel={"zwerfafval": {"A": 2, "C": 10}})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Wie mag het
# ---------------------------------------------------------------------------

def test_alleen_de_beheerder_legt_grenzen_vast(client, viewer_user):
    r = _zet(client, viewer_user, per_verschijnsel={"zwerfafval": OPLOPEND})
    assert r.status_code == 403


def test_iedereen_mag_ze_wel_lezen(client, viewer_user):
    """Een inspecteur moet weten waar hij op beoordeeld wordt."""
    assert client.get("/api/schouw/drempels",
                      headers=auth(viewer_user)).status_code == 200


def test_kapotte_opslag_sloopt_de_schouw_niet(client, admin_user, org):
    """Handmatig gerommel in de database mag geen 500 opleveren."""
    from database import SessionLocal
    from models import Organization
    db = SessionLocal()
    try:
        o = db.query(Organization).filter(Organization.id == org.id).first()
        o.schouw_drempels = "{dit is geen json"
        db.commit()
    finally:
        db.close()

    r = client.get("/api/schouw/drempels", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["per_verschijnsel"] == {}
