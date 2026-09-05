import json
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from database import get_db
from models import Project, User, Melding
from schemas import ProjectCreate, ProjectUpdate
from auth import get_current_user, require_admin
from audit import log_action, ACTION

router = APIRouter(prefix="/api/projects", tags=["Projecten"])


def _project_to_dict(p):
    """Convert project to dict with categories as list."""
    cats = None
    if p.categories:
        try:
            cats = json.loads(p.categories)
        except (json.JSONDecodeError, TypeError):
            cats = []
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "gemeente": p.gemeente,
        "status": p.status,
        "boundary_geojson": p.boundary_geojson,
        "color": p.color,
        "categories": cats,
        "created_by": p.created_by,
        "created_at": p.created_at,
    }


@router.get("/")
def list_projects(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alle projecten van de organisatie ophalen."""
    projects = (
        db.query(Project)
        .filter(
            Project.organization_id == current_user.organization_id,
            Project.status != "archived",
        )
        .order_by(Project.created_at.desc())
        .all()
    )
    return [_project_to_dict(p) for p in projects]


@router.post("/")
def create_project(
    data: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Nieuw project aanmaken."""
    project = Project(
        name=data.name,
        description=data.description,
        gemeente=data.gemeente,
        boundary_geojson=data.boundary_geojson,
        color=data.color or "#00d4ff",
        categories=json.dumps(data.categories) if data.categories else None,
        organization_id=current_user.organization_id,
        created_by=current_user.id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _project_to_dict(project)


@router.get("/{project_id}")
def get_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Enkel project ophalen."""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.organization_id == current_user.organization_id,
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project niet gevonden")
    return _project_to_dict(project)


@router.put("/{project_id}")
def update_project(
    project_id: str,
    update: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Project bijwerken (admin of aanmaker)."""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.organization_id == current_user.organization_id,
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project niet gevonden")

    # Alleen admin of de aanmaker mag wijzigen
    if not current_user.is_org_admin and project.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Geen rechten om dit project te wijzigen")

    for field, value in update.model_dump(exclude_unset=True).items():
        if field == "categories" and value is not None:
            setattr(project, field, json.dumps(value))
        else:
            setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return _project_to_dict(project)


@router.delete("/{project_id}")
def delete_project(
    project_id: str,
    request: Request,
    hard: bool = Query(False, description="True = permanent verwijderen. False = archiveren (default, soft)."),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Verwijder project. Default = soft-archive. Met `?hard=true` permanent.

    Bij hard-delete worden meldingen ge-orphand (project_id wordt null);
    de meldingen blijven bestaan voor audit + historie.
    De audit-log entry blijft permanent — alleen de project-row verdwijnt.
    """
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.organization_id == current_user.organization_id,
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project niet gevonden")

    snapshot = {"name": project.name, "description": project.description, "status": project.status}

    if not hard:
        # Soft archive (existing behavior)
        if project.status == "archived":
            return {"message": "Reeds gearchiveerd", "deleted": False}
        project.status = "archived"
        db.commit()
        log_action(db, request, current_user,
                   action="project.archive", entity_type="project", entity_id=project.id,
                   before=snapshot)
        return {"message": f"Project '{project.name}' is gearchiveerd", "deleted": False}

    # HARD DELETE — alles wat naar dit project wijst eerst losmaken.
    #
    # Postgres weigert de DELETE zodra er ook maar één rij naar het project
    # verwijst, en gaf dan een 500 waar de gebruiker "onbekende fout" van zag.
    # In de praktijk raakte dat vrijwel elk actief project: het werkdagboek
    # vult zich dagelijks, en assets en inspecties hangen er per definitie aan.
    #
    # Alle zeven verwijzingen zijn nulbaar, dus we maken ze los in plaats van
    # ze mee te verwijderen. Dat past bij de bestaande keuze voor meldingen:
    # de registratie blijft bestaan voor de audit, alleen de koppeling met het
    # project verdwijnt. Wie het project weggooit, gooit niet zijn historie weg.
    from models import (Asset, DaybookEntry, EmailInboxRoute, IncomingWebhook,
                        Inspection, Oplevering, Organization)

    losgemaakt = {}
    for label, model, kolom in (
        ("meldingen",   Melding,          Melding.project_id),
        ("assets",      Asset,            Asset.project_id),
        ("inspecties",  Inspection,       Inspection.project_id),
        ("opleveringen", Oplevering,      Oplevering.project_id),
        ("werkdagboek", DaybookEntry,     DaybookEntry.project_id),
        ("e-mailroutes", EmailInboxRoute, EmailInboxRoute.default_project_id),
        ("webhooks",    IncomingWebhook,  IncomingWebhook.default_project_id),
    ):
        aantal = (db.query(model)
                    .filter(kolom == project_id)
                    .update({kolom: None}, synchronize_session=False))
        if aantal:
            losgemaakt[label] = aantal

    # Verraderlijkste van de zeven: de organisatie zelf kan dit project als
    # standaard voor het burgerportaal hebben. Dan komt de blokkade van de
    # ouder-rij en ziet de beheerder niets in het projectscherm.
    org_reset = (db.query(Organization)
                   .filter(Organization.public_meld_default_project_id == project_id)
                   .update({Organization.public_meld_default_project_id: None},
                           synchronize_session=False))
    if org_reset:
        losgemaakt["burgerportaal-standaard"] = org_reset

    melding_count = losgemaakt.get("meldingen", 0)
    db.delete(project)
    db.commit()

    log_action(db, request, current_user,
               action=ACTION.PROJECT_DELETE, entity_type="project", entity_id=project_id,
               before=snapshot,
               extra={"orphaned_meldingen": melding_count, "losgemaakt": losgemaakt})
    return {
        "message": f"Project '{snapshot['name']}' permanent verwijderd",
        "deleted": True,
        "orphaned": {"meldingen": melding_count},
    }
