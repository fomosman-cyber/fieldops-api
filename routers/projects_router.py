from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Project, User
from schemas import ProjectCreate, ProjectResponse, ProjectUpdate
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/projects", tags=["Projecten"])


@router.get("/", response_model=list[ProjectResponse])
def list_projects(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alle projecten van de organisatie ophalen."""
    return (
        db.query(Project)
        .filter(
            Project.organization_id == current_user.organization_id,
            Project.status != "archived",
        )
        .order_by(Project.created_at.desc())
        .all()
    )


@router.post("/", response_model=ProjectResponse)
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
        organization_id=current_user.organization_id,
        created_by=current_user.id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectResponse)
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
    return project


@router.put("/{project_id}", response_model=ProjectResponse)
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
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}")
def archive_project(
    project_id: str,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Project archiveren (alleen admin)."""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.organization_id == current_user.organization_id,
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project niet gevonden")

    project.status = "archived"
    db.commit()
    return {"message": f"Project '{project.name}' is gearchiveerd"}
