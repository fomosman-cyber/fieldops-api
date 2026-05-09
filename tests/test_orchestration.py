"""Job Orchestration Engine — clustering + skills + savings."""

import pytest
from database import SessionLocal
from models import Melding, UserSkill, JobCluster
from orchestration import (
    generate_clusters, users_with_skill, maatregel_specialists,
    haversine_km,
)
from crow_kosten import (
    SKILL_CODES, MAATREGEL_TO_SKILL, maatregel_to_skill,
    estimate_cluster_hours,
)
from tests.conftest import auth


def test_skill_catalog_complete():
    assert "VULLEN_POLYMEER" in SKILL_CODES
    assert "VOEGVULLING" in SKILL_CODES
    assert "ASFALT_DEKLAAG" in SKILL_CODES
    assert len(SKILL_CODES) >= 14


def test_maatregel_to_skill_mapping():
    assert maatregel_to_skill("Vullen polymeer") == "VULLEN_POLYMEER"
    assert maatregel_to_skill("Voegvulling") == "VOEGVULLING"
    assert maatregel_to_skill("Slembehandeling") == "SLEMBEHANDELING"
    assert maatregel_to_skill(None) is None
    assert maatregel_to_skill("Onbekende techniek") is None


def test_haversine_distance():
    # Amsterdam → Rotterdam ≈ 60 km
    d = haversine_km(52.37, 4.90, 51.92, 4.48)
    assert 50 < d < 70


def test_estimate_cluster_hours_savings():
    """Bij een cluster van 50m¹ scheurvulling moet baseline > clustered."""
    clustered, baseline = estimate_cluster_hours("VULLEN_POLYMEER", 50)
    assert clustered > 0
    assert baseline > clustered  # clustering bespaart tijd


def test_skills_catalog_endpoint(client, admin_user):
    r = client.get("/api/skills/catalog", headers=auth(admin_user))
    assert r.status_code == 200
    data = r.json()
    assert any(s["code"] == "VULLEN_POLYMEER" for s in data)


def test_my_skills_set_and_get(client, inspector_user):
    # Set
    r = client.put("/api/users/me/skills",
                   json={"skills": [
                       {"skill_code": "VULLEN_POLYMEER", "proficiency": 4},
                       {"skill_code": "VOEGVULLING", "proficiency": 3},
                   ]},
                   headers=auth(inspector_user))
    assert r.status_code == 200
    # Get
    r = client.get("/api/users/me/skills", headers=auth(inspector_user))
    assert r.status_code == 200
    skills = r.json()
    assert len(skills) == 2
    codes = {s["skill_code"] for s in skills}
    assert "VULLEN_POLYMEER" in codes


def test_set_skills_unknown_code_ignored(client, inspector_user):
    r = client.put("/api/users/me/skills",
                   json={"skills": [
                       {"skill_code": "NIET_BESTAAND", "proficiency": 3},
                       {"skill_code": "VULLEN_POLYMEER", "proficiency": 5},
                   ]},
                   headers=auth(inspector_user))
    assert r.status_code == 200
    r = client.get("/api/users/me/skills", headers=auth(inspector_user))
    skills = r.json()
    assert len(skills) == 1
    assert skills[0]["skill_code"] == "VULLEN_POLYMEER"


def test_users_with_skill(org, admin_user, inspector_user):
    db = SessionLocal()
    try:
        # Geef inspector de skill
        db.add(UserSkill(user_id=inspector_user.id, skill_code="VULLEN_POLYMEER", proficiency=4))
        db.commit()
        users = users_with_skill(db, org.id, "VULLEN_POLYMEER")
        emails = [u.email for u in users]
        assert inspector_user.email in emails
    finally:
        db.close()


def test_cluster_generation_creates_clusters(client, org, admin_user):
    """Genereer clusters uit 4 open meldingen met dezelfde maatregel."""
    db = SessionLocal()
    try:
        # 4 meldingen Vullen polymeer in Amsterdam-area
        for i in range(4):
            m = Melding(
                title=f"Scheur #{i}",
                organization_id=org.id, created_by=admin_user.id,
                status="open",
                lat=52.37 + i * 0.001, lng=4.90 + i * 0.001,
                gw_term="Vullen polymeer (cold-pour)",
                gw_maatregel="Vullen polymeer",
                crow_klasse="M2",
                onderhoud_categorie="KO",
            )
            db.add(m)
        db.commit()
    finally:
        db.close()

    r = client.post("/api/clusters/generate",
                    json={"radius_km": 5.0, "min_cluster_size": 2},
                    headers=auth(admin_user))
    assert r.status_code == 200
    summary = r.json()
    assert summary["clusters_created"] >= 1
    assert summary["meldingen_clustered"] >= 4
    assert summary["total_savings_hours"] > 0


def test_cluster_assign_cascades_to_meldingen(client, org, admin_user, inspector_user):
    db = SessionLocal()
    try:
        # 2 meldingen voorbereiden
        for i in range(2):
            m = Melding(
                title=f"Voeg #{i}",
                organization_id=org.id, created_by=admin_user.id,
                status="open",
                lat=52.0 + i * 0.001, lng=5.0,
                gw_term="Voegvulling lassen",
                gw_maatregel="Voegvulling",
                crow_klasse="M2",
                onderhoud_categorie="KO",
            )
            db.add(m)
        db.commit()
    finally:
        db.close()

    # Generate
    r = client.post("/api/clusters/generate", json={},
                    headers=auth(admin_user))
    assert r.status_code == 200
    # Lijst clusters → pak één
    r = client.get("/api/clusters", headers=auth(admin_user))
    clusters = r.json()
    assert len(clusters) > 0
    cluster_id = clusters[0]["id"]

    # Wijs toe aan inspector
    r = client.patch(f"/api/clusters/{cluster_id}/assign",
                     json={"user_id": inspector_user.id},
                     headers=auth(admin_user))
    assert r.status_code == 200
    # Check cascade: meldingen.assigned_to is gezet
    db = SessionLocal()
    try:
        meldingen = db.query(Melding).filter(Melding.job_cluster_id == cluster_id).all()
        for m in meldingen:
            assert m.assigned_to == inspector_user.id
    finally:
        db.close()


def test_savings_dashboard_endpoint(client, admin_user):
    r = client.get("/api/orchestration/savings", headers=auth(admin_user))
    assert r.status_code == 200
    data = r.json()
    # Met of zonder clusters: structuur moet kloppen
    assert "total_clusters" in data
    assert "total_savings_hours" in data
    assert "total_savings_euro" in data
    assert "by_skill" in data


def test_my_assigned_clusters_for_assignee(client, inspector_user):
    """Een inspector ziet z'n eigen toegewezen clusters."""
    r = client.get("/api/users/me/clusters", headers=auth(inspector_user))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_viewer_cannot_generate_clusters(client, viewer_user):
    r = client.post("/api/clusters/generate", json={},
                    headers=auth(viewer_user))
    assert r.status_code == 403
