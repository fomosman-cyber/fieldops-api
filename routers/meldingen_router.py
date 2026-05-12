from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from typing import Optional
from database import get_db
from models import Melding, User
from schemas import MeldingCreate, MeldingResponse, MeldingUpdate
from auth import get_current_user
from permissions import (
    can_create_meldingen, can_change_status, can_edit_melding_full,
    is_inspector, require_org_admin,
)
from audit import log_action, ACTION

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
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Nieuwe melding aanmaken."""
    if not can_create_meldingen(current_user):
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
        asset_id=getattr(data, "asset_id", None),
        organization_id=current_user.organization_id,
        created_by=current_user.id,
        # CROW 146 + GWWkosten classificatie (optioneel)
        crow_schadegroep=getattr(data, "crow_schadegroep", None),
        crow_schadebeeld=getattr(data, "crow_schadebeeld", None),
        crow_ernst=getattr(data, "crow_ernst", None),
        crow_omvang=getattr(data, "crow_omvang", None),
        crow_klasse=getattr(data, "crow_klasse", None),
        nen_2767_conditie=getattr(data, "nen_2767_conditie", None),
        onderhoud_categorie=getattr(data, "onderhoud_categorie", None),
        gw_maatregel=getattr(data, "gw_maatregel", None),
        gw_term=getattr(data, "gw_term", None),
        gw_kosten_orde=getattr(data, "gw_kosten_orde", None),
    )
    db.add(melding)
    db.commit()
    db.refresh(melding)
    log_action(db, request, current_user,
               action=ACTION.MELDING_CREATE,
               entity_type="melding", entity_id=melding.id,
               after={"title": melding.title, "category": melding.category,
                      "priority": melding.priority, "project_id": melding.project_id,
                      "crow_klasse": melding.crow_klasse,
                      "onderhoud_categorie": melding.onderhoud_categorie})
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
    request: Request,
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

    update_data = update.model_dump(exclude_unset=True)
    is_status_only = set(update_data.keys()) == {"status"}
    has_status_field = "status" in update_data

    # Permissielogica gerouteerd via permissions.py:
    # - Volledige edit (alle velden incl. status): admin/manager/technician
    # - Inspector mag eigen meldingen, MAAR geen status
    # - Contractor mag enkel status (en alleen via een status-only payload)
    # - Viewer mag niets
    if can_edit_melding_full(current_user):
        pass  # alle velden ok
    elif is_inspector(current_user):
        if melding.created_by != current_user.id:
            raise HTTPException(status_code=403, detail="Toezichthouders mogen alleen eigen meldingen bewerken")
        if has_status_field:
            raise HTTPException(status_code=403, detail="Toezichthouders mogen geen status wijzigen")
    elif can_change_status(current_user):
        # Aannemers e.d. — alleen pure status-update toegestaan
        if not is_status_only:
            raise HTTPException(status_code=403, detail="Je rol mag alleen de status wijzigen")
    else:
        raise HTTPException(status_code=403, detail="Geen rechten om meldingen te wijzigen")

    before = {k: getattr(melding, k) for k in update_data.keys()}
    status_change = has_status_field and update_data["status"] != melding.status

    # Foto-na verplicht vóór afsluiten — bewijslast voor opdrachtgevers
    # dat het werk daadwerkelijk is uitgevoerd. Geldt voor de twee terminal-
    # statussen 'opgelost' en 'afgerond'. Een melding direct herstellen naar
    # 'open' (heropen) blijft mogelijk zonder foto.
    if status_change and update_data["status"] in ("opgelost", "afgerond"):
        new_photo_after = update_data.get("photo_after_url")
        existing_photo_after = melding.photo_after_url
        if not (new_photo_after or existing_photo_after):
            raise HTTPException(
                status_code=400,
                detail="Foto na uitvoering vereist voordat de melding afgesloten "
                       "(opgelost/afgerond) kan worden. Upload eerst een foto.",
            )

    for field, value in update_data.items():
        setattr(melding, field, value)
    db.commit()
    db.refresh(melding)

    log_action(db, request, current_user,
               action=ACTION.MELDING_STATUS if status_change else ACTION.MELDING_UPDATE,
               entity_type="melding", entity_id=melding.id,
               before=before, after=update_data)
    return _melding_to_response(melding)


@router.delete("/{melding_id}")
def delete_melding(
    melding_id: str,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Melding verwijderen (alleen admin)."""
    melding = db.query(Melding).filter(
        Melding.id == melding_id,
        Melding.organization_id == current_user.organization_id,
    ).first()
    if not melding:
        raise HTTPException(status_code=404, detail="Melding niet gevonden")

    snapshot = {"title": melding.title, "category": melding.category,
                "status": melding.status, "project_id": melding.project_id}
    db.delete(melding)
    db.commit()
    log_action(db, request, current_user,
               action=ACTION.MELDING_DELETE,
               entity_type="melding", entity_id=melding_id, before=snapshot)
    return {"message": "Melding verwijderd"}
