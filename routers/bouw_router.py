"""BOEI-router — conditie- en risico-inspectie van gebouwen.

Endpoints:
  GET    /api/bouw/checklist                 Vragenlijst, elementen en pijlers
  GET    /api/bouw/                          Lijst van opnames
  POST   /api/bouw/                          Start een opname (vult de vragen voor)
  GET    /api/bouw/{id}                      Detail met antwoorden en condities
  PATCH  /api/bouw/{id}                      Kop bijwerken
  PATCH  /api/bouw/{id}/antwoorden/{aid}     Een vraag beantwoorden
  POST   /api/bouw/{id}/condities            Conditiescore van een element vastleggen
  DELETE /api/bouw/{id}/condities/{cid}      Conditieregel verwijderen
  POST   /api/bouw/{id}/afronden             Vastzetten en scores berekenen
  DELETE /api/bouw/{id}                      Verwijderen
  GET    /api/bouw/acties/open               Openstaande acties over alle opnames

**Per gebouw of per straat.** Een opname hangt aan een asset (het gebouw) of aan
een straatnaam, en aan precies een van de twee. Een schoolgebouw neem je op als
object; een rij portiekwoningen of een bedrijventerrein loopt een inspecteur per
straat af. Beide toestaan in hetzelfde model voorkomt dat iemand vijftig losse
opnames maakt die niemand meer bij elkaar zoekt.

De conditiescores gaan door dezelfde motor als de infrastructuur-inspecties:
``nen2767_scoring``. NEN 2767 is een methodiek voor gebouwen (-1) en
infrastructuur (-4); daar hoeft niets voor gedupliceerd te worden.

Rollen volgen de rest van het inspectiewerk: opnemen en afronden is voor admin
en manager, lezen mag iedereen binnen de organisatie.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import bouw_boei as bb
import nen2767_scoring as scoring
from audit import log_action
from auth import get_current_user
from database import get_db
from models import (Asset, BouwAntwoord, BouwElementConditie, BouwInspectie,
                    Project, User)
from permissions import can_manage_toolbox, require_module

router = APIRouter(prefix="/api/bouw", tags=["Bouw"],
                   dependencies=[Depends(require_module("bouw"))])


# ── Schemas ──────────────────────────────────────────────────────────

class BouwIn(BaseModel):
    # Precies een van deze twee; zie _eis_object.
    asset_id: Optional[str] = None
    straatnaam: Optional[str] = None

    project_id: Optional[str] = None
    plaats: Optional[str] = None
    gebouw_type: Optional[str] = None
    bouwjaar: Optional[int] = Field(default=None, ge=1000, le=2100)
    datum: Optional[datetime] = None
    # Welke pijlers je opneemt. Leeg = alle vier.
    pijlers: Optional[list[str]] = None


class BouwUpdate(BaseModel):
    straatnaam: Optional[str] = None
    plaats: Optional[str] = None
    gebouw_type: Optional[str] = None
    bouwjaar: Optional[int] = Field(default=None, ge=1000, le=2100)
    datum: Optional[datetime] = None
    algemene_indruk: Optional[str] = None


class AntwoordIn(BaseModel):
    antwoord: Optional[str] = Field(default=None, pattern="^(ja|nee|nvt)$")
    waarde: Optional[str] = None
    toelichting: Optional[str] = None
    photo_url: Optional[str] = None
    bewijs_aanwezig: Optional[bool] = None
    bewijs_geldig_tot: Optional[datetime] = None
    actie: Optional[str] = None
    actiehouder_id: Optional[str] = None
    actie_gereed: Optional[bool] = None


class ConditieIn(BaseModel):
    element_code: str = Field(..., min_length=1, max_length=20)
    gebrek: Optional[str] = None
    ernst: Optional[int] = Field(default=None, ge=1, le=3)
    intensiteit: Optional[int] = Field(default=None, ge=1, le=3)
    omvang_klasse: Optional[int] = Field(default=None, ge=1, le=5)
    toelichting: Optional[str] = None
    photo_url: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────

def _eis_beheer(current_user: User) -> None:
    if not can_manage_toolbox(current_user):
        raise HTTPException(
            status_code=403,
            detail="Alleen een beheerder of manager kan een gebouwinspectie invullen")


def _eis_object(asset_id: Optional[str], straatnaam: Optional[str]) -> None:
    """Precies een van beide, en niet allebei.

    Allebei toestaan levert opnames op waarvan niemand weet of ze bij het
    gebouw of bij de straat horen -- en dus dubbeltellingen in de rapportage.
    """
    heeft_asset = bool(asset_id)
    heeft_straat = bool(straatnaam and straatnaam.strip())
    if heeft_asset and heeft_straat:
        raise HTTPException(
            status_code=400,
            detail="Kies een gebouw of een straat, niet allebei")
    if not heeft_asset and not heeft_straat:
        raise HTTPException(
            status_code=400,
            detail="Geef een gebouw (asset_id) of een straatnaam op")


def _antwoord_to_dict(a: BouwAntwoord) -> dict:
    return {
        "id": a.id,
        "question_code": a.question_code,
        "question_version": a.question_version,
        "vraag": a.question_text_snapshot,
        "pijler": a.pijler,
        "antwoord": a.antwoord,
        "waarde": a.waarde,
        "toelichting": a.toelichting,
        "photo_url": a.photo_url,
        "bewijs_aanwezig": a.bewijs_aanwezig,
        "bewijs_geldig_tot": a.bewijs_geldig_tot.isoformat() if a.bewijs_geldig_tot else None,
        "actie": a.actie,
        "actiehouder_id": a.actiehouder_id,
        "actiehouder_naam": a.actiehouder_naam,
        "actie_gereed": a.actie_gereed,
        "order_index": a.order_index,
    }


def _conditie_to_dict(c: BouwElementConditie) -> dict:
    return {
        "id": c.id,
        "element_code": c.element_code,
        "element": c.element_naam_snapshot,
        "groep": c.groep,
        "gebrek": c.gebrek,
        "ernst": c.ernst,
        "intensiteit": c.intensiteit,
        "omvang_klasse": c.omvang_klasse,
        "conditie": c.conditie,
        "conditie_label": scoring.CONDITIE_LABELS.get(c.conditie) if c.conditie else None,
        "toelichting": c.toelichting,
        "photo_url": c.photo_url,
        "order_index": c.order_index,
    }


def _inspectie_to_dict(b: BouwInspectie, *, detail: bool = False) -> dict:
    antwoorden = list(b.antwoorden or [])
    condities = list(b.condities or [])
    uit = {
        "id": b.id,
        "asset_id": b.asset_id,
        "straatnaam": b.straatnaam,
        "plaats": b.plaats,
        "project_id": b.project_id,
        "gebouw_type": b.gebouw_type,
        "gebouw_type_label": bb.GEBOUW_TYPES.get(b.gebouw_type or ""),
        "bouwjaar": b.bouwjaar,
        "datum": b.datum.isoformat() if b.datum else None,
        "inspecteur_naam": b.inspecteur_naam,
        "status": b.status,
        "checklist_versie": b.checklist_versie,
        "pijlers": _pijlers_uit(b),
        "algemene_indruk": b.algemene_indruk,
        "score_pct": b.score_pct,
        "aantal_aandachtspunten": b.aantal_aandachtspunten,
        "conditie_hoogste": b.conditie_hoogste,
        "aantal_elementen": len(condities),
        "open_acties": sum(1 for a in antwoorden
                           if a.antwoord == "nee" and not a.actie_gereed),
        "afgerond_op": b.afgerond_op.isoformat() if b.afgerond_op else None,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }
    if b.status == "concept":
        # Tussenstand zolang de opname loopt, zodat je ziet hoe ver je bent.
        uit["voortgang"] = bb.bereken_score(
            [{"code": a.question_code, "antwoord": a.antwoord} for a in antwoorden])
    if detail:
        uit["antwoorden"] = [_antwoord_to_dict(a) for a in antwoorden]
        uit["condities"] = [_conditie_to_dict(c) for c in condities]
        uit["pijler_labels"] = {k: v["naam"] for k, v in bb.PIJLERS.items()}
        uit["elementgroepen"] = bb.ELEMENTGROEPEN
    return uit


def _pijlers_uit(b: BouwInspectie) -> list[str]:
    if not b.pijlers:
        return list(bb.PIJLERS)
    import json
    try:
        waarde = json.loads(b.pijlers)
        return [p for p in waarde if p in bb.PIJLERS] or list(bb.PIJLERS)
    except Exception:  # noqa: BLE001 — kapotte JSON mag de opname niet blokkeren
        return list(bb.PIJLERS)


def _get_or_404(db: Session, bouw_id: str, current_user: User) -> BouwInspectie:
    b = (db.query(BouwInspectie)
           .filter(BouwInspectie.id == bouw_id,
                   BouwInspectie.organization_id == current_user.organization_id)
           .first())
    if not b:
        raise HTTPException(status_code=404, detail="Gebouwinspectie niet gevonden")
    return b


def _eis_niet_afgerond(b: BouwInspectie) -> None:
    if b.status == "afgerond":
        raise HTTPException(
            status_code=409,
            detail="Deze gebouwinspectie is afgerond en kan niet meer worden gewijzigd")


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/checklist")
def checklist(gebouw_type: Optional[str] = None, pijler: Optional[str] = None):
    """De vragenlijst, de elementenstructuur en de gevraagde bewijsstukken."""
    return {
        "versie": bb.BOEI_VERSION,
        "pijlers": bb.pijlers(),
        "gebouw_types": bb.GEBOUW_TYPES,
        "elementgroepen": bb.ELEMENTGROEPEN,
        "elementen": bb.elementen_voor(),
        "vragen": bb.vragen_voor(pijler=pijler, gebouw_type=gebouw_type),
        "bewijsstukken": bb.bewijsstukken(),
    }


@router.get("/acties/open")
def open_acties(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alle openstaande aandachtspunten over alle opnames van deze organisatie."""
    rijen = (db.query(BouwAntwoord)
               .filter(BouwAntwoord.organization_id == current_user.organization_id,
                       BouwAntwoord.antwoord == "nee",
                       BouwAntwoord.actie_gereed == False)  # noqa: E712
               .order_by(BouwAntwoord.created_at.desc())
               .limit(500).all())
    return [{
        "id": a.id,
        "bouw_inspectie_id": a.bouw_inspectie_id,
        "pijler": a.pijler,
        "vraag": a.question_text_snapshot,
        "actie": a.actie,
        "actiehouder_naam": a.actiehouder_naam,
    } for a in rijen]


