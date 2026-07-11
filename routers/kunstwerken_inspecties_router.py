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
from datetime import datetime, timedelta, timezone
from typing import Optional
import base64
import csv
import io
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload, selectinload

from database import get_db
from models import (
    User, Asset, Melding, Organization,
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
import kunstwerken_i18n as kw_i18n
import crow_kosten as ck

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
        "en1176_categorie": d.en1176_categorie,
        "en1176_acute_afsluiting": bool(d.en1176_acute_afsluiting),
        "vta_risicoklasse": d.vta_risicoklasse,
        "vta_holte_pct": d.vta_holte_pct,
        "vta_t_r_ratio": d.vta_t_r_ratio,
        "nen3140_isolatie_megaohm": d.nen3140_isolatie_megaohm,
        "nen3140_aardingsweerstand_ohm": d.nen3140_aardingsweerstand_ohm,
        "nen3140_aardlek_ms": d.nen3140_aardlek_ms,
        "nen3140_aardlek_ma": d.nen3140_aardlek_ma,
        "crow145_rl_droog_mcd": d.crow145_rl_droog_mcd,
        "crow145_rl_nat_mcd": d.crow145_rl_nat_mcd,
        "nen3399_code": d.nen3399_code,
        "nen3399_klasse": d.nen3399_klasse,
        "nen3399_streng_id": d.nen3399_streng_id,
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


def _inspection_dict(i: Inspection, *, include_elements: bool = False,
                     meldingen_count: int = 0, melding_ids: Optional[list] = None) -> dict:
    advies = scoring.maatregel_advies(i.conditiescore_overall)
    out = {
        "id": i.id,
        # Gekoppelde meldingen (via defect_to_melding → InspectionDefect.melding_id).
        # Voor de Inspecties-tab: telling + doorklik naar de meldingen.
        "meldingen_count": meldingen_count,
        "melding_ids": melding_ids or [],
        "asset_id": i.asset_id,
        "asset_code": i.asset.code if i.asset else None,
        "asset_name": i.asset.name if i.asset else None,
        "kunstwerk_type": i.kunstwerk_type,
        "project_id": i.project_id,
        "project_name": i.project.name if i.project else None,
        "title": i.title,
        "inspectie_type": i.inspectie_type,
        "nen1176_inspectie_kind": i.nen1176_inspectie_kind,
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


@router.get("/i18n/{lang}")
def get_taxonomy_i18n(lang: str, current_user: User = Depends(get_current_user)):
    """i18n-bundle voor labels (element/groep/vraag/type) in opgegeven taal.

    Frontend gebruikt deze om labels client-side te vertalen zonder
    server-side de hele taxonomy te dupliceren. Voor NL (bron-taal) wordt
    een lege bundle teruggegeven.
    """
    return kw_i18n.get_i18n_bundle(lang)


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
    # Eager-load asset/project + elementen zodat _inspection_dict (asset.code,
    # project.name, len(elementen)) geen N+1 over max 500 inspecties triggert (perf).
    q = (db.query(Inspection)
           .options(joinedload(Inspection.asset),
                    joinedload(Inspection.project),
                    selectinload(Inspection.elementen))
           .filter(Inspection.organization_id == current_user.organization_id))
    if asset_id:        q = q.filter(Inspection.asset_id == asset_id)
    if project_id:      q = q.filter(Inspection.project_id == project_id)
    if status:          q = q.filter(Inspection.status == status)
    if kunstwerk_type:  q = q.filter(Inspection.kunstwerk_type == kunstwerk_type)
    items = q.order_by(Inspection.created_at.desc()).limit(500).all()

    # Gekoppelde meldingen per inspectie in één bulk-query (geen N+1). Koppeling
    # loopt via InspectionDefect.melding_id (defect → element → inspectie).
    insp_ids = [i.id for i in items]
    melding_map: dict[str, list] = {}
    if insp_ids:
        rows = (db.query(InspectionElement.inspection_id, InspectionDefect.melding_id)
                  .join(InspectionDefect, InspectionDefect.element_id == InspectionElement.id)
                  .filter(InspectionElement.inspection_id.in_(insp_ids),
                          InspectionDefect.melding_id.isnot(None))
                  .all())
        for insp_id, melding_id in rows:
            melding_map.setdefault(insp_id, []).append(melding_id)

    return [_inspection_dict(i, meldingen_count=len(melding_map.get(i.id, [])),
                             melding_ids=melding_map.get(i.id, []))
            for i in items]


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

    # NEN 3140 — verlichting-inspecties vereisen VOP/VP/VIOP-gekwalificeerd
    # inspecteur. Het certificaat wordt opgeslagen in inspecteur_certificaat.
    # Patroon: 'VOP', 'VP' of 'VIOP' (optioneel met cijfers/jaartal erachter).
    if kunstwerk_type == "verlichting":
        cert = (payload.inspecteur_certificaat or "").strip().upper()
        if not cert:
            raise HTTPException(
                status_code=400,
                detail="NEN 3140-keuring vereist inspecteur_certificaat (VOP / VP / VIOP). "
                       "Vul deze in op de inspectie-creatie.",
            )
        if not re.match(r"\b(VOP|VP|VIOP)\b", cert):
            raise HTTPException(
                status_code=400,
                detail=f"NEN 3140-certificaat moet beginnen met VOP, VP of VIOP (ontvangen: '{cert[:40]}'). "
                       "Voorbeeld geldig: 'VOP 2026-1234', 'VIOP-N3140-2025'.",
            )

    # NEN-EN 1176 — valideer inspectie-kind alleen voor speeltoestellen
    nen1176_kind = None
    if payload.nen1176_inspectie_kind:
        if kunstwerk_type != "speeltoestel":
            raise HTTPException(
                status_code=400,
                detail="nen1176_inspectie_kind is alleen geldig voor kunstwerk_type=speeltoestel",
            )
        kind = payload.nen1176_inspectie_kind.strip().lower()
        if kind not in {"routine", "operationeel", "hoofd"}:
            raise HTTPException(
                status_code=400,
                detail="nen1176_inspectie_kind moet 'routine', 'operationeel' of 'hoofd' zijn",
            )
        nen1176_kind = kind

    insp = Inspection(
        organization_id=current_user.organization_id,
        asset_id=asset.id,
        kunstwerk_type=kunstwerk_type,
        project_id=payload.project_id or asset.project_id,
        title=payload.title,
        inspectie_type=payload.inspectie_type or "visueel",
        nen1176_inspectie_kind=nen1176_kind,
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


@router.get("/{inspection_id}/streng-aggregatie")
def get_nen3399_streng_aggregatie(
    inspection_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """NEN 3399 streng-eindklasse aggregatie voor riolering-inspecties.

    Groepeert alle defecten per `nen3399_streng_id` en bepaalt per streng:
      - max_klasse (zwaarste schade — bepalend voor vervang-prioriteit)
      - aantal_defecten
      - codes (lijst BAA/BAB/etc. die voorkomen)
      - dominant_code (meest-frequente)

    NEN 3399 § 5: 'Eindklasse strengtoestand 1-5 op basis van zwaarste
    BAA-BAQ-schade in streng' — deze endpoint geeft die aggregatie.
    """
    from collections import defaultdict, Counter
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    if insp.kunstwerk_type != "riolering":
        raise HTTPException(
            status_code=400,
            detail="streng-aggregatie is alleen relevant voor kunstwerk_type=riolering",
        )

    # Verzamel alle defecten met streng-ID via element-relaties
    strengen: dict[str, list] = defaultdict(list)
    ongegroepeerd = []
    for el in insp.elementen or []:
        for d in (el.defecten or []):
            sid = d.nen3399_streng_id
            if sid:
                strengen[sid].append(d)
            else:
                ongegroepeerd.append(d)

    result = []
    for sid, defects in sorted(strengen.items()):
        klassen = [d.nen3399_klasse for d in defects if d.nen3399_klasse is not None]
        codes = [d.nen3399_code for d in defects if d.nen3399_code]
        max_klasse = max(klassen) if klassen else None
        code_counts = Counter(codes)
        dominant = code_counts.most_common(1)[0][0] if code_counts else None
        result.append({
            "streng_id": sid,
            "max_klasse": max_klasse,
            "max_klasse_advies": _nen3399_advies(max_klasse),
            "aantal_defecten": len(defects),
            "codes": sorted(set(codes)),
            "dominant_code": dominant,
        })

    return {
        "inspection_id": inspection_id,
        "strengen_count": len(strengen),
        "ongegroepeerd_defects": len(ongegroepeerd),
        "strengen": result,
        "norm": "NEN 3399 § 5 — eindklasse strengtoestand",
    }


def _nen3399_advies(klasse: Optional[int]) -> str:
    return {
        1: "Geen actie",
        2: "Monitoren — volgende inspectie binnen 5 jaar",
        3: "Onderhoud binnen 1-2 jaar",
        4: "Vervangen binnen 5-10 jaar — planmatig",
        5: "Direct vervangen — acute schade",
    }.get(klasse, "Onbekend (klasse niet ingevuld)")


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

    # Werkdagboek: auto-entry voor de inspecteur (best-effort)
    from daybook_logger import log_daybook
    asset_label = ""
    if insp.asset:
        asset_label = " — " + (insp.asset.code or insp.asset.naam or "")
    log_daybook(
        db,
        user_id=current_user.id,
        organization_id=current_user.organization_id,
        entry_type="inspection_completed",
        title="Inspectie afgerond" + asset_label,
        description=("Conditiescore: " + str(insp.conditiescore_overall or "-")
                     + " · " + str(beoordeeld_count) + " elementen beoordeeld"),
        source_type="inspection",
        source_id=insp.id,
        lat=insp.asset.lat if insp.asset else None,
        lng=insp.asset.lng if insp.asset else None,
        project_id=insp.project_id if hasattr(insp, "project_id") else None,
    )

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

    # Cyclus-bepaling — kunstwerk-type van inspectie heeft voorrang,
    # anders asset.asset_type
    type_key = insp.kunstwerk_type or asset.asset_type

    # NEN-EN 1176 speeltoestel: cyclus hangt af van het inspectie-kind
    # (routine=7d, operationeel=30d, hoofd=365d). Alleen bij hoofdinspectie
    # zetten we de standaard 12-maandelijkse next-due; bij operationeel/routine
    # is de volgende inspectie korter en gaan we niet de hoofd-cyclus overschrijven.
    next_due = None
    if type_key == "speeltoestel" and insp.nen1176_inspectie_kind:
        days = cycle.nen1176_next_due_days(insp.nen1176_inspectie_kind)
        if days:
            next_due = now + timedelta(days=days)
            # Alleen bij hoofdinspectie ook de asset-cyclus updaten (jaarlijks).
            # Operationeel/routine inspecties hebben tussentijds een kortere
            # eigen volgende-due die niet de hoofd-cyclus van het asset overschrijft.
            if insp.nen1176_inspectie_kind == "hoofd":
                asset.inspection_cycle_months = 12
                asset.next_inspection_due = next_due
    elif type_key in ("wegmarkering", "markering", "belijning"):
        # CROW 145: cyclus afhankelijk van weg-categorie (stroomweg 12mnd vs erftoegang 24mnd).
        # Pak weg_categorie uit asset.properties_json indien aanwezig.
        weg_cat = None
        if asset.properties_json:
            try:
                import json as _json
                props = _json.loads(asset.properties_json)
                weg_cat = props.get("weg_categorie") or props.get("wegtype")
            except (ValueError, TypeError):
                weg_cat = None
        months = cycle.wegmarkering_cycle_months(weg_cat)
        asset.inspection_cycle_months = months
        next_due = cycle.next_due_date(now, months)
        if next_due:
            asset.next_inspection_due = next_due
    else:
        months = cycle.cycle_months_for(type_key, insp.conditiescore_overall)
        if months:
            asset.inspection_cycle_months = months
            next_due = cycle.next_due_date(now, months)
            if next_due:
                asset.next_inspection_due = next_due

    # Sync de inspection.volgende_inspectie_op als de gebruiker geen
    # handmatige datum heeft gezet (geldt voor alle types)
    if next_due and not insp.volgende_inspectie_op:
        insp.volgende_inspectie_op = next_due


# ─────────────────────────────────────────────────────────────────────────────
# Excel/CSV-export per inspectie — voor RAW/begroting-import
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{inspection_id}/export.csv")
def export_inspection_csv(
    inspection_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Exporteer kunstwerk-inspectie als Excel-vriendelijk CSV.

    Bevat 2 secties: elementen-overzicht + defecten-overzicht. Voor
    importeren in besteks-software (RAW) of MJOP-spreadsheet.
    """
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    buf = io.StringIO()
    buf.write("﻿")  # BOM voor Excel UTF-8
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)

    # Header-meta
    w.writerow(["FieldOps Kunstwerk-Inspectie Export"])
    w.writerow(["Inspectie-ID", insp.id])
    w.writerow(["Titel", insp.title or ""])
    w.writerow(["Kunstwerk-type", insp.kunstwerk_type or ""])
    w.writerow(["Status", insp.status])
    w.writerow(["Eindscore", insp.conditiescore_overall or ""])
    w.writerow(["Eindscore-label", scoring.conditie_label(insp.conditiescore_overall)])
    w.writerow(["Datum", insp.datum_inspectie.isoformat() if insp.datum_inspectie else ""])
    w.writerow(["Inspecteur", insp.inspecteur_naam or ""])
    w.writerow([])

    # Sectie 1 — Elementen
    w.writerow(["=== ELEMENTEN ==="])
    w.writerow(["Code", "Naam", "Groep", "Conditiescore", "Label",
                "Defecten", "Aandacht", "Bevindingen", "Aanbevolen actie"])
    for e in (insp.elementen or []):
        attn = sum(1 for a in (e.antwoorden or []) if a.requires_attention)
        w.writerow([
            e.element_code, e.element_naam, e.element_groep or "",
            e.conditiescore or "", scoring.conditie_label(e.conditiescore),
            len(e.defecten or []), attn,
            e.bevindingen or "", e.aanbevolen_actie or "",
        ])

    w.writerow([])

    # Sectie 2 — Defecten
    w.writerow(["=== DEFECTEN ==="])
    w.writerow(["Element-code", "Gebrek-code", "Gebrek-naam",
                "Ernst", "Intensiteit", "Omvang-klasse", "Omvang%",
                "Defect-score", "Toelichting", "Foto-URL"])
    for e in (insp.elementen or []):
        for d in (e.defecten or []):
            w.writerow([
                e.element_code,
                d.gebrek_code or "",
                d.gebrek_naam or "",
                d.ernst or "", d.intensiteit or "",
                d.omvang_klasse or "", d.omvang_percentage or "",
                d.defect_score or "",
                d.omschrijving or "",
                d.photo_url or "",
            ])

    w.writerow([])
    w.writerow([f"Geëxporteerd op {datetime.now(timezone.utc).isoformat()}"])
    w.writerow(["Conform NEN 2767-2 + CROW 134"])
    w.writerow(["LET OP: scores berekend volgens NEN 2767-2 worst-defect-rule"])

    buf.seek(0)
    fname = f"inspectie-{(insp.title or 'rapport').replace(' ', '-')[:40]}-{datetime.now(timezone.utc).date().isoformat()}.csv"
    return StreamingResponse(
        iter([buf.read().encode("utf-8")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/{inspection_id}/export.pdf")
def export_inspection_pdf(
    inspection_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genereer een CROW 134 + NEN 2767-2 conform PDF-inspectierapport.

    Bevat cover + kerncijfers, samenvatting + maatregel-advies, locatie,
    elementen-overzicht, en per defect de NEN-classificatie + foto-bewijs +
    (bij CROW-verharding) maatregel + GWWkosten-orde. Voor opdrachtgever,
    directie en Rekenkamer-onderbouwing. Zet pdf_generated_at + audit-record.
    """
    try:
        from fpdf import FPDF
    except ImportError:
        return StreamingResponse(
            iter([b"PDF-generator niet geinstalleerd: pip install fpdf2"]),
            status_code=500, media_type="text/plain",
        )

    insp = _get_inspection_or_404(db, inspection_id, current_user)
    org = db.query(Organization).filter(
        Organization.id == current_user.organization_id).first()
    org_name = org.name if org else "-"
    asset = insp.asset
    metrics = _compute_metrics(db, insp)

    # fpdf2 core-font Helvetica is latin-1: saneer alle tekst (€, en-dash etc.)
    def safe(v) -> str:
        if v is None:
            return ""
        s = str(v)
        for k, r in (("€", "EUR "), ("–", "-"), ("—", "-"), ("•", "-"),
                     ("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                     ("…", "..."), ("→", "->"), ("×", "x"), ("·", "-"),
                     ("™", "(TM)")):
            s = s.replace(k, r)
        return s.encode("latin-1", "replace").decode("latin-1")

    def _hex_rgb(hexstr: str, default=(148, 163, 184)):
        try:
            h = (hexstr or "").lstrip("#")
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except Exception:
            return default

    # White-label (A): huisstijlkleur + logo van de organisatie. Zo is het
    # rapport het deliverable van de inspecteur, niet van FieldOps. Fallback =
    # FieldOps-blauw als de org geen huisstijl heeft ingesteld.
    BRAND = _hex_rgb(getattr(org, "brand_color", None) or "", (2, 132, 199))
    org_logo = getattr(org, "logo_data_url", None) if org else None

    class _ReportPDF(FPDF):
        """A4-rapport met voettekst (object/org links, paginanummer rechts)."""
        footer_left = ""

        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(120, 120, 120)
            self.set_x(18)
            self.cell(120, 5, self.footer_left)
            self.cell(0, 5, f"Pagina {self.page_no()}/{{nb}}", align="R")
            self.set_text_color(0, 0, 0)

    pdf = _ReportPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.alias_nb_pages()

    def _section_title(title: str) -> None:
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*BRAND)
        pdf.cell(0, 8, safe(title), new_x="LMARGIN", new_y="NEXT", border="B")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(3)

    def _info_row(label: str, value: str) -> None:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(55, 7, safe(label))
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 7, safe(value), new_x="LMARGIN", new_y="NEXT")

    def _embed_image(data_url, w=55, x=None, y=None) -> bool:
        """Bed een afbeelding in: base64 data-URL OF https-URL. Best-effort.

        Sinds de R2-offload (V5) kunnen foto's https-URLs zijn i.p.v. base64;
        beide worden hier ondersteund zodat het foto-bewijs in het rapport blijft
        staan. Faalt nooit hard (een onbereikbare foto laat het rapport door).
        """
        try:
            if not data_url or not isinstance(data_url, str):
                return False
            if data_url.startswith("data:image"):
                raw = base64.b64decode(data_url.split(",", 1)[1])
            elif data_url.startswith(("https://", "http://")):
                import httpx
                resp = httpx.get(data_url, timeout=10.0, follow_redirects=True)
                resp.raise_for_status()
                raw = resp.content
            else:
                return False
            pdf.image(io.BytesIO(raw), w=w, x=x, y=y)
            return True
        except Exception:
            return False

    # ═══ PAGINA 1 — COVER + KERNCIJFERS ═══
    pdf.add_page()
    pdf.set_fill_color(*BRAND)
    pdf.rect(0, 0, 210, 50, "F")
    # White-label logo rechtsboven op de band (best-effort; org stelt 'm in)
    if org_logo:
        _embed_image(org_logo, w=42, x=150, y=10)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_xy(18, 14)
    pdf.cell(0, 10, "INSPECTIERAPPORT", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_x(18)
    kw_label = kt.KUNSTWERK_TYPES.get(insp.kunstwerk_type, insp.kunstwerk_type or "")
    pdf.cell(0, 7, safe(f"{kw_label} - {(asset.code if asset else '') or insp.title or ''}"))
    pdf.set_text_color(0, 0, 0)
    pdf.ln(42)

    _info_row("Organisatie:", org_name)
    obj = (f"{asset.code} - " if asset and asset.code else "") + ((asset.name if asset else "") or "-")
    _info_row("Object:", obj)
    pdf.footer_left = safe(f"{obj} - {org_name}")[:80]

    # Rijkere object-metadata (D) — uit asset.properties_json + kolommen, indien aanwezig
    _props = {}
    if asset and getattr(asset, "properties_json", None):
        try:
            import json as _json_d
            _props = _json_d.loads(asset.properties_json) or {}
        except (ValueError, TypeError):
            _props = {}
    _low = {str(k).lower(): v for k, v in _props.items()} if isinstance(_props, dict) else {}

    def _prop(*keys):
        for k in keys:
            v = _low.get(k)
            if v not in (None, "", []):
                return str(v)
        return None

    _bouwjaar = _prop("bouwjaar", "construction_year", "bouwjaar_aanleg") or (
        asset.installed_at.strftime("%Y") if asset and getattr(asset, "installed_at", None) else None)
    _beheerder = _prop("beheerder", "eigenaar", "owner", "wegbeheerder")
    _wegnr = _prop("wegnummer", "wegnr", "road_number", "straatnaam") or (
        getattr(asset, "nwb_wvk_id", None) if asset else None)
    if _bouwjaar:
        _info_row("Bouwjaar:", _bouwjaar)
    if _beheerder:
        _info_row("Beheerder:", _beheerder)
    if _wegnr:
        _info_row("Wegnummer/WVK:", _wegnr)

    _info_row("Titel:", insp.title or "-")
    _info_row("Inspectie-type:", insp.inspectie_type or "-")
    _info_row("Datum inspectie:",
              insp.datum_inspectie.strftime("%d-%m-%Y") if insp.datum_inspectie else "-")
    _info_row("Inspecteur:", (insp.inspecteur_naam or "-") +
              (f" ({insp.inspecteur_certificaat})" if insp.inspecteur_certificaat else ""))
    _info_row("Weer:", insp.weersomstandigheden or "-")
    _info_row("Norm-referenties:", insp.norm_referenties or "NEN 2767-2; CROW 134")
    _info_row("Status:", insp.status)
    _info_row("Gegenereerd:", datetime.now(timezone.utc).strftime("%d-%m-%Y %H:%M UTC"))
    pdf.ln(6)

    # Kerncijfers-grid (3 grote getallen)
    eind = insp.conditiescore_overall
    eind_label = scoring.conditie_label(eind)
    kpi_w, kpi_h, kpi_gap = 58, 26, 4
    kpi_y = pdf.get_y()
    kpis = [
        (f"{eind if eind is not None else '-'} ({eind_label})",
         "eindconditie NEN 2767-2", _hex_rgb(scoring.conditie_color(eind))),
        (f"{metrics['defecten_totaal']} ({metrics['defecten_kritiek']} kritiek)",
         "defecten", (234, 88, 12)),
        (f"{metrics['elementen_beoordeeld']}/{metrics['elementen_totaal']}",
         "elementen beoordeeld", BRAND),
    ]
    for i, (val, lbl, rgb) in enumerate(kpis):
        x = 18 + i * (kpi_w + kpi_gap)
        pdf.set_fill_color(*rgb)
        pdf.rect(x, kpi_y, kpi_w, kpi_h, "F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_xy(x, kpi_y + 5)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(kpi_w, 7, safe(val), align="C")
        pdf.set_xy(x, kpi_y + 16)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(kpi_w, 5, safe(lbl), align="C")
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(kpi_y + kpi_h + 8)

    advies = scoring.maatregel_advies(eind)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Maatregel-advies", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    termijn = advies.get("termijn_jaren")
    pdf.multi_cell(0, 5, safe(
        f"Categorie: {advies.get('categorie', '-')}. {advies.get('actie', '-')}"
        + (f" (binnen {termijn} jaar)" if termijn else "")
    ))

    # ═══ PAGINA 2 — SAMENVATTING + LOCATIE ═══
    pdf.add_page()
    _section_title("1. Samenvatting en advies")
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Samenvatting", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, safe(insp.samenvatting or "Geen samenvatting ingevuld."))
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Aanbevolen acties", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, safe(insp.aanbevolen_acties or advies.get("actie", "-")))
    pdf.ln(2)
    if insp.volgende_inspectie_op:
        _info_row("Volgende inspectie:", insp.volgende_inspectie_op.strftime("%d-%m-%Y"))
    if insp.bijzonderheden:
        pdf.ln(1)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 6, "Bijzonderheden", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, safe(insp.bijzonderheden))

    pdf.ln(3)
    _section_title("2. Locatie")
    if asset and asset.lat is not None and asset.lng is not None:
        _info_row("Coordinaten:", f"{asset.lat:.6f}, {asset.lng:.6f}")
    _info_row("Omschrijving:", (asset.location_description if asset else None) or "-")

    # ═══ PAGINA 3 — ELEMENTEN-OVERZICHT ═══
    pdf.add_page()
    _section_title("3. Elementen-overzicht")
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(30, 6, "Code", border=1, fill=True)
    pdf.cell(60, 6, "Element", border=1, fill=True)
    pdf.cell(38, 6, "Conditie", border=1, fill=True)
    pdf.cell(22, 6, "Defecten", border=1, fill=True, align="R")
    pdf.cell(0, 6, "Beoordeeld", border=1, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for e in (insp.elementen or []):
        pdf.cell(30, 5, safe(e.element_code)[:16], border=1)
        pdf.cell(60, 5, safe(e.element_naam)[:34], border=1)
        pdf.cell(38, 5, safe(f"{e.conditiescore or '-'} {scoring.conditie_label(e.conditiescore)}")[:20], border=1)
        pdf.cell(22, 5, safe(len(e.defecten or [])), border=1, align="R")
        pdf.cell(0, 5, "ja" if (e.beoordeeld or e.niet_inspecteerbaar_reden) else "nee", border=1)
        pdf.ln()

    # ═══ PAGINA 4 — DEFECTEN-DETAIL + FOTO-BEWIJS ═══
    has_defects = any((e.defecten for e in (insp.elementen or [])))
    if has_defects:
        pdf.add_page()
        _section_title("4. Defecten-detail (NEN 2767-2)")
        photo_budget = 60  # cap foto-inbedding om bestandsgrootte te beperken
        truncated = False
        for e in (insp.elementen or []):
            for d in (e.defecten or []):
                pdf.set_font("Helvetica", "B", 10)
                pdf.multi_cell(0, 6, safe(f"{e.element_naam} - {d.gebrek_naam or 'gebrek'}"),
                               new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", "", 9)
                cls = []
                if d.ernst:
                    cls.append(f"ernst {d.ernst}")
                if d.intensiteit:
                    cls.append(f"intensiteit {d.intensiteit}")
                if d.omvang_klasse:
                    cls.append(f"omvang-klasse {d.omvang_klasse}")
                score_txt = (f"defect-score {d.defect_score} ({scoring.conditie_label(d.defect_score)})"
                             if d.defect_score else "geen score")
                pdf.multi_cell(0, 5, safe(
                    "NEN 2767-2: " + (", ".join(cls) if cls else "n.v.t.") + " | " + score_txt),
                    new_x="LMARGIN", new_y="NEXT")
                if d.locatie_beschrijving:
                    pdf.multi_cell(0, 5, safe(f"Locatie: {d.locatie_beschrijving}"),
                                   new_x="LMARGIN", new_y="NEXT")
                if d.omschrijving:
                    pdf.multi_cell(0, 5, safe(f"Observatie: {d.omschrijving}"),
                                   new_x="LMARGIN", new_y="NEXT")
                # CROW-maatregel + GWWkosten (verharding op kunstwerk, bv brugdek-asfalt)
                if d.crow_klasse:
                    try:
                        m = ck.lookup_maatregel(d.gebrek_code or d.gebrek_naam or "", d.crow_klasse)
                        pdf.multi_cell(0, 5, safe(
                            f"CROW-klasse {d.crow_klasse} ({m.get('categorie', '')}): "
                            f"{m.get('maatregel', '')} - {m.get('kosten_orde', '')} "
                            f"({m.get('gw_term', '')})"),
                            new_x="LMARGIN", new_y="NEXT")
                    except Exception:
                        if d.gw_maatregel:
                            pdf.multi_cell(0, 5, safe(f"Maatregel: {d.gw_maatregel}"),
                                           new_x="LMARGIN", new_y="NEXT")
                elif d.gw_maatregel:
                    pdf.multi_cell(0, 5, safe(f"Maatregel: {d.gw_maatregel}"),
                                   new_x="LMARGIN", new_y="NEXT")
                # Foto-bewijs
                if photo_budget > 0:
                    if _embed_image(d.photo_url, w=55):
                        photo_budget -= 1
                elif d.photo_url:
                    truncated = True
                pdf.ln(3)
        if truncated:
            pdf.set_font("Helvetica", "I", 8)
            pdf.multi_cell(0, 4, "(Niet alle foto's zijn ingesloten om de bestandsgrootte te beperken.)")

    # ═══ PAGINA 5 — ONDERTEKENING + VERANTWOORDING ═══
    pdf.add_page()
    _section_title("5. Ondertekening en verantwoording")
    if insp.status in ("signed", "delivered") and insp.signature_data_url:
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, safe(f"Ondertekend door: {insp.inspecteur_naam or '-'}"),
                 new_x="LMARGIN", new_y="NEXT")
        if insp.signed_at:
            pdf.cell(0, 6, safe(f"Datum: {insp.signed_at.strftime('%d-%m-%Y %H:%M UTC')}"),
                     new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        _embed_image(insp.signature_data_url, w=60)
    else:
        pdf.set_font("Helvetica", "I", 10)
        pdf.multi_cell(0, 5, "Deze inspectie is nog niet ondertekend. Een ondertekend "
                             "rapport is pas rechtsgeldig na status 'signed'.")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 5, safe(
        "Methodiek: conditiescores zijn bepaald volgens NEN 2767-2 (worst-defect-rule per "
        "element; het slechtste element bepaalt de objectconditie). Maatregel-categorieen "
        "volgen de CROW 134 maatregelmatrix. Kosten-ordes zijn indicatief (GWWkosten 2024); "
        "voor aanbesteding is een RAW-besteksraming per project vereist."))
    pdf.ln(2)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(0, 4, safe(
        f"Gegenereerd door FieldOps op {datetime.now(timezone.utc).strftime('%d-%m-%Y %H:%M UTC')} "
        f"- inspectie-ID {insp.id}"))

    # Persist + audit-record (compliance: wie genereerde het rapport, wanneer)
    insp.pdf_generated_at = datetime.now(timezone.utc)
    db.commit()
    log_action(db, request, current_user,
               action=ACTION.INSPECTION_PDF_GENERATE,
               entity_type="inspection", entity_id=insp.id,
               extra={"status": insp.status,
                      "conditiescore_overall": insp.conditiescore_overall})

    out = bytes(pdf.output())
    base = (asset.code if asset and asset.code else (insp.title or "rapport"))
    fname = f"inspectierapport-{base}-{datetime.now(timezone.utc).date().isoformat()}.pdf"
    fname = fname.replace(" ", "-").encode("ascii", "ignore").decode("ascii") or "inspectierapport.pdf"
    return StreamingResponse(
        iter([out]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


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


@router.delete("/{inspection_id}/elementen/{element_id}")
def delete_element(
    inspection_id: str,
    element_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verwijder een bouwdeel uit de inspectie — incl. zijn vragen en defecten.

    Voor bouwdelen die niet van toepassing zijn op dit specifieke object (elk
    kunstwerk is anders; de standaard-decompositie is een vertrekpunt dat je
    toespitst). Niet toegestaan op afgesloten inspecties. Herberekent daarna de
    objectconditie (worst-element).
    """
    insp = _get_inspection_or_404(db, inspection_id, current_user)
    if insp.status in ("signed", "delivered"):
        raise HTTPException(status_code=409, detail="Inspectie is afgesloten")
    el = _get_element_or_404(db, insp, element_id)
    code = el.element_code
    # cascade="all, delete-orphan" op InspectionElement.defecten/antwoorden ruimt
    # de bijbehorende vragen + defecten automatisch mee op.
    db.delete(el)
    db.flush()
    _recompute_inspection_score(db, insp)
    db.commit()
    log_action(db, request, current_user,
               action=ACTION.INSPECTION_ELEMENT_UPDATE,
               entity_type="inspection_element", entity_id=element_id,
               extra={"inspection_id": insp.id, "action_sub": "delete",
                      "element_code": code})
    return {"deleted": element_id, "conditiescore_overall": insp.conditiescore_overall}


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

    # NEN-EN 1176 — valideer A/B/C/D classificatie (alleen voor speeltoestellen)
    en1176_cat = data.get("en1176_categorie")
    en1176_acute = data.get("en1176_acute_afsluiting")
    if en1176_cat is not None:
        if insp.kunstwerk_type != "speeltoestel":
            raise HTTPException(
                status_code=400,
                detail="en1176_categorie is alleen geldig voor kunstwerk_type=speeltoestel",
            )
        en1176_cat = en1176_cat.upper().strip()
        if en1176_cat not in {"A", "B", "C", "D"}:
            raise HTTPException(
                status_code=400,
                detail="en1176_categorie moet 'A', 'B', 'C' of 'D' zijn (NEN-EN 1176 § 8.2)",
            )
        # Server-side enforcement: categorie C of D = acute afsluiting verplicht
        # tenzij de inspecteur expliciet false geeft (met motivering elders)
        if en1176_cat in {"C", "D"} and en1176_acute is None:
            en1176_acute = True

    # VTA boom (Mattheck) — risicoklasse alleen voor boom
    vta_klasse = data.get("vta_risicoklasse")
    if vta_klasse is not None:
        if insp.kunstwerk_type != "boom":
            raise HTTPException(
                status_code=400,
                detail="vta_risicoklasse is alleen geldig voor kunstwerk_type=boom",
            )
        if not (1 <= int(vta_klasse) <= 5):
            raise HTTPException(
                status_code=400,
                detail="vta_risicoklasse moet 1-5 zijn (Mattheck VTA)",
            )
    vta_holte = data.get("vta_holte_pct")
    if vta_holte is not None and not (0 <= float(vta_holte) <= 100):
        raise HTTPException(status_code=400, detail="vta_holte_pct moet 0-100 zijn")

    # VTA t/r-ratio < 0.30 = breukrisico volgens Mattheck-criterium.
    # Server forceert dan risicoklasse 5 (acuut) als niet expliciet anders + log alert.
    vta_tr = data.get("vta_t_r_ratio")
    if vta_tr is not None:
        if insp.kunstwerk_type != "boom":
            raise HTTPException(
                status_code=400,
                detail="vta_t_r_ratio is alleen geldig voor kunstwerk_type=boom",
            )
        if float(vta_tr) < 0.30:
            # Auto-escaleer naar klasse 5 als geen expliciete (lagere) klasse
            if vta_klasse is None:
                data["vta_risicoklasse"] = 5
                vta_klasse = 5
            # Voeg waarschuwing toe aan omschrijving — zichtbaar in PDF + frontend
            warning = "[ACUUT BREUKRISICO] Mattheck-criterium t/r < 0.30 — nader onderzoek + maatregel binnen 4 weken."
            existing_desc = (data.get("omschrijving") or "").strip()
            if warning not in existing_desc:
                data["omschrijving"] = (warning + "\n\n" + existing_desc).strip() if existing_desc else warning

    # NEN 3140 elektrische meetwaarden — alleen voor verlichting
    nen3140_fields = ("nen3140_isolatie_megaohm", "nen3140_aardingsweerstand_ohm",
                       "nen3140_aardlek_ms", "nen3140_aardlek_ma")
    if any(data.get(f) is not None for f in nen3140_fields):
        if insp.kunstwerk_type != "verlichting":
            raise HTTPException(
                status_code=400,
                detail="NEN 3140 meetvelden zijn alleen geldig voor kunstwerk_type=verlichting",
            )
        # Sanity-check: isolatie < 0.5 MΩ is direct gevaar (NEN 3140 § 6.4)
        iso = data.get("nen3140_isolatie_megaohm")
        if iso is not None and iso < 0:
            raise HTTPException(status_code=400, detail="nen3140_isolatie_megaohm moet >= 0 zijn")

    # CROW 145 retroreflectie — alleen voor wegmarkering
    crow_fields = ("crow145_rl_droog_mcd", "crow145_rl_nat_mcd")
    if any(data.get(f) is not None for f in crow_fields):
        if insp.kunstwerk_type != "wegmarkering":
            raise HTTPException(
                status_code=400,
                detail="CROW 145 retroreflectie-velden zijn alleen geldig voor wegmarkering",
            )

    # NEN 3399 schadecodes — alleen voor riolering
    nen3399_code = data.get("nen3399_code")
    nen3399_klasse = data.get("nen3399_klasse")
    nen3399_streng = data.get("nen3399_streng_id")
    if nen3399_code is not None or nen3399_klasse is not None or nen3399_streng is not None:
        if insp.kunstwerk_type != "riolering":
            raise HTTPException(
                status_code=400,
                detail="NEN 3399 velden zijn alleen geldig voor kunstwerk_type=riolering",
            )
        if nen3399_code is not None:
            nen3399_code = nen3399_code.upper().strip()
            # NEN-EN 13508-2 codes: BAA..BAZ (3-letter codes)
            if not (len(nen3399_code) == 3 and nen3399_code.startswith("BA")):
                raise HTTPException(
                    status_code=400,
                    detail="nen3399_code moet 3 chars zijn beginnend met 'BA' (bv. BAA, BAB, BAJ)",
                )
        if nen3399_klasse is not None and not (1 <= int(nen3399_klasse) <= 5):
            raise HTTPException(status_code=400, detail="nen3399_klasse moet 1-5 zijn")

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
        en1176_categorie=en1176_cat,
        en1176_acute_afsluiting=bool(en1176_acute) if en1176_acute is not None else False,
        vta_risicoklasse=data.get("vta_risicoklasse"),
        vta_holte_pct=data.get("vta_holte_pct"),
        vta_t_r_ratio=data.get("vta_t_r_ratio"),
        nen3140_isolatie_megaohm=data.get("nen3140_isolatie_megaohm"),
        nen3140_aardingsweerstand_ohm=data.get("nen3140_aardingsweerstand_ohm"),
        nen3140_aardlek_ms=data.get("nen3140_aardlek_ms"),
        nen3140_aardlek_ma=data.get("nen3140_aardlek_ma"),
        crow145_rl_droog_mcd=data.get("crow145_rl_droog_mcd"),
        crow145_rl_nat_mcd=data.get("crow145_rl_nat_mcd"),
        nen3399_code=nen3399_code,
        nen3399_klasse=data.get("nen3399_klasse"),
        nen3399_streng_id=(nen3399_streng.strip() if isinstance(nen3399_streng, str) else nen3399_streng),
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
