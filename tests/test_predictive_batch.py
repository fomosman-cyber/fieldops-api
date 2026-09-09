"""Tests voor de gebatchte risicoberekening van de Voorspeller.

`/api/predictive/summary` draait bij elke keer dat het dashboard opent, over
alle assets van de organisatie. Per asset werden de meldingen, de inspectie-
defecten en het buurt-signaal apart opgehaald: bij vijftig assets ruim
driehonderd queries, en dat groeide mee. Bij een klant met vijfduizend wegvakken
worden dat er tienduizenden -- op Postgres is elke query een round-trip.

Twee dingen worden hier bewaakt, en de eerste is de belangrijkste:

  1. **De uitkomst mag niet veranderen.** Een snellere weg die andere scores
     geeft is geen optimalisatie maar een bug. De test draait beide wegen over
     dezelfde assets en eist dat score, band, confidence en de volledige
     onderbouwing identiek zijn.
  2. Het aantal queries groeit niet mee met het aantal assets.
"""

from datetime import datetime, timedelta, timezone

import predictive as pr
from database import SessionLocal
from models import Asset, Melding
from tests.conftest import auth


def _asset(org_id, user_id, code, **kw):
    db = SessionLocal()
    try:
        a = Asset(organization_id=org_id, created_by=user_id, code=code,
                  name=kw.pop("name", code),
                  asset_type=kw.pop("asset_type", "wegvak"),
                  condition_score=kw.pop("condition_score", 3),
                  lat=kw.pop("lat", None), lng=kw.pop("lng", None),
                  installed_at=kw.pop("installed_at", None),
                  expected_lifespan_years=kw.pop("expected_lifespan_years", None),
                  **kw)
        db.add(a)
        db.commit()
        db.refresh(a)
        return a.id
    finally:
        db.close()


def _melding(org_id, user_id, asset_id, *, dagen_geleden=10, **kw):
    db = SessionLocal()
    try:
        m = Melding(organization_id=org_id, created_by=user_id, asset_id=asset_id,
                    title=kw.pop("title", "Melding"),
                    priority=kw.pop("priority", "normaal"),
                    status="open",
                    crow_klasse=kw.pop("crow_klasse", None),
                    lat=kw.pop("lat", None), lng=kw.pop("lng", None))
        m.created_at = datetime.now(timezone.utc) - timedelta(days=dagen_geleden)
        db.add(m)
        db.commit()
    finally:
        db.close()


def _bouw_gevarieerde_set(org_id, user_id):
    """Assets die alle takken van de score raken, niet alleen de makkelijke."""
    ids = []
    # Kaal: geen meldingen, geen leeftijd
    ids.append(_asset(org_id, user_id, "KAAL-01", condition_score=3))
    # Met leeftijd over de levensduur heen
    ids.append(_asset(org_id, user_id, "OUD-01", condition_score=4,
                      installed_at=datetime.now(timezone.utc) - timedelta(days=365 * 30),
                      expected_lifespan_years=20))
    # Met meldingen in beide trendvensters
    druk = _asset(org_id, user_id, "DRUK-01", condition_score=5,
                  lat=52.10, lng=4.30)
    for d, prio, klasse in ((5, "kritiek", "E3"), (20, "hoog", "M2"),
                            (40, "normaal", "L1"), (120, "laag", None),
                            (150, "normaal", "M1")):
        _melding(org_id, user_id, druk, dagen_geleden=d, priority=prio,
                 crow_klasse=klasse, lat=52.10, lng=4.30)
    ids.append(druk)
    # Buur met eigen meldingen, vlakbij -- voedt het buurt-signaal
    buur = _asset(org_id, user_id, "BUUR-01", condition_score=3,
                  lat=52.1005, lng=4.3005)
    for d in (3, 8, 12):
        _melding(org_id, user_id, buur, dagen_geleden=d, priority="hoog",
                 crow_klasse="M3", lat=52.1005, lng=4.3005)
    ids.append(buur)
    # Zonder coordinaten: buurt-signaal moet overgeslagen worden
    geen_geo = _asset(org_id, user_id, "GEENGEO-01", condition_score=4)
    _melding(org_id, user_id, geen_geo, dagen_geleden=15, priority="kritiek",
             crow_klasse="E1")
    ids.append(geen_geo)
    # Type met een eigen override-regel
    ids.append(_asset(org_id, user_id, "SPEEL-01", asset_type="speeltoestel",
                      condition_score=3, lat=52.11, lng=4.31))
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# De uitkomst mag niet veranderen
# ─────────────────────────────────────────────────────────────────────────────

