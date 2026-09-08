"""De laatste-minuut risicoanalyse.

De LMRA is een stopinstrument. Alles wat hier wordt vastgelegd draait om die
ene eigenschap, en de tests dus ook:

1. **Met een NEE kun je niet doorgaan.** De uitkomst wordt op de server
   berekend, niet meegestuurd, zodat een cliënt geen "veilig" kan opsturen bij
   een lijst met een nee erin.
2. **Een NEE zonder toelichting bestaat niet.** Anders staat er over een half
   jaar "gestopt" zonder dat iemand weet waarom.
3. **Een gestopte LMRA blijft gestopt.** De maatregel is een aanvulling, geen
   correctie. Wie verder wil, doet een nieuwe beoordeling die naar de vorige
   verwijst.
4. **Drie vragen kunnen nooit 'niet van toepassing' zijn.** Je weet altijd wat
   je gaat doen, je draagt altijd iets, en je weet altijd hoe je alarmeert.
"""

import pytest

import lmra as L
from database import SessionLocal
from models import Lmra, Organization

from .conftest import auth


ALLES_JA = {c: "ja" for c in L.CODES}


def _met(**afwijkingen):
    """Alle vragen op ja, behalve wat je hier meegeeft."""
    antwoorden = dict(ALLES_JA)
    antwoorden.update(afwijkingen)
    return antwoorden


def _post(client, user, **velden):
    body = {"werkzaamheid": "Putdeksel vervangen", "antwoorden": ALLES_JA}
    body.update(velden)
    return client.post("/api/lmra", json=body, headers=auth(user))


# ---------------------------------------------------------------------------
# De rekenregel zelf, zonder HTTP
# ---------------------------------------------------------------------------

def test_alles_ja_is_veilig():
    r = L.beoordeel(ALLES_JA)
    assert r["uitkomst"] == L.VEILIG
    assert r["mag_beginnen"] is True
    assert r["aantal_nee"] == 0


def test_een_nee_stopt_het_werk():
    r = L.beoordeel(_met(**{"LMRA.OMGEVING": "nee"}),
                    toelichtingen={"LMRA.OMGEVING": "Afzetting staat er niet"})
    assert r["uitkomst"] == L.GESTOPT
    assert r["mag_beginnen"] is False
    assert r["blokkades"][0]["code"] == "LMRA.OMGEVING"
    assert "Afzetting" in r["blokkades"][0]["toelichting"]


def test_nee_zonder_toelichting_mag_niet():
    """Anders staat er later 'gestopt' zonder dat iemand weet waarom."""
    with pytest.raises(L.OngeldigeLmra) as fout:
        L.beoordeel(_met(**{"LMRA.MIDDELEN": "nee"}))
    assert "toelichting" in str(fout.value).lower()


def test_onvolledige_lijst_wordt_geweigerd():
    deel = {c: "ja" for c in L.CODES[:4]}
    with pytest.raises(L.OngeldigeLmra) as fout:
        L.beoordeel(deel)
    assert "LMRA.OMGEVING" in str(fout.value)


@pytest.mark.parametrize("code", sorted(L.NVT_NIET_TOEGESTAAN))
def test_sommige_vragen_kunnen_niet_nvt_zijn(code):
    """Je weet altijd wat je doet, wat je draagt en hoe je alarmeert."""
    with pytest.raises(L.OngeldigeLmra) as fout:
        L.beoordeel(_met(**{code: "nvt"}))
    assert code in str(fout.value)


def test_nvt_mag_wel_bij_een_vergunning():
    r = L.beoordeel(_met(**{"LMRA.VERGUNNING": "nvt"}))
    assert r["uitkomst"] == L.VEILIG


def test_onbekende_vraag_wordt_geweigerd():
    with pytest.raises(L.OngeldigeLmra):
        L.beoordeel({**ALLES_JA, "LMRA.VERZONNEN": "ja"})


def test_alle_vragen_staan_positief():
    """Een lijst waarin één vraag omgekeerd werkt, levert fouten op.

    Deze test is een leesbaarheidsafspraak: geen vraagtekst mag zo staan dat
    'ja' het probleem is. Vandaar het verbod op woorden die de vraag omdraaien.
    """
    verboden = ("kan ", "risico dat", "gevaar dat", "ontbreek", "niet ")
    for v in L.VRAGEN:
        tekst = v["vraag"].lower()
        for w in verboden:
            assert w not in tekst, f"{v['code']} draait de vraag om: {v['vraag']}"


