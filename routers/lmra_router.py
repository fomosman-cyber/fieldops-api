"""LMRA-router — de Laatste Minuut Risico Analyse vlak voor de start van een taak.

Endpoints:
  GET    /api/lmra/checklist               De vragenlijst zelf
  GET    /api/lmra/                        Lijst van uitgevoerde LMRA's
  GET    /api/lmra/statistiek              Tellingen voor de VCA-verantwoording
  POST   /api/lmra/                        Start een LMRA (vult alle vragen voor)
  GET    /api/lmra/{id}                    Detail met alle antwoorden
  PATCH  /api/lmra/{id}                    Kop bijwerken (taak, locatie)
  PATCH  /api/lmra/{id}/antwoorden/{aid}   Een vraag beantwoorden
  POST   /api/lmra/{id}/afronden           Afsluiten met een oordeel
  DELETE /api/lmra/{id}                    Verwijderen

Rollen wijken bewust af van de rest van Veiligheid. Een toolbox en een
werkplekinspectie zijn het werk van de uitvoerder of KAM; een LMRA doet degene
die zo begint, over zijn eigen taak. Iedereen binnen de organisatie mag er dus
een starten en invullen -- moeten wachten op een leidinggevende is precies het
gedrag dat je hier niet wilt. Wijzigen mag alleen door wie hem gestart is (of
een beheerder), want het is zijn eigen verantwoording. Verwijderen is voor
beheer: een LMRA die je niet meer bevalt hoort niet zomaar te verdwijnen.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import lmra_checklist as lc
from audit import log_action
from auth import get_current_user
from database import get_db
from models import Lmra, LmraAntwoord, Melding, Project, User
from permissions import can_manage_toolbox, require_module

router = APIRouter(prefix="/api/lmra", tags=["Veiligheid"],
                   dependencies=[Depends(require_module("veiligheid"))])


# ── Pydantic-schemas ─────────────────────────────────────────────────

class LmraIn(BaseModel):
    project_id: Optional[str] = None
    melding_id: Optional[str] = None
    taak: Optional[str] = None
    locatie: Optional[str] = None
    datum: Optional[datetime] = None


class LmraUpdate(BaseModel):
    taak: Optional[str] = None
    locatie: Optional[str] = None
    datum: Optional[datetime] = None
    project_id: Optional[str] = None
    melding_id: Optional[str] = None


class AntwoordIn(BaseModel):
    antwoord: Optional[str] = Field(default=None, pattern="^(ja|nee|nvt)$")
    toelichting: Optional[str] = None
    photo_url: Optional[str] = None
    maatregel: Optional[str] = None


class AfrondenIn(BaseModel):
    oordeel: str = Field(..., pattern="^(veilig|niet_starten)$")
    maatregelen: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────

def _antwoord_to_dict(a: LmraAntwoord) -> dict:
    return {
        "id": a.id,
        "question_code": a.question_code,
        "vraag": a.question_text_snapshot,
        "categorie": a.categorie,
        "antwoord": a.antwoord,
        "toelichting": a.toelichting,
        "photo_url": a.photo_url,
        "maatregel": a.maatregel,
        "order_index": a.order_index,
    }


def _lmra_to_dict(l: Lmra, *, include_antwoorden: bool = False) -> dict:
    out = {
        "id": l.id,
        "project_id": l.project_id,
        "project_naam": l.project.name if l.project else None,
        "melding_id": l.melding_id,
        "datum": l.datum.isoformat() if l.datum else None,
        "locatie": l.locatie,
        "taak": l.taak,
        "uitvoerder_id": l.uitvoerder_id,
        "uitvoerder_naam": l.uitvoerder_naam,
        "status": l.status,
        "oordeel": l.oordeel,
        "oordeel_label": lc.OORDELEN.get(l.oordeel or "", None),
        "checklist_versie": l.checklist_versie,
        "aantal_niet_in_orde": l.aantal_niet_in_orde,
        "maatregelen": l.maatregelen,
        "afgerond_op": l.afgerond_op.isoformat() if l.afgerond_op else None,
        "created_at": l.created_at.isoformat() if l.created_at else None,
    }
    if include_antwoorden:
        out["antwoorden"] = [_antwoord_to_dict(a) for a in l.antwoorden]
    return out


def _get_lmra_or_404(db: Session, lmra_id: str, current_user: User) -> Lmra:
    l = (db.query(Lmra)
           .filter(Lmra.id == lmra_id,
                   Lmra.organization_id == current_user.organization_id)
           .first())
    if not l:
        raise HTTPException(status_code=404, detail="LMRA niet gevonden")
    return l


def _eis_eigenaar(l: Lmra, current_user: User) -> None:
    """Alleen wie hem gestart is mag hem invullen — of een beheerder.

    Een LMRA is een persoonlijke verantwoording. Iemand anders die er
    antwoorden in zet maakt er een papieren werkelijkheid van.
    """
    if l.created_by == current_user.id or can_manage_toolbox(current_user):
        return
    raise HTTPException(
        status_code=403,
        detail="Alleen wie deze LMRA gestart is kan hem invullen")


def _eis_niet_afgerond(l: Lmra) -> None:
    if l.status != "concept":
        raise HTTPException(status_code=409,
                            detail="Deze LMRA is al afgesloten en kan niet meer wijzigen")


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/checklist")
def get_checklist(current_user: User = Depends(get_current_user)):
    """De vragenlijst. Los opvraagbaar zodat de app hem kan tonen voordat
    iemand een LMRA start — je wilt kunnen zien wat je te wachten staat."""
    return lc.checklist()


@router.get("/")
def list_lmra(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    alleen_eigen: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Lmra).filter(Lmra.organization_id == current_user.organization_id)
    if project_id:
        q = q.filter(Lmra.project_id == project_id)
    if status:
        q = q.filter(Lmra.status == status)
    if alleen_eigen:
        q = q.filter(Lmra.created_by == current_user.id)
    items = q.order_by(Lmra.created_at.desc()).limit(500).all()
    return [_lmra_to_dict(l) for l in items]


@router.get("/statistiek")
def statistiek(
    project_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Tellingen voor de VCA-verantwoording.

    Het getal waar het om draait is niet "hoeveel LMRA's", maar hoe vaak er
    niet gestart is. Een organisatie die honderden LMRA's doet en nooit stopt,
    vinkt af in plaats van te kijken.
    """
    q = db.query(Lmra).filter(Lmra.organization_id == current_user.organization_id)
    if project_id:
        q = q.filter(Lmra.project_id == project_id)
    alle = q.all()
    afgerond = [l for l in alle if l.status != "concept"]
    niet_gestart = [l for l in alle if l.oordeel == "niet_starten"]
    return {
        "totaal": len(alle),
        "concept": len(alle) - len(afgerond),
        "afgerond": len(afgerond),
        "veilig": sum(1 for l in alle if l.oordeel == "veilig"),
        "niet_gestart": len(niet_gestart),
        "met_aandachtspunt": sum(1 for l in afgerond if (l.aantal_niet_in_orde or 0) > 0),
        "checklist_versie": lc.LMRA_VERSION,
    }