def test_gebatcht_geeft_exact_hetzelfde_als_los(client, admin_user):
    """Een snellere weg die andere scores geeft is geen optimalisatie."""
    _bouw_gevarieerde_set(admin_user.organization_id, admin_user.id)

    db = SessionLocal()
    try:
        assets = (db.query(Asset)
                    .filter(Asset.organization_id == admin_user.organization_id,
                            Asset.archived_at.is_(None))
                    .order_by(Asset.code).all())
        assert len(assets) >= 6, "te weinig assets om iets te bewijzen"

        zonder = [pr.compute_asset_risk(db, a) for a in assets]
        ctx = pr.MeldingContext(db, [a.id for a in assets],
                                datetime.now(timezone.utc),
                                organization_id=admin_user.organization_id)
        met = [pr.compute_asset_risk(db, a, ctx) for a in assets]
    finally:
        db.close()

    for a, z, m in zip(assets, zonder, met):
        assert z["score"] == m["score"], f"{a.code}: score {z['score']} -> {m['score']}"
        assert z["band"] == m["band"], f"{a.code}: band"
        assert z["confidence"] == m["confidence"], f"{a.code}: confidence"
        # De onderbouwing is wat de inspecteur leest; die mag ook niet schuiven.
        assert z.get("rationale") == m.get("rationale"), f"{a.code}: rationale"
        assert z.get("geo_signal") == m.get("geo_signal"), f"{a.code}: buurt-signaal"


def test_gedeelde_rekenregels_hebben_maar_een_bron():
    """De drempels mogen niet in twee kopieen bestaan.

    Anders scoort dezelfde boom op het dashboard anders dan in de drilldown,
    en dat merk je pas als iemand het verschil ziet.
    """
    assert pr._trend_punten(0, 5) == 0
    assert pr._trend_punten(3, 0) > 0
    assert pr._ergste_klasse(["L1", "E3", "M2"]) == "E3"
    assert pr._ergste_klasse(["onzin", None]) is None
    assert pr._inspectie_punten([]) == (0, 0, 0)


# ─────────────────────────────────────────────────────────────────────────────
# En het moet ook echt schalen
# ─────────────────────────────────────────────────────────────────────────────

def test_summary_schaalt_niet_met_het_aantal_assets(client, admin_user):
    """Vangnet tegen een N+1 die terugsluipt."""
    from sqlalchemy import event
    from database import engine

    kop = auth(admin_user)

    def tel():
        aantal = {"n": 0}

        def _t(conn, cursor, statement, parameters, context, executemany):
            aantal["n"] += 1

        client.get("/api/predictive/summary", headers=kop)   # opwarmen
        event.listen(engine, "before_cursor_execute", _t)
        try:
            r = client.get("/api/predictive/summary", headers=kop)
        finally:
            event.remove(engine, "before_cursor_execute", _t)
        assert r.status_code == 200, r.text
        return aantal["n"], r.json()

    _bouw_gevarieerde_set(admin_user.organization_id, admin_user.id)
    klein, body_klein = tel()

    for i in range(20):
        aid = _asset(admin_user.organization_id, admin_user.id, f"EXTRA-{i:02d}",
                     condition_score=4, lat=52.12 + i / 1000, lng=4.32)
        _melding(admin_user.organization_id, admin_user.id, aid,
                 dagen_geleden=7, priority="hoog", crow_klasse="M2",
                 lat=52.12 + i / 1000, lng=4.32)
    groot, body_groot = tel()

    assert body_groot["total_assets"] > body_klein["total_assets"]
    assert groot == klein, (
        f"{klein} queries bij {body_klein['total_assets']} assets, "
        f"{groot} bij {body_groot['total_assets']} -- dat groeit mee, "
        "dus er is een N+1 terug")


def test_ranglijst_gebruikt_ook_de_batch(client, admin_user):
    """list_at_risk draait dezelfde lus en had hetzelfde probleem."""
    from sqlalchemy import event
    from database import engine

    _bouw_gevarieerde_set(admin_user.organization_id, admin_user.id)
    kop = auth(admin_user)

    def tel_ranglijst():
        aantal = {"n": 0}

        def _t(conn, cursor, statement, parameters, context, executemany):
            aantal["n"] += 1

        client.get("/api/predictive/at-risk?limit=50", headers=kop)
        event.listen(engine, "before_cursor_execute", _t)
        try:
            r = client.get("/api/predictive/at-risk?limit=50", headers=kop)
        finally:
            event.remove(engine, "before_cursor_execute", _t)
        return aantal["n"], r

    klein, r1 = tel_ranglijst()
    assert r1.status_code == 200, r1.text

    for i in range(15):
        _asset(admin_user.organization_id, admin_user.id, f"RANG-{i:02d}",
               condition_score=5, lat=52.13, lng=4.33)
    groot, r2 = tel_ranglijst()
    assert r2.status_code == 200
    assert groot == klein, (
        f"ranglijst: {klein} queries klein, {groot} groot -- groeit mee")


def test_lege_organisatie_valt_niet_om(client, admin_user):
    """Nul assets mag geen lege IN-clausule of deling door nul geven."""
    r = client.get("/api/predictive/summary", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["total_assets"] == 0

    db = SessionLocal()
    try:
        ctx = pr.MeldingContext(db, [], datetime.now(timezone.utc),
                                organization_id=admin_user.organization_id)
        assert ctx.geo == []
        assert ctx.defecten == {}
    finally:
        db.close()
