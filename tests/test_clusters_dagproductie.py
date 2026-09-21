"""Clusters als werkdagen: ingedeeld op dagproductie en gemeten m², niet alleen straal.

Wat hier vastligt:

1. **Een cluster is een werkdag.** Zoveel meldingen van één werksoort als een
   ploeg op een dag haalt -- gerekend met de gemeten m² en de dagproductie.
2. **De eigen dagproductie telt.** Een organisatie met snellere ploegen krijgt
   vollere dagen.
3. **Meer dan een dag werk staat er eerlijk bij.** Een hele straat herstraten
   is één pakket van meer dan een werkdag, geen dagploeg.
4. **Afstand blijft een grens.** Twee meldingen aan weerszijden van de stad
   horen niet in dezelfde dag, ook al passen ze qua m².
5. **Geschatte maten worden niet als gemeten verkocht.**
6. **Alleen beheerder of manager stelt de dagproductie in.**
"""

import io
import json

from database import SessionLocal
from models import JobCluster, Melding, Organization
from orchestration import cluster_instellingen, generate_clusters, standaard_dagproductie

from .conftest import auth

KLINKERS = {"gw_term": "Herstraten elementen", "gw_maatregel": "Klinker-herstraten"}
METER_LAT = 1 / 111_320


def _meldingen(org, user, m2_lijst, *, noord_m=0.0, stap_m=20.0, **extra):
    db = SessionLocal()
    try:
        for i, m2 in enumerate(m2_lijst):
            db.add(Melding(
                title=f"Verzakking {i}", organization_id=org.id, created_by=user.id,
                status="open", lat=52.0 + (noord_m + i * stap_m) * METER_LAT, lng=4.2,
                norm_data_json=json.dumps({"oppervlakte_m2": m2}) if m2 else None,
                **{**KLINKERS, **extra}))
        db.commit()
    finally:
        db.close()


def _clusters(org_id):
    db = SessionLocal()
    try:
        return (db.query(JobCluster).filter(JobCluster.organization_id == org_id)
                  .order_by(JobCluster.eenheden).all())
    finally:
        db.close()


def _genereer(org_id, **kw):
    db = SessionLocal()
    try:
        org = db.get(Organization, org_id)
        return generate_clusters(db, org_id, radius_km=kw.pop("radius_km", 2.0),
                                 instellingen=cluster_instellingen(org), **kw)
    finally:
        db.close()


def test_kengetal_is_een_werkdag():
    # (8 uur - 1 uur opzet) / 0,08 uur per m² = 87,5 m² klinkers per dag.
    assert standaard_dagproductie("KLINKER_HERSTRATEN") == 87.5
    assert standaard_dagproductie("VULLEN_POLYMEER") == 280.0
    assert standaard_dagproductie("ONBEKEND") is None


def test_meldingen_worden_over_werkdagen_verdeeld(org, admin_user):
    _meldingen(org, admin_user, [30] * 6)          # 180 m², dagen van hooguit 87,5
    _genereer(org.id)
    clusters = _clusters(org.id)
    assert [c.melding_count for c in clusters] == [2, 2, 2]
    assert all(c.eenheden == 60 and c.eenheid == "m²" for c in clusters)
    assert all(c.dagproductie == 87.5 and round(c.werkdagen, 2) == 0.69 for c in clusters)
    assert all(c.gemeten_aandeel == 1.0 for c in clusters)


def test_eigen_dagproductie_geeft_vollere_dagen(client, org, admin_user):
    r = client.put("/api/clusters/instellingen", headers=auth(admin_user),
                   json={"dagproductie": {"KLINKER_HERSTRATEN": 100}})
    assert r.status_code == 200, r.text
    _meldingen(org, admin_user, [30] * 6)
    _genereer(org.id)
    assert [c.melding_count for c in _clusters(org.id)] == [3, 3]


def test_meer_dan_een_dag_werk_is_een_eigen_pakket(org, admin_user):
    _meldingen(org, admin_user, [200])
    _genereer(org.id)
    [c] = _clusters(org.id)
    assert c.melding_count == 1 and round(c.werkdagen, 2) == 2.29


def test_kleine_losse_melding_is_geen_cluster(org, admin_user):
    _meldingen(org, admin_user, [10])
    _genereer(org.id)
    assert _clusters(org.id) == []