@router.get("/")
def lijst(
    status: Optional[str] = None,
    asset_id: Optional[str] = None,
    straatnaam: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = (db.query(BouwInspectie)
           .filter(BouwInspectie.organization_id == current_user.organization_id))
    if status:
        q = q.filter(BouwInspectie.status == status)
    if asset_id:
        q = q.filter(BouwInspectie.asset_id == asset_id)
    if straatnaam:
        q = q.filter(BouwInspectie.straatnaam == straatnaam)
    rijen = q.order_by(BouwInspectie.created_at.desc()).limit(200).all()
    return [_inspectie_to_dict(b) for b in rijen]


@router.post("/")
def start(
    payload: BouwIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start een opname. De vragen worden meteen aangemaakt.

    Bewust vooraf en niet gaandeweg: zo staat de complete lijst op je scherm en
    zie je wat je nog niet gehad hebt. Alleen de vragen van de gekozen pijlers
    en het gekozen gebouwtype worden aangemaakt -- een vraag over roltrappen in
    een gemeentewerf leidt alleen maar af.
    """
    _eis_beheer(current_user)
    _eis_object(payload.asset_id, payload.straatnaam)

    asset = None
    if payload.asset_id:
        asset = (db.query(Asset)
                   .filter(Asset.id == payload.asset_id,
                           Asset.organization_id == current_user.organization_id)
                   .first())
        if not asset:
            raise HTTPException(status_code=404, detail="Asset niet gevonden")

    if payload.project_id:
        project = (db.query(Project)
                     .filter(Project.id == payload.project_id,
                             Project.organization_id == current_user.organization_id)
                     .first())
        if not project:
            raise HTTPException(status_code=404, detail="Project niet gevonden")

    if payload.gebouw_type and payload.gebouw_type not in bb.GEBOUW_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Onbekend gebouwtype. Kies uit: {', '.join(bb.GEBOUW_TYPES)}")

    gekozen = [p.upper() for p in (payload.pijlers or []) if p.upper() in bb.PIJLERS]
    if not gekozen:
        gekozen = list(bb.PIJLERS)

    import json
    naam = " ".join(x for x in (current_user.first_name, current_user.last_name) if x).strip()
    b = BouwInspectie(
        organization_id=current_user.organization_id,
        project_id=payload.project_id,
        asset_id=payload.asset_id,
        straatnaam=(payload.straatnaam or "").strip() or None,
        plaats=payload.plaats,
        gebouw_type=payload.gebouw_type,
        bouwjaar=payload.bouwjaar,
        datum=payload.datum or datetime.now(timezone.utc),
        inspecteur_id=current_user.id,
        inspecteur_naam=naam or current_user.email,
        status="concept",
        checklist_versie=bb.BOEI_VERSION,
        pijlers=json.dumps(gekozen),
        created_by=current_user.id,
    )
    db.add(b)
    db.flush()

    vragen = [v for v in bb.vragen_voor(gebouw_type=payload.gebouw_type)
              if v["pijler"] in gekozen]
    for i, v in enumerate(vragen):
        db.add(BouwAntwoord(
            bouw_inspectie_id=b.id,
            organization_id=current_user.organization_id,
            question_code=v["code"],
            question_version=bb.BOEI_VERSION,
            question_text_snapshot=v["vraag"][:500],
            pijler=v["pijler"],
            order_index=i,
        ))

    db.commit()
    db.refresh(b)
    log_action(db, request, current_user, action="bouw.create",
               entity_type="bouw_inspectie", entity_id=b.id,
               after={"asset_id": b.asset_id, "straatnaam": b.straatnaam,
                      "pijlers": gekozen, "vragen": len(vragen)})
    return _inspectie_to_dict(b, detail=True)


@router.get("/{bouw_id}")
def detail(
    bouw_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _inspectie_to_dict(_get_or_404(db, bouw_id, current_user), detail=True)


@router.patch("/{bouw_id}")
def bijwerken(
    bouw_id: str,
    payload: BouwUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    b = _get_or_404(db, bouw_id, current_user)
    _eis_niet_afgerond(b)

    velden = payload.model_dump(exclude_unset=True)
    if "gebouw_type" in velden and velden["gebouw_type"] not in (None, *bb.GEBOUW_TYPES):
        raise HTTPException(status_code=400, detail="Onbekend gebouwtype")
    # Een straatopname mag hernoemd worden, maar niet omgekat naar een gebouw:
    # de vragenlijst is dan al op het verkeerde type aangemaakt.
    if "straatnaam" in velden and b.asset_id:
        raise HTTPException(
            status_code=400,
            detail="Deze opname hangt aan een gebouw; een straatnaam hoort daar niet bij")
    for k, v in velden.items():
        setattr(b, k, v)
    db.commit()
    db.refresh(b)
    return _inspectie_to_dict(b, detail=True)


@router.patch("/{bouw_id}/antwoorden/{antwoord_id}")
def beantwoorden(
    bouw_id: str,
    antwoord_id: str,
    payload: AntwoordIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    b = _get_or_404(db, bouw_id, current_user)
    _eis_niet_afgerond(b)

    a = (db.query(BouwAntwoord)
           .filter(BouwAntwoord.id == antwoord_id,
                   BouwAntwoord.bouw_inspectie_id == b.id,
                   BouwAntwoord.organization_id == current_user.organization_id)
           .first())
    if not a:
        raise HTTPException(status_code=404, detail="Vraag niet gevonden")

    velden = payload.model_dump(exclude_unset=True)
    if velden.get("actiehouder_id"):
        houder = (db.query(User)
                    .filter(User.id == velden["actiehouder_id"],
                            User.organization_id == current_user.organization_id)
                    .first())
        if not houder:
            raise HTTPException(status_code=404, detail="Actiehouder niet gevonden")
        a.actiehouder_naam = " ".join(
            x for x in (houder.first_name, houder.last_name) if x).strip() or houder.email

    for k, v in velden.items():
        setattr(a, k, v)
    db.commit()
    db.refresh(a)
    return _antwoord_to_dict(a)


@router.post("/{bouw_id}/condities")
def conditie_vastleggen(
    bouw_id: str,
    payload: ConditieIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Conditiescore van een element vastleggen.

    De score wordt hier berekend en opgeslagen, niet bij het uitlezen. Zo
    verandert een afgeronde opname niet als de rekenkern later wordt bijgesteld.
    """
    _eis_beheer(current_user)
    b = _get_or_404(db, bouw_id, current_user)
    _eis_niet_afgerond(b)

    el = bb.element(payload.element_code)
    if el is None:
        raise HTTPException(
            status_code=404,
            detail=f"Onbekend element: {payload.element_code}")

    conditie = scoring.defect_to_score(
        payload.ernst, payload.intensiteit, payload.omvang_klasse)

    bestaand = (db.query(BouwElementConditie)
                  .filter(BouwElementConditie.bouw_inspectie_id == b.id,
                          BouwElementConditie.element_code == payload.element_code,
                          BouwElementConditie.organization_id == current_user.organization_id)
                  .first())
    if bestaand is None:
        volgende = (db.query(BouwElementConditie)
                      .filter(BouwElementConditie.bouw_inspectie_id == b.id)
                      .count())
        bestaand = BouwElementConditie(
            bouw_inspectie_id=b.id,
            organization_id=current_user.organization_id,
            element_code=payload.element_code,
            order_index=volgende,
        )
        db.add(bestaand)

    bestaand.element_naam_snapshot = el["naam"][:160]
    bestaand.groep = el["groep"]
    bestaand.gebrek = payload.gebrek
    bestaand.ernst = payload.ernst
    bestaand.intensiteit = payload.intensiteit
    bestaand.omvang_klasse = payload.omvang_klasse
    bestaand.conditie = conditie
    bestaand.scoring_versie = scoring.SCORING_VERSION
    bestaand.toelichting = payload.toelichting
    bestaand.photo_url = payload.photo_url

    db.commit()
    db.refresh(bestaand)
    return _conditie_to_dict(bestaand)


@router.delete("/{bouw_id}/condities/{conditie_id}")
def conditie_verwijderen(
    bouw_id: str,
    conditie_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    b = _get_or_404(db, bouw_id, current_user)
    _eis_niet_afgerond(b)
    c = (db.query(BouwElementConditie)
           .filter(BouwElementConditie.id == conditie_id,
                   BouwElementConditie.bouw_inspectie_id == b.id,
                   BouwElementConditie.organization_id == current_user.organization_id)
           .first())
    if not c:
        raise HTTPException(status_code=404, detail="Conditieregel niet gevonden")
    db.delete(c)
    db.commit()
    return {"status": "verwijderd"}


@router.post("/{bouw_id}/afronden")
def afronden(
    bouw_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Opname vastzetten en de scores berekenen."""
    _eis_beheer(current_user)
    b = _get_or_404(db, bouw_id, current_user)
    _eis_niet_afgerond(b)

    antwoorden = list(b.antwoorden or [])
    onbeantwoord = [a for a in antwoorden if not a.antwoord]
    if onbeantwoord:
        raise HTTPException(
            status_code=400,
            detail=f"Nog {len(onbeantwoord)} vragen onbeantwoord")

    resultaat = bb.bereken_score(
        [{"code": a.question_code, "antwoord": a.antwoord} for a in antwoorden])

    condities = [c.conditie for c in (b.condities or []) if c.conditie]
    b.conditie_hoogste = max(condities) if condities else None
    b.score_pct = int(round(resultaat["score_pct"])) if resultaat["score_pct"] is not None else None
    b.aantal_aandachtspunten = resultaat["aandachtspunten"]
    b.status = "afgerond"
    b.afgerond_op = datetime.now(timezone.utc)
    db.commit()
    db.refresh(b)

    log_action(db, request, current_user, action="bouw.afronden",
               entity_type="bouw_inspectie", entity_id=b.id,
               after={"score_pct": b.score_pct,
                      "aandachtspunten": b.aantal_aandachtspunten,
                      "conditie_hoogste": b.conditie_hoogste})
    return _inspectie_to_dict(b, detail=True)


@router.delete("/{bouw_id}")
def verwijderen(
    bouw_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    b = _get_or_404(db, bouw_id, current_user)
    db.delete(b)
    db.commit()
    log_action(db, request, current_user, action="bouw.delete",
               entity_type="bouw_inspectie", entity_id=bouw_id)
    return {"status": "verwijderd"}
