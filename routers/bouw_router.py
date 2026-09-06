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
  GET    /api/bouw/{id}/export.pdf           Rapport voor de opdrachtgever
  GET    /api/bouw/acties/open               Openstaande acties over alle opnames

**Het gebouw benoem je zelf.** Een inspecteur staat voor een pand en legt het
vast met naam, straat, huisnummer, postcode, plaats en eigenaar. Het gebouw hoeft
niet eerst als asset te bestaan -- dat zou betekenen dat je voor elke opname
eerst het areaal moet bijwerken, en dan gebeurt de opname niet. Wie het pand wel
in zijn areaal heeft kan het koppelen via `asset_id`; dat is gemak, geen
voorwaarde.

**Per gebouw of per straat.** Het verschil zit in het huisnummer: mét huisnummer
is het een pand, zonder huisnummer een straat. Een rij portiekwoningen of een
bedrijventerrein loop je per straat af. Minimaal een gebouwnaam of een straat is
verplicht -- een opname zonder plek is later niet terug te vinden.

De conditiescores gaan door dezelfde motor als de infrastructuur-inspecties:
``nen2767_scoring``. NEN 2767 is een methodiek voor gebouwen (-1) en
infrastructuur (-4); daar hoeft niets voor gedupliceerd te worden.

Rollen volgen de rest van het inspectiewerk: opnemen en afronden is voor admin
en manager, lezen mag iedereen binnen de organisatie.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import bouw_boei as bb
import nen2767_scoring as scoring
from audit import log_action
from auth import get_current_user
from database import get_db
from models import (Asset, BouwAntwoord, BouwElementConditie, BouwInspectie,
                    Organization, Project, User)
from permissions import can_manage_toolbox, require_module

router = APIRouter(prefix="/api/bouw", tags=["Bouw"],
                   dependencies=[Depends(require_module("bouw"))])


# ── Schemas ──────────────────────────────────────────────────────────

class BouwIn(BaseModel):
    # Minimaal een van gebouw_naam of straatnaam; zie _eis_plek.
    gebouw_naam: Optional[str] = Field(default=None, max_length=255)
    straatnaam: Optional[str] = Field(default=None, max_length=255)
    huisnummer: Optional[str] = Field(default=None, max_length=20)
    postcode: Optional[str] = Field(default=None, max_length=12)
    plaats: Optional[str] = Field(default=None, max_length=120)
    eigenaar: Optional[str] = Field(default=None, max_length=255)
    # Optionele koppeling aan een bestaand areaal-object.
    asset_id: Optional[str] = None
    # Wie de opname doet. Standaard de ingelogde gebruiker, maar overschrijfbaar
    # -- een bureau laat een ingehuurde inspecteur onder eigen naam werken.
    inspecteur_naam: Optional[str] = Field(default=None, max_length=120)

    project_id: Optional[str] = None
    gebouw_type: Optional[str] = None
    bouwjaar: Optional[int] = Field(default=None, ge=1000, le=2100)
    datum: Optional[datetime] = None
    # Welke pijlers je opneemt. Leeg = alle vier.
    pijlers: Optional[list[str]] = None


class BouwUpdate(BaseModel):
    gebouw_naam: Optional[str] = Field(default=None, max_length=255)
    straatnaam: Optional[str] = Field(default=None, max_length=255)
    huisnummer: Optional[str] = Field(default=None, max_length=20)
    postcode: Optional[str] = Field(default=None, max_length=12)
    plaats: Optional[str] = Field(default=None, max_length=120)
    eigenaar: Optional[str] = Field(default=None, max_length=255)
    inspecteur_naam: Optional[str] = Field(default=None, max_length=120)
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