def test_afstand_blijft_een_grens(org, admin_user):
    _meldingen(org, admin_user, [20, 20])                         # samen, dichtbij
    _meldingen(org, admin_user, [20, 20], noord_m=5000)           # 5 km verderop
    _genereer(org.id, radius_km=1.0)
    clusters = _clusters(org.id)
    assert [c.melding_count for c in clusters] == [2, 2]


def test_geschatte_maten_worden_niet_als_gemeten_verkocht(org, admin_user):
    _meldingen(org, admin_user, [None] * 4)
    _genereer(org.id)
    clusters = _clusters(org.id)
    assert clusters and all(c.gemeten_aandeel == 0.0 for c in clusters)


def test_straal_modus_is_het_oude_gedrag(org, admin_user):
    _meldingen(org, admin_user, [30] * 6)
    _genereer(org.id, modus="straal")
    [c] = _clusters(org.id)
    assert c.melding_count == 6 and c.werkdagen is None


def test_api_geeft_het_dagpakket_terug(client, org, admin_user):
    _meldingen(org, admin_user, [40, 40])
    r = client.post("/api/clusters/generate", headers=auth(admin_user), json={})
    assert r.status_code == 200, r.text
    [c] = client.get("/api/clusters", headers=auth(admin_user)).json()
    assert (c["eenheden"], c["eenheid"], c["dagproductie"]) == (80.0, "m²", 87.5)
    assert c["vulling_pct"] == 91


# ---------------------------------------------------------------------------
# Instellingen
# ---------------------------------------------------------------------------

def test_instellingen_zijn_niet_een_cluster_id(client, admin_user):
    r = client.get("/api/clusters/instellingen", headers=auth(admin_user))
    assert r.status_code == 200
    d = r.json()
    klinkers = [w for w in d["werksoorten"] if w["code"] == "KLINKER_HERSTRATEN"][0]
    assert (klinkers["standaard"], klinkers["eigen"], klinkers["eenheid"]) == (87.5, None, "m²")
    assert d["werkdag_uren"] == 8.0 and d["kan_wijzigen"] is True


def test_manager_mag_instellen_viewer_niet(client, manager_user, viewer_user):
    assert client.put("/api/clusters/instellingen", headers=auth(manager_user),
                      json={"werkdag_uren": 9}).status_code == 200
    assert client.put("/api/clusters/instellingen", headers=auth(viewer_user),
                      json={"werkdag_uren": 9}).status_code in (403,)


def test_ongeldige_instelling_wordt_geweigerd(client, admin_user):
    for body in ({"werkdag_uren": 20}, {"max_afstand_km": 0}, {"dagproductie": {"ONZIN": 5}},
                 {"dagproductie": {"KLINKER_HERSTRATEN": -3}}):
        assert client.put("/api/clusters/instellingen", headers=auth(admin_user),
                          json=body).status_code == 400, body


def test_leeg_zetten_is_terug_naar_het_kengetal(client, admin_user):
    client.put("/api/clusters/instellingen", headers=auth(admin_user),
               json={"dagproductie": {"KLINKER_HERSTRATEN": 120}})
    d = client.put("/api/clusters/instellingen", headers=auth(admin_user),
                   json={"dagproductie": {"KLINKER_HERSTRATEN": None}}).json()
    klinkers = [w for w in d["werksoorten"] if w["code"] == "KLINKER_HERSTRATEN"][0]
    assert klinkers["eigen"] is None and klinkers["dagproductie"] == 87.5


def test_langere_werkdag_verschuift_het_kengetal(client, admin_user):
    d = client.put("/api/clusters/instellingen", headers=auth(admin_user),
                   json={"werkdag_uren": 9}).json()
    klinkers = [w for w in d["werksoorten"] if w["code"] == "KLINKER_HERSTRATEN"][0]
    assert klinkers["standaard"] == 100.0                     # (9 - 1) / 0,08


# ---------------------------------------------------------------------------
# De kaart
# ---------------------------------------------------------------------------

def test_kaart_toont_elke_melding_als_eigen_stip():
    with io.open("templates/portaal.html", encoding="utf-8") as f:
        html = f.read()
    assert "markerClusterGroup" not in html and "leaflet.markercluster" not in html
    assert "L.circleMarker([m.lat, m.lng]" in html
    assert "L.canvas({ padding: 0.5, tolerance: 6 })" in html
