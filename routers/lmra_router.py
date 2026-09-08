"""Endpoints voor de laatste-minuut risicoanalyse.

  GET  /api/lmra/checklist              De acht vragen
  GET  /api/lmra                        Lijst, met filters
  POST /api/lmra                        Invullen en meteen afronden
  GET  /api/lmra/{id}                   Detail met alle antwoorden
  POST /api/lmra/{id}/maatregel         Wat er na een stop is gedaan
  GET  /api/lmra/statistiek             Waar wordt het vaakst op gestopt

**Geen concept-status en geen PATCH.** Een LMRA legt de situatie van één
moment vast. Een half ingevulde LMRA die je later afmaakt beschrijft dat moment
niet meer, en een LMRA die je achteraf kunt bijwerken is als bewijsstuk
waardeloos. Hij komt er in één keer in en verandert daarna niet meer.

Wat er wél bij kan: de maatregel na een stop. Dat is geen wijziging van het
oordeel maar een aanvulling erop, en hij kan maar één keer.

**Iedereen mag er een doen.** Anders dan bij de toolbox is dit geen taak van de
uitvoerder maar van degene die zo dadelijk het gereedschap oppakt. Een LMRA die
je moet aanvragen is geen LMRA. Lezen mag iedereen binnen de organisatie;
verwijderen kan niemand.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import lmra as L
from audit import log_action
from auth import get_current_user
from database import get_db
from models import Asset, Lmra, LmraAntwoord, Melding, Project, User
from permissions import require_module

router = APIRouter(prefix="/api/lmra", tags=["Veiligheid"],
                   dependencies=[Depends(require_module("veiligheid"))])


# ---------------------------------------------------------------------------
# Schema's
# ---------------------------------------------------------------------------

class LmraIn(BaseModel):
    werkzaamheid: str = Field(..., min_length=2, max_length=255,
                              description="Wat ga je nu doen")
    antwoorden: dict = Field(..., description="{'LMRA.TAAK': 'ja', ...}")
    toelichtingen: Optional[dict] = Field(
        None, description="Per vraagcode een toelichting; verplicht bij 'nee'")

    locatie: Optional[str] = Field(None, max_length=255)
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    project_id: Optional[str] = None
    asset_id: Optional[str] = None
    melding_id: Optional[str] = None

    # Bij een hernieuwde beoordeling na een stop.
    vorige_lmra_id: Optional[str] = None


class MaatregelIn(BaseModel):
    maatregel: str = Field(..., min_length=3,
                           description="Wat is er gedaan om het op te lossen")


# ---------------------------------------------------------------------------
# Hulp
# ---------------------------------------------------------------------------

def _nu() -> datetime:
    return datetime.now(timezone.utc)


def _naam(user: User) -> str:
    return " ".join(p for p in (user.first_name, user.last_name) if p).strip() \
        or user.email


def _van_mijn_org(db: Session, model, ident: Optional[str], org_id: str,
                  label: str):
    """Een verwijzing mag alleen naar iets van de eigen organisatie wijzen."""
    if not ident:
        return None
    rij = db.query(model).filter(model.id == ident,
                                 model.organization_id == org_id).first()
    if rij is None:
        raise HTTPException(status_code=404, detail=f"{label} niet gevonden")
    return rij


def _als_dict(rij: Lmra, *, met_antwoorden: bool = False) -> dict:
    uit = {
        "id": rij.id,
        "werkzaamheid": rij.werkzaamheid,
        "locatie": rij.locatie,
        "latitude": rij.latitude,
        "longitude": rij.longitude,
        "uitvoerder": rij.uitvoerder_naam,
        "uitvoerder_id": rij.uitvoerder_id,
        "uitkomst": rij.uitkomst,
        "aantal_nee": rij.aantal_nee,
        "samenvatting": L.samenvatting(rij.uitkomst, rij.aantal_nee or 0),
        "mag_beginnen": rij.uitkomst == L.VEILIG,
        "checklist_versie": rij.checklist_versie,
        "project_id": rij.project_id,
        "asset_id": rij.asset_id,
        "melding_id": rij.melding_id,
        "maatregel": rij.maatregel,
        "maatregel_op": rij.maatregel_op.isoformat() if rij.maatregel_op else None,
        "vorige_lmra_id": rij.vorige_lmra_id,
        "created_at": rij.created_at.isoformat() if rij.created_at else None,
    }
    if met_antwoorden:
        uit["antwoorden"] = [{
            "code": a.question_code,
            "vraag": a.question_text_snapshot,
            "antwoord": a.antwoord,
            "toelichting": a.toelichting,
        } for a in rij.antwoorden]
    return uit


# ---------------------------------------------------------------------------
# Checklist
# ---------------------------------------------------------------------------

@router.get("/checklist")
def get_checklist(current_user: User = Depends(get_current_user)):
    """De vragen zoals het scherm ze toont."""
    return L.checklist()


# ---------------------------------------------------------------------------
# Statistiek -- vóór /{lmra_id}, anders vangt die route dit pad af
# ---------------------------------------------------------------------------

@router.get("/statistiek")
def statistiek(
    dagen: int = 90,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Waar wordt het vaakst op gestopt.

    Dit is de vraag waar een KAM-functionaris iets mee kan. Twintig LMRA's die
    allemaal op dezelfde vraag stranden, zeggen dat er iets structureel niet
    klopt in de voorbereiding -- en dat is precies wat een toolbox of een
    aanpassing in het werkproces kan oplossen.
    """
    dagen = max(1, min(dagen, 365))
    grens = _nu().replace(tzinfo=None) - timedelta(days=dagen)

    rijen = db.query(Lmra).filter(
        Lmra.organization_id == current_user.organization_id,
        Lmra.created_at >= grens,
    ).all()

    per_vraag: dict[str, int] = {c: 0 for c in L.CODES}
    for a in db.query(LmraAntwoord).filter(
            LmraAntwoord.organization_id == current_user.organization_id,
            LmraAntwoord.antwoord == L.NEE,
            LmraAntwoord.lmra_id.in_([r.id for r in rijen] or [""])).all():
        per_vraag[a.question_code] = per_vraag.get(a.question_code, 0) + 1

    gestopt = [r for r in rijen if r.uitkomst == L.GESTOPT]
    return {
        "periode_dagen": dagen,
        "totaal": len(rijen),
        "veilig": len(rijen) - len(gestopt),
        "gestopt": len(gestopt),
        "zonder_maatregel": sum(1 for r in gestopt if not r.maatregel),
        "per_vraag": [{
            "code": c,
            "vraag": L.VRAAG_PER_CODE[c]["vraag"],
            "aantal_nee": per_vraag.get(c, 0),
        } for c in sorted(L.CODES, key=lambda c: -per_vraag.get(c, 0))],
    }