def test_elke_vraag_heeft_uitleg_en_norm():
    for v in L.VRAGEN:
        assert v["uitleg"].strip(), v["code"]
        assert v["norm_ref"].strip(), v["code"]


# ---------------------------------------------------------------------------
# Via de API
# ---------------------------------------------------------------------------

def test_checklist_is_op_te_halen(client, admin_user):
    r = client.get("/api/lmra/checklist", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["vragen"]) == 8
    assert d["vragen"][0]["nvt_toegestaan"] is False


def test_veilige_lmra_wordt_opgeslagen(client, admin_user):
    r = _post(client, admin_user)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["uitkomst"] == "veilig"
    assert d["mag_beginnen"] is True
    assert d["uitvoerder"]
    assert len(d["antwoorden"]) == 8


def test_de_server_rekent_de_uitkomst_zelf_uit(client, admin_user):
    """De cliënt mag de uitkomst niet bepalen.

    Zonder deze regel kan een app "veilig" opsturen bij een lijst met een nee
    erin, en dan is het hele instrument een vinkje.
    """
    r = _post(client, admin_user,
              antwoorden=_met(**{"LMRA.WERKPLEK": "nee"}),
              toelichtingen={"LMRA.WERKPLEK": "Kabels los over het pad"},
              uitkomst="veilig")          # wordt genegeerd
    assert r.status_code == 201, r.text
    assert r.json()["uitkomst"] == "gestopt"
    assert r.json()["mag_beginnen"] is False


def test_nee_zonder_toelichting_geeft_400(client, admin_user):
    r = _post(client, admin_user, antwoorden=_met(**{"LMRA.PBM": "nee"}))
    assert r.status_code == 400
    assert "toelichting" in r.json()["detail"].lower()


def test_toelichting_komt_bij_de_juiste_vraag(client, admin_user):
    r = _post(client, admin_user,
              antwoorden=_met(**{"LMRA.DERDEN": "nee"}),
              toelichtingen={"LMRA.DERDEN": "Kraan draait boven ons"})
    antwoorden = {a["code"]: a for a in r.json()["antwoorden"]}
    assert antwoorden["LMRA.DERDEN"]["toelichting"] == "Kraan draait boven ons"
    assert antwoorden["LMRA.TAAK"]["toelichting"] is None


def test_lmra_hoeft_niet_aan_een_project_te_hangen(client, admin_user):
    """Wie op een melding afgaat staat op straat zonder projectnummer."""
    r = _post(client, admin_user)
    assert r.status_code == 201
    assert r.json()["project_id"] is None


def test_project_van_een_andere_organisatie_wordt_geweigerd(client, admin_user):
    db = SessionLocal()
    try:
        vreemd = Organization(name="Andere BV", max_users=5)
        db.add(vreemd)
        db.commit()
        vreemd_id = vreemd.id
    finally:
        db.close()

    from models import Project
    db = SessionLocal()
    try:
        p = Project(name="Niet van jou", organization_id=vreemd_id,
                    created_by=admin_user.id)
        db.add(p)
        db.commit()
        project_id = p.id
    finally:
        db.close()

    r = _post(client, admin_user, project_id=project_id)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Na een stop
# ---------------------------------------------------------------------------

