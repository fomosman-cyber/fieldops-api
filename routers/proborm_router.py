"""PROBORM-koppeling — Project Boomonderhoud Registratie Module.

PROBORM is de landelijke richtlijn voor digitale bomeninspecties — een
gestandaardiseerd uitwisselingsformaat tussen gemeente, boomverzorger en
keuringsbureau. Niet wettelijk verplicht maar de facto standaard voor
inter-organisatie uitwisseling.

Specificatie: PROBORM v2.x JSON-formaat met velden:
  - boom_id (uniek per gemeente)
  - locatie (RD + WGS84)
  - boomsoort (Latijnse naam + Nederlands)
  - DBH, hoogte, kroon-diameter
  - VTA-risicoklasse (1-5)
  - inspectie-datum + inspecteur-naam
  - bevindingen + aanbevolen-maatregelen

Endpoints:
  GET  /api/proborm/export/{inspection_id}   Eén inspectie als PROBORM JSON
  GET  /api/proborm/export-batch              Bulk-export alle VTA-inspecties
  POST /api/proborm/import                    Import PROBORM-JSON van extern

Voor 'echte' PROBORM API integratie (registreren bij landelijke db) is
authenticatie + branche-credentials nodig — buiten scope MVP.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import User, Asset, Inspection, InspectionElement, InspectionAnswer
from auth import get_current_user

router = APIRouter(prefix="/api/proborm", tags=["PROBORM"])

PROBORM_VERSION = "PROBORM-v2.0-FieldOps-2026-05"


def _vta_klasse_uit_score(score: Optional[int]) -> Optional[int]:
    """Converteer NEN 1-6 score naar VTA risicoklasse 1-5.

    Onze interne scoring slaat VTA 4 en 5 op als 5 en 6 (niet-lineair).
    """
    if not score:
        return None
    return {1: 1, 2: 2, 3: 3, 4: 3, 5: 4, 6: 5}.get(int(score))


def _answer_text(antwoorden: list, code: str) -> Optional[str]:
    """Vind antwoord-tekst voor specifieke question_code."""
    for a in antwoorden or []:
        if a.question_code == code:
            return a.answer_value_text or (str(a.answer_value_number) if a.answer_value_number is not None else None)
    return None


def _build_proborm_record(insp: Inspection, asset: Optional[Asset]) -> dict:
    """Bouw PROBORM-JSON voor één boom-inspectie."""
    # Verzamel alle vragen-antwoorden over alle elementen
    all_answers = []
    for e in (insp.elementen or []):
        all_answers.extend(e.antwoorden or [])

    # Probeer DBH te vinden (VTA.DBH question)
    dbh_cm = _answer_text(all_answers, "VTA.DBH")
    vitaliteit = _answer_text(all_answers, "VTA.VITALITEIT")
    stam_holte = _answer_text(all_answers, "VTA.STAM_HOLTE_PCT")
    standplaats_type = _answer_text(all_answers, "VTA.STANDPLAATS_TYPE")

    # Bevindingen samenvoegen
    bevindingen = []
    for e in (insp.elementen or []):
        if e.bevindingen:
            bevindingen.append(f"[{e.element_naam}] {e.bevindingen}")

    # Defecten als gevonden-issues
    issues = []
    for e in (insp.elementen or []):
        for d in (e.defecten or []):
            issues.append({
                "element": e.element_code,
                "type": d.gebrek_code or "anders",
                "naam": d.gebrek_naam or "",
                "ernst": d.ernst, "intensiteit": d.intensiteit,
                "omvang_klasse": d.omvang_klasse,
                "score": d.score,
            })

    return {
        "proborm_version": PROBORM_VERSION,
        "inspection_id": insp.id,
        "boom_id": asset.code if asset else None,
        "boom_naam": asset.name if asset else None,
        "locatie": {
            "lat": asset.lat if asset else None,
            "lng": asset.lng if asset else None,
            "adres": asset.location_description if asset else None,
            "crs": "WGS84",
        },
        "boomsoort_latijn": None,  # MVP — uit asset.properties_json te halen
        "boomsoort_nl": None,
        "dbh_cm": dbh_cm,
        "vitaliteit_klasse": vitaliteit,
        "stam_holte_pct": stam_holte,
        "standplaats_type": standplaats_type,
        "inspectie_datum": (insp.inspectie_datum.date().isoformat()
                             if insp.inspectie_datum else None),
        "inspecteur": insp.inspecteur_naam,
        "vta_risicoklasse": _vta_klasse_uit_score(insp.conditiescore_overall),
        "conditie_label": insp.conditie_label or None,
        "bevindingen": " · ".join(bevindingen) if bevindingen else None,
        "issues": issues,
        "ondertekend": insp.status in ("signed", "delivered"),
        "ondertekend_op": insp.signed_at.isoformat() if insp.signed_at else None,
        "volgende_inspectie": (insp.volgende_inspectie_op.date().isoformat()
                                if insp.volgende_inspectie_op else None),
        "schema": "PROBORM v2.0",
        "bron_systeem": "FieldOps",
    }


@router.get("/export/{inspection_id}")
def export_proborm(
    inspection_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Exporteer één boom-inspectie als PROBORM v2 JSON."""
    insp = db.query(Inspection).options(
        joinedload(Inspection.elementen).joinedload(InspectionElement.antwoorden),
        joinedload(Inspection.elementen).joinedload(InspectionElement.defecten),
    ).filter(
        Inspection.id == inspection_id,
        Inspection.organization_id == current_user.organization_id,
    ).first()
    if not insp:
        raise HTTPException(status_code=404, detail="Inspectie niet gevonden")
    if insp.kunstwerk_type != "boom":
        raise HTTPException(status_code=400,
                            detail="PROBORM-export is alleen voor boom-inspecties")
    asset = db.query(Asset).filter(Asset.id == insp.asset_id).first() if insp.asset_id else None
    return _build_proborm_record(insp, asset)


