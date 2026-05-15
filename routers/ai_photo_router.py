"""AI-foto-classificatie — smart stub met heuristieken.

Voor inspecteurs in opleiding die hulp willen bij defect-herkenning:
upload een foto + context (asset_type + element_code), krijg suggesties
voor mogelijke defect-types met waarschijnlijkheid.

**Belangrijk:** dit is een **heuristieken-stub**, geen echte ML-classificatie.
Een productie-versie heeft nodig:
  - Getrainde model op 10.000+ gelabelde defect-foto's per asset-type
  - GPU-inference of cloud-API (Azure Custom Vision / GCP AutoML)
  - Confidence-callibration + uitleg

In MVP genereren we suggesties op basis van:
  1. Asset-type + element-code → top-N waarschijnlijke defecten uit taxonomy
  2. Foto-metadata (helderheid, kleur, EXIF) → simpele heuristieken
  3. Eerder geconstateerde defecten op vergelijkbare assets → frequentie-prior

Output: gerangschikte lijst defect-codes met confidence-percentage.
Endpoint:
  POST /api/ai/classify   Foto + context → defect-suggesties
  GET  /api/ai/status     Versie + capabilities
"""
from __future__ import annotations
import base64
import io
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import User, Asset, Inspection, InspectionDefect, InspectionElement
from auth import get_current_user

import kunstwerken_taxonomy as kt

router = APIRouter(prefix="/api/ai", tags=["AI-foto-classificatie"])

AI_VERSION = "ai-stub.v0.1-heuristic-2026-05"


class ClassifyRequest(BaseModel):
    asset_type: str = Field(..., description="brug, boom, riolering, ...")
    element_code: Optional[str] = Field(None, description="bv. BRUG.PIJLERS, BOOM.STAM")
    photo_data_url: Optional[str] = Field(None, description="data:image/...;base64,..")
    photo_mean_brightness: Optional[float] = Field(None, ge=0, le=255,
        description="Optionele helderheid 0-255 (als frontend al heeft uitgerekend)")
    photo_dominant_color: Optional[str] = Field(None,
        description="Hex kleur '#rrggbb' voor heuristieken")


class DefectSuggestion(BaseModel):
    gebrek_code: str
    gebrek_naam: str
    confidence_pct: int  # 0-100
    reason: str          # waarom voorgesteld
    method: str = "heuristic"


@router.get("/status")
def ai_status(current_user: User = Depends(get_current_user)):
    """Versie + capabilities."""
    return {
        "version": AI_VERSION,
        "method": "heuristic-stub",
        "supported_inputs": ["asset_type", "element_code", "photo_data_url",
                              "photo_mean_brightness", "photo_dominant_color"],
        "supported_asset_types": list(kt.KUNSTWERK_TYPES.keys()),
        "limitations": [
            "Geen echte vision-model — heuristieken op metadata + taxonomy-priors",
            "Productie-versie heeft GPU-inference op getraind model nodig",
            "Confidence-cijfers zijn indicatief, geen statistisch valide kansen",
        ],
        "next_milestones": [
            "Verzamel 10k+ gelabelde foto's per asset-type",
            "Train op Azure Custom Vision of GCP AutoML",
            "A/B-test heuristiek vs. model op test-set",
        ],
    }


def _color_to_rgb(hex_color: str) -> Optional[tuple]:
    if not hex_color or not hex_color.startswith("#"):
        return None
    try:
        h = hex_color.lstrip("#")
        if len(h) == 6:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        pass
    return None


def _frequency_prior(db: Session, organization_id: str,
                     element_code: str) -> dict:
    """Tel hoe vaak elke defect-code voorkwam op vergelijkbare elementen.

    Returns dict: {defect_code: count}. Gebruikt als prior voor confidence.
    """
    rows = db.query(
        InspectionDefect.gebrek_code,
    ).join(InspectionElement, InspectionDefect.element_id == InspectionElement.id).filter(
        InspectionElement.element_code == element_code,
        InspectionDefect.organization_id == organization_id,
        InspectionDefect.gebrek_code.isnot(None),
    ).limit(500).all()
    freq = {}
    for r in rows:
        if r.gebrek_code:
            freq[r.gebrek_code] = freq.get(r.gebrek_code, 0) + 1
    return freq


