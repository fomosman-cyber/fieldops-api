"""Camera-instellingen van de schouw: bij de gebruiker, niet bij het toestel.

Vier dingen die eerder misgingen en hier vastliggen: de beeldgrootte werd
rijdend genegeerd, de zoom werd vergeten, de instellingen bleven achter op één
toestel, en het ritme gold niet waar het scherm suggereerde.
"""

import json

import pytest

import schouw_camera as sc
from database import SessionLocal
from models import Organization, User
from tests.conftest import auth


# ── De validatie ─────────────────────────────────────────────────────

def test_standaard_is_compleet_en_geldig():
    """Wat we als standaard uitdelen moet door de eigen keuring komen."""
    assert sc.valideer(sc.STANDAARD) == sc.STANDAARD


def test_onbekende_instelling_wordt_geweigerd():
    with pytest.raises(sc.OngeldigeInstelling):
        sc.valideer({"nachtstand": True})


def test_de_lens_hoort_hier_niet():
    """Een deviceId van de ene telefoon zegt op de andere niets, dus de lens
    gaat niet mee naar de server."""
    assert "lens" not in sc.STANDAARD
    with pytest.raises(sc.OngeldigeInstelling):
        sc.valideer({"lens": "abc123"})


@pytest.mark.parametrize("veld,waarde", [
    ("grootte", 4096),          # geen keuze die het scherm aanbiedt
    ("ritme", "elke-minuut"),
    ("rijAfstand", 3),
    ("wegdekBoven", 0.9),       # buiten de grens
    ("wegdekBoven", "veel"),
    ("zoom", 0),                # 0 is geen zoom, leeg is geen zoom
    ("zoom", 50),
    ("scherp", "ja"),
])
def test_waarden_buiten_de_lijst_worden_geweigerd(veld, waarde):
    with pytest.raises(sc.OngeldigeInstelling):
        sc.valideer({veld: waarde})


def test_zoom_mag_leeg_blijven():
    """Leeg betekent: de camera houdt zijn eigen zoom. Dat is iets anders dan
    een zoom van nul."""
    assert sc.valideer({"zoom": None})["zoom"] is None
    assert sc.valideer({"zoom": 2.5})["zoom"] == 2.5


def test_kapotte_opgeslagen_waarde_sloopt_de_schouw_niet():
    gebruiker = type("U", (), {"schouw_camera": "{kapot"})()
    assert sc.lees(gebruiker, None) == sc.STANDAARD


def test_verouderde_opgeslagen_waarde_valt_terug():
    """Een waarde die ooit geldig was maar nu niet meer (keuzelijst gewijzigd)
    mag geen zwart beeld opleveren."""
    gebruiker = type("U", (), {"schouw_camera": json.dumps({"grootte": 4096})})()
    assert sc.lees(gebruiker, None)["grootte"] == sc.STANDAARD["grootte"]


# ── Wie wint van wie ─────────────────────────────────────────────────

def test_eigen_keuze_gaat_voor_de_organisatie():
    org = type("O", (), {"schouw_camera_standaard": json.dumps(
        dict(sc.STANDAARD, grootte=960))})()
    gebruiker = type("U", (), {"schouw_camera": json.dumps(
        dict(sc.STANDAARD, grootte=1920))})()
    assert sc.lees(gebruiker, org)["grootte"] == 1920


def test_zonder_eigen_keuze_geldt_de_organisatie():
    org = type("O", (), {"schouw_camera_standaard": json.dumps(
        dict(sc.STANDAARD, grootte=1920, rijAfstand=5))})()
    gebruiker = type("U", (), {"schouw_camera": None})()
    gelezen = sc.lees(gebruiker, org)
    assert gelezen["grootte"] == 1920 and gelezen["rijAfstand"] == 5


def test_zonder_iets_geldt_de_standaard():
    assert sc.lees(None, None) == sc.STANDAARD


# ── Via de API ───────────────────────────────────────────────────────

def test_instellingen_komen_terug_op_een_ander_toestel(client, admin_user):
    """Dit is waar het om begonnen was: een nieuwe telefoon begon weer blanco."""
    r = client.put("/api/schouw/camera", headers=auth(admin_user),
                   json=dict(sc.STANDAARD, grootte=1920, zoom=2.5, rijAfstand=5))
    assert r.status_code == 200, r.text
    assert r.json()["eigen"] is True

    # Ander toestel = nieuwe sessie zonder localStorage; alleen de server telt.
    opnieuw = client.get("/api/schouw/camera", headers=auth(admin_user)).json()
    assert opnieuw["instellingen"]["grootte"] == 1920
    assert opnieuw["instellingen"]["zoom"] == 2.5
    assert opnieuw["instellingen"]["rijAfstand"] == 5


def test_onzin_wordt_geweigerd_via_de_api(client, admin_user):
    r = client.put("/api/schouw/camera", headers=auth(admin_user),
                   json={"grootte": 4096})
    assert r.status_code == 400
    assert "grootte" in r.json()["detail"]


