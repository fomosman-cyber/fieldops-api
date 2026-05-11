"""Oplevering-router — proces-verbalen van uitgevoerd werk.

Endpoints:
  GET    /api/opleveringen                 Lijst van opleveringen (mijn org)
  POST   /api/opleveringen                 Nieuwe oplevering (concept)
  GET    /api/opleveringen/{id}            Detail + alle punten
  PATCH  /api/opleveringen/{id}            Update header (status, datum, etc.)
  DELETE /api/opleveringen/{id}            Verwijder (cascade punten)
  POST   /api/opleveringen/{id}/punten     Voeg punt toe
  PATCH  /api/opleveringen/{id}/punten/{punt_id}   Wijzig punt
  DELETE /api/opleveringen/{id}/punten/{punt_id}   Verwijder punt
"""
from typing import Optional, List
from datetime import datetime
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import User, Oplevering, OpleveringPunt, Asset
from auth import get_current_user
from audit import log_action, ACTION

router = APIRouter(prefix="/api/opleveringen", tags=["Opleveringen"])


# ── Pydantic-schemas ─────────────────────────────────────────────────

class OpleveringPuntIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    omschrijving: str = Field(..., min_length=1)
    uitvoeringsmethode: Optional[str] = None
    photo_url: Optional[str] = None
    asset_id: Optional[str] = None
    order_index: int = 0
    status: str = Field(default="gereed", pattern="^(gereed|restpunt|afgekeurd)$")


class OpleveringPuntUpdate(BaseModel):
    code: Optional[str] = None
    omschrijving: Optional[str] = None
    uitvoeringsmethode: Optional[str] = None
    photo_url: Optional[str] = None
    asset_id: Optional[str] = None
    order_index: Optional[int] = None
    status: Optional[str] = Field(default=None, pattern="^(gereed|restpunt|afgekeurd)$")


class OpleveringIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    project_id: Optional[str] = None
    locatie: Optional[str] = None
    datum_oplevering: Optional[datetime] = None
    opdrachtgever_naam: Optional[str] = None
    opdrachtgever_email: Optional[str] = None
    aannemer_naam: Optional[str] = None
    aannemer_email: Optional[str] = None
    extra_questions: Optional[dict] = None
    notes: Optional[str] = None
    status: str = Field(default="concept", pattern="^(concept|opgeleverd|aanvaard|afgewezen)$")


class OpleveringUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    project_id: Optional[str] = None
    locatie: Optional[str] = None
    datum_oplevering: Optional[datetime] = None
    opdrachtgever_naam: Optional[str] = None
    opdrachtgever_email: Optional[str] = None
    aannemer_naam: Optional[str] = None
    aannemer_email: Optional[str] = None
    extra_questions: Optional[dict] = None
    notes: Optional[str] = None
    status: Optional[str] = Field(default=None, pattern="^(concept|opgeleverd|aanvaard|afgewezen)$")


# ── Helpers ──────────────────────────────────────────────────────────

