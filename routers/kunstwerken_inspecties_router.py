"""Kunstwerken-inspecties — NEN 2767-2 + CROW 134.

Endpoints:
  GET    /api/kunstwerken-inspecties                              Lijst
  POST   /api/kunstwerken-inspecties                              Nieuwe inspectie
  GET    /api/kunstwerken-inspecties/{id}                         Detail (elementen + defecten)
  PATCH  /api/kunstwerken-inspecties/{id}                         Update header
  DELETE /api/kunstwerken-inspecties/{id}                         Verwijder
  POST   /api/kunstwerken-inspecties/{id}/complete                Bereken eindscore + status=completed
  POST   /api/kunstwerken-inspecties/{id}/sign                    Onderteken + status=signed

  POST   .../{id}/elementen                                       Custom element toevoegen
  PATCH  .../{id}/elementen/{el_id}                               Update element (bevindingen etc.)
  POST   .../{id}/elementen/{el_id}/defecten                      Defect registreren
  PATCH  .../{id}/elementen/{el_id}/defecten/{def_id}             Defect bijwerken
  DELETE .../{id}/elementen/{el_id}/defecten/{def_id}             Defect verwijderen
  POST   .../{id}/elementen/{el_id}/defecten/{def_id}/to-melding  Genereer melding uit defect

  GET    /api/kunstwerken-inspecties/taxonomy/types               Beschikbare kunstwerk-types
  GET    /api/kunstwerken-inspecties/taxonomy/{type}              Element-bibliotheek voor type

Conditie-scores worden server-side berekend (NEN 2767-2) en gepersisteerd
zodat queries en PDF-rapport altijd identieke waarden tonen.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import (
    User, Asset, Project, Melding,
    Inspection, InspectionElement, InspectionDefect, InspectionAnswer,
)
from schemas import (
    KunstwerkInspectionCreate, KunstwerkInspectionUpdate,
    InspectionElementCreate, InspectionElementUpdate,
    InspectionDefectCreate, InspectionDefectUpdate,
    InspectionSignRequest, DefectToMeldingRequest,
    InspectionAnswerUpdate,
)
from auth import get_current_user
from audit import log_action, ACTION
from permissions import can_create_meldingen

import kunstwerken_taxonomy as kt
import nen2767_scoring as scoring
import inspection_cycle as cycle

router = APIRouter(prefix="/api/kunstwerken-inspecties", tags=["Kunstwerken-inspecties"])


# ─────────────────────────────────────────────────────────────────────────────
# Serialisatie
# ─────────────────────────────────────────────────────────────────────────────

def _defect_dict(d: InspectionDefect) -> dict:
    return {
        "id": d.id,
        "element_id": d.element_id,
        "gebrek_code": d.gebrek_code,
        "gebrek_naam": d.gebrek_naam,
        "omschrijving": d.omschrijving,
        "ernst": d.ernst,
        "intensiteit": d.intensiteit,
        "omvang_klasse": d.omvang_klasse,
        "omvang_percentage": d.omvang_percentage,
        "defect_score": d.defect_score,
        "defect_score_label": scoring.conditie_label(d.defect_score),
        "locatie_beschrijving": d.locatie_beschrijving,
        "lat": d.lat,
        "lng": d.lng,
        "photo_url": d.photo_url,
        "photo_url_2": d.photo_url_2,
        "ai_analysis_id": d.ai_analysis_id,
        "crow_klasse": d.crow_klasse,
        "gw_maatregel": d.gw_maatregel,
        "melding_id": d.melding_id,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def _element_dict(e: InspectionElement, *, include_defects: bool = True,
                  include_antwoorden: bool = False,
                  inspection_kunstwerk_type: Optional[str] = None) -> dict:
    # Aandacht-count uit cached antwoorden (geen extra DB-query als antwoorden
    # al geladen zijn — voor list-views met joinedload werkt dit lazy-free)
    attention_count = sum(1 for a in (e.antwoorden or []) if a.requires_attention)
    out = {
        "id": e.id,
        "inspection_id": e.inspection_id,
        "element_code": e.element_code,
        "element_naam": e.element_naam,
        "element_groep": e.element_groep,
        "beoordeeld": e.beoordeeld,
        "niet_inspecteerbaar_reden": e.niet_inspecteerbaar_reden,
        "conditiescore": e.conditiescore,
        "conditiescore_label": scoring.conditie_label(e.conditiescore),
        "conditiescore_color": scoring.conditie_color(e.conditiescore),
        "bevindingen": e.bevindingen,
        "aanbevolen_actie": e.aanbevolen_actie,
        "order_index": e.order_index,
        "defecten_count": len(e.defecten or []),
        "aandacht_count": attention_count,
    }
    if include_defects:
        out["defecten"] = [_defect_dict(d) for d in (e.defecten or [])]
    if include_antwoorden:
        defs = kt.vragen_voor(inspection_kunstwerk_type or "",
                              e.element_code, e.element_groep)
        by_code = {a.question_code: a for a in (e.antwoorden or [])}
        out["vragen"] = [{
            "question": q,
            "answer": _answer_dict(by_code[q["code"]]) if q["code"] in by_code else None,
        } for q in defs]
    return out


def _inspection_dict(i: Inspection, *, include_elements: bool = False) -> dict:
    advies = scoring.maatregel_advies(i.conditiescore_overall)
    out = {
        "id": i.id,
        "asset_id": i.asset_id,
        "asset_code": i.asset.code if i.asset else None,
        "asset_name": i.asset.name if i.asset else None,
        "kunstwerk_type": i.kunstwerk_type,
        "project_id": i.project_id,
        "project_name": i.project.name if i.project else None,
        "title": i.title,
        "inspectie_type": i.inspectie_type,
        "norm_referenties": i.norm_referenties,
        "datum_inspectie": i.datum_inspectie.isoformat() if i.datum_inspectie else None,
        "inspecteur_id": i.inspecteur_id,
        "inspecteur_naam": i.inspecteur_naam,
        "inspecteur_certificaat": i.inspecteur_certificaat,
        "weersomstandigheden": i.weersomstandigheden,
        "bijzonderheden": i.bijzonderheden,
        "opdrachtgever_naam": i.opdrachtgever_naam,
        "opdrachtgever_email": i.opdrachtgever_email,
        "conditiescore_overall": i.conditiescore_overall,
        "conditiescore_label": scoring.conditie_label(i.conditiescore_overall),
        "conditiescore_color": scoring.conditie_color(i.conditiescore_overall),
        "conditiescore_methode": i.conditiescore_methode,
        "maatregel_advies": advies,
        "samenvatting": i.samenvatting,
        "aanbevolen_acties": i.aanbevolen_acties,
        "status": i.status,
        "signed_at": i.signed_at.isoformat() if i.signed_at else None,
        "signed_by": i.signed_by,
        "has_signature": bool(i.signature_data_url),
        # Handtekening-image meesturen zodat de PDF-export het kan inbedden.
        # Alleen bij signed-state om payload van actieve inspecties licht te houden.
        "signature_data_url": i.signature_data_url if i.status in ("signed", "delivered") else None,
        "pdf_generated_at": i.pdf_generated_at.isoformat() if i.pdf_generated_at else None,
        "volgende_inspectie_op": i.volgende_inspectie_op.isoformat() if i.volgende_inspectie_op else None,
        "elementen_count": len(i.elementen or []),
        "created_by": i.created_by,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "updated_at": i.updated_at.isoformat() if i.updated_at else None,
    }
    if include_elements:
        out["elementen"] = [
            _element_dict(e, include_antwoorden=True,
                          inspection_kunstwerk_type=i.kunstwerk_type)
            for e in (i.elementen or [])
        ]
    return out


def _inspection_dict_with_metrics(db: Session, i: Inspection,
                                  *, include_elements: bool = False) -> dict:
    """Zoals _inspection_dict, maar inclusief computed metrics-dict.

    Aparte helper om DB-IO te isoleren — _inspection_dict is pure.
    """
    out = _inspection_dict(i, include_elements=include_elements)
    out["metrics"] = _compute_metrics(db, i)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_inspection_or_404(db: Session, inspection_id: str, user: User) -> Inspection:
    i = (db.query(Inspection)
            .options(
                joinedload(Inspection.elementen).joinedload(InspectionElement.defecten),
                joinedload(Inspection.elementen).joinedload(InspectionElement.antwoorden),
            )
            .filter(Inspection.id == inspection_id,
                    Inspection.organization_id == user.organization_id)
            .first())
    if not i:
        raise HTTPException(status_code=404, detail="Inspectie niet gevonden")
    return i


def _get_element_or_404(db: Session, inspection: Inspection, element_id: str) -> InspectionElement:
    e = (db.query(InspectionElement)
            .filter(InspectionElement.id == element_id,
                    InspectionElement.inspection_id == inspection.id)
            .first())
    if not e:
        raise HTTPException(status_code=404, detail="Element niet gevonden")
    return e


def _get_defect_or_404(db: Session, element: InspectionElement, defect_id: str) -> InspectionDefect:
    d = (db.query(InspectionDefect)
            .filter(InspectionDefect.id == defect_id,
                    InspectionDefect.element_id == element.id)
            .first())
    if not d:
        raise HTTPException(status_code=404, detail="Defect niet gevonden")
    return d


def _recompute_element_score(db: Session, element: InspectionElement) -> None:
    """Herbereken element.conditiescore uit defecten + checklist + aandachtspunten.

    Drie inputs spelen mee (worst-wins):
      1. Defect-scores van geregistreerde defecten
      2. Score_1_6 antwoorden van de checklist (visuele staat)
      3. Aandachtspunten-floor: ja-antwoorden die `requires_attention=True`
         opleveren (bv. ja op CONSTR.SCHEUR) forceren een minimum-score zodat
         een element met geconstateerde gebreken niet als "Goed" kan worden
         gerapporteerd.

    Aandachtspunten-floor:
      - 1-2 aandachtspunten → minimaal 3 (Redelijk)
      - 3-4 aandachtspunten → minimaal 4 (Matig)
      - 5+ aandachtspunten  → minimaal 5 (Slecht)

    Een element met checklist-antwoorden geldt automatisch als beoordeeld.
    """
    defect_rows = (db.query(InspectionDefect.defect_score)
                     .filter(InspectionDefect.element_id == element.id)
                     .all())
    answer_rows = (db.query(InspectionAnswer.answer_score)
                     .filter(InspectionAnswer.element_id == element.id,
                             InspectionAnswer.answer_type == "score_1_6")
                     .all())
    scores = [r[0] for r in defect_rows] + [r[0] for r in answer_rows]
    derived = scoring.element_score(scores)
    # Auto-mark beoordeeld zodra er bruikbare data is
    if any(s is not None for s in scores):
        element.beoordeeld = True

    # Aandachtspunten-floor
    attention_count = (db.query(InspectionAnswer)
                         .filter(InspectionAnswer.element_id == element.id,
                                 InspectionAnswer.requires_attention.is_(True))
                         .count())
    if attention_count > 0:
        element.beoordeeld = True
        floor = 3 if attention_count <= 2 else (4 if attention_count <= 4 else 5)
        derived = max(derived or 0, floor)
    if derived is None and element.beoordeeld:
        derived = 1
    element.conditiescore = derived


def _recompute_inspection_score(db: Session, inspection: Inspection) -> None:
    """Herbereken inspection.conditiescore_overall via een verse query."""
    rows = (db.query(InspectionElement.conditiescore)
              .filter(InspectionElement.inspection_id == inspection.id)
              .all())
    elem_scores = [r[0] for r in rows]
    inspection.conditiescore_overall = scoring.object_score(elem_scores)
    inspection.conditiescore_methode = "worst-element"


def _normalize_defect_inputs(payload: dict) -> None:
    """Map omvang_percentage → omvang_klasse als alleen percentage is gegeven."""
    if payload.get("omvang_klasse") is None and payload.get("omvang_percentage") is not None:
        payload["omvang_klasse"] = scoring.omvang_klasse_from_percentage(payload["omvang_percentage"])


# ─────────────────────────────────────────────────────────────────────────────
# Vragenlijst-helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_answered(a: InspectionAnswer) -> bool:
    """Beschouw vraag als beantwoord zodra ten minste één antwoord-veld een
    waarde heeft. Een lege string telt niet."""
    if a.answer_score is not None: return True
    if a.answer_bool is not None: return True
    if a.answer_value_text and a.answer_value_text.strip(): return True
    return False


def _compute_attention(question_def: dict, payload: InspectionAnswer) -> bool:
    """Bepaal of een antwoord een aandachtspunt opwerpt.

    Regels:
      - Voor `score_1_6`: score >= 4 (matig of slechter) → aandacht
      - Voor `ja_nee` / `ja_nee_nvt`: vraag.attention_when (default True) bepaalt of
        ja of nee aandacht oproept
      - Voor `keuze`: alleen 'niet' (inspecteerbaarheid) of 'acuut' (urgentie)
    """
    qtype = question_def.get("type")
    aw_when = question_def.get("attention_when", True)

    if qtype == "score_1_6" and payload.answer_score is not None:
        return payload.answer_score >= 4

    if qtype in ("ja_nee", "ja_nee_nvt") and payload.answer_bool is not None:
        return payload.answer_bool == aw_when
    if qtype == "ja_nee_nvt" and payload.answer_value_text:
        val = payload.answer_value_text.lower()
        return (val == "ja") == aw_when

    if qtype == "keuze" and payload.answer_value_text:
        val = payload.answer_value_text.lower()
        # Bekende attention-triggers
        if val in ("niet", "acuut"): return True
        if val == "verkort": return True

    return False


def _create_answers_for_element(db: Session, element: InspectionElement,
                                kunstwerk_type: str, org_id: str) -> int:
    """Maak InspectionAnswer-records voor alle relevante vragen bij dit element."""
    vragen = kt.vragen_voor(kunstwerk_type, element.element_code, element.element_groep)
    created = 0
    for q in vragen:
        db.add(InspectionAnswer(
            element_id=element.id,
            organization_id=org_id,
            question_code=q["code"],
            question_version=kt.QUESTIONS_VERSION,
            question_text_snapshot=q.get("vraag", "")[:500],
            answer_type=q.get("type", "tekst"),
            requires_attention=False,
        ))
        created += 1
    return created


def _question_def(question_code: str, kunstwerk_type: Optional[str],
                  element_code: Optional[str], element_groep: Optional[str]) -> Optional[dict]:
    """Zoek de vraag-definitie op (voor validatie + attention-berekening)."""
    candidates = kt.vragen_voor(kunstwerk_type, element_code or "", element_groep)
    for q in candidates:
        if q["code"] == question_code:
            return q
    return None


def _answer_dict(a: InspectionAnswer) -> dict:
    return {
        "id": a.id,
        "question_code": a.question_code,
        "question_version": a.question_version,
        "question_text": a.question_text_snapshot,
        "answer_type": a.answer_type,
        "answer_score": a.answer_score,
        "answer_bool": a.answer_bool,
        "answer_value_text": a.answer_value_text,
        "toelichting": a.toelichting,
        "photo_url": a.photo_url,
        "requires_attention": a.requires_attention,
        "answered": _is_answered(a),
        "answered_at": a.answered_at.isoformat() if a.answered_at else None,
    }


def _compute_metrics(db: Session, inspection: Inspection) -> dict:
    """Bereken metrics over de inspectie voor dashboard + executive summary.

    Levert:
      vragen_totaal / vragen_beantwoord / vragen_aandacht
      defecten_totaal / defecten_kritiek (score >= 5)
      elementen_per_conditie  → {1: n, 2: n, ..., 6: n}
      elementen_beoordeeld / elementen_totaal
      voortgang_pct (0-100)
    """
    elementen = inspection.elementen or []
    elementen_ids = [e.id for e in elementen]

    # Vragen-stats
    vragen_totaal = 0
    vragen_beantwoord = 0
    vragen_aandacht = 0
    if elementen_ids:
        rows = (db.query(InspectionAnswer.answer_score, InspectionAnswer.answer_bool,
                         InspectionAnswer.answer_value_text, InspectionAnswer.requires_attention)
                  .filter(InspectionAnswer.element_id.in_(elementen_ids))
                  .all())
        vragen_totaal = len(rows)
        for r in rows:
            if r[0] is not None or r[1] is not None or (r[2] and r[2].strip()):
                vragen_beantwoord += 1
            if r[3]:
                vragen_aandacht += 1

    # Defecten-stats
    defecten_totaal = 0
    defecten_kritiek = 0
    if elementen_ids:
        rows = (db.query(InspectionDefect.defect_score)
                  .filter(InspectionDefect.element_id.in_(elementen_ids))
                  .all())
        defecten_totaal = len(rows)
        defecten_kritiek = sum(1 for r in rows if r[0] is not None and r[0] >= 5)

    # Elementen-conditie verdeling
    elementen_per_conditie = {str(i): 0 for i in range(1, 7)}
    beoordeeld = 0
    for e in elementen:
        if e.conditiescore is not None and 1 <= e.conditiescore <= 6:
            elementen_per_conditie[str(e.conditiescore)] += 1
        if e.beoordeeld or e.niet_inspecteerbaar_reden:
            beoordeeld += 1

    totaal_el = len(elementen)
    voortgang_pct = round(100 * beoordeeld / totaal_el) if totaal_el else 0

    return {
        "vragen_totaal": vragen_totaal,
        "vragen_beantwoord": vragen_beantwoord,
        "vragen_aandacht": vragen_aandacht,
        "vragen_voortgang_pct": (round(100 * vragen_beantwoord / vragen_totaal)
                                 if vragen_totaal else 0),
        "defecten_totaal": defecten_totaal,
        "defecten_kritiek": defecten_kritiek,
        "elementen_totaal": totaal_el,
        "elementen_beoordeeld": beoordeeld,
        "elementen_per_conditie": elementen_per_conditie,
        "voortgang_pct": voortgang_pct,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Taxonomy endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/taxonomy/types")
def get_kunstwerk_types(current_user: User = Depends(get_current_user)):
    """Lijst van ondersteunde kunstwerk-types met label."""
    return {"types": [{"key": k, "label": v} for k, v in kt.KUNSTWERK_TYPES.items()]}


@router.get("/taxonomy/{kunstwerk_type}")
def get_taxonomy(kunstwerk_type: str, current_user: User = Depends(get_current_user)):
    """Standaard-elementen + gebreken-bibliotheek voor één kunstwerk-type."""
    normalized = kt.normalize_type(kunstwerk_type)
    if not normalized:
        raise HTTPException(status_code=404,
                            detail=f"Onbekend kunstwerk-type: {kunstwerk_type}")
    return {
        "kunstwerk_type": normalized,
        "label": kt.KUNSTWERK_TYPES[normalized],
        "elementen": kt.elementen_voor(normalized),
        "scoring": {
            "version": scoring.SCORING_VERSION,
            "labels": scoring.CONDITIE_LABELS,
            "ernst": scoring.ERNST_LABELS,
            "intensiteit": scoring.INTENSITEIT_LABELS,
            "omvang": scoring.OMVANG_KLASSE_LABELS,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Inspection CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/")
def list_inspections(
    asset_id: Optional[str] = None,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    kunstwerk_type: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Inspection).filter(Inspection.organization_id == current_user.organization_id)
    if asset_id:        q = q.filter(Inspection.asset_id == asset_id)
    if project_id:      q = q.filter(Inspection.project_id == project_id)
    if status:          q = q.filter(Inspection.status == status)
    if kunstwerk_type:  q = q.filter(Inspection.kunstwerk_type == kunstwerk_type)
    items = q.order_by(Inspection.created_at.desc()).limit(500).all()
    return [_inspection_dict(i) for i in items]


@router.post("/")
def create_inspection(
    payload: KunstwerkInspectionCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not can_create_meldingen(current_user):
        raise HTTPException(status_code=403, detail="Geen rechten voor inspecties")

    # Valideer asset
    asset = (db.query(Asset)
                .filter(Asset.id == payload.asset_id,
                        Asset.organization_id == current_user.organization_id)
                .first())
    if not asset:
        raise HTTPException(status_code=404,
                            detail="Asset (kunstwerk) niet gevonden in jouw organisatie")

    kunstwerk_type = kt.normalize_type(payload.kunstwerk_type or asset.asset_type)
    if not kunstwerk_type:
        raise HTTPException(
            status_code=400,
            detail=("Kunstwerk-type onbekend. Geef expliciet `kunstwerk_type` mee "
                    f"of zet Asset.asset_type op één van: {sorted(kt.KUNSTWERK_TYPES)}"),
        )

    # Inspecteur default = current_user
    inspecteur_id = payload.inspecteur_id or current_user.id
    if inspecteur_id != current_user.id:
        ins_user = (db.query(User)
                       .filter(User.id == inspecteur_id,
                               User.organization_id == current_user.organization_id)
                       .first())
        if not ins_user:
            raise HTTPException(status_code=400, detail="Inspecteur niet gevonden in organisatie")
    inspecteur_naam = payload.inspecteur_naam or f"{current_user.first_name} {current_user.last_name}".strip()

    insp = Inspection(
        organization_id=current_user.organization_id,
        asset_id=asset.id,
        kunstwerk_type=kunstwerk_type,
        project_id=payload.project_id or asset.project_id,
        title=payload.title,
        inspectie_type=payload.inspectie_type or "visueel",
        datum_inspectie=payload.datum_inspectie or datetime.now(timezone.utc),
        inspecteur_id=inspecteur_id,
        inspecteur_naam=inspecteur_naam,
        inspecteur_certificaat=payload.inspecteur_certificaat,
        weersomstandigheden=payload.weersomstandigheden,
        bijzonderheden=payload.bijzonderheden,
        opdrachtgever_naam=payload.opdrachtgever_naam,
        opdrachtgever_email=payload.opdrachtgever_email,
        status="draft",
        created_by=current_user.id,
    )
    db.add(insp)
    db.flush()  # zodat we insp.id hebben voor elementen

    # Auto-elementen vanuit taxonomy + vragenlijst per element
    total_answers = 0
    if payload.auto_elements:
        for idx, e in enumerate(kt.elementen_voor(kunstwerk_type)):
            el = InspectionElement(
                inspection_id=insp.id,
                organization_id=current_user.organization_id,
                element_code=e["code"],
                element_naam=e["naam"],
                element_groep=e.get("groep"),
                beoordeeld=False,
                order_index=idx,
            )
            db.add(el)
            db.flush()  # voor el.id
            total_answers += _create_answers_for_element(
                db, el, kunstwerk_type, current_user.organization_id)

    db.commit()
    db.refresh(insp)
    log_action(db, request, current_user,
               action=ACTION.INSPECTION_CREATE,
               entity_type="inspection", entity_id=insp.id,
               extra={"asset_id": asset.id, "kunstwerk_type": kunstwerk_type,
                      "auto_elements": payload.auto_elements,
                      "element_count": len(insp.elementen or []),
                      "vragen_count": total_answers,
                      "questions_version": kt.QUESTIONS_VERSION})
    return _inspection_dict_with_metrics(db, insp, include_elements=True)


@router.get("/{inspection_id}")
def get_inspection(
    inspection_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    return _inspection_dict_with_metrics(db, insp, include_elements=True)


@router.get("/{inspection_id}/metrics")
def get_metrics(
    inspection_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aparte endpoint voor metrics — lichtgewicht voor dashboard refresh."""
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    return _compute_metrics(db, insp)


