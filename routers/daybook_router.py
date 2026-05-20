"""Werkdagboek — registratie van wat een gebruiker heeft gedaan.

Endpoints:
  - GET    /api/daybook/day?date=YYYY-MM-DD&user_id=...   entries van 1 dag
  - GET    /api/daybook/range?from=...&to=...&user_id=... entries over periode
  - GET    /api/daybook/summary?from=...&to=...           aggregaten + uren-totaal
  - POST   /api/daybook/entries                            handmatige notitie
  - PATCH  /api/daybook/entries/{id}                       eigen notitie bewerken
  - DELETE /api/daybook/entries/{id}                       soft-delete eigen notitie

RBAC:
  - User mag eigen entries CRUD'en
  - Org-admin mag entries van eigen org-leden lezen (transparantie)
  - Manager mag entries van assigned team-leden lezen (wordt later geregeld
    via Project.team_members — voor MVP: org-admin scope is genoeg)

Auto-entries (source='auto') worden niet via deze endpoints aangemaakt maar
via de `daybook_logger.log(...)` helper die in andere routers wordt aangeroepen
(meldingen_router, kunstwerken_inspecties_router, etc).
"""
from datetime import datetime, timezone, timedelta, date as date_type
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from pydantic import BaseModel, Field

from database import get_db
from models import DaybookEntry, User, Project
from auth import get_current_user
from permissions import require_org_admin

router = APIRouter(prefix="/api/daybook", tags=["Werkdagboek"])


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class DaybookEntryCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    entry_type: str = "manual_note"
    occurred_at: Optional[datetime] = None  # default = now
    lat: Optional[float] = None
    lng: Optional[float] = None
    duration_minutes: Optional[int] = Field(None, ge=0, le=1440)  # max 1 dag
    project_id: Optional[str] = None
    source_type: Optional[str] = None
    source_id: Optional[str] = None


class DaybookEntryUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    occurred_at: Optional[datetime] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    duration_minutes: Optional[int] = Field(None, ge=0, le=1440)
    project_id: Optional[str] = None


class DaybookEntryOut(BaseModel):
    id: str
    user_id: str
    user_name: Optional[str] = None
    entry_type: str
    source: str
    source_type: Optional[str] = None
    source_id: Optional[str] = None
    occurred_at: datetime
    title: str
    description: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    duration_minutes: Optional[int] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _can_view_user_entries(current_user: User, target_user_id: str) -> bool:
    """Een user mag eigen entries lezen; org-admin mag entries van team-leden lezen."""
    if current_user.id == target_user_id:
        return True
    if current_user.is_org_admin:
        return True
    return False


