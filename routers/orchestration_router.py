"""Job Orchestration Engine endpoints (sinds v3.0).

  GET    /api/clusters                Lijst van job-clusters per organisatie
  POST   /api/clusters/generate       Trigger clustering op open meldingen
  GET    /api/clusters/{id}           Detail van één cluster + meldingen
  PATCH  /api/clusters/{id}/assign    Wijs cluster toe aan een gebruiker
  PATCH  /api/clusters/{id}/status    Update status (proposed/assigned/in_progress/done)
  DELETE /api/clusters/{id}           Verwijder cluster (en ontkoppel meldingen)

  GET    /api/users/me/clusters       Mijn toegewezen clusters (voor uitvoerder)
  GET    /api/users/me/skills         Mijn skills
  PUT    /api/users/me/skills         Update mijn skills (admin/manager kan ook anderen)
  GET    /api/skills/catalog          Alle beschikbare skill-codes + namen

  GET    /api/orchestration/savings   Productiviteit-savings dashboard (ROI)
"""

from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import User, JobCluster, UserSkill, Melding
from auth import get_current_user
from permissions import UserRole, require_org_admin
from audit import log_action, ACTION
from orchestration import (
    generate_clusters,
    assign_cluster,
    my_clusters,
    cluster_summary,
)
from crow_kosten import SKILL_CODES, MAATREGEL_TO_SKILL

router = APIRouter(prefix="/api", tags=["Job Orchestration"])


# ════════════════════════════════════════════════════════════
# Pydantic schemas
# ════════════════════════════════════════════════════════════

class ClusterAssignRequest(BaseModel):
    user_id: str
    planned_date: Optional[datetime] = None


class ClusterStatusRequest(BaseModel):
    status: str = Field(..., pattern="^(proposed|assigned|in_progress|done|cancelled)$")


class GenerateClustersRequest(BaseModel):
    radius_km: float = Field(default=5.0, ge=0.5, le=50.0)
    min_cluster_size: int = Field(default=2, ge=1, le=20)


class UserSkillItem(BaseModel):
    skill_code: str
    proficiency: int = Field(default=3, ge=1, le=5)


class UserSkillsRequest(BaseModel):
    skills: List[UserSkillItem]


# ════════════════════════════════════════════════════════════
# Cluster endpoints
# ════════════════════════════════════════════════════════════