# ---------------------------------------------------------------------------
# Lijst en aanmaken
# ---------------------------------------------------------------------------

@router.get("")
def lijst(
    project_id: Optional[str] = None,
    uitkomst: Optional[str] = None,
    alleen_eigen: bool = False,
    limiet: int = 100,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Lmra).filter(Lmra.organization_id == current_user.organization_id)
    if project_id:
        q = q.filter(Lmra.project_id == project_id)
    if uitkomst in (L.VEILIG, L.GESTOPT):
        q = q.filter(Lmra.uitkomst == uitkomst)
    if alleen_eigen:
        q = q.filter(Lmra.uitvoerder_id == current_user.id)
    rijen = q.order_by(Lmra.created_at.desc()).limit(max(1, min(limiet, 500))).all()
    return {"lmras": [_als_dict(r) for r in rijen], "totaal": len(rijen)}


@router.post("", status_code=201)
def maak(
    body: LmraIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Een LMRA invullen en meteen vastzetten.

    De uitkomst wordt hier niet meegegeven maar berekend. Zo kan een cliënt
    niet "veilig" opsturen bij een lijst met een nee erin -- en dat is de enige
    regel die dit instrument iets waard maakt.
    """
    org_id = current_user.organization_id

    try:
        oordeel = L.beoordeel(body.antwoorden, toelichtingen=body.toelichtingen)
    except L.OngeldigeLmra as fout:
        raise HTTPException(status_code=400, detail=str(fout))

    _van_mijn_org(db, Project, body.project_id, org_id, "Project")
    _van_mijn_org(db, Asset, body.asset_id, org_id, "Object")
    _van_mijn_org(db, Melding, body.melding_id, org_id, "Melding")

    vorige = None
    if body.vorige_lmra_id:
        vorige = db.query(Lmra).filter(
            Lmra.id == body.vorige_lmra_id,
            Lmra.organization_id == org_id).first()
        if vorige is None:
            raise HTTPException(status_code=404, detail="Vorige LMRA niet gevonden")
        if vorige.uitkomst != L.GESTOPT:
            raise HTTPException(
                status_code=400,
                detail="Een hernieuwde beoordeling hoort bij een gestopte LMRA")

    rij = Lmra(
        organization_id=org_id,
        project_id=body.project_id,
        asset_id=body.asset_id,
        melding_id=body.melding_id,
        uitvoerder_id=current_user.id,
        uitvoerder_naam=_naam(current_user),
        werkzaamheid=body.werkzaamheid.strip(),
        locatie=(body.locatie or "").strip() or None,
        latitude=body.latitude,
        longitude=body.longitude,
        checklist_versie=oordeel["versie"],
        uitkomst=oordeel["uitkomst"],
        aantal_nee=oordeel["aantal_nee"],
        vorige_lmra_id=vorige.id if vorige else None,
        created_by=current_user.id,
    )
    db.add(rij)
    db.flush()

    toelichtingen = {str(k).strip().upper(): (v or "").strip()
                     for k, v in (body.toelichtingen or {}).items()}
    for i, code in enumerate(L.CODES):
        db.add(LmraAntwoord(
            lmra_id=rij.id,
            organization_id=org_id,
            question_code=code,
            question_version=oordeel["versie"],
            question_text_snapshot=L.VRAAG_PER_CODE[code]["vraag"],
            antwoord=oordeel["antwoorden"][code],
            toelichting=toelichtingen.get(code) or None,
            order_index=i,
        ))

    log_action(db, request, current_user, action="lmra.create",
               entity_type="lmra", entity_id=rij.id,
               extra={"uitkomst": rij.uitkomst, "aantal_nee": rij.aantal_nee,
                      "werkzaamheid": rij.werkzaamheid}, commit=False)
    db.commit()
    db.refresh(rij)

    uit = _als_dict(rij, met_antwoorden=True)
    uit["blokkades"] = oordeel["blokkades"]
    return uit


@router.get("/{lmra_id}")
def detail(
    lmra_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rij = db.query(Lmra).filter(
        Lmra.id == lmra_id,
        Lmra.organization_id == current_user.organization_id).first()
    if rij is None:
        raise HTTPException(status_code=404, detail="LMRA niet gevonden")
    return _als_dict(rij, met_antwoorden=True)


@router.post("/{lmra_id}/maatregel")
def maatregel(
    lmra_id: str,
    body: MaatregelIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Vastleggen wat er na een stop is gedaan.

    Verandert het oordeel niet. De LMRA blijft gestopt; wie daarna wil
    beginnen, doet een nieuwe met ``vorige_lmra_id`` erbij. Zo laat het
    dossier zien dat er tussen die twee momenten iets is opgelost, in plaats
    van dat een stop achteraf verdampt.
    """
    rij = db.query(Lmra).filter(
        Lmra.id == lmra_id,
        Lmra.organization_id == current_user.organization_id).first()
    if rij is None:
        raise HTTPException(status_code=404, detail="LMRA niet gevonden")
    if rij.uitkomst != L.GESTOPT:
        raise HTTPException(status_code=400,
                            detail="Deze LMRA is niet gestopt; er valt niets op te lossen")
    if rij.maatregel:
        raise HTTPException(status_code=409,
                            detail="Er staat al een maatregel bij deze LMRA")

    rij.maatregel = body.maatregel.strip()
    rij.maatregel_op = _nu().replace(tzinfo=None)
    rij.maatregel_door_id = current_user.id

    log_action(db, request, current_user, action="lmra.maatregel",
               entity_type="lmra", entity_id=rij.id,
               extra={"maatregel": rij.maatregel}, commit=False)
    db.commit()
    db.refresh(rij)
    return _als_dict(rij, met_antwoorden=True)