@router.get("/export-batch")
def export_proborm_batch(
    limit: int = Query(100, ge=1, le=1000),
    project_id: Optional[str] = Query(None),
    only_signed: bool = Query(True, description="Alleen ondertekende inspecties"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bulk-export alle boom-inspecties als PROBORM-records."""
    q = db.query(Inspection).options(
        joinedload(Inspection.elementen).joinedload(InspectionElement.antwoorden),
        joinedload(Inspection.elementen).joinedload(InspectionElement.defecten),
    ).filter(
        Inspection.organization_id == current_user.organization_id,
        Inspection.kunstwerk_type == "boom",
    )
    if project_id:
        q = q.filter(Inspection.project_id == project_id)
    if only_signed:
        q = q.filter(Inspection.status.in_(("signed", "delivered")))
    insps = q.order_by(Inspection.signed_at.desc().nullslast()).limit(limit).all()

    assets_by_id = {}
    asset_ids = [i.asset_id for i in insps if i.asset_id]
    if asset_ids:
        for a in db.query(Asset).filter(Asset.id.in_(asset_ids)).all():
            assets_by_id[a.id] = a

    records = [_build_proborm_record(i, assets_by_id.get(i.asset_id)) for i in insps]
    return {
        "proborm_version": PROBORM_VERSION,
        "count": len(records),
        "records": records,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "organization_id": current_user.organization_id,
            "project_id": project_id,
            "only_signed": only_signed,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Import — voor migratie uit andere PROBORM-bronnen
# ─────────────────────────────────────────────────────────────────────────────

class ProbormImportRecord(BaseModel):
    boom_id: str
    boom_naam: Optional[str] = None
    locatie_lat: float
    locatie_lng: float
    boomsoort_latijn: Optional[str] = None
    dbh_cm: Optional[float] = None
    vta_risicoklasse: Optional[int] = Field(None, ge=1, le=5)
    inspectie_datum: Optional[str] = None
    inspecteur: Optional[str] = None


class ProbormImportRequest(BaseModel):
    records: List[ProbormImportRecord]
    create_assets: bool = Field(True, description="Maak Asset-records aan als nog niet bestaan")


@router.post("/import")
def import_proborm(
    payload: ProbormImportRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Import PROBORM-records — maak Assets aan voor onbekende bomen.

    Bedoeld voor migratie uit andere systemen (Geomilieu, eigen GIS).
    Voor productie-volume: gebruik staging-tabel + batch-commit.
    """
    if current_user.role not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Alleen admin of manager")

    created = 0
    updated = 0
    skipped = 0
    for r in payload.records:
        existing = db.query(Asset).filter(
            Asset.organization_id == current_user.organization_id,
            Asset.code == r.boom_id,
            Asset.asset_type == "boom",
        ).first()
        if existing:
            if r.locatie_lat: existing.lat = r.locatie_lat
            if r.locatie_lng: existing.lng = r.locatie_lng
            if r.vta_risicoklasse:
                # Convert VTA 1-5 → onze NEN 1-6
                vta_to_nen = {1: 1, 2: 2, 3: 3, 4: 5, 5: 6}
                existing.condition_score = vta_to_nen.get(r.vta_risicoklasse)
            updated += 1
            continue

        if not payload.create_assets:
            skipped += 1
            continue

        # Maak nieuwe Asset
        vta_to_nen = {1: 1, 2: 2, 3: 3, 4: 5, 5: 6}
        new_asset = Asset(
            code=r.boom_id,
            name=r.boom_naam or f"Boom {r.boom_id}",
            asset_type="boom",
            lat=r.locatie_lat, lng=r.locatie_lng,
            condition_score=vta_to_nen.get(r.vta_risicoklasse) if r.vta_risicoklasse else None,
            organization_id=current_user.organization_id,
            created_by=current_user.id,
        )
        db.add(new_asset)
        created += 1

    db.commit()
    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "proborm_version": PROBORM_VERSION,
    }
