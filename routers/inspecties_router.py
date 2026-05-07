"""AI-inspecties router.

Endpoints:
  POST /api/inspecties/analyse-foto       multipart-upload (file) → Claude → AIAnalysis
  POST /api/inspecties/analyse-url        analyse op bestaande photo_url
  GET  /api/inspecties/                   lijst (eigen organisatie)
  GET  /api/inspecties/{id}               detail
  POST /api/inspecties/{id}/accept        markeer als geaccepteerd, optioneel naar melding
  POST /api/inspecties/{id}/reject        markeer als afgewezen met reden

Mens-in-de-loop: het analyse-resultaat is een SUGGESTIE. Pas na een expliciete
accept-action wordt 't naar een melding gepusht.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import get_db
from models import AIAnalysis, Asset, Melding, User
from schemas import (
    InspectionAnalyseUrlRequest, InspectionResponse,
    InspectionAcceptRequest, InspectionRejectRequest,
)
from auth import get_current_user
from permissions import can_create_meldingen
from audit import log_action, ACTION
from inspections import analyze_image, detect_media_type, sha256_of, PROMPT_VERSION

router = APIRouter(prefix="/api/inspecties", tags=["AI-inspecties"])

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


def _serialize(a: AIAnalysis) -> dict:
    bevindingen = []
    if a.bevindingen_json:
        try:
            bevindingen = json.loads(a.bevindingen_json) or []
        except json.JSONDecodeError:
            bevindingen = []
    return {
        "id": a.id,
        "photo_url": a.photo_url,
        "asset_type_context": a.asset_type_context,
        "asset_id": a.asset_id,
        "melding_id": a.melding_id,
        "schade_aanwezig": a.schade_aanwezig,
        "schade_type": a.schade_type,
        "ernst": a.ernst,
        "kosten_klasse": a.kosten_klasse,
        "aanbevolen_actie": a.aanbevolen_actie,
        "bevindingen": bevindingen,
        "confidence": a.confidence,
        "model_id": a.model_id,
        "prompt_version": a.prompt_version,
        "accepted_at": a.accepted_at,
        "rejected_at": a.rejected_at,
        "rejection_reason": a.rejection_reason,
        "created_at": a.created_at,
    }


def _resolve_asset_type(db: Session, current_user: User,
                       asset_id: Optional[str], explicit: Optional[str]) -> Optional[str]:
    """Asset-type uit de gekoppelde asset halen tenzij expliciet meegegeven."""
    if explicit:
        return explicit
    if asset_id:
        asset = db.query(Asset).filter(
            Asset.id == asset_id,
            Asset.organization_id == current_user.organization_id,
        ).first()
        if asset:
            return asset.asset_type
    return None


def _persist(
    db: Session, current_user: User, *,
    photo_url: Optional[str], photo_sha256: Optional[str],
    asset_type_context: Optional[str], asset_id: Optional[str], melding_id: Optional[str],
    result: dict,
) -> AIAnalysis:
    rec = AIAnalysis(
        photo_url=photo_url,
        photo_sha256=photo_sha256,
        asset_type_context=asset_type_context,
        asset_id=asset_id,
        melding_id=melding_id,
        prompt_version=result.get("_prompt_version") or PROMPT_VERSION,
        model_id=result.get("_model_id") or "unknown",
        schade_aanwezig=result.get("schade_aanwezig"),
        schade_type=result.get("schade_type"),
        ernst=result.get("ernst"),
        kosten_klasse=result.get("kosten_klasse"),
        aanbevolen_actie=result.get("aanbevolen_actie"),
        bevindingen_json=json.dumps(result.get("bevindingen") or [], ensure_ascii=False),
        confidence=result.get("confidence"),
        raw_response=result.get("_raw_response"),
        organization_id=current_user.organization_id,
        created_by=current_user.id,
    )
    db.add(rec); db.commit(); db.refresh(rec)
    return rec


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/analyse-batch")
async def analyse_batch(
    request: Request,
    files: List[UploadFile] = File(..., description="Meerdere foto's (drone/inspectie-batch). Max 25 per call."),
    asset_id: Optional[str] = Form(None),
    asset_type: Optional[str] = Form(None),
    extra_context: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bulk-analyse van meerdere foto's tegen Claude vision (drone-workflow).

    Per foto wordt een AIAnalysis record aangemaakt en losse audit-events gelogd.
    De call retourneert per foto de status (succes / fout) zodat de UI kan tonen
    welke nog menselijke review nodig hebben."""
    if not can_create_meldingen(current_user):
        raise HTTPException(status_code=403, detail="Geen rechten voor AI-inspectie")
    if not files:
        raise HTTPException(status_code=400, detail="Minimaal één bestand vereist")
    if len(files) > 25:
        raise HTTPException(status_code=400, detail="Max 25 foto's per call (split anders je upload)")

    resolved_type = _resolve_asset_type(db, current_user, asset_id, asset_type)
    items = []

    from inspections import analyze_image  # lokaal om import-cycli te vermijden

    for f in files:
        try:
            raw = await f.read()
            if not raw or len(raw) > MAX_IMAGE_BYTES:
                items.append({"filename": f.filename, "ok": False,
                              "error": "leeg of te groot (>10 MB)"})
                continue
            media = detect_media_type(f.filename, f.content_type)
            if not media.startswith("image/"):
                items.append({"filename": f.filename, "ok": False,
                              "error": f"geen afbeelding ({media})"})
                continue

            result = analyze_image(image_bytes=raw, image_media_type=media,
                                   asset_type=resolved_type, extra_context=extra_context)
            rec = _persist(
                db, current_user,
                photo_url=None, photo_sha256=sha256_of(raw),
                asset_type_context=resolved_type, asset_id=asset_id, melding_id=None,
                result=result,
            )
            log_action(db, request, current_user,
                       action=ACTION.AI_ANALYSIS_RUN, entity_type="ai_analysis", entity_id=rec.id,
                       extra={"asset_id": asset_id, "batch": True,
                              "filename": f.filename,
                              "model_id": rec.model_id, "ernst": rec.ernst,
                              "confidence": rec.confidence})
            items.append({"filename": f.filename, "ok": True,
                          "analysis": _serialize(rec)})
        except Exception as e:
            items.append({"filename": f.filename, "ok": False, "error": str(e)[:300]})

    succeeded = sum(1 for i in items if i["ok"])
    return {"total": len(items), "succeeded": succeeded, "failed": len(items) - succeeded,
            "items": items}


