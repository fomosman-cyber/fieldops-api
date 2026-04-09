from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from database import get_db
from models import Melding, User
from schemas import MeldingCreate, MeldingResponse, MeldingUpdate
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/meldingen", tags=["Meldingen"])


def _melding_to_response(melding: Melding) -> dict:
    """Converteer Melding naar response dict met creator_name."""
    creator = melding.creator
    creator_name = f"{creator.first_name} {creator.last_name}" if creator else None
    return {
        "id": melding.id,
        "title": melding.title,
        "description": melding.description,
        "category": melding.category,
        "priority": melding.priority,
        "status": melding.status,
        "lat": melding.lat,
        "lng": melding.lng,
        "photo_url": melding.photo_url,
        "photo_after_url": melding.photo_after_url,
        "project_id": melding.project_id,
        "created_by": melding.created_by,
        "created_at": melding.created_at,
        "creator_name": creator_name,
    }


@router.get("/", response_model=list[MeldingResponse])
def list_meldingen(
    project_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alle meldingen van de organisatie ophalen, optioneel gefilterd op project."""
    query = db.query(Melding).filter(
        Melding.organization_id == current_user.organization_id,
    )
    if project_id:
        query = query.filter(Melding.project_id == project_id)
    meldingen = query.order_by(Melding.created_at.desc()).all()
    return [_melding_to_response(m) for m in meldingen]


@router.post("/", response_model=MeldingResponse)
def create_melding(
    data: MeldingCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Nieuwe melding aanmaken."""
    # Viewer en Contractor mogen geen meldingen aanmaken
    if current_user.role and current_user.role.value in ("viewer", "contractor"):
        raise HTTPException(status_code=403, detail="Je rol heeft geen rechten om meldingen aan te maken")
    melding = Melding(
        title=data.title,
        description=data.description,
        category=data.category,
        priority=data.priority or "normaal",
        lat=data.lat,
        lng=data.lng,
        photo_url=data.photo_url,
        photo_after_url=data.photo_after_url,
        project_id=data.project_id,
        organization_id=current_user.organization_id,
        created_by=current_user.id,
    )
    db.add(melding)
    db.commit()
    db.refresh(melding)
    return _melding_to_response(melding)


@router.get("/{melding_id}", response_model=MeldingResponse)
def get_melding(
    melding_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Enkele melding ophalen."""
    melding = db.query(Melding).filter(
        Melding.id == melding_id,
        Melding.organization_id == current_user.organization_id,
    ).first()
    if not melding:
        raise HTTPException(status_code=404, detail="Melding niet gevonden")
    return _melding_to_response(melding)


@router.put("/{melding_id}", response_model=MeldingResponse)
def update_melding(
    melding_id: str,
    update: MeldingUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Melding bijwerken."""
    melding = db.query(Melding).filter(
        Melding.id == melding_id,
        Melding.organization_id == current_user.organization_id,
    ).first()
    if not melding:
        raise HTTPException(status_code=404, detail="Melding niet gevonden")

    role = current_user.role.value if current_user.role else "viewer"

    # Viewer mag niets wijzigen
    if role == "viewer":
        raise HTTPException(status_code=403, detail="Opdrachtgevers kunnen geen meldingen wijzigen")

    # Toezichthouder/Inspector mag alleen eigen meldingen bewerken (geen status)
    if role == "inspector":
        if melding.created_by != current_user.id:
            raise HTTPException(status_code=403, detail="Toezichthouders mogen alleen eigen meldingen bewerken")
        update_data = update.model_dump(exclude_unset=True)
        if "status" in update_data:
            raise HTTPException(status_code=403, detail="Toezichthouders mogen geen status wijzigen")

    # Aannemer/Contractor mag alleen status wijzigen (niet andere velden)
    if role == "contractor":
        update_data = update.model_dump(exclude_unset=True)
        allowed = {"status"}
        if set(update_data.keys()) - allowed:
            raise HTTPException(status_code=403, detail="Aannemers mogen alleen de status wijzigen")

    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(melding, field, value)
    db.commit()
    db.refresh(melding)
    return _melding_to_response(melding)


@router.delete("/{melding_id}")
def delete_melding(
    melding_id: str,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Melding verwijderen (alleen admin)."""
    melding = db.query(Melding).filter(
        Melding.id == melding_id,
        Melding.organization_id == current_user.organization_id,
    ).first()
    if not melding:
        raise HTTPException(status_code=404, detail="Melding niet gevonden")

    db.delete(melding)
    db.commit()
    return {"message": "Melding verwijderd"}
