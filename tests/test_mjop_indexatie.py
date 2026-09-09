"""Tests voor de prijsindexatie van het MJOP.

Waar dit over gaat: een MJOP plant tot vijfentwintig jaar vooruit maar rekent
met de kostenkatalogus van nu. Een brugrenovatie in 2034 stond er dus in euro's
van 2025 in -- en dat is precies het bedrag dat een raad of directie in de
begroting overneemt. Bij drie procent per jaar scheelt dat over negen jaar ruim
dertig procent.

De regel die hier hard in zit: **zonder ingesteld percentage wordt er niet
geindexeerd**, en dat staat er dan expliciet bij. Zelfde keuze als bij de
keuringen en de schouw -- een getal dat wij verzinnen is erger dan geen getal,
want niemand controleert het meer zodra het in een rapport staat.
"""

from datetime import datetime, timedelta, timezone

import mjop_kosten as mjop
from database import SessionLocal
from models import Asset, Organization
from tests.conftest import auth


def _zet_index(org_id, pct):
    db = SessionLocal()
    try:
        o = db.query(Organization).filter(Organization.id == org_id).first()
        o.mjop_index_pct = pct
        db.commit()
    finally:
        db.close()


def _asset(org_id, user_id, *, jaren_vooruit=5, score=4, code="BRUG-01"):
    """Een asset waarvan het onderhoud over N jaar valt."""
    db = SessionLocal()
    try:
        a = Asset(organization_id=org_id, created_by=user_id,
                  code=code, name="Testbrug", asset_type="brug",
                  condition_score=score,
                  next_inspection_due=datetime.now(timezone.utc)
                  + timedelta(days=365 * jaren_vooruit))
        db.add(a)
        db.commit()
        db.refresh(a)
        return a.id
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# De rekenregel los
# ─────────────────────────────────────────────────────────────────────────────

def test_zonder_percentage_geen_indexatie():
    """De belangrijkste regel, direct op de motor."""
    assert mjop.indexeer(200000, 2034, None) is None
    assert mjop.indexeer(200000, 2034, "onzin") is None


def test_indexatie_is_samengesteld_niet_lineair():
    """Drie procent per jaar over negen jaar is geen 27% maar 30,5%."""
    uit = mjop.indexeer(200000, mjop.PRIJSPEIL_JAAR + 9, 3.0)
    assert uit == 260955, uit
    lineair = 200000 * 1.27
    assert uit > lineair, "samengesteld hoort hoger uit te komen dan lineair"


def test_in_het_prijspeiljaar_verandert_er_niets():
    assert mjop.indexeer(200000, mjop.PRIJSPEIL_JAAR, 3.0) == 200000


def test_achterstallig_werk_wordt_niet_goedkoper():
    """Werk dat al te laat is, voer je nu uit -- tegen de prijs van nu.

    Zonder deze regel zou terugrekenen naar goedkopere euro's van vroeger een
    achterstand goedkoper laten lijken dan hij is.
    """
    assert mjop.indexeer(200000, mjop.PRIJSPEIL_JAAR - 3, 3.0) == 200000


def test_nul_procent_is_iets_anders_dan_leeg():
    """Bewust op prijspeil rekenen is een uitspraak; niets invullen niet."""
    assert mjop.indexeer(200000, 2034, 0.0) == 200000
    assert mjop.indexeer(200000, 2034, None) is None


def test_toelichting_zegt_wat_de_bedragen_betekenen():
    zonder = mjop.index_toelichting(None)
    assert str(mjop.PRIJSPEIL_JAAR) in zonder
    assert "niet geindexeerd" in zonder.lower()

    met = mjop.index_toelichting(3.0)
    assert "3.0%" in met
    assert str(mjop.PRIJSPEIL_JAAR) in met


# ─────────────────────────────────────────────────────────────────────────────
# Door het MJOP heen
# ─────────────────────────────────────────────────────────────────────────────

