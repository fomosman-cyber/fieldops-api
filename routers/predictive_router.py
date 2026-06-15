"""Predictive Maintenance endpoints.

  GET /api/predictive/at-risk             top-N assets boven een drempel
  GET /api/predictive/asset/{asset_id}    risicoscore + rationale voor één asset
  GET /api/predictive/summary             distributie over band + asset-type
  GET /api/predictive/clusters            geografische meldingen-clusters (wijk-alerts)
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import Asset, User
from auth import get_current_user
from predictive import compute_asset_risk, list_at_risk, find_geo_clusters

router = APIRouter(prefix="/api/predictive", tags=["Predictive Maintenance"])


@router.get("/at-risk")
def at_risk(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    min_score: int = Query(60, ge=0, le=100),
    asset_type: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None, description="Filter op project (#13)"),
    limit: int = Query(100, ge=1, le=500),
):
    return list_at_risk(db, current_user.organization_id,
                        min_score=min_score, asset_type=asset_type,
                        project_id=project_id, limit=limit)


@router.get("/asset/{asset_id}")
def asset_risk(
    asset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    a = db.query(Asset).filter(
        Asset.id == asset_id,
        Asset.organization_id == current_user.organization_id,
    ).first()
    if not a:
        raise HTTPException(status_code=404, detail="Asset niet gevonden")
    return compute_asset_risk(db, a)


@router.get("/summary")
def summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Snelle aggregatie voor het dashboard."""
    assets = (db.query(Asset)
                .filter(Asset.organization_id == current_user.organization_id,
                        Asset.archived_at.is_(None)).all())
    bands = {"laag": 0, "matig": 0, "hoog": 0}
    by_type: dict[str, dict[str, int]] = {}
    for a in assets:
        r = compute_asset_risk(db, a)
        bands[r["band"]] += 1
        t = by_type.setdefault(a.asset_type, {"laag": 0, "matig": 0, "hoog": 0})
        t[r["band"]] += 1
    return {
        "total_assets": len(assets),
        "bands": bands,
        "by_asset_type": by_type,
    }


@router.get("/clusters")
def clusters(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    window_days: int = Query(30, ge=7, le=180),
    radius_m: int = Query(200, ge=50, le=2000),
    min_count: int = Query(3, ge=2, le=20),
):
    """Geografische meldingen-clusters in een tijdvenster.

    Toont wijken/straten waar meerdere meldingen samenkomen — typisch een
    signaal voor onderliggend infra-probleem (verzakking, slechte fundering,
    mast-corrosie). Per-asset-scores missen dit type signaal omdat zij elk
    afzonderlijk niet boven de drempel uitkomen.

    Output gesorteerd op cluster-grootte (groot eerst) zodat het ergste
    bovenaan staat in de dashboard-lijst.
    """
    return find_geo_clusters(
        db, current_user.organization_id,
        window_days=window_days, radius_m=radius_m, min_count=min_count,
    )
