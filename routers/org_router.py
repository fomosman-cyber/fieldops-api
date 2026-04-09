from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import User, Organization
from schemas import OrganizationResponse
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/organization", tags=["Organisatie"])


@router.get("/", response_model=OrganizationResponse)
def get_organization(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Huidige organisatie ophalen."""
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organisatie niet gevonden")
    return org


@router.get("/stats")
def get_org_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dashboard statistieken ophalen."""
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    active_users = db.query(User).filter(
        User.organization_id == org.id,
        User.is_active == True,
    ).count()

    return {
        "organization": org.name,
        "plan": org.plan.value,
        "status": org.status.value,
        "active_users": active_users,
        "max_users": org.max_users,
        "trial_ends_at": org.trial_ends_at.isoformat() if org.trial_ends_at else None,
    }