@router.post("/analyse-foto", response_model=InspectionResponse)
async def analyse_foto_upload(
    request: Request,
    file: UploadFile = File(..., description="Afbeelding (jpeg/png/webp/gif, max 10MB)"),
    asset_id: Optional[str] = Form(None),
    melding_id: Optional[str] = Form(None),
    asset_type: Optional[str] = Form(None),
    extra_context: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload een foto en krijg direct AI-schadeanalyse terug."""
    if not can_create_meldingen(current_user):
        raise HTTPException(status_code=403, detail="Geen rechten voor AI-inspectie")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Leeg bestand")
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail=f"Bestand te groot (max {MAX_IMAGE_BYTES // (1024*1024)} MB)")

    media = detect_media_type(file.filename, file.content_type)
    if not media.startswith("image/"):
        raise HTTPException(status_code=400, detail=f"Onverwacht bestandstype: {media}")

    resolved_type = _resolve_asset_type(db, current_user, asset_id, asset_type)

    try:
        result = analyze_image(
            image_bytes=raw, image_media_type=media,
            asset_type=resolved_type, extra_context=extra_context,
        )
    except Exception as e:
        # Audit ook de mislukte poging
        log_action(db, request, current_user,
                   action=ACTION.AI_ANALYSIS_RUN, entity_type="ai_analysis",
                   extra={"status": "error", "error": str(e)[:300],
                          "asset_id": asset_id, "asset_type_context": resolved_type})
        raise HTTPException(status_code=502, detail=f"AI-analyse mislukt: {e}")

    rec = _persist(
        db, current_user,
        photo_url=None, photo_sha256=sha256_of(raw),
        asset_type_context=resolved_type, asset_id=asset_id, melding_id=melding_id,
        result=result,
    )
    log_action(db, request, current_user,
               action=ACTION.AI_ANALYSIS_RUN, entity_type="ai_analysis", entity_id=rec.id,
               extra={"asset_id": asset_id, "melding_id": melding_id,
                      "asset_type_context": resolved_type,
                      "model_id": rec.model_id, "prompt_version": rec.prompt_version,
                      "ernst": rec.ernst, "confidence": rec.confidence})
    return _serialize(rec)


@router.post("/analyse-url", response_model=InspectionResponse)
def analyse_foto_url(
    payload: InspectionAnalyseUrlRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run AI-analyse op een al-geüploade foto (URL)."""
    if not can_create_meldingen(current_user):
        raise HTTPException(status_code=403, detail="Geen rechten voor AI-inspectie")

    resolved_type = _resolve_asset_type(db, current_user, payload.asset_id, payload.asset_type)

    try:
        result = analyze_image(
            image_url=payload.photo_url,
            asset_type=resolved_type, extra_context=payload.extra_context,
        )
    except Exception as e:
        log_action(db, request, current_user,
                   action=ACTION.AI_ANALYSIS_RUN, entity_type="ai_analysis",
                   extra={"status": "error", "error": str(e)[:300],
                          "asset_id": payload.asset_id})
        raise HTTPException(status_code=502, detail=f"AI-analyse mislukt: {e}")

    rec = _persist(
        db, current_user,
        photo_url=payload.photo_url, photo_sha256=None,
        asset_type_context=resolved_type,
        asset_id=payload.asset_id, melding_id=payload.melding_id,
        result=result,
    )
    log_action(db, request, current_user,
               action=ACTION.AI_ANALYSIS_RUN, entity_type="ai_analysis", entity_id=rec.id,
               extra={"asset_id": payload.asset_id, "melding_id": payload.melding_id,
                      "model_id": rec.model_id, "prompt_version": rec.prompt_version,
                      "ernst": rec.ernst, "confidence": rec.confidence})
    return _serialize(rec)


@router.get("/", response_model=list[InspectionResponse])
def list_inspections(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    asset_id: Optional[str] = Query(None),
    melding_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    q = db.query(AIAnalysis).filter(AIAnalysis.organization_id == current_user.organization_id)
    if asset_id: q = q.filter(AIAnalysis.asset_id == asset_id)
    if melding_id: q = q.filter(AIAnalysis.melding_id == melding_id)
    rows = q.order_by(desc(AIAnalysis.created_at)).limit(limit).all()
    return [_serialize(r) for r in rows]


@router.get("/{analysis_id}", response_model=InspectionResponse)
def get_inspection(
    analysis_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rec = db.query(AIAnalysis).filter(
        AIAnalysis.id == analysis_id,
        AIAnalysis.organization_id == current_user.organization_id,
    ).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Analyse niet gevonden")
    return _serialize(rec)


@router.post("/{analysis_id}/accept", response_model=InspectionResponse)
def accept_inspection(
    analysis_id: str,
    payload: InspectionAcceptRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Markeer analyse als geaccepteerd. Optioneel: pas de gekoppelde melding aan
    met de AI-suggestie (category/priority/description)."""
    rec = db.query(AIAnalysis).filter(
        AIAnalysis.id == analysis_id,
        AIAnalysis.organization_id == current_user.organization_id,
    ).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Analyse niet gevonden")
    if rec.accepted_at:
        return _serialize(rec)
    if rec.rejected_at:
        raise HTTPException(status_code=400, detail="Reeds afgewezen — kan niet accepteren")

    rec.accepted_at = datetime.now(timezone.utc)
    rec.accepted_by = current_user.id

    # Optioneel: AI-suggestie naar melding pushen
    if payload.apply_to_melding and rec.melding_id:
        m = db.query(Melding).filter(
            Melding.id == rec.melding_id,
            Melding.organization_id == current_user.organization_id,
        ).first()
        if m:
            if rec.schade_type and not m.category:
                m.category = rec.schade_type
            if rec.ernst and rec.ernst in ("ernstig", "kritiek"):
                m.priority = "hoog" if rec.ernst == "ernstig" else "kritiek"
            # Bevindingen aanvullen op beschrijving (niet overschrijven)
            try:
                bev = json.loads(rec.bevindingen_json or "[]")
            except json.JSONDecodeError:
                bev = []
            if bev and not m.description:
                m.description = "AI-bevindingen:\n- " + "\n- ".join(bev)

    db.commit(); db.refresh(rec)
    log_action(db, request, current_user,
               action=ACTION.AI_ANALYSIS_ACCEPT, entity_type="ai_analysis", entity_id=rec.id,
               extra={"applied_to_melding": payload.apply_to_melding and bool(rec.melding_id)})
    return _serialize(rec)


@router.post("/{analysis_id}/reject", response_model=InspectionResponse)
def reject_inspection(
    analysis_id: str,
    payload: InspectionRejectRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rec = db.query(AIAnalysis).filter(
        AIAnalysis.id == analysis_id,
        AIAnalysis.organization_id == current_user.organization_id,
    ).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Analyse niet gevonden")
    if rec.rejected_at:
        return _serialize(rec)
    if rec.accepted_at:
        raise HTTPException(status_code=400, detail="Reeds geaccepteerd — kan niet afwijzen")

    rec.rejected_at = datetime.now(timezone.utc)
    rec.rejection_reason = payload.reason[:500]
    db.commit(); db.refresh(rec)
    log_action(db, request, current_user,
               action=ACTION.AI_ANALYSIS_REJECT, entity_type="ai_analysis", entity_id=rec.id,
               extra={"reason": payload.reason[:300]})
    return _serialize(rec)