def _serialize(e: DaybookEntry, user_map: dict, project_map: dict) -> dict:
    return {
        "id": e.id,
        "user_id": e.user_id,
        "user_name": user_map.get(e.user_id),
        "entry_type": e.entry_type,
        "source": e.source,
        "source_type": e.source_type,
        "source_id": e.source_id,
        "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
        "title": e.title,
        "description": e.description,
        "lat": e.lat,
        "lng": e.lng,
        "duration_minutes": e.duration_minutes,
        "project_id": e.project_id,
        "project_name": project_map.get(e.project_id) if e.project_id else None,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


def _enrich(db: Session, entries: list) -> list:
    """Voeg user_name + project_name toe aan elke entry voor frontend-gemak."""
    if not entries:
        return []
    user_ids = list({e.user_id for e in entries if e.user_id})
    project_ids = list({e.project_id for e in entries if e.project_id})

    user_map = {}
    if user_ids:
        users = db.query(User.id, User.first_name, User.last_name, User.email).filter(
            User.id.in_(user_ids)
        ).all()
        user_map = {
            u.id: ((u.first_name or "") + " " + (u.last_name or "")).strip() or u.email
            for u in users
        }
    project_map = {}
    if project_ids:
        projects = db.query(Project.id, Project.name).filter(
            Project.id.in_(project_ids)
        ).all()
        project_map = {p.id: p.name for p in projects}

    return [_serialize(e, user_map, project_map) for e in entries]


# ─────────────────────────────────────────────────────────────────────────────
# Read endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/day")
def get_day(
    date: Optional[str] = Query(None, description="YYYY-MM-DD; default = vandaag"),
    user_id: Optional[str] = Query(None, description="Default = current user"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Entries van 1 specifieke dag voor 1 gebruiker.

    Default: vandaag, current user. Org-admin mag andere user_id opgeven.
    """
    target_user_id = user_id or current_user.id
    if not _can_view_user_entries(current_user, target_user_id):
        raise HTTPException(403, "Geen toegang tot dagboek van andere gebruiker")

    # Parse date
    if date:
        try:
            d = date_type.fromisoformat(date)
        except ValueError:
            raise HTTPException(400, "date moet YYYY-MM-DD formaat hebben")
    else:
        d = datetime.now(timezone.utc).date()

    start = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    entries = (db.query(DaybookEntry)
                 .filter(DaybookEntry.user_id == target_user_id,
                         DaybookEntry.organization_id == current_user.organization_id,
                         DaybookEntry.occurred_at >= start,
                         DaybookEntry.occurred_at < end,
                         DaybookEntry.deleted_at.is_(None))
                 .order_by(DaybookEntry.occurred_at.asc())
                 .all())

    # Aggregaten voor de dag
    total_minutes = sum(e.duration_minutes or 0 for e in entries)
    by_type = {}
    for e in entries:
        by_type[e.entry_type] = by_type.get(e.entry_type, 0) + 1

    return {
        "date": d.isoformat(),
        "user_id": target_user_id,
        "total_entries": len(entries),
        "total_minutes": total_minutes,
        "by_type": by_type,
        "entries": _enrich(db, entries),
    }


@router.get("/range")
def get_range(
    date_from: str = Query(..., alias="from", description="YYYY-MM-DD"),
    date_to: str = Query(..., alias="to", description="YYYY-MM-DD (inclusief)"),
    user_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Entries over een periode (max 92 dagen). Default = current user."""
    target_user_id = user_id or current_user.id
    if not _can_view_user_entries(current_user, target_user_id):
        raise HTTPException(403, "Geen toegang tot dagboek van andere gebruiker")

    try:
        d_from = date_type.fromisoformat(date_from)
        d_to = date_type.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(400, "from/to moet YYYY-MM-DD formaat hebben")
    if d_to < d_from:
        raise HTTPException(400, "to mag niet voor from liggen")
    if (d_to - d_from).days > 92:
        raise HTTPException(400, "Max 92 dagen per query — gebruik /summary voor langere periodes")

    start = datetime.combine(d_from, datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(d_to, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)

    entries = (db.query(DaybookEntry)
                 .filter(DaybookEntry.user_id == target_user_id,
                         DaybookEntry.organization_id == current_user.organization_id,
                         DaybookEntry.occurred_at >= start,
                         DaybookEntry.occurred_at < end,
                         DaybookEntry.deleted_at.is_(None))
                 .order_by(DaybookEntry.occurred_at.asc())
                 .all())

    return {
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "user_id": target_user_id,
        "total_entries": len(entries),
        "entries": _enrich(db, entries),
    }


@router.get("/summary")
def get_summary(
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    user_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aggregaten over een periode — per dag, per type, totaal minuten."""
    target_user_id = user_id or current_user.id
    if not _can_view_user_entries(current_user, target_user_id):
        raise HTTPException(403, "Geen toegang")

    try:
        d_from = date_type.fromisoformat(date_from)
        d_to = date_type.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(400, "from/to moet YYYY-MM-DD formaat hebben")

    start = datetime.combine(d_from, datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(d_to, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)

    rows = (db.query(DaybookEntry)
              .filter(DaybookEntry.user_id == target_user_id,
                      DaybookEntry.organization_id == current_user.organization_id,
                      DaybookEntry.occurred_at >= start,
                      DaybookEntry.occurred_at < end,
                      DaybookEntry.deleted_at.is_(None))
              .all())

    by_day = {}
    by_type = {}
    by_project = {}
    total_minutes = 0
    for r in rows:
        day_key = r.occurred_at.date().isoformat() if r.occurred_at else "unknown"
        by_day.setdefault(day_key, {"count": 0, "minutes": 0})
        by_day[day_key]["count"] += 1
        by_day[day_key]["minutes"] += (r.duration_minutes or 0)

        by_type[r.entry_type] = by_type.get(r.entry_type, 0) + 1

        if r.project_id:
            by_project.setdefault(r.project_id, {"count": 0, "minutes": 0})
            by_project[r.project_id]["count"] += 1
            by_project[r.project_id]["minutes"] += (r.duration_minutes or 0)

        total_minutes += (r.duration_minutes or 0)

    # Project-namen erbij
    project_map = {}
    if by_project:
        prs = db.query(Project.id, Project.name).filter(Project.id.in_(list(by_project.keys()))).all()
        project_map = {p.id: p.name for p in prs}

    return {
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "user_id": target_user_id,
        "total_entries": len(rows),
        "total_minutes": total_minutes,
        "by_day": [{"date": k, **v} for k, v in sorted(by_day.items())],
        "by_type": [{"type": k, "count": v} for k, v in sorted(by_type.items(), key=lambda x: -x[1])],
        "by_project": [{"project_id": k, "project_name": project_map.get(k, "(onbekend)"),
                        "count": v["count"], "minutes": v["minutes"]}
                       for k, v in sorted(by_project.items(), key=lambda x: -x[1]["minutes"])],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Write endpoints — alleen handmatige entries (auto-entries via daybook_logger)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/entries", response_model=None)
def create_entry(
    payload: DaybookEntryCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Voeg een handmatige notitie toe aan het eigen dagboek.

    Auto-entries (melding aangemaakt, inspectie afgerond, etc) komen via
    `daybook_logger.log(...)` automatisch — niet via dit endpoint.
    """
    # Validatie: project moet bij eigen org horen (als opgegeven)
    if payload.project_id:
        proj = db.query(Project).filter(
            Project.id == payload.project_id,
            Project.organization_id == current_user.organization_id,
        ).first()
        if not proj:
            raise HTTPException(404, "Project niet gevonden binnen je organisatie")

    entry = DaybookEntry(
        user_id=current_user.id,
        organization_id=current_user.organization_id,
        entry_type=payload.entry_type or "manual_note",
        source="manual",
        source_type=payload.source_type,
        source_id=payload.source_id,
        occurred_at=payload.occurred_at or datetime.now(timezone.utc),
        title=payload.title.strip(),
        description=(payload.description or "").strip() or None,
        lat=payload.lat,
        lng=payload.lng,
        duration_minutes=payload.duration_minutes,
        project_id=payload.project_id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _enrich(db, [entry])[0]


@router.patch("/entries/{entry_id}")
def update_entry(
    entry_id: str,
    payload: DaybookEntryUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Eigen entry bewerken — alleen handmatige entries, niet auto-entries."""
    entry = db.query(DaybookEntry).filter(
        DaybookEntry.id == entry_id,
        DaybookEntry.organization_id == current_user.organization_id,
        DaybookEntry.deleted_at.is_(None),
    ).first()
    if not entry:
        raise HTTPException(404, "Entry niet gevonden")
    if entry.user_id != current_user.id:
        raise HTTPException(403, "Je kunt alleen je eigen entries bewerken")
    if entry.source == "auto":
        raise HTTPException(400, "Auto-entries kunnen niet bewerkt worden (audit-integriteit)")

    data = payload.model_dump(exclude_unset=True)
    if "project_id" in data and data["project_id"]:
        proj = db.query(Project).filter(
            Project.id == data["project_id"],
            Project.organization_id == current_user.organization_id,
        ).first()
        if not proj:
            raise HTTPException(404, "Project niet gevonden")

    for field, value in data.items():
        if isinstance(value, str):
            value = value.strip() or None
        setattr(entry, field, value)
    db.commit()
    db.refresh(entry)
    return _enrich(db, [entry])[0]


@router.delete("/entries/{entry_id}")
def delete_entry(
    entry_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Soft-delete eigen entry. Auto-entries blijven bewaard (audit)."""
    entry = db.query(DaybookEntry).filter(
        DaybookEntry.id == entry_id,
        DaybookEntry.organization_id == current_user.organization_id,
        DaybookEntry.deleted_at.is_(None),
    ).first()
    if not entry:
        raise HTTPException(404, "Entry niet gevonden")
    if entry.user_id != current_user.id:
        raise HTTPException(403, "Je kunt alleen je eigen entries verwijderen")
    if entry.source == "auto":
        raise HTTPException(400, "Auto-entries kunnen niet verwijderd worden")

    entry.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"deleted": True, "entry_id": entry_id}


@router.get("/team-overview")
def team_overview(
    days: int = Query(7, ge=1, le=92),
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Org-admin overview: dagboek-activiteit per team-lid in laatste X dagen."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (db.query(DaybookEntry.user_id,
                     func.count(DaybookEntry.id).label("entry_count"),
                     func.sum(DaybookEntry.duration_minutes).label("total_minutes"),
                     func.max(DaybookEntry.occurred_at).label("last_activity"))
              .filter(DaybookEntry.organization_id == current_user.organization_id,
                      DaybookEntry.occurred_at >= cutoff,
                      DaybookEntry.deleted_at.is_(None))
              .group_by(DaybookEntry.user_id)
              .all())

    user_ids = [r.user_id for r in rows]
    user_map = {}
    if user_ids:
        users = db.query(User.id, User.first_name, User.last_name, User.email, User.role).filter(
            User.id.in_(user_ids)
        ).all()
        user_map = {u.id: u for u in users}

    out = []
    for r in rows:
        u = user_map.get(r.user_id)
        out.append({
            "user_id": r.user_id,
            "user_name": (((u.first_name or "") + " " + (u.last_name or "")).strip() or u.email) if u else "(verwijderd)",
            "user_role": u.role.value if u and u.role else None,
            "entry_count": r.entry_count,
            "total_minutes": int(r.total_minutes or 0),
            "last_activity": r.last_activity.isoformat() if r.last_activity else None,
        })
    out.sort(key=lambda x: -x["entry_count"])
    return {
        "days": days,
        "team_size": len(out),
        "members": out,
    }
