"""Regressietest voor het demo-seed-script.

Borgt dat seed_demo_org tegen het huidige datamodel blijft werken en echt
idempotent is (een tweede run maakt geen duplicaten). Ruimt zichzelf op zodat
de 'Demo Gemeente'-org andere tests niet beinvloedt.
"""
from database import SessionLocal
from models import Asset, Inspection, Melding, Oplevering, Organization, User


def _run_seed(seed):
    db = SessionLocal()
    try:
        org = seed.get_or_create_org(db)
        users = seed.seed_users(db, org)
        projects = seed.seed_projects(db, org, users["admin"])
        assets = seed.seed_assets(db, org, users["admin"], projects)
        seed.seed_meldingen(db, org, users, assets)
        seed.seed_inspecties(db, org, users, assets)
        seed.seed_opleveringen(db, org, users, projects, assets)
    finally:
        db.close()


def _counts(seed):
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.name == seed.ORG_NAME).first()
        return {
            m.__name__: db.query(m).filter(m.organization_id == org.id).count()
            for m in (User, Asset, Melding, Inspection, Oplevering)
        }
    finally:
        db.close()


def test_seed_demo_org_counts_and_idempotent(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", "SeedTestPw123!")
    import seed_demo_org as seed

    try:
        _run_seed(seed)
        first = _counts(seed)
        assert first == {
            "User": 4, "Asset": 50, "Melding": 30, "Inspection": 10, "Oplevering": 4,
        }, first

        # Tweede run mag geen duplicaten opleveren (echt idempotent).
        _run_seed(seed)
        assert _counts(seed) == first

        # Geen demo-data lekt naar andere organisaties.
        db = SessionLocal()
        try:
            org = db.query(Organization).filter(Organization.name == seed.ORG_NAME).first()
            assert db.query(Asset).filter(Asset.organization_id != org.id).count() == 0
        finally:
            db.close()
    finally:
        # Opruimen: verwijder de demo-org volledig.
        db = SessionLocal()
        try:
            org = db.query(Organization).filter(Organization.name == seed.ORG_NAME).first()
            if org:
                seed.reset_demo_org(db, org)
                db.delete(org)
                db.commit()
        finally:
            db.close()