def _gestopte(client, user):
    r = _post(client, user,
              antwoorden=_met(**{"LMRA.OMGEVING": "nee"}),
              toelichtingen={"LMRA.OMGEVING": "Afzetting ontbreekt"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_maatregel_verandert_het_oordeel_niet(client, admin_user):
    """De kern: een stop verdampt niet achteraf."""
    lmra_id = _gestopte(client, admin_user)
    r = client.post(f"/api/lmra/{lmra_id}/maatregel",
                    json={"maatregel": "Afzetting geplaatst volgens CROW 96b"},
                    headers=auth(admin_user))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["uitkomst"] == "gestopt"
    assert d["mag_beginnen"] is False
    assert "Afzetting geplaatst" in d["maatregel"]


def test_maatregel_kan_maar_een_keer(client, admin_user):
    lmra_id = _gestopte(client, admin_user)
    body = {"maatregel": "Opgelost"}
    assert client.post(f"/api/lmra/{lmra_id}/maatregel", json=body,
                       headers=auth(admin_user)).status_code == 200
    assert client.post(f"/api/lmra/{lmra_id}/maatregel", json=body,
                       headers=auth(admin_user)).status_code == 409


def test_maatregel_op_een_veilige_lmra_slaat_nergens_op(client, admin_user):
    r = _post(client, admin_user)
    lmra_id = r.json()["id"]
    r2 = client.post(f"/api/lmra/{lmra_id}/maatregel",
                     json={"maatregel": "Niets aan de hand"},
                     headers=auth(admin_user))
    assert r2.status_code == 400


def test_hernieuwde_beoordeling_verwijst_naar_de_vorige(client, admin_user):
    lmra_id = _gestopte(client, admin_user)
    r = _post(client, admin_user, vorige_lmra_id=lmra_id)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["uitkomst"] == "veilig"
    assert d["vorige_lmra_id"] == lmra_id

    # En de oude staat er nog precies zo bij.
    oud = client.get(f"/api/lmra/{lmra_id}", headers=auth(admin_user)).json()
    assert oud["uitkomst"] == "gestopt"


def test_hernieuwde_beoordeling_hoort_bij_een_stop(client, admin_user):
    veilig_id = _post(client, admin_user).json()["id"]
    r = _post(client, admin_user, vorige_lmra_id=veilig_id)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Lijst, detail en statistiek
# ---------------------------------------------------------------------------

def test_lijst_toont_alleen_de_eigen_organisatie(client, admin_user):
    _post(client, admin_user)
    db = SessionLocal()
    try:
        vreemd = Organization(name="Buurbedrijf", max_users=5)
        db.add(vreemd)
        db.commit()
        db.add(Lmra(organization_id=vreemd.id, werkzaamheid="Van iemand anders",
                    checklist_versie=L.LMRA_VERSIE, uitkomst="veilig",
                    aantal_nee=0, created_by=admin_user.id))
        db.commit()
    finally:
        db.close()

    d = client.get("/api/lmra", headers=auth(admin_user)).json()
    assert d["totaal"] == 1
    assert all(x["werkzaamheid"] != "Van iemand anders" for x in d["lmras"])


def test_filteren_op_gestopt(client, admin_user):
    _post(client, admin_user)
    _gestopte(client, admin_user)
    d = client.get("/api/lmra?uitkomst=gestopt", headers=auth(admin_user)).json()
    assert d["totaal"] == 1
    assert d["lmras"][0]["uitkomst"] == "gestopt"


def test_statistiek_telt_waar_op_gestopt_wordt(client, admin_user):
    """De vraag waar een KAM-functionaris iets mee kan."""
    _post(client, admin_user)
    _gestopte(client, admin_user)
    _gestopte(client, admin_user)

    d = client.get("/api/lmra/statistiek", headers=auth(admin_user)).json()
    assert d["totaal"] == 3
    assert d["gestopt"] == 2
    assert d["veilig"] == 1
    assert d["zonder_maatregel"] == 2
    bovenaan = d["per_vraag"][0]
    assert bovenaan["code"] == "LMRA.OMGEVING"
    assert bovenaan["aantal_nee"] == 2


def test_detail_van_een_andere_organisatie_geeft_404(client, admin_user, viewer_user):
    lmra_id = _post(client, admin_user).json()["id"]
    db = SessionLocal()
    try:
        vreemd = Organization(name="Concurrent", max_users=5)
        db.add(vreemd)
        db.commit()
        from models import User as U
        from auth import hash_password
        buiten = U(email="buiten@concurrent.nl",
                   hashed_password=hash_password("test1234"),
                   first_name="Bui", last_name="Ten",
                   organization_id=vreemd.id, is_org_admin=True)
        db.add(buiten)
        db.commit()
        db.refresh(buiten)
    finally:
        db.close()

    r = client.get(f"/api/lmra/{lmra_id}", headers=auth(buiten))
    assert r.status_code == 404