@router.post("/")
def create_lmra(
    payload: LmraIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start een LMRA. Alle vragen worden meteen aangemaakt.

    Project en melding zijn allebei optioneel: een LMRA bij een storing in de
    berm hoort ook vastgelegd te worden, en die heeft nog geen projectnummer.
    """
    project = None
    if payload.project_id:
        project = (db.query(Project)
                     .filter(Project.id == payload.project_id,
                             Project.organization_id == current_user.organization_id)
                     .first())
        if not project:
            raise HTTPException(status_code=404, detail="Project niet gevonden")
    if payload.melding_id:
        melding = (db.query(Melding)
                     .filter(Melding.id == payload.melding_id,
                             Melding.organization_id == current_user.organization_id)
                     .first())
        if not melding:
            raise HTTPException(status_code=404, detail="Melding niet gevonden")

    naam = " ".join(x for x in (current_user.first_name, current_user.last_name) if x).strip()
    l = Lmra(
        organization_id=current_user.organization_id,
        project_id=project.id if project else None,
        melding_id=payload.melding_id,
        datum=payload.datum or datetime.now(timezone.utc),
        locatie=payload.locatie,
        taak=payload.taak,
        uitvoerder_id=current_user.id,
        uitvoerder_naam=naam or current_user.email,
        status="concept",
        checklist_versie=lc.LMRA_VERSION,
        created_by=current_user.id,
    )
    db.add(l)
    db.flush()

    for i, v in enumerate(lc.VRAGEN):
        db.add(LmraAntwoord(
            lmra_id=l.id,
            organization_id=current_user.organization_id,
            question_code=v["code"],
            question_version=lc.LMRA_VERSION,
            question_text_snapshot=v["vraag"][:500],
            categorie=v["categorie"],
            order_index=i,
        ))

    db.commit()
    db.refresh(l)
    log_action(db, request, current_user, action="lmra.create",
               entity_type="lmra", entity_id=l.id,
               after={"project_id": l.project_id, "vragen": len(lc.VRAGEN)})
    return _lmra_to_dict(l, include_antwoorden=True)


@router.get("/{lmra_id}")
def get_lmra(
    lmra_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _lmra_to_dict(_get_lmra_or_404(db, lmra_id, current_user),
                         include_antwoorden=True)


@router.patch("/{lmra_id}")
def update_lmra(
    lmra_id: str,
    payload: LmraUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    l = _get_lmra_or_404(db, lmra_id, current_user)
    _eis_eigenaar(l, current_user)
    _eis_niet_afgerond(l)
    velden = payload.model_dump(exclude_unset=True)
    if velden.get("project_id"):
        if not (db.query(Project)
                  .filter(Project.id == velden["project_id"],
                          Project.organization_id == current_user.organization_id)
                  .first()):
            raise HTTPException(status_code=404, detail="Project niet gevonden")
    for veld, waarde in velden.items():
        setattr(l, veld, waarde)
    db.commit()
    db.refresh(l)
    return _lmra_to_dict(l, include_antwoorden=True)


@router.patch("/{lmra_id}/antwoorden/{antwoord_id}")
def beantwoord(
    lmra_id: str,
    antwoord_id: str,
    payload: AntwoordIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    l = _get_lmra_or_404(db, lmra_id, current_user)
    _eis_eigenaar(l, current_user)
    _eis_niet_afgerond(l)
    a = (db.query(LmraAntwoord)
           .filter(LmraAntwoord.id == antwoord_id, LmraAntwoord.lmra_id == l.id)
           .first())
    if not a:
        raise HTTPException(status_code=404, detail="Vraag niet gevonden")
    for veld, waarde in payload.model_dump(exclude_unset=True).items():
        setattr(a, veld, waarde)
    db.commit()
    db.refresh(a)
    return _antwoord_to_dict(a)


@router.post("/{lmra_id}/afronden")
def afronden(
    lmra_id: str,
    payload: AfrondenIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sluit de LMRA af met een oordeel: veilig om te starten, of niet starten.

    Twee dingen worden hier bewust afgedwongen. Alle vragen moeten beantwoord
    zijn -- een half ingevulde LMRA is geen LMRA. En bij "veilig om te starten"
    terwijl er een NEE staat, moet er staan wat je eraan gedaan hebt: dat is de
    hele bedoeling van dit formulier. Wie niet start hoeft dat niet in te
    vullen, maar mag het wel.
    """
    l = _get_lmra_or_404(db, lmra_id, current_user)
    _eis_eigenaar(l, current_user)
    _eis_niet_afgerond(l)

    onbeantwoord = [a for a in l.antwoorden if not a.antwoord]
    if onbeantwoord:
        raise HTTPException(
            status_code=400,
            detail=f"Nog {len(onbeantwoord)} van de {len(l.antwoorden)} vragen "
                   "niet beantwoord")

    nee = [a for a in l.antwoorden if a.antwoord == "nee"]
    if payload.oordeel == "veilig" and nee:
        zonder_maatregel = [a for a in nee if not (a.maatregel or "").strip()]
        if zonder_maatregel and not (payload.maatregelen or "").strip():
            raise HTTPException(
                status_code=400,
                detail="Er staat een 'nee' zonder maatregel. Schrijf op wat je "
                       "eraan gedaan hebt, of sluit af met 'niet starten'.")

    l.oordeel = payload.oordeel
    l.status = "veilig" if payload.oordeel == "veilig" else "niet_gestart"
    l.aantal_niet_in_orde = len(nee)
    if payload.maatregelen is not None:
        l.maatregelen = payload.maatregelen
    l.afgerond_op = datetime.now(timezone.utc)
    db.commit()
    db.refresh(l)

    log_action(db, request, current_user, action="lmra.afronden",
               entity_type="lmra", entity_id=l.id,
               after={"oordeel": l.oordeel, "aantal_niet_in_orde": l.aantal_niet_in_orde})
    return _lmra_to_dict(l, include_antwoorden=True)


@router.delete("/{lmra_id}")
def delete_lmra(
    lmra_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verwijderen is voor beheer. Een LMRA die iemand niet bevalt hoort niet
    zomaar te verdwijnen — juist een 'niet starten' is het bewijs dat het
    systeem werkt."""
    if not can_manage_toolbox(current_user):
        raise HTTPException(status_code=403,
                            detail="Alleen een beheerder of manager kan een LMRA verwijderen")
    l = _get_lmra_or_404(db, lmra_id, current_user)
    snapshot = {"taak": l.taak, "oordeel": l.oordeel, "status": l.status}
    db.delete(l)
    db.commit()
    log_action(db, request, current_user, action="lmra.delete",
               entity_type="lmra", entity_id=lmra_id, before=snapshot)
    return {"message": "LMRA verwijderd", "deleted": True}