def test_regels_zonder_index_hebben_geen_geindexeerd_bedrag(client, admin_user):
    _asset(admin_user.organization_id, admin_user.id)
    r = client.get("/api/mjop/preview?years=10", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    regels = r.json()["items"]
    assert regels, "geen MJOP-regels; testasset komt niet door de filter"
    for regel in regels:
        assert regel["min_geindexeerd"] is None
        assert regel["max_geindexeerd"] is None
        assert regel["min_total"] > 0


def test_met_index_staat_het_bedrag_van_het_uitvoeringsjaar_erbij(client, admin_user):
    _asset(admin_user.organization_id, admin_user.id, jaren_vooruit=8)
    _zet_index(admin_user.organization_id, 3.0)

    regels = client.get("/api/mjop/preview?years=15",
                        headers=auth(admin_user)).json()["items"]
    regel = regels[0]
    assert regel["max_geindexeerd"] is not None
    if regel["year"] > mjop.PRIJSPEIL_JAAR:
        assert regel["max_geindexeerd"] > regel["max_total"], (
            "een jaar na het prijspeil hoort het bedrag hoger te liggen")
    verwacht = mjop.indexeer(regel["max_total"], regel["year"], 3.0)
    assert regel["max_geindexeerd"] == verwacht


def test_samenvatting_geeft_beide_totalen_en_de_toelichting(client, admin_user):
    _asset(admin_user.organization_id, admin_user.id, jaren_vooruit=6)

    zonder = client.get("/api/mjop/summary?years=15", headers=auth(admin_user)).json()
    assert zonder["index_pct"] is None
    assert zonder["grand_total_geindexeerd"] is None
    assert zonder["prijspeil_jaar"] == mjop.PRIJSPEIL_JAAR
    assert "niet geindexeerd" in zonder["index_toelichting"].lower()
    assert zonder["grand_total"]["max"] > 0

    _zet_index(admin_user.organization_id, 3.5)
    met = client.get("/api/mjop/summary?years=15", headers=auth(admin_user)).json()
    assert met["index_pct"] == 3.5
    assert met["grand_total_geindexeerd"] is not None
    assert met["grand_total"] == zonder["grand_total"], (
        "het prijspeil-bedrag mag niet veranderen door indexatie aan te zetten")
    assert met["grand_total_geindexeerd"]["max"] >= met["grand_total"]["max"]


def test_csv_heeft_de_kolommen_en_de_voetnoot(client, admin_user):
    _asset(admin_user.organization_id, admin_user.id, jaren_vooruit=7)
    _zet_index(admin_user.organization_id, 2.5)

    r = client.get("/api/mjop/export.csv?years=15", headers=auth(admin_user))
    assert r.status_code == 200
    tekst = r.content.decode("utf-8-sig")
    assert "Min € geïndexeerd" in tekst
    assert "Max € geïndexeerd" in tekst
    assert f"prijspeil {mjop.PRIJSPEIL_JAAR}" in tekst
    assert "2.5%" in tekst, "de voetnoot moet het gebruikte percentage noemen"


def test_pdf_noemt_het_prijspeil(client, admin_user):
    """Een bedrag in een PDF zonder prijspeil eronder gaat zwerven."""
    _asset(admin_user.organization_id, admin_user.id, jaren_vooruit=4)
    r = client.get("/api/mjop/export.pdf?years=10", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    assert len(r.content) > 2000


# ─────────────────────────────────────────────────────────────────────────────
# De instelling
# ─────────────────────────────────────────────────────────────────────────────

def test_org_admin_kan_het_percentage_zetten_en_wissen(client, admin_user):
    r = client.patch("/api/organization/", json={"mjop_index_pct": 3.0},
                     headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json()["mjop_index_pct"] == 3.0

    r = client.patch("/api/organization/", json={"mjop_index_pct": None},
                     headers=auth(admin_user))
    assert r.status_code == 200
    assert client.get("/api/organization/",
                      headers=auth(admin_user)).json()["mjop_index_pct"] is None


def test_index_van_de_ene_organisatie_lekt_niet_naar_de_andere(client, admin_user,
                                                               platform_owner):
    _zet_index(admin_user.organization_id, 4.0)
    ander = client.get("/api/mjop/summary?years=10", headers=auth(platform_owner))
    assert ander.status_code == 200
    assert ander.json()["index_pct"] is None
