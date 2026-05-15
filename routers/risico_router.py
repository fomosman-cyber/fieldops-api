"""Risico-matrix + CROW 96b endpoints.

  GET  /api/risico/asset/{asset_id}          Risico-assessment per asset
  GET  /api/risico/heatmap                   Heatmap-data alle assets
  GET  /api/crow96b/categories               Afzettings-categorieën
  GET  /api/crow96b/figuur/{key}             Afzettings-figuur details
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import User, Asset, Melding
from auth import get_current_user

import risico_matrix as rm
import crow96b_afzetting as c96b

router = APIRouter(prefix="/api", tags=["Risico-matrix + CROW 96b"])


# ─────────────────────────────────────────────────────────────────────────────
# Risico-matrix
# ─────────────────────────────────────────────────────────────────────────────

def _properties_dict(a: Asset) -> dict:
    """Parse Asset.properties_json -> dict (safe)."""
    if not a.properties_json:
        return {}
    try:
        import json
        return json.loads(a.properties_json) if isinstance(a.properties_json, str) else (a.properties_json or {})
    except Exception:
        return {}


def _count_meldingen_12mnd(db: Session, asset_id: str) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=365)
    return db.query(Melding).filter(
        Melding.asset_id == asset_id,
        Melding.created_at >= cutoff,
    ).count()


@router.get("/risico/asset/{asset_id}")
def risico_voor_asset(
    asset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    a = db.query(Asset).filter(
        Asset.id == asset_id,
        Asset.organization_id == current_user.organization_id,
        Asset.archived_at.is_(None),
    ).first()
    if not a:
        raise HTTPException(status_code=404, detail="Asset niet gevonden")

    meldingen_count = _count_meldingen_12mnd(db, asset_id)
    assessment = rm.assess_asset(
        asset_type=a.asset_type,
        conditie_score=a.condition_score,
        installed_at=a.installed_at,
        expected_lifespan_years=a.expected_lifespan_years,
        meldingen_12mnd=meldingen_count,
        property_dict=_properties_dict(a),
    )
    return {
        "asset_id": a.id,
        "asset_code": a.code,
        "asset_type": a.asset_type,
        "meldingen_12mnd": meldingen_count,
        **assessment,
        "rams_version": rm.RAMS_VERSION,
    }


@router.get("/risico/heatmap")
def risico_heatmap(
    asset_type: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    limit: int = Query(500, le=2000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Heatmap-data: top-N assets gesorteerd op risico-score (afnemend).

    Voor strategische asset-management dashboard.
    """
    q = db.query(Asset).filter(
        Asset.organization_id == current_user.organization_id,
        Asset.archived_at.is_(None),
    )
    if project_id:
        q = q.filter(Asset.project_id == project_id)
    if asset_type:
        q = q.filter(Asset.asset_type == asset_type)
    assets = q.limit(limit * 2).all()  # ophalen + sorteren

    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=365)
    melding_count_by_asset = {}
    for m in db.query(Melding.asset_id).filter(Melding.created_at >= cutoff).all():
        if m.asset_id:
            melding_count_by_asset[m.asset_id] = melding_count_by_asset.get(m.asset_id, 0) + 1

    for a in assets:
        mc = melding_count_by_asset.get(a.id, 0)
        assessment = rm.assess_asset(
            asset_type=a.asset_type,
            conditie_score=a.condition_score,
            installed_at=a.installed_at,
            expected_lifespan_years=a.expected_lifespan_years,
            meldingen_12mnd=mc,
            property_dict=_properties_dict(a),
        )
        items.append({
            "asset_id": a.id,
            "asset_code": a.code,
            "asset_type": a.asset_type,
            "asset_name": a.name,
            "lat": a.lat, "lng": a.lng,
            "condition_score": a.condition_score,
            "meldingen_12mnd": mc,
            **assessment,
        })
    items.sort(key=lambda x: -x["risico_score"])
    return {
        "count": len(items),
        "items": items[:limit],
        "rams_version": rm.RAMS_VERSION,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CROW 96b afzettingsplan
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/crow96b/categories")
def list_crow96b_categories(
    current_user: User = Depends(get_current_user),
):
    """Lijst beschikbare CROW 96b-werkcategorieën voor dropdown."""
    return {
        "categories": c96b.list_categories(),
        "version": c96b.CROW96B_VERSION,
    }


@router.get("/crow96b/figuur/{key}")
def get_crow96b_figuur(
    key: str,
    current_user: User = Depends(get_current_user),
):
    """Detail van één afzettings-figuur (borden, veiligheid, etc.)."""
    f = c96b.get_afzetting(key)
    if not f:
        raise HTTPException(status_code=404, detail=f"Werkcategorie '{key}' niet gevonden")
    return {**f, "key": key, "version": c96b.CROW96B_VERSION}
