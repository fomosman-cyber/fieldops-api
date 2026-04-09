from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import User, Organization, DemoRequest
from auth import get_current_user

router = APIRouter(prefix="/api/admin", tags=["Admin"])


def require_owner(current_user: User = Depends(get_current_user)) -> User:
    """Alleen de eigenaar (fomosman@gmail.com) of org admins met org 'FieldOps'."""
    if not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Geen toegang - eigenaar rechten vereist")
    org = current_user.organization
    if org.name != "FieldOps":
        raise HTTPException(status_code=403, detail="Geen toegang - alleen platform eigenaar")
    return current_user


@router.get("/overview")
def admin_overview(
    current_user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Volledig overzicht voor de platform eigenaar."""
    # Alle organisaties
    orgs = db.query(Organization).order_by(Organization.created_at.desc()).all()
    org_data = []
    for o in orgs:
        user_count = db.query(User).filter(
            User.organization_id == o.id,
            User.is_active == True,
        ).count()
        org_data.append({
            "id": o.id,
            "name": o.name,
            "plan": o.plan.value if o.plan else None,
            "status": o.status.value if o.status else None,
            "max_users": o.max_users,
            "user_count": user_count,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        })

    # Alle gebruikers
    users = db.query(User).order_by(User.created_at.desc()).all()
    users_data = []
    for u in users:
        org_name = u.organization.name if u.organization else "-"
        users_data.append({
            "id": u.id,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "email": u.email,
            "role": u.role.value if u.role else None,
            "is_active": u.is_active,
            "is_org_admin": u.is_org_admin,
            "org_name": org_name,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login": u.last_login.isoformat() if u.last_login else None,
        })

    # Demo aanvragen
    demos = db.query(DemoRequest).order_by(DemoRequest.created_at.desc()).all()
    demos_data = []
    for d in demos:
        demos_data.append({
            "id": d.id,
            "first_name": d.first_name,
            "last_name": d.last_name,
            "company_name": d.company_name,
            "email": d.email,
            "plan": d.plan.value if d.plan else None,
            "num_users": d.num_users,
            "processed": d.processed,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        })

    return {
        "organizations": org_data,
        "all_users": users_data,
        "total_users": len(users_data),
        "demo_requests": demos_data,
    }
