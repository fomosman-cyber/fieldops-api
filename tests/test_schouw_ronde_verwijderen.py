"""Een schouwronde verwijderen.

Het endpoint bestond al, maar er was geen knop, dus in de praktijk kon niemand
een ronde weggooien. Hier ligt vast wat er bij het verwijderen wél en niet
verdwijnt, en dat de knop er is.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from database import SessionLocal
from models import (Melding, Organization, SchouwBeeld, SchouwOpname, Schouwrit,
                    Schouwwaarneming)
from tests.conftest import auth


def _ronde(org_id, user_id, *, gebied="Blijdorp", waarnemingen=2, beelden=3, opnames=1):
    """Een ronde met alles wat eraan kan hangen."""
    db = SessionLocal()
    try:
        rit = Schouwrit(organization_id=org_id, gebied=gebied, gebiedstype="woonwijk",
                        ambitie="B", created_by=user_id, inspecteur_id=user_id)
        db.add(rit); db.commit(); db.refresh(rit)
        for i in range(waarnemingen):
            db.add(Schouwwaarneming(schouwrit_id=rit.id, organization_id=org_id,
                                    detectieklasse="zwerfafval", meetlat="zwerfafval.woonwijk",
                                    waarde=float(i), bron="ai"))
        for i in range(beelden):
            db.add(SchouwBeeld(schouwrit_id=rit.id, organization_id=org_id))
        for i in range(opnames):
            db.add(SchouwOpname(schouwrit_id=rit.id, organization_id=org_id,
                                volgnummer=i + 1,
                                gemaakt_op=datetime.now(timezone.utc)))
        db.commit()
        return rit.id
    finally:
        db.close()


def _tel(model, rit_id):
    db = SessionLocal()
    try:
        return db.query(model).filter(model.schouwrit_id == rit_id).count()
    finally:
        db.close()


def test_een_ronde_verdwijnt_met_alles_wat_eraan_hangt(client, admin_user):
    rit_id = _ronde(admin_user.organization_id, admin_user.id)

    r = client.delete(f"/api/schouw/ritten/{rit_id}", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    uit = r.json()
    assert uit["status"] == "verwijderd"
    assert uit["waarnemingen"] == 2 and uit["beelden"] == 3 and uit["opnames"] == 1
    assert uit["gebied"] == "Blijdorp"

    db = SessionLocal()
    try:
        assert db.query(Schouwrit).filter(Schouwrit.id == rit_id).first() is None
    finally:
        db.close()


def test_de_waarnemingen_blijven_niet_achter(client, admin_user):
    """Dit ging mis: beelden en opnames werden expliciet opgeruimd, de
    waarnemingen niet. Op SQLite handhaaft niets de cascade, dus bleven ze als
    losse rijen staan -- onzichtbaar, want alles wordt per rit opgevraagd."""
    rit_id = _ronde(admin_user.organization_id, admin_user.id, waarnemingen=5)
    assert _tel(Schouwwaarneming, rit_id) == 5

    client.delete(f"/api/schouw/ritten/{rit_id}", headers=auth(admin_user))

    assert _tel(Schouwwaarneming, rit_id) == 0
    assert _tel(SchouwBeeld, rit_id) == 0
    assert _tel(SchouwOpname, rit_id) == 0


def test_een_melding_uit_een_waarneming_blijft_bestaan(client, admin_user):
    """De melding heeft een eigen opvolging en gooi je niet weg omdat de ronde
    waarin hij is gezien wordt opgeruimd. Zelfde keuze als bij projecten."""
    rit_id = _ronde(admin_user.organization_id, admin_user.id)
    db = SessionLocal()
    try:
        melding = Melding(title="Zwerfafval Blijdorp", organization_id=admin_user.organization_id,
                          created_by=admin_user.id)
        db.add(melding); db.commit(); db.refresh(melding)
        melding_id = melding.id
        w = db.query(Schouwwaarneming).filter(
            Schouwwaarneming.schouwrit_id == rit_id).first()
        w.melding_id = melding_id
        db.commit()
    finally:
        db.close()

    assert client.delete(f"/api/schouw/ritten/{rit_id}",
                         headers=auth(admin_user)).status_code == 200

    db = SessionLocal()
    try:
        assert db.query(Melding).filter(Melding.id == melding_id).first() is not None
    finally:
        db.close()


def test_een_lopende_ronde_mag_ook_weg(client, admin_user):
    """Een ronde die je per ongeluk bent gestart moet je kunnen opruimen zonder
    hem eerst te moeten afronden."""
    rit_id = _ronde(admin_user.organization_id, admin_user.id)
    db = SessionLocal()
    try:
        rit = db.query(Schouwrit).filter(Schouwrit.id == rit_id).first()
        assert rit.status == "bezig"
    finally:
        db.close()
    assert client.delete(f"/api/schouw/ritten/{rit_id}",
                         headers=auth(admin_user)).status_code == 200


def test_wie_geen_schouw_mag_uitvoeren_verwijdert_niets(client, admin_user, technician_user):
    rit_id = _ronde(admin_user.organization_id, admin_user.id)
    r = client.delete(f"/api/schouw/ritten/{rit_id}", headers=auth(technician_user))
    assert r.status_code == 403
    db = SessionLocal()
    try:
        assert db.query(Schouwrit).filter(Schouwrit.id == rit_id).first() is not None
    finally:
        db.close()


def test_een_ronde_van_een_andere_organisatie_bestaat_niet_voor_je(client, admin_user):
    rit_id = _ronde(admin_user.organization_id, admin_user.id)
    db = SessionLocal()
    try:
        from auth import hash_password
        from models import AccountStatus, SubscriptionPlan, User, UserRole
        andere = Organization(name="Andere Schouwer BV", plan=SubscriptionPlan.PROFESSIONAL,
                              status=AccountStatus.ACTIVE, max_users=5)
        db.add(andere); db.commit(); db.refresh(andere)
        vreemde = User(email="vreemd@rondes.nl", hashed_password=hash_password("test1234"),
                       first_name="V", last_name="B", role=UserRole.ADMIN,
                       is_org_admin=True, organization_id=andere.id)
        db.add(vreemde); db.commit(); db.refresh(vreemde)
    finally:
        db.close()

    assert client.delete(f"/api/schouw/ritten/{rit_id}",
                         headers=auth(vreemde)).status_code == 404
    db = SessionLocal()
    try:
        assert db.query(Schouwrit).filter(Schouwrit.id == rit_id).first() is not None
    finally:
        db.close()


def test_verwijderen_komt_in_de_audit(client, admin_user):
    rit_id = _ronde(admin_user.organization_id, admin_user.id)
    client.delete(f"/api/schouw/ritten/{rit_id}", headers=auth(admin_user))

    db = SessionLocal()
    try:
        from models import AuditLog
        regel = (db.query(AuditLog)
                   .filter(AuditLog.action == "schouw.delete",
                           AuditLog.entity_id == rit_id).first())
        assert regel is not None, "een onomkeerbare actie hoort in het logboek"
        details = json.loads(regel.details or "{}")
        assert details.get("extra", {}).get("waarnemingen") == 2
    finally:
        db.close()


# ── Het scherm ───────────────────────────────────────────────────────

def _portaal() -> str:
    return (Path(__file__).resolve().parent.parent / "templates" / "portaal.html"
            ).read_text(encoding="utf-8")


def test_er_is_een_knop_om_een_ronde_te_verwijderen():
    """Waar het om begonnen was: het endpoint bestond, de knop niet."""
    inhoud = _portaal()
    assert "schouwRondeVerwijderen" in inhoud
    assert "'/api/schouw/ritten/' + id" in inhoud
    assert 'id="schouwVerwijderBtn"' in inhoud


def test_de_knop_in_de_lijst_opent_de_ronde_niet():
    """De knop zit binnen de klikbare regel; zonder stopPropagation opent de
    ronde zich onder de bevestigingsvraag door."""
    inhoud = _portaal()
    assert "event.stopPropagation();schouwRondeVerwijderen(" in inhoud


def test_het_scherm_waarschuwt_voor_wat_er_verdwijnt():
    inhoud = _portaal()
    assert "Dit kan niet ongedaan worden gemaakt." in inhoud
    assert "herkenning leert" in inhoud
