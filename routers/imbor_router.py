"""IMBOR-taxonomie endpoints — read-only, auth required.

Geeft cascading dropdown-data voor asset-creation flow:
  hoofdgroep → asset-type → defaults (inspectie-norm + frequentie + properties)

Endpoints:
  GET /api/imbor/hoofdgroepen          11 categorieën met asset-counts
  GET /api/imbor/types                 alle ~50 asset-types
  GET /api/imbor/types/{code}          1 type met defaults
  GET /api/imbor/tree                  hierarchical voor cascading dropdown
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from auth import get_current_user
from models import User
import imbor_taxonomy as it

router = APIRouter(prefix="/api/imbor", tags=["IMBOR-taxonomie"])


@router.get("/hoofdgroepen")
def hoofdgroepen(current_user: User = Depends(get_current_user)):
    """11 IMBOR-hoofdgroepen + asset-count per groep."""
    return {"hoofdgroepen": it.list_hoofdgroepen()}


@router.get("/types")
def types(
    hoofdgroep: Optional[str] = Query(None, description="Filter op hoofdgroep-code"),
    current_user: User = Depends(get_current_user),
):
    """Alle asset-types, optioneel gefilterd op hoofdgroep."""
    types_list = it.list_types(hoofdgroep)
    return {"types": types_list, "total": len(types_list)}


@router.get("/types/{code}")
def type_info(code: str, current_user: User = Depends(get_current_user)):
    """Details van 1 asset-type — defaults voor inspectie + properties."""
    info = it.get_type_info(code)
    if not info:
        raise HTTPException(404, f"Asset-type '{code}' niet gevonden in IMBOR-taxonomie")
    return info


@router.get("/tree")
def tree(current_user: User = Depends(get_current_user)):
    """Hierarchical tree voor cascading dropdown UI."""
    return it.get_tree()
