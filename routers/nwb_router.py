"""NWB-Wegvakken endpoints — zoek + bulk-import als assets.

  GET  /api/nwb/zoek?straat=Verdilaan&gemeente=Westland
  GET  /api/nwb/wegvak/{wvk_id}
  POST /api/nwb/import-wegvakken
"""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import Asset, User
from auth import get_current_user
from permissions import require_org_admin, UserRole
from audit import log_action
import nwb_integration as nwb

router = APIRouter(prefix="/api/nwb", tags=["NWB-Wegvakken"])


# ════════════════════════════════════════════════════════════
# Pydantic schemas
# ════════════════════════════════════════════════════════════

class WvkImportItem(BaseModel):
    wvk_id: str
    straatnaam: Optional[str] = None
    gemeente: Optional[str] = None
    wegnummer: Optional[str] = None
    wvk_begdat: Optional[str] = None
    jte_id_beg: Optional[str] = None
    jte_id_end: Optional[str] = None
    geometry: dict   # GeoJSON LineString of MultiLineString
    length_m: Optional[float] = None


class ImportWegvakkenRequest(BaseModel):
    project_id: Optional[str] = None
    parent_asset_id: Optional[str] = None     # om wegvakken aan een parent-weg te hangen
    asset_type: str = Field(default="wegdek")
    code_prefix: Optional[str] = None         # bv "WL" → codes worden WL-WVK-12345
    items: list[WvkImportItem]


# ════════════════════════════════════════════════════════════
# Endpoints
# ════════════════════════════════════════════════════════════

