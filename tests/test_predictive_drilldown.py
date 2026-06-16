"""Tests voor Voorspeller drill-down + per-project filter (bug #13).

Mo's bug: klikken op een asset in de Voorspeller gaf alleen de asset, niet de
meldingen die erop zitten. De drill-down haalt meldingen op via
GET /api/meldingen/?asset_id=... en de Voorspeller filtert per project via
GET /api/predictive/at-risk?project_id=...
"""
from datetime import datetime, timezone, timedelta

from database import SessionLocal
from models import Asset, Melding, Project
from tests.conftest import auth


def _project(org, admin_user, name="P"):
    db = SessionLocal()
    try:
        p = Project(name=name, organization_id=org.id, created_by=admin_user.id)
        db.add(p); db.commit(); db.refresh(p)
        return p
    finally:
        db.close()


def _asset(org, admin_user, *, code, project_id=None, old=False):
    db = SessionLocal()
    try:
        a = Asset(code=code, asset_type="put", organization_id=org.id,
                  created_by=admin_user.id, project_id=project_id)
        if old:  # oud + slechte conditie → komt boven elke drempel uit
            a.installed_at = datetime.now(timezone.utc) - timedelta(days=365 * 19)
            a.expected_lifespan_years = 20
            a.condition_score = 5
        db.add(a); db.commit(); db.refresh(a)
        return a
    finally:
        db.close()


def _melding(org, admin_user, *, asset_id, title):
    db = SessionLocal()
    try:
        m = Melding(title=title, organization_id=org.id,
                    created_by=admin_user.id, asset_id=asset_id, status="open")
        db.add(m); db.commit(); db.refresh(m)
        return m
    finally:
        db.close()


def test_meldingen_filtered_by_asset(client, org, admin_user):
    """GET /api/meldingen/?asset_id=X geeft alleen meldingen van die asset."""
    a1 = _asset(org, admin_user, code="A1")
    a2 = _asset(org, admin_user, code="A2")
    _melding(org, admin_user, asset_id=a1.id, title="Scheur in put A1")
    _melding(org, admin_user, asset_id=a1.id, title="Verzakking A1")
    _melding(org, admin_user, asset_id=a2.id, title="Iets op A2")

    r = client.get(f"/api/meldingen/?asset_id={a1.id}", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 2
    titles = {m["title"] for m in items}
    assert titles == {"Scheur in put A1", "Verzakking A1"}


def test_meldingen_asset_filter_empty(client, org, admin_user):
    """Asset zonder meldingen geeft lege lijst (drill-down toont 'geen meldingen')."""
    a = _asset(org, admin_user, code="LEEG")
    r = client.get(f"/api/meldingen/?asset_id={a.id}", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_at_risk_filtered_by_project(client, org, admin_user):
    """GET /api/predictive/at-risk?project_id=X toont alleen assets van dat project."""
    p1 = _project(org, admin_user, "Project-1")
    p2 = _project(org, admin_user, "Project-2")
    _asset(org, admin_user, code="P1-OUD", project_id=p1.id, old=True)
    _asset(org, admin_user, code="P2-OUD", project_id=p2.id, old=True)

    r = client.get(f"/api/predictive/at-risk?min_score=0&project_id={p1.id}",
                   headers=auth(admin_user))
    assert r.status_code == 200, r.text
    codes = {item["asset_code"] for item in r.json()}
    assert "P1-OUD" in codes
    assert "P2-OUD" not in codes
