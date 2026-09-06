"""Incidenten-router — ongeval, bijna-ongeval en gevaarlijke situatie.

Endpoints:
  GET    /api/incidenten/              Lijst (eigen meldingen, of alles voor beheer)
  POST   /api/incidenten/              Melden -- mag iedereen
  GET    /api/incidenten/{id}          Detail
  PATCH  /api/incidenten/{id}          Onderzoek bijwerken (beheer)
  POST   /api/incidenten/{id}/afhandelen  Afronden (beheer)
  DELETE /api/incidenten/{id}          Verwijderen (beheer)

Twee dingen sturen het rechtenmodel hier, en ze wijzen verschillende kanten op.

Melden moet zo laagdrempelig mogelijk: iedereen mag het, ook een viewer. Een
bijna-ongeval wordt alleen gemeld als het makkelijk gaat en niemand bang hoeft
te zijn dat het tegen hem gebruikt wordt. Een drempel opwerpen betekent geen
meldingen, en dan is de registratie waardeloos.

Inzage moet juist beperkt: hier staan gezondheidsgegevens van met naam genoemde
mensen in. Wie geen beheerder of manager is, ziet alleen zijn eigen meldingen.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from audit import log_action
from auth import get_current_user
from database import get_db
from models import Asset, Incident, Project, User
from permissions import can_manage_toolbox, require_module

router = APIRouter(prefix="/api/incidenten", tags=["Veiligheid"],
                   dependencies=[Depends(require_module("veiligheid"))])

SOORTEN = ("ongeval", "bijna_ongeval", "gevaarlijke_situatie")
LETSEL = ("geen", "ehbo", "behandeling", "ziekenhuis", "dodelijk")

# Bij deze uitkomsten moet een werkgever het ongeval melden bij de Nederlandse
# Arbeidsinspectie. Wij beslissen dat niet voor de klant -- we wijzen erop.
INSPECTIE_LETSEL = ("ziekenhuis", "dodelijk")


# ── Pydantic-schemas ─────────────────────────────────────────────────

class IncidentIn(BaseModel):
    soort: str = Field(..., pattern="^(ongeval|bijna_ongeval|gevaarlijke_situatie)$")
    omschrijving: str = Field(..., min_length=1)
    gebeurd_op: Optional[datetime] = None
    project_id: Optional[str] = None
    asset_id: Optional[str] = None
    locatie: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    direct_genomen: Optional[str] = None
    letsel: Optional[str] = Field(default=None, pattern="^(geen|ehbo|behandeling|ziekenhuis|dodelijk)$")
    verzuim: bool = False
    betrokkene_naam: Optional[str] = None
    betrokkene_user_id: Optional[str] = None
    photo_url: Optional[str] = None
    photo_2_url: Optional[str] = None


class IncidentUpdate(BaseModel):
    omschrijving: Optional[str] = None
    gebeurd_op: Optional[datetime] = None
    locatie: Optional[str] = None
    direct_genomen: Optional[str] = None
    oorzaak: Optional[str] = None
    vervolgmaatregelen: Optional[str] = None
    letsel: Optional[str] = Field(default=None, pattern="^(geen|ehbo|behandeling|ziekenhuis|dodelijk)$")
    verzuim: Optional[bool] = None
    gemeld_bij_inspectie: Optional[bool] = None
    status: Optional[str] = Field(default=None, pattern="^(gemeld|in_onderzoek|afgehandeld)$")


class AfhandelenIn(BaseModel):
    oorzaak: Optional[str] = None
    vervolgmaatregelen: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────

def _mag_beheren(user: User) -> bool:
    """Beheer van incidenten volgt dezelfde rollen als de toolbox: admin en
    manager, plus de org-admin. Melden staat daar los van en mag iedereen."""
    return can_manage_toolbox(user)


def _incident_to_dict(i: Incident, *, volledig: bool) -> dict:
    """`volledig=False` laat de persoonsgegevens weg.

    Zo kan een lijstweergave voor iemand zonder beheerrecht wel het aantal en
    de aard tonen zonder te verklappen wie er gewond raakte.
    """
    uit = {
        "id": i.id,
        "soort": i.soort,
        "status": i.status,
        "gebeurd_op": i.gebeurd_op.isoformat() if i.gebeurd_op else None,
        "locatie": i.locatie,
        "lat": i.lat,
        "lng": i.lng,
        "omschrijving": i.omschrijving,
        "direct_genomen": i.direct_genomen,
        "project_id": i.project_id,
        "project_name": i.project.name if i.project else None,
        "asset_id": i.asset_id,
        "photo_url": i.photo_url,
        "photo_2_url": i.photo_2_url,
        "created_by": i.created_by,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "updated_at": i.updated_at.isoformat() if i.updated_at else None,
    }
    if volledig:
        uit.update({
            "letsel": i.letsel,
            "verzuim": bool(i.verzuim),
            "betrokkene_naam": i.betrokkene_naam,
            "betrokkene_user_id": i.betrokkene_user_id,
            "gemeld_bij_inspectie": bool(i.gemeld_bij_inspectie),
            "oorzaak": i.oorzaak,
            "vervolgmaatregelen": i.vervolgmaatregelen,
            "afgehandeld_op": i.afgehandeld_op.isoformat() if i.afgehandeld_op else None,
            "afgehandeld_door": i.afgehandeld_door,
            "inspectie_verplicht": i.letsel in INSPECTIE_LETSEL,
        })
    return uit


def _get_incident_or_404(db: Session, incident_id: str, current_user: User) -> Incident:
    i = (db.query(Incident)
           .filter(Incident.id == incident_id,
                   Incident.organization_id == current_user.organization_id)
           .first())
    if not i:
        raise HTTPException(status_code=404, detail="Incident niet gevonden")
    # Zonder beheerrecht alleen je eigen melding -- 404 en niet 403, zodat je
    # niet kunt aftasten of een bepaald incident bestaat.
    if not _mag_beheren(current_user) and i.created_by != current_user.id:
        raise HTTPException(status_code=404, detail="Incident niet gevonden")
    return i


def _controleer_koppeling(db: Session, model, obj_id: Optional[str],
                          current_user: User, naam: str):
    """Een gekoppeld project of asset moet van de eigen organisatie zijn.

    Zonder deze check kun je met een gegokt id een incident aan de data van een
    andere klant hangen.
    """
    if not obj_id:
        return None
    obj = (db.query(model)
             .filter(model.id == obj_id,
                     model.organization_id == current_user.organization_id)
             .first())
    if not obj:
        raise HTTPException(status_code=404, detail=f"{naam} niet gevonden")
    return obj


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/")
def list_incidenten(
    soort: Optional[str] = None,
    status: Optional[str] = None,
    project_id: Optional[str] = None,
    alleen_eigen: bool = Query(False, description="Ook als beheerder alleen je eigen meldingen"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    beheer = _mag_beheren(current_user)
    q = db.query(Incident).filter(Incident.organization_id == current_user.organization_id)
    if not beheer or alleen_eigen:
        q = q.filter(Incident.created_by == current_user.id)
    if soort:
        q = q.filter(Incident.soort == soort)
    if status:
        q = q.filter(Incident.status == status)
    if project_id:
        q = q.filter(Incident.project_id == project_id)

    items = q.order_by(Incident.created_at.desc()).limit(500).all()
    # De query hierboven beperkt een niet-beheerder al tot zijn eigen meldingen,
    # dus wat hij terugkrijgt zijn zijn eigen gegevens en die mag hij volledig
    # zien. Voor cijfers zonder persoonsgegevens is er /statistiek/samenvatting.
    return [_incident_to_dict(i, volledig=True) for i in items]


@router.post("/")
def meld_incident(
    payload: IncidentIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Melden mag iedereen -- bewust geen rolcheck. Zie de moduledocstring."""
    _controleer_koppeling(db, Project, payload.project_id, current_user, "Project")
    _controleer_koppeling(db, Asset, payload.asset_id, current_user, "Asset")

    betrokkene_id = None
    if payload.betrokkene_user_id:
        u = (db.query(User)
               .filter(User.id == payload.betrokkene_user_id,
                       User.organization_id == current_user.organization_id)
               .first())
        if not u:
            raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")
        betrokkene_id = u.id

    i = Incident(
        organization_id=current_user.organization_id,
        project_id=payload.project_id,
        asset_id=payload.asset_id,
        soort=payload.soort,
        gebeurd_op=payload.gebeurd_op or datetime.now(timezone.utc),
        locatie=payload.locatie,
        lat=payload.lat,
        lng=payload.lng,
        omschrijving=payload.omschrijving,
        direct_genomen=payload.direct_genomen,
        letsel=payload.letsel,
        verzuim=payload.verzuim,
        betrokkene_naam=(payload.betrokkene_naam or "").strip() or None,
        betrokkene_user_id=betrokkene_id,
        photo_url=payload.photo_url,
        photo_2_url=payload.photo_2_url,
        status="gemeld",
        created_by=current_user.id,
    )
    db.add(i)
    db.commit()
    db.refresh(i)

    # Geen letsel- of persoonsgegevens in de audit-log: die is breder inzichtelijk
    # dan het incident zelf.
    log_action(db, request, current_user, action="incident.meld",
               entity_type="incident", entity_id=i.id,
               after={"soort": i.soort, "project_id": i.project_id})

    uit = _incident_to_dict(i, volledig=True)
    if i.letsel in INSPECTIE_LETSEL:
        uit["waarschuwing"] = ("Bij ziekenhuisopname, blijvend letsel of overlijden moet dit "
                               "ongeval direct worden gemeld bij de Nederlandse Arbeidsinspectie.")
    return uit