@router.post("/{inspection_id}/recompute")
def recompute_scores(
    inspection_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Forceer herberekening van alle element-scores + overall-score.

    Bedoeld na scoring-regel-wijzigingen (bv. aandachtspunten-floor) of als
    data is bijgewerkt buiten de normale flow om. Werkt ook op signed
    inspecties — alleen scores worden bijgewerkt, antwoorden blijven intact.
    """
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    for el in (insp.elementen or []):
        _recompute_element_score(db, el)
    _recompute_inspection_score(db, insp)
    if insp.asset and insp.conditiescore_overall is not None:
        insp.asset.condition_score = min(5, insp.conditiescore_overall)
    db.commit()
    db.refresh(insp)
    log_action(db, request, current_user,
               action=ACTION.INSPECTION_UPDATE,
               entity_type="inspection", entity_id=insp.id,
               extra={"action_sub": "recompute",
                      "conditiescore_overall": insp.conditiescore_overall})
    return _inspection_dict_with_metrics(db, insp, include_elements=True)


@router.patch("/{inspection_id}")
def update_inspection(
    inspection_id: str,
    payload: KunstwerkInspectionUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    before_status = insp.status
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(insp, k, v)
    db.commit()
    db.refresh(insp)
    log_action(db, request, current_user,
               action=ACTION.INSPECTION_UPDATE,
               entity_type="inspection", entity_id=insp.id,
               extra={"before_status": before_status, "after_status": insp.status,
                      "changed": list(data.keys())})
    return _inspection_dict_with_metrics(db, insp, include_elements=True)


@router.delete("/{inspection_id}")
def delete_inspection(
    inspection_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    if insp.status in ("signed", "delivered"):
        raise HTTPException(status_code=409,
                            detail="Ondertekende inspectie kan niet worden verwijderd")
    title = insp.title
    db.delete(insp)
    db.commit()
    log_action(db, request, current_user,
               action=ACTION.INSPECTION_DELETE,
               entity_type="inspection", entity_id=inspection_id,
               extra={"title": title})
    return {"message": "Inspectie verwijderd"}


# ─────────────────────────────────────────────────────────────────────────────
# Status-overgangen
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{inspection_id}/complete")
def complete_inspection(
    inspection_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Markeer veldwerk als klaar. Herbereken alle scores en sluit verder af.

    Vereist: minimaal één element 'beoordeeld' of 'niet-inspecteerbaar'.
    """
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    if insp.status in ("signed", "delivered"):
        raise HTTPException(status_code=409,
                            detail="Inspectie is al ondertekend / afgeleverd")
    beoordeeld_count = sum(1 for e in (insp.elementen or [])
                           if e.beoordeeld or e.niet_inspecteerbaar_reden)
    if beoordeeld_count == 0:
        raise HTTPException(status_code=400,
                            detail="Markeer eerst minimaal één element als beoordeeld")
    for e in insp.elementen:
        _recompute_element_score(db, e)
    _recompute_inspection_score(db, insp)
    insp.status = "completed"
    # Update asset.condition_score + last_inspection_at
    if insp.asset and insp.conditiescore_overall is not None:
        insp.asset.last_inspection_at = datetime.now(timezone.utc)
        # Asset.condition_score is bestaand veld (1-5 schaal in oude model);
        # cap NEN 6 → 5 om backwards compat te behouden.
        insp.asset.condition_score = min(5, insp.conditiescore_overall)
    db.commit()
    db.refresh(insp)
    log_action(db, request, current_user,
               action=ACTION.INSPECTION_COMPLETE,
               entity_type="inspection", entity_id=insp.id,
               extra={"conditiescore_overall": insp.conditiescore_overall,
                      "beoordeeld_count": beoordeeld_count})
    return _inspection_dict_with_metrics(db, insp, include_elements=True)


@router.post("/{inspection_id}/sign")
def sign_inspection(
    inspection_id: str,
    payload: InspectionSignRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Onderteken een afgeronde inspectie. Bevriest de inhoud."""
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    if insp.status not in ("completed", "signed"):
        raise HTTPException(
            status_code=400,
            detail="Markeer eerst als 'completed' voordat je tekent",
        )
    if insp.status == "signed":
        return _inspection_dict_with_metrics(db, insp, include_elements=True)
    if not payload.signature_data_url or not payload.signature_data_url.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="Geen geldige handtekening (data-URL verwacht)")
    if len(payload.signature_data_url) > 200_000:
        raise HTTPException(status_code=400, detail="Handtekening te groot (>200KB)")

    insp.signature_data_url = payload.signature_data_url
    insp.signed_at = datetime.now(timezone.utc)
    insp.signed_by = current_user.id
    insp.status = "signed"
    if payload.volgende_inspectie_op:
        insp.volgende_inspectie_op = payload.volgende_inspectie_op

    # Auto-update asset met volgende-inspectie-cyclus (norm-conform)
    _update_asset_cycle(db, insp)

    db.commit()
    db.refresh(insp)
    log_action(db, request, current_user,
               action=ACTION.INSPECTION_SIGN,
               entity_type="inspection", entity_id=insp.id,
               extra={"conditiescore_overall": insp.conditiescore_overall,
                      "next_inspection_due": (insp.volgende_inspectie_op.isoformat()
                                              if insp.volgende_inspectie_op else None)})
    return _inspection_dict_with_metrics(db, insp, include_elements=True)


def _update_asset_cycle(db: Session, insp: Inspection) -> None:
    """Update gekoppelde asset met inspectie-cyclus info.

    Bij elke `sign_inspection` wordt:
      - Asset.last_inspection_at      = nu
      - Asset.last_inspection_id      = deze inspectie
      - Asset.condition_score         = berekende eindscore (NEN 2767)
      - Asset.inspection_cycle_months = cyclus-duur volgens norm
      - Asset.next_inspection_due     = nu + cycle_months
      - Inspection.volgende_inspectie_op idem (als niet handmatig gezet)

    Geen DB-commit hier — caller is verantwoordelijk.
    """
    if not insp.asset_id:
        return  # vrijstaande inspectie zonder asset — niets te updaten
    asset = db.query(Asset).filter(Asset.id == insp.asset_id).first()
    if not asset:
        return

    now = datetime.now(timezone.utc)
    asset.last_inspection_at = now
    asset.last_inspection_id = insp.id
    if insp.conditiescore_overall is not None:
        asset.condition_score = insp.conditiescore_overall

    # Cyclus-maanden bepalen — kunstwerk-type van inspectie heeft voorrang,
    # anders asset.asset_type
    type_key = insp.kunstwerk_type or asset.asset_type
    months = cycle.cycle_months_for(type_key, insp.conditiescore_overall)
    if months:
        asset.inspection_cycle_months = months
        next_due = cycle.next_due_date(now, months)
        if next_due:
            asset.next_inspection_due = next_due
            # Sync ook op de inspection als de gebruiker geen handmatige
            # datum heeft gezet
            if not insp.volgende_inspectie_op:
                insp.volgende_inspectie_op = next_due


# ─────────────────────────────────────────────────────────────────────────────
# Elementen
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{inspection_id}/elementen")
def add_element(
    inspection_id: str,
    payload: InspectionElementCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    if insp.status in ("signed", "delivered"):
        raise HTTPException(status_code=409, detail="Inspectie is afgesloten")
    e = InspectionElement(
        inspection_id=insp.id,
        organization_id=current_user.organization_id,
        element_code=payload.element_code,
        element_naam=payload.element_naam,
        element_groep=payload.element_groep,
        beoordeeld=False,
        order_index=payload.order_index,
    )
    db.add(e)
    db.flush()
    # Vragenlijst auto-aanmaken zodat handmatig toegevoegd element ook checklist krijgt
    _create_answers_for_element(db, e, insp.kunstwerk_type, current_user.organization_id)
    db.commit()
    db.refresh(e)
    log_action(db, request, current_user,
               action=ACTION.INSPECTION_ELEMENT_UPDATE,
               entity_type="inspection_element", entity_id=e.id,
               extra={"inspection_id": insp.id, "action_sub": "add",
                      "element_code": e.element_code})
    return _element_dict(e)


# ─────────────────────────────────────────────────────────────────────────────
# Vragenlijst per element
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{inspection_id}/elementen/{element_id}/vragen")
def get_element_vragen(
    inspection_id: str,
    element_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Vraag-definities + huidige antwoorden voor één element.

    De definitie-lijst komt uit de taxonomy (versie meegestuurd). Antwoorden
    komen uit de DB. Lengte definitie-lijst hoort gelijk te zijn aan
    aantal Answer-rijen — als de versies divergent zijn (norm-update) krijgt
    de inspecteur de nieuwe vragen erbij met lege antwoorden.
    """
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    el = _get_element_or_404(db, insp, element_id)
    defs = kt.vragen_voor(insp.kunstwerk_type, el.element_code, el.element_groep)
    answers = (db.query(InspectionAnswer)
                 .filter(InspectionAnswer.element_id == el.id)
                 .all())
    by_code = {a.question_code: a for a in answers}

    items = []
    for q in defs:
        a = by_code.get(q["code"])
        items.append({
            "question": q,
            "answer": _answer_dict(a) if a else None,
        })

    return {
        "version": kt.QUESTIONS_VERSION,
        "element": _element_dict(el, include_defects=False),
        "items": items,
    }


@router.patch("/{inspection_id}/elementen/{element_id}/vragen/{question_code}")
def update_element_vraag(
    inspection_id: str,
    element_id: str,
    question_code: str,
    payload: InspectionAnswerUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update één antwoord. Maakt de Answer aan als die nog niet bestaat
    (bv. bij nieuwe vragen na een norm-update)."""
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    if insp.status in ("signed", "delivered"):
        raise HTTPException(status_code=409, detail="Inspectie is afgesloten")
    el = _get_element_or_404(db, insp, element_id)

    qdef = _question_def(question_code, insp.kunstwerk_type, el.element_code, el.element_groep)
    if not qdef:
        raise HTTPException(status_code=404, detail=f"Onbekende vraag-code: {question_code}")

    a = (db.query(InspectionAnswer)
            .filter(InspectionAnswer.element_id == el.id,
                    InspectionAnswer.question_code == question_code)
            .first())
    if not a:
        a = InspectionAnswer(
            element_id=el.id,
            organization_id=current_user.organization_id,
            question_code=question_code,
            question_version=kt.QUESTIONS_VERSION,
            question_text_snapshot=qdef.get("vraag", "")[:500],
            answer_type=qdef.get("type", "tekst"),
        )
        db.add(a)

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(a, k, v)
    a.answered_at = datetime.now(timezone.utc)
    a.answered_by = current_user.id
    a.requires_attention = _compute_attention(qdef, a)

    if insp.status == "draft" and _is_answered(a):
        insp.status = "in_progress"

    # Score-antwoorden EN aandachtspunten beïnvloeden de element-conditie —
    # direct herrekenen na elke patch zodat element.conditiescore live klopt.
    db.flush()
    _recompute_element_score(db, el)

    db.commit()
    db.refresh(a)
    log_action(db, request, current_user,
               action=ACTION.INSPECTION_ANSWER_UPDATE,
               entity_type="inspection_answer", entity_id=a.id,
               extra={"inspection_id": insp.id, "element_code": el.element_code,
                      "question_code": question_code, "requires_attention": a.requires_attention})
    return _answer_dict(a)


@router.patch("/{inspection_id}/elementen/{element_id}")
def update_element(
    inspection_id: str,
    element_id: str,
    payload: InspectionElementUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    if insp.status in ("signed", "delivered"):
        raise HTTPException(status_code=409, detail="Inspectie is afgesloten")
    e = _get_element_or_404(db, insp, element_id)
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(e, k, v)
    # Als beoordeeld → herbereken score
    _recompute_element_score(db, e)
    # Status auto: draft → in_progress als iemand begint te beoordelen
    if insp.status == "draft" and (e.beoordeeld or e.niet_inspecteerbaar_reden):
        insp.status = "in_progress"
    db.commit()
    db.refresh(e)
    log_action(db, request, current_user,
               action=ACTION.INSPECTION_ELEMENT_UPDATE,
               entity_type="inspection_element", entity_id=e.id,
               extra={"inspection_id": insp.id,
                      "element_code": e.element_code,
                      "conditiescore": e.conditiescore,
                      "changed": list(data.keys())})
    return _element_dict(e)


# ─────────────────────────────────────────────────────────────────────────────
# Defecten
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{inspection_id}/elementen/{element_id}/defecten")
def add_defect(
    inspection_id: str,
    element_id: str,
    payload: InspectionDefectCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    if insp.status in ("signed", "delivered"):
        raise HTTPException(status_code=409, detail="Inspectie is afgesloten")
    el = _get_element_or_404(db, insp, element_id)

    data = payload.model_dump(exclude_unset=True)
    _normalize_defect_inputs(data)

    d = InspectionDefect(
        element_id=el.id,
        organization_id=current_user.organization_id,
        gebrek_code=data.get("gebrek_code"),
        gebrek_naam=data["gebrek_naam"],
        omschrijving=data.get("omschrijving"),
        ernst=data.get("ernst"),
        intensiteit=data.get("intensiteit"),
        omvang_klasse=data.get("omvang_klasse"),
        omvang_percentage=data.get("omvang_percentage"),
        defect_score=scoring.defect_to_score(
            data.get("ernst"), data.get("intensiteit"), data.get("omvang_klasse"),
        ),
        locatie_beschrijving=data.get("locatie_beschrijving"),
        lat=data.get("lat"),
        lng=data.get("lng"),
        photo_url=data.get("photo_url"),
        photo_url_2=data.get("photo_url_2"),
        ai_analysis_id=data.get("ai_analysis_id"),
        crow_klasse=data.get("crow_klasse"),
        gw_maatregel=data.get("gw_maatregel"),
    )
    db.add(d)
    db.flush()
    # Markeer element als beoordeeld zodra er een defect is
    el.beoordeeld = True
    _recompute_element_score(db, el)
    if insp.status == "draft":
        insp.status = "in_progress"
    db.commit()
    db.refresh(d)
    log_action(db, request, current_user,
               action=ACTION.INSPECTION_DEFECT_CREATE,
               entity_type="inspection_defect", entity_id=d.id,
               extra={"inspection_id": insp.id, "element_code": el.element_code,
                      "gebrek_naam": d.gebrek_naam, "defect_score": d.defect_score})
    return _defect_dict(d)


@router.patch("/{inspection_id}/elementen/{element_id}/defecten/{defect_id}")
def update_defect(
    inspection_id: str,
    element_id: str,
    defect_id: str,
    payload: InspectionDefectUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    if insp.status in ("signed", "delivered"):
        raise HTTPException(status_code=409, detail="Inspectie is afgesloten")
    el = _get_element_or_404(db, insp, element_id)
    d = _get_defect_or_404(db, el, defect_id)

    data = payload.model_dump(exclude_unset=True)
    _normalize_defect_inputs(data)
    for k, v in data.items():
        setattr(d, k, v)
    # Herbereken defect_score als classificatie wijzigde
    d.defect_score = scoring.defect_to_score(d.ernst, d.intensiteit, d.omvang_klasse)
    _recompute_element_score(db, el)
    db.commit()
    db.refresh(d)
    log_action(db, request, current_user,
               action=ACTION.INSPECTION_DEFECT_UPDATE,
               entity_type="inspection_defect", entity_id=d.id,
               extra={"inspection_id": insp.id,
                      "defect_score": d.defect_score,
                      "changed": list(data.keys())})
    return _defect_dict(d)


@router.delete("/{inspection_id}/elementen/{element_id}/defecten/{defect_id}")
def delete_defect(
    inspection_id: str,
    element_id: str,
    defect_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    if insp.status in ("signed", "delivered"):
        raise HTTPException(status_code=409, detail="Inspectie is afgesloten")
    el = _get_element_or_404(db, insp, element_id)
    d = _get_defect_or_404(db, el, defect_id)
    naam = d.gebrek_naam
    db.delete(d)
    db.flush()
    db.refresh(el)
    _recompute_element_score(db, el)
    db.commit()
    log_action(db, request, current_user,
               action=ACTION.INSPECTION_DEFECT_DELETE,
               entity_type="inspection_defect", entity_id=defect_id,
               extra={"inspection_id": insp.id, "gebrek_naam": naam})
    return {"message": "Defect verwijderd"}


# ─────────────────────────────────────────────────────────────────────────────
# Defect → Melding
# ─────────────────────────────────────────────────────────────────────────────

_PRIORITY_FROM_SCORE = {
    1: "laag", 2: "laag", 3: "normaal", 4: "normaal", 5: "hoog", 6: "kritiek",
}


@router.post("/{inspection_id}/elementen/{element_id}/defecten/{defect_id}/to-melding")
def defect_to_melding(
    inspection_id: str,
    element_id: str,
    defect_id: str,
    payload: DefectToMeldingRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genereer een Melding uit een defect zodat orchestration het oppakt.

    Het defect blijft in de inspectie staan en wordt gelinkt via melding_id.
    """
    if not can_create_meldingen(current_user):
        raise HTTPException(status_code=403, detail="Geen rechten")

    insp = _get_inspection_or_404(db, inspection_id, current_user)
    el = _get_element_or_404(db, insp, element_id)
    d = _get_defect_or_404(db, el, defect_id)

    if d.melding_id:
        # idempotent: bestaande melding teruggeven
        return {"melding_id": d.melding_id, "message": "Melding bestaat al"}

    priority = payload.priority or _PRIORITY_FROM_SCORE.get(d.defect_score or 3, "normaal")
    titel = f"{el.element_naam}: {d.gebrek_naam}"
    if d.locatie_beschrijving:
        titel += f" ({d.locatie_beschrijving[:80]})"

    desc_parts = []
    if d.omschrijving:
        desc_parts.append(d.omschrijving)
    if d.defect_score:
        desc_parts.append(
            f"NEN 2767-2: ernst={d.ernst} · intensiteit={d.intensiteit} · "
            f"omvang-klasse={d.omvang_klasse} → defect-score {d.defect_score} "
            f"({scoring.conditie_label(d.defect_score)})"
        )
    desc_parts.append(f"Bron: inspectierapport {insp.title}")
    if payload.extra_description:
        desc_parts.append(payload.extra_description)
    description = "\n\n".join(desc_parts)

    m = Melding(
        title=titel[:255],
        description=description,
        category="inspectie-bevinding",
        priority=priority,
        status="open",
        lat=d.lat,
        lng=d.lng,
        photo_url=d.photo_url,
        project_id=insp.project_id,
        asset_id=insp.asset_id,
        organization_id=current_user.organization_id,
        created_by=current_user.id,
        crow_klasse=d.crow_klasse,
        gw_maatregel=d.gw_maatregel,
        nen_2767_conditie=min(5, d.defect_score) if d.defect_score else None,
    )
    db.add(m)
    db.flush()
    d.melding_id = m.id
    db.commit()
    db.refresh(m)
    log_action(db, request, current_user,
               action=ACTION.INSPECTION_DEFECT_TO_MELDING,
               entity_type="melding", entity_id=m.id,
               extra={"inspection_id": insp.id, "defect_id": d.id,
                      "defect_score": d.defect_score, "priority": priority})
    # Ook melding.create loggen zodat orchestration / push die ziet
    log_action(db, request, current_user,
               action=ACTION.MELDING_CREATE,
               entity_type="melding", entity_id=m.id,
               after={"title": m.title, "priority": m.priority,
                      "status": m.status, "gw_maatregel": m.gw_maatregel,
                      "from_inspection": insp.id})
    return {"melding_id": m.id, "message": "Melding aangemaakt", "priority": priority}
