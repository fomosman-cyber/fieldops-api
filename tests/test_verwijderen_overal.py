"""Verwijderen: één regel voor alle modules (permissions.eis_verwijderen).

1. **Een lezer verwijdert nooit iets.**
2. **Een beheerder of projectleider mag alles verwijderen**, ook wat afgerond
   of ondertekend is -- dat laatste pas na een tweede bevestiging
   (``?bevestig=true``). Zonder die bevestiging een 409 die begint met
   "Bevestiging nodig", zodat het portaal het nog een keer kan vragen.
3. **Ieder ander verwijdert alleen wat hij zelf maakte, zolang het niet af is.**
4. **Het werkdagboek**: ook automatische regels kun je uit je eigen dagboek
   halen; bij materieel gaat de CO2-regel mee.
"""

from datetime import date

import pytest

from database import SessionLocal
from models import (AIAnalysis, Asset, DaybookEntry, Inspection, MaterieelInzet, Oplevering,
                    Toolbox, Werkplekinspectie)
from tests.conftest import auth


def _maak(model, **velden):
    db = SessionLocal()
    try:
        rij = model(**velden)
        db.add(rij)
        db.commit()
        return rij.id
    finally:
        db.close()


def _bestaat(model, rij_id) -> bool:
    db = SessionLocal()
    try:
        return db.query(model).filter(model.id == rij_id).first() is not None
    finally:
        db.close()


def _verwijder(client, user, pad, bevestig=False):
    return client.delete(pad + ("?bevestig=true" if bevestig else ""), headers=auth(user))


# ---------------------------------------------------------------------------
# Afgerond of ondertekend: beheerder na tweede bevestiging
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("soort", ["toolbox", "wpi", "oplevering", "kunstwerk", "ai"])
def test_afgerond_kan_weg_na_tweede_bevestiging(client, admin_user, org, soort):
    if soort == "toolbox":
        rid = _maak(Toolbox, organization_id=org.id, onderwerp="Werken langs de weg",
                    status="afgesloten", created_by=admin_user.id)
        pad, model = f"/api/toolbox/{rid}", Toolbox
    elif soort == "wpi":
        rid = _maak(Werkplekinspectie, organization_id=org.id, status="afgerond",
                    created_by=admin_user.id)
        pad, model = f"/api/wpi/{rid}", Werkplekinspectie
    elif soort == "oplevering":
        rid = _maak(Oplevering, organization_id=org.id, title="Fase 2", status="aanvaard",
                    created_by=admin_user.id)
        pad, model = f"/api/opleveringen/{rid}", Oplevering
    elif soort == "kunstwerk":
        asset = _maak(Asset, organization_id=org.id, name="Brug", code="KW-1", asset_type="brug",
                      created_by=admin_user.id)
        rid = _maak(Inspection, organization_id=org.id, asset_id=asset, kunstwerk_type="brug",
                    title="Inspectie brug", inspecteur_id=admin_user.id, status="signed",
                    created_by=admin_user.id)
        pad, model = f"/api/kunstwerken-inspecties/{rid}", Inspection
    else:
        from datetime import datetime, timezone
        rid = _maak(AIAnalysis, organization_id=org.id, prompt_version="v1", model_id="x",
                    created_by=admin_user.id, accepted_at=datetime.now(timezone.utc))
        pad, model = f"/api/inspecties/{rid}", AIAnalysis

    r = _verwijder(client, admin_user, pad)
    assert r.status_code == 409, r.text
    assert r.json()["detail"].startswith("Bevestiging nodig")
    assert _bestaat(model, rid)

    r = _verwijder(client, admin_user, pad, bevestig=True)
    assert r.status_code == 200, r.text
    assert not _bestaat(model, rid)


def test_projectleider_mag_ook_alles(client, admin_user, manager_user, org):
    rid = _maak(Oplevering, organization_id=org.id, title="Van een ander", status="opgeleverd",
                created_by=admin_user.id)
    assert _verwijder(client, manager_user, f"/api/opleveringen/{rid}", bevestig=True).status_code == 200


# ---------------------------------------------------------------------------
# Lezer en maker
# ---------------------------------------------------------------------------

def test_een_lezer_verwijdert_niets(client, admin_user, viewer_user, org):
    """Voorheen kon iedereen in de organisatie, ook een lezer, een oplevering
    weggooien: er stond geen enkele rolcontrole op."""
    rid = _maak(Oplevering, organization_id=org.id, title="Concept", status="concept",
                created_by=viewer_user.id)
    r = _verwijder(client, viewer_user, f"/api/opleveringen/{rid}", bevestig=True)
    assert r.status_code == 403
    assert _bestaat(Oplevering, rid)


def test_maker_verwijdert_eigen_concept_maar_niet_wat_af_is(client, admin_user, inspector_user, org):
    eigen = _maak(Oplevering, organization_id=org.id, title="Mijn concept", status="concept",
                  created_by=inspector_user.id)
    van_ander = _maak(Oplevering, organization_id=org.id, title="Van de beheerder", status="concept",
                      created_by=admin_user.id)
    af = _maak(Oplevering, organization_id=org.id, title="Mijn opgeleverde", status="opgeleverd",
               created_by=inspector_user.id)
    assert _verwijder(client, inspector_user, f"/api/opleveringen/{van_ander}").status_code == 403
    assert _verwijder(client, inspector_user, f"/api/opleveringen/{af}", bevestig=True).status_code == 403
    assert _verwijder(client, inspector_user, f"/api/opleveringen/{eigen}").status_code == 200