def test_terugzetten_laat_de_organisatiestandaard_weer_gelden(client, admin_user):
    client.put("/api/schouw/camera/standaard", headers=auth(admin_user),
               json=dict(sc.STANDAARD, grootte=1920))
    client.put("/api/schouw/camera", headers=auth(admin_user),
               json=dict(sc.STANDAARD, grootte=960))
    assert client.get("/api/schouw/camera",
                      headers=auth(admin_user)).json()["instellingen"]["grootte"] == 960

    r = client.delete("/api/schouw/camera", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["eigen"] is False
    assert r.json()["instellingen"]["grootte"] == 1920


def test_alleen_een_beheerder_zet_de_organisatiestandaard(client, technician_user):
    r = client.put("/api/schouw/camera/standaard", headers=auth(technician_user),
                   json=dict(sc.STANDAARD, grootte=1920))
    assert r.status_code == 403


def test_de_standaard_overschrijft_niemands_eigen_keuze(client, admin_user, technician_user):
    """Anders schuift halverwege een ronde de manier van opnemen onder iemand
    vandaan."""
    client.put("/api/schouw/camera", headers=auth(technician_user),
               json=dict(sc.STANDAARD, grootte=960))
    client.put("/api/schouw/camera/standaard", headers=auth(admin_user),
               json=dict(sc.STANDAARD, grootte=1920))

    van_hem = client.get("/api/schouw/camera", headers=auth(technician_user)).json()
    assert van_hem["instellingen"]["grootte"] == 960
    assert van_hem["organisatie_standaard"]["grootte"] == 1920


def test_organisaties_delen_geen_standaard(client, admin_user):
    client.put("/api/schouw/camera/standaard", headers=auth(admin_user),
               json=dict(sc.STANDAARD, grootte=1920))
    db = SessionLocal()
    try:
        from auth import hash_password
        from models import AccountStatus, SubscriptionPlan, UserRole
        andere = Organization(name="Andere Schouw BV", plan=SubscriptionPlan.PROFESSIONAL,
                              status=AccountStatus.ACTIVE, max_users=5)
        db.add(andere); db.commit(); db.refresh(andere)
        vreemde = User(email="vreemd@schouw.nl", hashed_password=hash_password("test1234"),
                       first_name="V", last_name="B", role=UserRole.ADMIN,
                       is_org_admin=True, organization_id=andere.id)
        db.add(vreemde); db.commit(); db.refresh(vreemde)
    finally:
        db.close()

    uit = client.get("/api/schouw/camera", headers=auth(vreemde)).json()
    assert uit["organisatie_standaard"] is None
    assert uit["instellingen"] == sc.STANDAARD


def test_zonder_schouwmodule_geen_camera_instellingen(client, admin_user):
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(
            Organization.id == admin_user.organization_id).first()
        org.enabled_modules = json.dumps(["dagboek"])
        db.commit()
    finally:
        db.close()
    assert client.get("/api/schouw/camera", headers=auth(admin_user)).status_code == 403


# ── Het scherm ───────────────────────────────────────────────────────

def _portaal() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "templates" / "portaal.html"
            ).read_text(encoding="utf-8")


def test_de_beeldgrootte_wordt_rijdend_niet_meer_genegeerd():
    """`_schouwCamEisen` zette rijdend 1920 hardgecodeerd, waardoor de keuze in
    het scherm niets deed. Deze guard houdt dat weg."""
    inhoud = _portaal()
    assert "_schouwRijdend() ? 1920" not in inhoud
    assert "var breed = Number(_schouwCam.grootte) || 1280;" in inhoud
    # In plaats daarvan waarschuwt het scherm wie te weinig detail kiest.
    assert "schouwGrootteWaarschuwing" in inhoud


def test_het_scherm_bewaart_en_haalt_op_bij_de_gebruiker():
    inhoud = _portaal()
    assert "/api/schouw/camera" in inhoud
    assert "_schouwCamLaad" in inhoud
    # De lens gaat niet mee naar de server.
    assert "SCHOUW_CAM_SERVERVELDEN" in inhoud
    velden = inhoud.split("var SCHOUW_CAM_SERVERVELDEN = ")[1].split("];")[0]
    assert "lens" not in velden
    assert "zoom" in velden


def test_de_standen_staan_uit_elkaar_in_het_scherm():
    """Ritme en 'elke X meter' stonden naast elkaar alsof ze om beurten
    golden; nu staat per kopje welke stand het betreft."""
    inhoud = _portaal()
    assert ">Lopend of staand</div>" in inhoud
    assert ">Rijdend</div>" in inhoud
    assert "Rijdend telt alleen de afstand" in inhoud


def test_de_organisatiestandaard_kan_ook_weer_weg(client, admin_user):
    """Wat je kunt zetten moet je ook kunnen loslaten, anders zit een
    organisatie vast aan een keuze uit een demo."""
    client.put("/api/schouw/camera/standaard", headers=auth(admin_user),
               json=dict(sc.STANDAARD, grootte=1920))
    r = client.delete("/api/schouw/camera/standaard", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["organisatie_standaard"] is None
    assert r.json()["instellingen"] == sc.STANDAARD


def test_alleen_een_beheerder_wist_de_organisatiestandaard(client, technician_user):
    assert client.delete("/api/schouw/camera/standaard",
                         headers=auth(technician_user)).status_code == 403