def _punt_to_dict(p: OpleveringPunt) -> dict:
    return {
        "id": p.id,
        "code": p.code,
        "omschrijving": p.omschrijving,
        "uitvoeringsmethode": p.uitvoeringsmethode,
        "photo_url": p.photo_url,
        "asset_id": p.asset_id,
        "order_index": p.order_index,
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _oplevering_to_dict(o: Oplevering, *, include_punten: bool = False) -> dict:
    extra = None
    if o.extra_questions_json:
        try:
            extra = json.loads(o.extra_questions_json)
        except (json.JSONDecodeError, TypeError):
            extra = None
    out = {
        "id": o.id,
        "title": o.title,
        "description": o.description,
        "project_id": o.project_id,
        "project_name": o.project.name if o.project else None,
        "locatie": o.locatie,
        "datum_oplevering": o.datum_oplevering.isoformat() if o.datum_oplevering else None,
        "opdrachtgever_naam": o.opdrachtgever_naam,
        "opdrachtgever_email": o.opdrachtgever_email,
        "aannemer_naam": o.aannemer_naam,
        "aannemer_email": o.aannemer_email,
        "extra_questions": extra,
        "notes": o.notes,
        "status": o.status,
        "signed_off_at": o.signed_off_at.isoformat() if o.signed_off_at else None,
        "punten_count": len(o.punten or []),
        "created_by": o.created_by,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "updated_at": o.updated_at.isoformat() if o.updated_at else None,
    }
    if include_punten:
        out["punten"] = [_punt_to_dict(p) for p in (o.punten or [])]
    return out


def _get_oplevering_or_404(db: Session, oplevering_id: str, current_user: User) -> Oplevering:
    o = (db.query(Oplevering)
            .filter(Oplevering.id == oplevering_id,
                    Oplevering.organization_id == current_user.organization_id)
            .first())
    if not o:
        raise HTTPException(status_code=404, detail="Oplevering niet gevonden")
    return o


# ── Endpoints: opleveringen ─────────────────────────────────────────

@router.get("/")
def list_opleveringen(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Oplevering).filter(Oplevering.organization_id == current_user.organization_id)
    if project_id:
        q = q.filter(Oplevering.project_id == project_id)
    if status:
        q = q.filter(Oplevering.status == status)
    items = q.order_by(Oplevering.created_at.desc()).all()
    return [_oplevering_to_dict(o) for o in items]


@router.post("/")
def create_oplevering(
    payload: OpleveringIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    extra_json = json.dumps(payload.extra_questions) if payload.extra_questions else None
    o = Oplevering(
        organization_id=current_user.organization_id,
        project_id=payload.project_id,
        title=payload.title,
        description=payload.description,
        locatie=payload.locatie,
        datum_oplevering=payload.datum_oplevering,
        opdrachtgever_naam=payload.opdrachtgever_naam,
        opdrachtgever_email=payload.opdrachtgever_email,
        aannemer_naam=payload.aannemer_naam,
        aannemer_email=payload.aannemer_email,
        extra_questions_json=extra_json,
        notes=payload.notes,
        status=payload.status,
        created_by=current_user.id,
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    log_action(db, request, current_user, action="oplevering.create",
               entity_type="oplevering", entity_id=o.id,
               extra={"title": o.title, "status": o.status})
    return _oplevering_to_dict(o, include_punten=True)


@router.get("/{oplevering_id}")
def get_oplevering(
    oplevering_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    o = _get_oplevering_or_404(db, oplevering_id, current_user)
    return _oplevering_to_dict(o, include_punten=True)


@router.patch("/{oplevering_id}")
def update_oplevering(
    oplevering_id: str,
    payload: OpleveringUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    o = _get_oplevering_or_404(db, oplevering_id, current_user)
    before_status = o.status
    data = payload.model_dump(exclude_unset=True)
    if "extra_questions" in data:
        eq = data.pop("extra_questions")
        o.extra_questions_json = json.dumps(eq) if eq else None
    for k, v in data.items():
        setattr(o, k, v)
    # Markeer signed_off_at als status overgaat naar aanvaard
    if before_status != "aanvaard" and o.status == "aanvaard":
        o.signed_off_at = datetime.utcnow()
    db.commit()
    db.refresh(o)
    log_action(db, request, current_user, action="oplevering.update",
               entity_type="oplevering", entity_id=o.id,
               extra={"before_status": before_status, "after_status": o.status})
    return _oplevering_to_dict(o, include_punten=True)


@router.delete("/{oplevering_id}")
def delete_oplevering(
    oplevering_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    o = _get_oplevering_or_404(db, oplevering_id, current_user)
    title = o.title
    db.delete(o)
    db.commit()
    log_action(db, request, current_user, action="oplevering.delete",
               entity_type="oplevering", entity_id=oplevering_id,
               extra={"title": title})
    return {"message": "Oplevering verwijderd"}


# ── Endpoints: punten ─────────────────────────────────────────────

@router.post("/{oplevering_id}/punten")
def add_punt(
    oplevering_id: str,
    payload: OpleveringPuntIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    o = _get_oplevering_or_404(db, oplevering_id, current_user)
    # Optionele asset-validatie (binnen eigen org)
    if payload.asset_id:
        asset = (db.query(Asset)
                   .filter(Asset.id == payload.asset_id,
                           Asset.organization_id == current_user.organization_id)
                   .first())
        if not asset:
            raise HTTPException(status_code=404, detail="Asset niet gevonden in jouw organisatie")
    # Auto order_index als 0 of niet gezet
    next_order = payload.order_index
    if not next_order:
        existing = db.query(OpleveringPunt).filter(OpleveringPunt.oplevering_id == o.id).count()
        next_order = existing + 1
    p = OpleveringPunt(
        oplevering_id=o.id,
        organization_id=current_user.organization_id,
        code=payload.code,
        omschrijving=payload.omschrijving,
        uitvoeringsmethode=payload.uitvoeringsmethode,
        photo_url=payload.photo_url,
        asset_id=payload.asset_id,
        order_index=next_order,
        status=payload.status,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    log_action(db, request, current_user, action="oplevering.punt_add",
               entity_type="oplevering_punt", entity_id=p.id,
               extra={"oplevering_id": o.id, "code": p.code})
    return _punt_to_dict(p)


@router.patch("/{oplevering_id}/punten/{punt_id}")
def update_punt(
    oplevering_id: str,
    punt_id: str,
    payload: OpleveringPuntUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    o = _get_oplevering_or_404(db, oplevering_id, current_user)
    p = (db.query(OpleveringPunt)
            .filter(OpleveringPunt.id == punt_id,
                    OpleveringPunt.oplevering_id == o.id)
            .first())
    if not p:
        raise HTTPException(status_code=404, detail="Punt niet gevonden")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    log_action(db, request, current_user, action="oplevering.punt_update",
               entity_type="oplevering_punt", entity_id=p.id,
               extra={"oplevering_id": o.id, "code": p.code})
    return _punt_to_dict(p)


@router.delete("/{oplevering_id}/punten/{punt_id}")
def delete_punt(
    oplevering_id: str,
    punt_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    o = _get_oplevering_or_404(db, oplevering_id, current_user)
    p = (db.query(OpleveringPunt)
            .filter(OpleveringPunt.id == punt_id,
                    OpleveringPunt.oplevering_id == o.id)
            .first())
    if not p:
        raise HTTPException(status_code=404, detail="Punt niet gevonden")
    code = p.code
    db.delete(p)
    db.commit()
    log_action(db, request, current_user, action="oplevering.punt_delete",
               entity_type="oplevering_punt", entity_id=punt_id,
               extra={"oplevering_id": o.id, "code": code})
    return {"message": "Punt verwijderd"}