def test_ai_inspectie_weg_melding_blijft(client, admin_user, org):
    from models import Melding
    melding = _maak(Melding, title="Uit AI", organization_id=org.id, created_by=admin_user.id)
    rid = _maak(AIAnalysis, organization_id=org.id, prompt_version="v1", model_id="x",
                created_by=admin_user.id, melding_id=melding)
    assert _verwijder(client, admin_user, f"/api/inspecties/{rid}").status_code == 200
    assert not _bestaat(AIAnalysis, rid)
    assert _bestaat(Melding, melding)


def test_andere_organisatie_kan_niets_verwijderen(client, admin_user, org, platform_owner):
    rid = _maak(Oplevering, organization_id=org.id, title="Van ons", status="concept",
                created_by=admin_user.id)
    assert _verwijder(client, platform_owner, f"/api/opleveringen/{rid}", bevestig=True).status_code == 404
    ai = _maak(AIAnalysis, organization_id=org.id, prompt_version="v1", model_id="x",
               created_by=admin_user.id)
    assert _verwijder(client, platform_owner, f"/api/inspecties/{ai}", bevestig=True).status_code == 404
    assert _bestaat(Oplevering, rid) and _bestaat(AIAnalysis, ai)


# ---------------------------------------------------------------------------
# Werkdagboek
# ---------------------------------------------------------------------------

def test_automatische_dagboekregel_uit_je_eigen_dagboek(client, admin_user, technician_user, org):
    rid = _maak(DaybookEntry, user_id=technician_user.id, organization_id=org.id,
                entry_type="melding_created", source="auto", title="Melding aangemaakt")
    # Niet uit het dagboek van een ander.
    assert client.delete(f"/api/daybook/entries/{rid}", headers=auth(admin_user)).status_code == 403
    r = client.delete(f"/api/daybook/entries/{rid}", headers=auth(technician_user))
    assert r.status_code == 200, r.text


def test_materieelregel_in_het_dagboek_neemt_de_co2_mee(client, technician_user):
    r = client.post("/api/materieel/inzet", headers=auth(technician_user), json={
        "materieel_naam": "Kraan", "energiedrager": "diesel", "brandstof_hoeveelheid": 10,
        "datum": str(date(2026, 9, 16))})
    inzet = r.json()
    dag = client.get("/api/daybook/day?date=2026-09-16", headers=auth(technician_user)).json()
    spiegel = next(e for e in dag["entries"] if e["entry_type"] == "materieel_inzet")
    assert client.delete(f"/api/daybook/entries/{spiegel['id']}",
                         headers=auth(technician_user)).status_code == 200
    db = SessionLocal()
    try:
        assert db.query(MaterieelInzet).filter(MaterieelInzet.id == inzet["id"]).one().deleted_at
    finally:
        db.close()
    uit = client.get("/api/materieel/inzet?datum=2026-09-16", headers=auth(technician_user)).json()
    assert uit["aantal"] == 0


# ---------------------------------------------------------------------------
# Foreign keys: Postgres weigert een DELETE waar nog iets naar wijst
# ---------------------------------------------------------------------------
# SQLite controleert foreign keys standaard niet, Postgres (Render) wel. Met
# PRAGMA foreign_keys=ON gedraagt SQLite zich op dit punt zoals Postgres.

@pytest.fixture
def strenge_fk():
    from sqlalchemy import event
    from database import engine

    def aan(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    engine.dispose()
    event.listen(engine, "connect", aan)
    yield
    event.remove(engine, "connect", aan)
    engine.dispose()


def test_melding_met_verwijzingen_is_verwijderbaar(client, admin_user, org, strenge_fk):
    from models import Lmra, Melding
    melding = _maak(Melding, title="Met verwijzingen", organization_id=org.id, created_by=admin_user.id)
    ai = _maak(AIAnalysis, organization_id=org.id, prompt_version="v1", model_id="x",
               created_by=admin_user.id, melding_id=melding)
    lmra = _maak(Lmra, organization_id=org.id, melding_id=melding, created_by=admin_user.id)
    r = client.delete(f"/api/meldingen/{melding}", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert not _bestaat(Melding, melding)
    assert _bestaat(AIAnalysis, ai) and _bestaat(Lmra, lmra)      # losgemaakt, niet weg


def test_ondertekende_inspectie_die_laatste_van_het_kunstwerk_is(client, admin_user, org, strenge_fk):
    asset = _maak(Asset, organization_id=org.id, name="Viaduct", code="KW-2", asset_type="viaduct",
                  created_by=admin_user.id)
    rid = _maak(Inspection, organization_id=org.id, asset_id=asset, kunstwerk_type="viaduct",
                title="Laatste inspectie", inspecteur_id=admin_user.id, status="signed",
                created_by=admin_user.id)
    db = SessionLocal()
    try:
        db.query(Asset).filter(Asset.id == asset).update({Asset.last_inspection_id: rid})
        db.commit()
    finally:
        db.close()
    r = client.delete(f"/api/kunstwerken-inspecties/{rid}?bevestig=true", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert not _bestaat(Inspection, rid) and _bestaat(Asset, asset)