@router.get("/{incident_id}")
def get_incident(
    incident_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    i = _get_incident_or_404(db, incident_id, current_user)
    # Je eigen melding zie je volledig; dat zijn je eigen gegevens.
    return _incident_to_dict(i, volledig=True)


@router.patch("/{incident_id}")
def update_incident(
    incident_id: str,
    payload: IncidentUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _mag_beheren(current_user):
        raise HTTPException(status_code=403,
                            detail="Alleen een beheerder of manager kan een incident bijwerken")
    i = _get_incident_or_404(db, incident_id, current_user)
    if i.status == "afgehandeld":
        raise HTTPException(status_code=409,
                            detail="Dit incident is afgehandeld en kan niet meer worden gewijzigd")

    for veld, waarde in payload.model_dump(exclude_unset=True).items():
        setattr(i, veld, waarde)
    db.commit()
    db.refresh(i)

    log_action(db, request, current_user, action="incident.update",
               entity_type="incident", entity_id=i.id, after={"status": i.status})
    return _incident_to_dict(i, volledig=True)


@router.post("/{incident_id}/afhandelen")
def afhandelen(
    incident_id: str,
    payload: AfhandelenIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Afronden: oorzaak en maatregelen vastleggen en dichtzetten.

    Een incident zonder vastgelegde maatregel is voor een auditor hetzelfde als
    een incident dat niet is opgepakt, dus dat weigeren we.
    """
    if not _mag_beheren(current_user):
        raise HTTPException(status_code=403,
                            detail="Alleen een beheerder of manager kan een incident afhandelen")
    i = _get_incident_or_404(db, incident_id, current_user)
    if i.status == "afgehandeld":
        raise HTTPException(status_code=409, detail="Dit incident is al afgehandeld")

    if payload.oorzaak is not None:
        i.oorzaak = payload.oorzaak
    if payload.vervolgmaatregelen is not None:
        i.vervolgmaatregelen = payload.vervolgmaatregelen

    if not (i.vervolgmaatregelen or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Leg eerst vast welke maatregel is genomen -- zonder maatregel is een "
                   "incident niet afgehandeld")

    i.status = "afgehandeld"
    i.afgehandeld_op = datetime.now(timezone.utc)
    i.afgehandeld_door = current_user.id
    db.commit()
    db.refresh(i)

    log_action(db, request, current_user, action="incident.afhandelen",
               entity_type="incident", entity_id=i.id,
               after={"soort": i.soort})
    return _incident_to_dict(i, volledig=True)


@router.delete("/{incident_id}")
def delete_incident(
    incident_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _mag_beheren(current_user):
        raise HTTPException(status_code=403,
                            detail="Alleen een beheerder of manager kan een incident verwijderen")
    i = _get_incident_or_404(db, incident_id, current_user)
    soort = i.soort
    db.delete(i)
    db.commit()
    log_action(db, request, current_user, action="incident.delete",
               entity_type="incident", entity_id=incident_id, before={"soort": soort})
    return {"ok": True}


@router.get("/statistiek/samenvatting")
def statistiek(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aantallen per soort en status, voor het overzicht.

    Alleen tellingen -- geen persoonsgegevens -- dus dit mag iedereen zien.
    Een ploeg die ziet dat er dit jaar veertig bijna-ongevallen zijn gemeld,
    meldt zelf ook eerder.
    """
    items = (db.query(Incident)
               .filter(Incident.organization_id == current_user.organization_id)
               .all())
    per_soort = {s: 0 for s in SOORTEN}
    per_status = {"gemeld": 0, "in_onderzoek": 0, "afgehandeld": 0}
    for i in items:
        if i.soort in per_soort:
            per_soort[i.soort] += 1
        if i.status in per_status:
            per_status[i.status] += 1
    return {"totaal": len(items), "per_soort": per_soort, "per_status": per_status}