def _eis_plek(gebouw_naam: Optional[str], straatnaam: Optional[str]) -> None:
    """Minimaal een gebouwnaam of een straat.

    Alleen een postcode of alleen een eigenaar is niet genoeg: een opname zonder
    herkenbare plek is over een jaar niet meer terug te vinden, en dan is de
    conditiemeting waardeloos. Alles daarboven mag leeg blijven -- een inspecteur
    die in de regen staat vult in wat hij weet.
    """
    if not (gebouw_naam or "").strip() and not (straatnaam or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Geef minimaal een gebouwnaam of een straatnaam op")


def _omschrijving(b) -> str:
    """Een leesbare aanduiding voor lijsten en de PDF.

    Valt terug op wat er is: naam, anders adres, anders straat. Nooit een leeg
    label, want dan staat er een regel in de lijst waar niemand iets aan heeft.
    """
    adres = " ".join(x for x in ((b.straatnaam or "").strip(),
                                 (b.huisnummer or "").strip()) if x)
    if b.gebouw_naam and adres:
        return f"{b.gebouw_naam} - {adres}"
    return (b.gebouw_naam or adres or b.straatnaam or "Naamloze opname").strip()


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
        "omschrijving": _omschrijving(b),
        "gebouw_naam": b.gebouw_naam,
        "straatnaam": b.straatnaam,
        "huisnummer": b.huisnummer,
        "postcode": b.postcode,
        "plaats": b.plaats,
        "eigenaar": b.eigenaar,
        "asset_id": b.asset_id,
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
    postcode: Optional[str] = None,
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
    if postcode:
        q = q.filter(BouwInspectie.postcode == postcode.strip().upper())
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
    _eis_plek(payload.gebouw_naam, payload.straatnaam)

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
    eigen_naam = " ".join(
        x for x in (current_user.first_name, current_user.last_name) if x).strip()
    naam = (payload.inspecteur_naam or "").strip() or eigen_naam
    b = BouwInspectie(
        organization_id=current_user.organization_id,
        project_id=payload.project_id,
        asset_id=payload.asset_id,
        gebouw_naam=(payload.gebouw_naam or "").strip() or None,
        straatnaam=(payload.straatnaam or "").strip() or None,
        huisnummer=(payload.huisnummer or "").strip() or None,
        postcode=(payload.postcode or "").strip().upper() or None,
        plaats=payload.plaats,
        eigenaar=(payload.eigenaar or "").strip() or None,
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
               after={"omschrijving": _omschrijving(b), "asset_id": b.asset_id,
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
    for k, v in velden.items():
        setattr(b, k, v)
    # De plek mag je bijwerken, maar niet weggommen.
    _eis_plek(b.gebouw_naam, b.straatnaam)
    if b.postcode:
        b.postcode = b.postcode.strip().upper()
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


@router.get("/{bouw_id}/export.pdf")
def export_pdf(
    bouw_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rapport van de gebouwinspectie.

    Opzet volgt de volgorde waarin een lezer het wil weten: eerst wat en waar,
    dan de conditiescores, dan de aandachtspunten per pijler, en tot slot de
    documenten die ontbraken. Die laatste lijst is bij een controle vaak het
    eerste dat gevraagd wordt, dus die staat er apart in plaats van verstopt
    tussen de antwoorden.
    """
    try:
        from fpdf import FPDF
    except ImportError:
        return StreamingResponse(
            iter([b"PDF-generator niet geinstalleerd: pip install fpdf2"]),
            status_code=500, media_type="text/plain",
        )

    b = _get_or_404(db, bouw_id, current_user)
    org = db.query(Organization).filter(
        Organization.id == current_user.organization_id).first()
    org_naam = org.name if org else "-"

    def safe(v) -> str:
        """fpdf2 schrijft latin-1; alles daarbuiten vervangen we netjes."""
        if v is None:
            return ""
        s = str(v)
        for k, r in (("\u20ac", "EUR "), ("\u2013", "-"), ("\u2014", "-"),
                     ("\u2022", "-"), ("\u2019", "'"), ("\u2018", "'"),
                     ("\u201c", '"'), ("\u201d", '"'), ("\u2026", "..."),
                     ("\u2192", "->"), ("\u00d7", "x"), ("\u00b7", "-")):
            s = s.replace(k, r)
        return s.encode("latin-1", "replace").decode("latin-1")

    def _hex_rgb(hexstr: str, default=(2, 132, 199)):
        try:
            h = (hexstr or "").lstrip("#")
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except Exception:  # noqa: BLE001
            return default

    BRAND = _hex_rgb(getattr(org, "brand_color", None) or "")

    class _BouwPDF(FPDF):
        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(120, 120, 120)
            self.set_x(15)
            self.cell(120, 5, safe(f"{org_naam} - gebouwinspectie (BOEI)"))
            self.cell(0, 5, f"Pagina {self.page_no()}/{{nb}}", align="R")
            self.set_text_color(0, 0, 0)

    pdf = _BouwPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.set_fill_color(*BRAND)
    pdf.rect(0, 0, 210, 34, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(15, 9)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 9, "Gebouwinspectie")
    pdf.set_xy(15, 19)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, safe(_omschrijving(b)))
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(42)

    def regel(label, waarde):
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_x(15)
        pdf.cell(42, 6, safe(label))
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, safe(waarde), new_x="LMARGIN", new_y="NEXT")

    def kop(tekst, ruimte=4):
        pdf.ln(ruimte)
        pdf.set_x(15)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 7, safe(tekst), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)

    adres = " ".join(x for x in ((b.straatnaam or ""), (b.huisnummer or "")) if x)
    regel("Gebouw", b.gebouw_naam or "-")
    regel("Adres", ", ".join(x for x in (adres, b.postcode or "", b.plaats or "") if x) or "-")
    regel("Eigenaar", b.eigenaar or "-")
    regel("Gebouwtype", bb.GEBOUW_TYPES.get(b.gebouw_type or "") or "-")
    regel("Bouwjaar", b.bouwjaar or "-")
    regel("Datum", b.datum.strftime("%d-%m-%Y") if b.datum else "-")
    regel("Inspecteur", b.inspecteur_naam or "-")
    regel("Status", "afgerond" if b.status == "afgerond" else "concept")
    regel("Vragenlijst", b.checklist_versie or "-")

    antwoorden = list(b.antwoorden or [])
    resultaat = bb.bereken_score(
        [{"code": a.question_code, "antwoord": a.antwoord} for a in antwoorden])

    # ── Samenvatting per pijler ─────────────────────────────────────
    gebruikt = {a.pijler for a in antwoorden if a.pijler}
    if gebruikt:
        kop("Samenvatting")
        for code in bb.PIJLERS:
            if code not in gebruikt:
                continue
            blok = resultaat["per_pijler"][code]
            score = ("-" if blok["score_pct"] is None
                     else f"{blok['score_pct']}% in orde")
            regel(f"{code} - {bb.PIJLERS[code]['naam']}",
                  f"{blok['beoordeeld']} beoordeeld, {blok['nee']} aandachtspunt(en), {score}")

    # ── Conditiemeting ──────────────────────────────────────────────
    condities = list(b.condities or [])
    if condities:
        kop("Conditiemeting (NEN 2767-1)")
        pdf.set_x(15)
        pdf.set_font("Helvetica", "B", 8)
        for breedte, titel in ((22, "Code"), (58, "Element"), (48, "Gebrek"),
                               (12, "E"), (12, "I"), (12, "O"), (16, "Conditie")):
            pdf.cell(breedte, 6, titel, border="B")
        pdf.ln(6)
        pdf.set_font("Helvetica", "", 8)
        for c in condities:
            pdf.set_x(15)
            pdf.cell(22, 5.5, safe(c.element_code))
            pdf.cell(58, 5.5, safe((c.element_naam_snapshot or "")[:34]))
            pdf.cell(48, 5.5, safe((c.gebrek or "-")[:28]))
            pdf.cell(12, 5.5, safe(c.ernst or "-"))
            pdf.cell(12, 5.5, safe(c.intensiteit or "-"))
            pdf.cell(12, 5.5, safe(c.omvang_klasse or "-"))
            label = scoring.CONDITIE_LABELS.get(c.conditie, "") if c.conditie else ""
            pdf.cell(16, 5.5, safe(f"{c.conditie or '-'} {label}"[:14]),
                     new_x="LMARGIN", new_y="NEXT")
        if b.conditie_hoogste:
            pdf.ln(1)
            pdf.set_x(15)
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(0, 6, safe(
                f"Hoogste conditiescore: {b.conditie_hoogste} "
                f"({scoring.CONDITIE_LABELS.get(b.conditie_hoogste, '')})"),
                new_x="LMARGIN", new_y="NEXT")

    # ── Aandachtspunten ─────────────────────────────────────────────
    punten = [a for a in antwoorden if a.antwoord == "nee"]
    kop("Aandachtspunten")
    if not punten:
        pdf.set_x(15)
        pdf.multi_cell(180, 5, safe("Geen. Alle beoordeelde vragen zijn in orde."))
    for a in punten:
        pdf.set_x(15)
        pdf.set_font("Helvetica", "B", 9)
        pdf.multi_cell(180, 5, safe(f"{a.question_code}  {a.question_text_snapshot or ''}"))
        pdf.set_font("Helvetica", "", 8.5)
        v = bb.vraag(a.question_code) or {}
        if v.get("norm_ref"):
            pdf.set_x(18)
            pdf.set_text_color(110, 110, 110)
            pdf.multi_cell(177, 4.5, safe(v["norm_ref"]))
            pdf.set_text_color(0, 0, 0)
        if a.toelichting:
            pdf.set_x(18)
            pdf.multi_cell(177, 4.5, safe(a.toelichting))
        if a.actie:
            pdf.set_x(18)
            pdf.multi_cell(177, 4.5, safe(
                "Actie: " + a.actie
                + (f" ({a.actiehouder_naam})" if a.actiehouder_naam else "")
                + (" - gereed" if a.actie_gereed else "")))
        pdf.ln(1)

    # ── Ontbrekende documenten ──────────────────────────────────────
    # Bij een controle is dit het eerste dat gevraagd wordt, dus apart.
    per_code = {a.question_code: a for a in antwoorden}
    ontbreekt = []
    for stuk in bb.bewijsstukken():
        a = per_code.get(stuk["code"])
        if a is None:
            continue
        if a.bewijs_aanwezig is False or (a.antwoord == "nee" and a.bewijs_aanwezig is None):
            ontbreekt.append((stuk["bewijs"], stuk["norm_ref"]))
    if ontbreekt:
        kop("Documenten die ontbreken of verlopen zijn")
        for naam, norm in ontbreekt:
            pdf.set_x(15)
            pdf.set_font("Helvetica", "", 9)
            pdf.multi_cell(180, 5, safe(f"- {naam}  ({norm})"))

    kop("Verantwoording", 6)
    pdf.set_x(15)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(110, 110, 110)
    pdf.multi_cell(180, 4.5, safe(
        "Opgesteld volgens de BOEI-methodiek: Brandveiligheid, Onderhoud, Energie "
        "en Inzicht in wet- en regelgeving. Conditiescores volgen NEN 2767-1; "
        "ernst, intensiteit en omvang zijn vastgelegd en de score is daaruit "
        f"berekend met {b.condities[0].scoring_versie if b.condities else scoring.SCORING_VERSION}. "
        "De vraagteksten zijn vastgelegd zoals ze bij deze opname luidden, ook als "
        "de vragenlijst later is gewijzigd."))
    pdf.set_text_color(0, 0, 0)

    b.pdf_generated_at = datetime.now(timezone.utc)
    db.commit()
    log_action(db, request, current_user, action="bouw.export.pdf",
               entity_type="bouw_inspectie", entity_id=b.id)

    inhoud = bytes(pdf.output())
    naam = f"gebouwinspectie-{(b.gebouw_naam or b.straatnaam or b.id)[:40]}.pdf"
    naam = "".join(c if c.isalnum() or c in "-_." else "-" for c in naam)
    return StreamingResponse(
        iter([inhoud]), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{naam}"'})