@router.get("/zoek")
def zoek_wegvakken(
    straat: str = Query(..., min_length=2, description="Straatnaam (b.v. 'Verdilaan')"),
    gemeente: Optional[str] = Query(None, description="Gemeente (b.v. 'Westland'). Optioneel."),
    max_results: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Zoek wegvakken in NWB op straatnaam (en optioneel gemeente).

    Returns lijst met geometry + WVK_ID + lengte. Voor frontend om op kaart
    te tonen + checkbox-selectie te doen.

    PDOK WFS is gratis open data — geen API-key nodig.
    """
    try:
        if gemeente:
            results = nwb.search_by_street_and_gemeente(straat, gemeente, max_features=max_results)
        else:
            results = nwb.search_by_street_anywhere(straat, max_features=max_results)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"PDOK WFS-fout: {e}")

    # Markeer wegvakken die al als asset bestaan in mijn org
    if results:
        wvk_ids = [r["wvk_id"] for r in results if r.get("wvk_id")]
        if wvk_ids:
            existing = (db.query(Asset.nwb_wvk_id, Asset.id, Asset.code, Asset.archived_at)
                          .filter(Asset.organization_id == current_user.organization_id,
                                  Asset.nwb_wvk_id.in_(wvk_ids))
                          .all())
            existing_map = {e.nwb_wvk_id: {"asset_id": e.id, "code": e.code,
                                           "archived": e.archived_at is not None} for e in existing}
            for r in results:
                wvk = r.get("wvk_id")
                if wvk in existing_map:
                    r["existing_asset"] = existing_map[wvk]

    return {
        "count": len(results),
        "straat": straat,
        "gemeente": gemeente,
        "wegvakken": results,
    }


@router.get("/wegvak/{wvk_id}")
def get_wegvak(
    wvk_id: str,
    current_user: User = Depends(get_current_user),
):
    """Haal één specifiek wegvak op via WVK_ID."""
    try:
        result = nwb.get_wegvak_by_id(wvk_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"PDOK WFS-fout: {e}")
    if not result:
        raise HTTPException(status_code=404, detail=f"Wegvak {wvk_id} niet gevonden in NWB")
    return result


@router.post("/import-wegvakken")
def import_wegvakken(
    payload: ImportWegvakkenRequest,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Bulk-create assets uit NWB-wegvak-data.

    Per wegvak: een Asset met geometry + WVK_ID + length_m + audit-velden.
    Skip wegvakken die al bestaan (op WVK_ID) in deze organisatie.

    Alleen admin/manager — staan stevig in de audit-trail.
    """
    if current_user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        raise HTTPException(status_code=403, detail="Alleen admin/manager kan wegvakken importeren")

    if not payload.items:
        raise HTTPException(status_code=400, detail="Geen wegvakken aangeleverd")

    # Validate parent_asset (als opgegeven) bestaat in mijn org
    if payload.parent_asset_id:
        parent = db.query(Asset).filter(
            Asset.id == payload.parent_asset_id,
            Asset.organization_id == current_user.organization_id,
        ).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent-asset niet gevonden")

    # Reeds geïmporteerde WVK_ID's deze org
    existing_ids = {r[0] for r in (db.query(Asset.nwb_wvk_id)
                                     .filter(Asset.organization_id == current_user.organization_id,
                                             Asset.nwb_wvk_id.isnot(None))
                                     .all())}

    created = []
    skipped = []
    errors = []

    for idx, item in enumerate(payload.items):
        if item.wvk_id in existing_ids:
            skipped.append({"wvk_id": item.wvk_id, "reason": "reeds geïmporteerd"})
            continue

        # Bouw asset-code: prefix-WVK_ID, of fallback "WVK-{id}"
        prefix = (payload.code_prefix or "").strip()
        code = (f"{prefix}-WVK-{item.wvk_id}" if prefix else f"WVK-{item.wvk_id}")
        # Korte naam voor UI: straatnaam (eventueel met huisnr-bereik later)
        name = item.straatnaam or f"Wegvak {item.wvk_id}"

        try:
            geom_str = json.dumps(item.geometry, separators=(",", ":"))
        except Exception as e:
            errors.append({"wvk_id": item.wvk_id, "error": f"geometry serialisatie: {e}"})
            continue

        # Center-punt uit geometry voor lat/lng (eerste punt of midden)
        center_lat = None
        center_lng = None
        try:
            coords = item.geometry.get("coordinates", [])
            if item.geometry.get("type") == "LineString" and coords:
                mid_idx = len(coords) // 2
                center_lng, center_lat = coords[mid_idx][0], coords[mid_idx][1]
            elif item.geometry.get("type") == "MultiLineString" and coords and coords[0]:
                first_line = coords[0]
                mid_idx = len(first_line) // 2
                center_lng, center_lat = first_line[mid_idx][0], first_line[mid_idx][1]
        except Exception:
            pass

        wvk_begdat = nwb.parse_begdat(item.wvk_begdat)

        try:
            asset = Asset(
                code=code,
                name=name,
                asset_type=payload.asset_type,
                organization_id=current_user.organization_id,
                created_by=current_user.id,
                project_id=payload.project_id,
                parent_asset_id=payload.parent_asset_id,
                lat=center_lat, lng=center_lng,
                location_description=(f"{item.straatnaam}, {item.gemeente}" if item.straatnaam and item.gemeente else None),
                # NWB
                geometry_geojson=geom_str,
                length_m=item.length_m,
                is_segment=True,
                nwb_wvk_id=item.wvk_id,
                nwb_wvk_begdat=wvk_begdat,
                nwb_jte_id_beg=item.jte_id_beg,
                nwb_jte_id_end=item.jte_id_end,
            )
            db.add(asset)
            db.flush()
            created.append({"wvk_id": item.wvk_id, "asset_id": asset.id, "code": code,
                            "length_m": item.length_m})
        except Exception as e:
            errors.append({"wvk_id": item.wvk_id, "error": str(e)[:200]})

    db.commit()

    log_action(db, request, current_user,
               action="nwb.wegvakken_imported",
               entity_type="asset",
               extra={
                   "imported": len(created),
                   "skipped": len(skipped),
                   "errors": len(errors),
                   "project_id": payload.project_id,
                   "parent_asset_id": payload.parent_asset_id,
               })

    return {
        "imported": len(created),
        "skipped": len(skipped),
        "errors": len(errors),
        "created": created,
        "skipped_details": skipped,
        "error_details": errors,
    }
