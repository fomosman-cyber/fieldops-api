"""Hard delete + soft archive — assets / projects / meldingen."""

from database import SessionLocal
from models import Asset, Project, Melding, AIAnalysis
from tests.conftest import auth


def test_asset_soft_archive(client, admin_user, org):
    """DELETE zonder ?hard=true → soft archive (archived_at gezet)."""
    db = SessionLocal()
    try:
        a = Asset(code="X-SOFT", asset_type="put",
                  organization_id=org.id, created_by=admin_user.id)
        db.add(a); db.commit(); db.refresh(a)
        aid = a.id
    finally:
        db.close()

    r = client.delete(f"/api/assets/{aid}", headers=auth(admin_user))
    assert r.status_code == 200
    body = r.json()
    assert body.get("deleted") is False

    # Asset bestaat nog, maar archived_at is gezet
    db = SessionLocal()
    try:
        rec = db.query(Asset).filter(Asset.id == aid).first()
        assert rec is not None
        assert rec.archived_at is not None
    finally:
        db.close()


def test_asset_hard_delete_with_cascade_orphan(client, admin_user, org):
    """DELETE ?hard=true → asset weg, meldingen + ai_analyses + children unlinked."""
    db = SessionLocal()
    try:
        parent = Asset(code="HARD-PARENT", asset_type="put",
                       organization_id=org.id, created_by=admin_user.id)
        db.add(parent); db.commit(); db.refresh(parent)
        parent_id = parent.id

        child = Asset(code="HARD-CHILD", asset_type="put",
                      parent_asset_id=parent_id,
                      organization_id=org.id, created_by=admin_user.id)
        db.add(child); db.commit()

        m = Melding(title="Hangt aan parent",
                    organization_id=org.id, created_by=admin_user.id,
                    asset_id=parent_id)
        db.add(m); db.commit()
        melding_id = m.id

        ai = AIAnalysis(asset_id=parent_id,
                        prompt_version="v1", model_id="stub",
                        organization_id=org.id, created_by=admin_user.id)
        db.add(ai); db.commit()
        ai_id = ai.id
    finally:
        db.close()

    r = client.delete(f"/api/assets/{parent_id}?hard=true", headers=auth(admin_user))
    assert r.status_code == 200
    body = r.json()
    assert body.get("deleted") is True
    assert body["orphaned"]["meldingen"] == 1
    assert body["orphaned"]["ai_analyses"] == 1
    assert body["orphaned"]["children"] == 1

    # Verifieer cascading
    db = SessionLocal()
    try:
        # Asset zelf is weg
        assert db.query(Asset).filter(Asset.id == parent_id).first() is None
        # Child bestaat nog, maar parent_asset_id is null
        ch = db.query(Asset).filter(Asset.code == "HARD-CHILD").first()
        assert ch is not None
        assert ch.parent_asset_id is None
        # Melding bestaat nog, maar asset_id is null
        m = db.query(Melding).filter(Melding.id == melding_id).first()
        assert m is not None
        assert m.asset_id is None
        # AIAnalysis bestaat nog, maar asset_id is null
        ai = db.query(AIAnalysis).filter(AIAnalysis.id == ai_id).first()
        assert ai is not None
        assert ai.asset_id is None
    finally:
        db.close()


def test_project_hard_delete_orphans_meldingen(client, admin_user, org):
    """Project ?hard=true → project weg, meldingen.project_id=null."""
    db = SessionLocal()
    try:
        p = Project(name="HARD-PROJ", organization_id=org.id, created_by=admin_user.id)
        db.add(p); db.commit(); db.refresh(p)
        pid = p.id
        m = Melding(title="In project", organization_id=org.id,
                    created_by=admin_user.id, project_id=pid)
        db.add(m); db.commit()
        melding_id = m.id
    finally:
        db.close()

    r = client.delete(f"/api/projects/{pid}?hard=true", headers=auth(admin_user))
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] is True
    assert body["orphaned"]["meldingen"] == 1

    db = SessionLocal()
    try:
        assert db.query(Project).filter(Project.id == pid).first() is None
        m = db.query(Melding).filter(Melding.id == melding_id).first()
        assert m is not None
        assert m.project_id is None
    finally:
        db.close()


def test_project_soft_archive_default(client, admin_user, org):
    """DELETE op project zonder ?hard → status='archived'."""
    db = SessionLocal()
    try:
        p = Project(name="SOFT-ARCH", organization_id=org.id, created_by=admin_user.id)
        db.add(p); db.commit(); db.refresh(p)
        pid = p.id
    finally:
        db.close()

    r = client.delete(f"/api/projects/{pid}", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json().get("deleted") is False

    db = SessionLocal()
    try:
        rec = db.query(Project).filter(Project.id == pid).first()
        assert rec is not None
        assert rec.status == "archived"
    finally:
        db.close()


def test_non_admin_cannot_hard_delete(client, viewer_user, admin_user, org):
    db = SessionLocal()
    try:
        a = Asset(code="VIEWER-CANT", asset_type="put",
                  organization_id=org.id, created_by=admin_user.id)
        db.add(a); db.commit(); db.refresh(a)
        aid = a.id
    finally:
        db.close()
    r = client.delete(f"/api/assets/{aid}?hard=true", headers=auth(viewer_user))
    assert r.status_code == 403


def test_delete_asset_other_org_404(client, admin_user, platform_owner):
    """Admin van org A kan geen asset van platform-owner-org verwijderen."""
    db = SessionLocal()
    try:
        a = Asset(code="OTHER-ORG", asset_type="put",
                  organization_id=platform_owner.organization_id,
                  created_by=platform_owner.id)
        db.add(a); db.commit(); db.refresh(a)
        aid = a.id
    finally:
        db.close()
    r = client.delete(f"/api/assets/{aid}?hard=true", headers=auth(admin_user))
    assert r.status_code == 404