@router.post("/classify")
def classify(
    req: ClassifyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genereer defect-suggesties op basis van context + heuristieken.

    Strategie:
      1. Taxonomy-prior: alle bekende defecten voor (asset_type, element_code)
         krijgen base-confidence 25.
      2. Frequentie-boost: defecten die vaker voorkomen op vergelijkbare
         elementen in deze organisatie krijgen +5-15.
      3. Heuristiek-boost:
         - Lage helderheid + asset_type='boom' → "holte" / "donkere plek" +10
         - Dominante kleur in oranje/rood range + asset met staal/beton →
           "wapeningscorrosie" / "rotting" +10
         - Lage helderheid + 'brug.pijlers' → "scheurvorming" +5
    """
    asset_type = kt.normalize_type(req.asset_type)
    if not asset_type:
        raise HTTPException(status_code=400,
                            detail=f"Onbekend asset_type: {req.asset_type}")

    # Verzamel kandidaten uit taxonomy
    if req.element_code:
        candidates = kt.gebreken_voor(asset_type, req.element_code) or []
    else:
        # Alle elementen samen
        candidates = []
        for e in kt.elementen_voor(asset_type) or []:
            for g in e.get("gebreken") or []:
                if not any(c["code"] == g["code"] for c in candidates):
                    candidates.append(g)

    if not candidates:
        return {
            "suggestions": [],
            "version": AI_VERSION,
            "note": "Geen defect-kandidaten gevonden in taxonomy",
        }

    # Frequentie-prior uit eerdere inspecties
    freq = (_frequency_prior(db, current_user.organization_id, req.element_code)
             if req.element_code else {})

    suggestions = []
    rgb = _color_to_rgb(req.photo_dominant_color or "")
    brightness = req.photo_mean_brightness

    for g in candidates:
        code = g["code"]
        confidence = 25  # base
        reasons = ["taxonomy"]

        # Frequentie-boost
        if code in freq:
            f = freq[code]
            boost = min(15, 3 + f)  # max +15
            confidence += boost
            reasons.append(f"vaak gezien ({f}x)")

        # Heuristiek: lage helderheid + boom + stam-holte
        if asset_type == "boom" and brightness is not None and brightness < 60:
            if code in ("holte", "rotting_wortel", "zwam"):
                confidence += 15
                reasons.append("donkere foto (mogelijk holte/rotting)")

        # Heuristiek: orange/rood + beton-element → corrosie / rotting
        if rgb and rgb[0] > 130 and rgb[1] < 100 and rgb[2] < 80:
            if code in ("wapeningscorrosie", "afspatting", "kanker", "rotting_wortel"):
                confidence += 10
                reasons.append("oranje/rood-tint (mogelijk corrosie)")

        # Heuristiek: lage helderheid + brug.pijlers
        if asset_type == "brug" and req.element_code in (
                "BRUG.PIJLERS", "BRUG.ONDERBOUW") and brightness is not None and brightness < 80:
            if code in ("scheurvorming", "afspatting"):
                confidence += 8
                reasons.append("schaduw-rijke foto (mogelijk scheur)")

        # Heuristiek: groen overheersend + boom → vitaliteit-issue minder waarschijnlijk
        if asset_type == "boom" and rgb and rgb[1] > rgb[0] and rgb[1] > rgb[2]:
            if code in ("dood_hout", "vitaliteitsverlies", "uitval_top"):
                confidence -= 5
                reasons.append("groen-overheersend (kroon mogelijk OK)")

        confidence = max(5, min(95, confidence))
        suggestions.append(DefectSuggestion(
            gebrek_code=code,
            gebrek_naam=g["naam"],
            confidence_pct=confidence,
            reason=" + ".join(reasons),
            method="heuristic",
        ))

    suggestions.sort(key=lambda s: -s.confidence_pct)
    return {
        "asset_type": asset_type,
        "element_code": req.element_code,
        "suggestions": [s.dict() for s in suggestions[:8]],
        "version": AI_VERSION,
        "method": "heuristic-stub",
        "note": "Confidence-cijfers zijn indicatief. Geen vervanging voor ervaren inspecteur.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