@router.get("/clusters")
def list_clusters(
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lijst alle clusters in mijn org. Optioneel filter op status."""
    q = db.query(JobCluster).filter(JobCluster.organization_id == current_user.organization_id)
    if status:
        q = q.filter(JobCluster.status == status)
    clusters = q.order_by(JobCluster.created_at.desc()).all()
    return [cluster_summary(c) for c in clusters]


@router.post("/clusters/generate")
def generate(
    payload: GenerateClustersRequest = GenerateClustersRequest(),
    request: Request = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genereer clusters op basis van open meldingen met CROW-classificatie.

    Alleen org-admins / managers / contractors kunnen dit triggeren.
    """
    if current_user.role not in (UserRole.ADMIN, UserRole.MANAGER, UserRole.CONTRACTOR):
        raise HTTPException(status_code=403,
                            detail="Alleen admin/manager/aannemer kan clusters genereren")

    summary = generate_clusters(
        db, current_user.organization_id,
        radius_km=payload.radius_km,
        min_cluster_size=payload.min_cluster_size,
        replace_existing=True,
    )
    log_action(db, request, current_user,
               action="orchestration.clusters_generated",
               entity_type="job_cluster",
               extra=summary)
    return summary


@router.get("/clusters/{cluster_id}")
def get_cluster(
    cluster_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Detail van één cluster + alle gekoppelde meldingen."""
    jc = db.query(JobCluster).filter(
        JobCluster.id == cluster_id,
        JobCluster.organization_id == current_user.organization_id,
    ).first()
    if not jc:
        raise HTTPException(status_code=404, detail="Cluster niet gevonden")

    meldingen = (db.query(Melding)
                   .filter(Melding.job_cluster_id == cluster_id)
                   .order_by(Melding.priority.desc())
                   .all())
    summary = cluster_summary(jc)
    summary["meldingen"] = [{
        "id": m.id, "title": m.title,
        "lat": m.lat, "lng": m.lng,
        "priority": m.priority,
        "crow_klasse": m.crow_klasse,
        "gw_term": m.gw_term,
        "status": m.status,
    } for m in meldingen]
    return summary


@router.patch("/clusters/{cluster_id}/assign")
def assign(
    cluster_id: str,
    payload: ClusterAssignRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Wijs cluster toe aan een gebruiker (admin/manager/contractor)."""
    if current_user.role not in (UserRole.ADMIN, UserRole.MANAGER, UserRole.CONTRACTOR):
        raise HTTPException(status_code=403, detail="Geen rechten om toe te wijzen")

    jc = db.query(JobCluster).filter(
        JobCluster.id == cluster_id,
        JobCluster.organization_id == current_user.organization_id,
    ).first()
    if not jc:
        raise HTTPException(status_code=404, detail="Cluster niet gevonden")

    target = db.query(User).filter(
        User.id == payload.user_id,
        User.organization_id == current_user.organization_id,
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="Gebruiker niet in jouw organisatie")

    if payload.planned_date:
        jc.planned_date = payload.planned_date

    assign_cluster(db, cluster_id, payload.user_id)
    log_action(db, request, current_user,
               action="orchestration.cluster_assigned",
               entity_type="job_cluster", entity_id=cluster_id,
               extra={"assigned_to": payload.user_id, "skill": jc.skill_code})
    return cluster_summary(jc)


@router.patch("/clusters/{cluster_id}/status")
def update_status(
    cluster_id: str,
    payload: ClusterStatusRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update cluster status."""
    jc = db.query(JobCluster).filter(
        JobCluster.id == cluster_id,
        JobCluster.organization_id == current_user.organization_id,
    ).first()
    if not jc:
        raise HTTPException(status_code=404, detail="Cluster niet gevonden")
    jc.status = payload.status
    db.commit()
    log_action(db, request, current_user,
               action="orchestration.cluster_status_changed",
               entity_type="job_cluster", entity_id=cluster_id,
               extra={"new_status": payload.status})
    return cluster_summary(jc)


@router.delete("/clusters/{cluster_id}")
def delete_cluster(
    cluster_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verwijder cluster + ontkoppel meldingen (admin/manager only)."""
    if current_user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        raise HTTPException(status_code=403, detail="Alleen admin/manager kan verwijderen")
    jc = db.query(JobCluster).filter(
        JobCluster.id == cluster_id,
        JobCluster.organization_id == current_user.organization_id,
    ).first()
    if not jc:
        raise HTTPException(status_code=404, detail="Cluster niet gevonden")
    # Ontkoppel
    for m in db.query(Melding).filter(Melding.job_cluster_id == cluster_id).all():
        m.job_cluster_id = None
        m.assigned_to = None
    db.delete(jc)
    db.commit()
    log_action(db, request, current_user,
               action="orchestration.cluster_deleted",
               entity_type="job_cluster", entity_id=cluster_id)
    return {"deleted": True}


# ════════════════════════════════════════════════════════════
# My-clusters + skills (mobile day-planner)
# ════════════════════════════════════════════════════════════

@router.get("/users/me/clusters")
def my_assigned_clusters(
    include_meldingen: bool = True,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mijn toegewezen clusters — voor mobile day-planner van uitvoerders.

    Met include_meldingen (default: true) embedded de complete melding-lijst
    per cluster, in greedy route-order vanaf cluster-centroid. Eén API-call
    geeft alle context die de mobile dag-planner nodig heeft.
    """
    clusters = my_clusters(db, current_user.id)
    return [cluster_summary(c, include_meldingen=include_meldingen, db=db) for c in clusters]


@router.patch("/clusters/{cluster_id}/meldingen/{melding_id}/status")
def update_melding_status_in_cluster(
    cluster_id: str,
    melding_id: str,
    payload: dict,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update status van een melding binnen een cluster (voor mobile day-planner).

    Statussen: open / in_behandeling / afgerond. Inspector/contractor mogen
    statussen wijzigen voor meldingen in clusters die aan hen zijn toegewezen.
    """
    from models import Melding
    new_status = payload.get("status")
    if new_status not in ("open", "in_behandeling", "afgerond"):
        raise HTTPException(status_code=400, detail="Ongeldige status")

    m = db.query(Melding).filter(
        Melding.id == melding_id,
        Melding.job_cluster_id == cluster_id,
        Melding.organization_id == current_user.organization_id,
    ).first()
    if not m:
        raise HTTPException(status_code=404, detail="Melding niet gevonden in deze cluster")

    # Permissie: assignee zelf, OF admin/manager/contractor
    if (m.assigned_to != current_user.id and
        current_user.role not in (UserRole.ADMIN, UserRole.MANAGER, UserRole.CONTRACTOR)):
        raise HTTPException(status_code=403, detail="Geen rechten voor deze melding")

    # Foto-na verplicht voordat veldwerker de melding mag afsluiten.
    # Verhindert dat clusters worden afgerond zonder bewijslast van uitvoering.
    if new_status == "afgerond" and m.status != "afgerond" and not m.photo_after_url:
        raise HTTPException(
            status_code=400,
            detail="Foto na uitvoering vereist voordat de melding afgesloten "
                   "kan worden. Upload eerst een foto.",
        )

    old_status = m.status
    m.status = new_status
    db.commit()
    log_action(db, request, current_user,
               action=ACTION.MELDING_STATUS,
               entity_type="melding", entity_id=melding_id,
               extra={"from": old_status, "to": new_status, "via": "day-planner"})
    return {"id": m.id, "status": m.status}


@router.get("/users/me/skills")
def my_skills(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mijn skills."""
    skills = db.query(UserSkill).filter(UserSkill.user_id == current_user.id).all()
    return [{
        "skill_code": s.skill_code,
        "skill_name": SKILL_CODES.get(s.skill_code, s.skill_code),
        "proficiency": s.proficiency,
    } for s in skills]


@router.put("/users/me/skills")
def set_my_skills(
    payload: UserSkillsRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Vervang mijn skills (full PUT)."""
    # Wis bestaande
    db.query(UserSkill).filter(UserSkill.user_id == current_user.id).delete()
    # Voeg nieuwe toe
    for s in payload.skills:
        if s.skill_code not in SKILL_CODES:
            continue  # ignore unknown codes silently
        db.add(UserSkill(
            user_id=current_user.id,
            skill_code=s.skill_code,
            proficiency=max(1, min(5, s.proficiency)),
        ))
    db.commit()
    log_action(db, request, current_user,
               action="orchestration.skills_updated",
               entity_type="user", entity_id=current_user.id,
               extra={"skill_count": len(payload.skills)})
    return {"updated": len(payload.skills)}


@router.get("/users/{user_id}/skills")
def user_skills(
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Skills van een specifieke gebruiker (binnen mijn org)."""
    target = db.query(User).filter(
        User.id == user_id,
        User.organization_id == current_user.organization_id,
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")
    skills = db.query(UserSkill).filter(UserSkill.user_id == user_id).all()
    return [{
        "skill_code": s.skill_code,
        "skill_name": SKILL_CODES.get(s.skill_code, s.skill_code),
        "proficiency": s.proficiency,
    } for s in skills]


@router.put("/users/{user_id}/skills")
def set_user_skills(
    user_id: str,
    payload: UserSkillsRequest,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Update skills van een andere gebruiker (alleen admins/managers)."""
    target = db.query(User).filter(
        User.id == user_id,
        User.organization_id == current_user.organization_id,
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")
    db.query(UserSkill).filter(UserSkill.user_id == user_id).delete()
    for s in payload.skills:
        if s.skill_code not in SKILL_CODES:
            continue
        db.add(UserSkill(
            user_id=user_id,
            skill_code=s.skill_code,
            proficiency=max(1, min(5, s.proficiency)),
        ))
    db.commit()
    log_action(db, request, current_user,
               action="orchestration.skills_updated",
               entity_type="user", entity_id=user_id,
               extra={"skill_count": len(payload.skills)})
    return {"updated": len(payload.skills)}


@router.get("/skills/catalog")
def skills_catalog():
    """Alle beschikbare skill-codes + omschrijvingen + welke maatregel ze afdekken."""
    return [{
        "code": code,
        "name": name,
        "covers_maatregelen": [m for m, s in MAATREGEL_TO_SKILL.items() if s == code],
    } for code, name in SKILL_CODES.items()]


# ════════════════════════════════════════════════════════════
# Productiviteit-savings dashboard (ROI)
# ════════════════════════════════════════════════════════════

@router.get("/orchestration/savings")
def savings_dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Geef productivity-savings overzicht voor mijn org.

    Toont totaal aan uren bespaard door clustering vs solo-jobs, met euro-
    indicatie (default €60/uur fully-loaded marktprijs).
    """
    HOURLY_RATE_EUR = 60.0  # gemiddelde fully-loaded uurkosten infra-aannemer

    clusters = (db.query(JobCluster)
                  .filter(JobCluster.organization_id == current_user.organization_id,
                          JobCluster.status.in_(["assigned", "in_progress", "done"]))
                  .all())

    total_baseline = sum((c.productivity_baseline_hours or 0) for c in clusters)
    total_clustered = sum((c.estimated_hours or 0) for c in clusters)
    total_savings = round(max(0.0, total_baseline - total_clustered), 1)

    # Per skill aggregeren
    by_skill: dict = {}
    for c in clusters:
        s = c.skill_code or "OVERIG"
        if s not in by_skill:
            by_skill[s] = {
                "skill_code": s, "skill_name": SKILL_CODES.get(s, s),
                "cluster_count": 0, "melding_count": 0,
                "savings_hours": 0.0,
            }
        by_skill[s]["cluster_count"] += 1
        by_skill[s]["melding_count"] += c.melding_count or 0
        by_skill[s]["savings_hours"] += round((c.productivity_savings_hours or 0), 1)

    return {
        "total_clusters": len(clusters),
        "total_meldingen_clustered": sum(c.melding_count or 0 for c in clusters),
        "total_baseline_hours": round(total_baseline, 1),
        "total_clustered_hours": round(total_clustered, 1),
        "total_savings_hours": total_savings,
        "total_savings_euro": round(total_savings * HOURLY_RATE_EUR, 2),
        "savings_pct": (
            round((total_savings / total_baseline * 100), 1) if total_baseline else 0
        ),
        "hourly_rate_used_eur": HOURLY_RATE_EUR,
        "by_skill": list(by_skill.values()),
    }
