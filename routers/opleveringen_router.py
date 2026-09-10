"""Oplevering-router — proces-verbalen van uitgevoerd werk.

Endpoints:
  GET    /api/opleveringen                 Lijst van opleveringen (mijn org)
  POST   /api/opleveringen                 Nieuwe oplevering (concept)
  GET    /api/opleveringen/{id}            Detail + alle punten
  PATCH  /api/opleveringen/{id}            Update header (status, datum, etc.)
  DELETE /api/opleveringen/{id}            Verwijder (cascade punten)
  POST   /api/opleveringen/{id}/punten     Voeg punt toe
  PATCH  /api/opleveringen/{id}/punten/{punt_id}   Wijzig punt
  DELETE /api/opleveringen/{id}/punten/{punt_id}   Verwijder punt
"""
from typing import Optional, List
from datetime import datetime, timezone
import base64
import re
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
import oplever_vision as ov
from models import (User, Oplevering, OpleveringPunt, Asset,
                    OpleverRonde)
from auth import get_current_user
from permissions import require_module
from audit import log_action
from email_service import send_oplevering_email


def _collect_recipients(o: Oplevering) -> list[str]:
    """Stuur naar opdrachtgever + aannemer als e-mail is ingevuld."""
    out = []
    if o.opdrachtgever_email:
        out.append(o.opdrachtgever_email)
    if o.aannemer_email and o.aannemer_email not in out:
        out.append(o.aannemer_email)
    return out

router = APIRouter(prefix="/api/opleveringen", tags=["Opleveringen"],
                   dependencies=[Depends(require_module("opleveren"))])


# ── Pydantic-schemas ─────────────────────────────────────────────────

class OpleveringPuntIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    omschrijving: str = Field(..., min_length=1)
    uitvoeringsmethode: Optional[str] = None
    photo_url: Optional[str] = None           # foto voor uitvoering
    photo_url_after: Optional[str] = None     # foto na uitvoering
    asset_id: Optional[str] = None
    order_index: int = 0
    status: str = Field(default="gereed", pattern="^(gereed|restpunt|actiepunt|afgekeurd)$")


class OpleveringPuntUpdate(BaseModel):
    code: Optional[str] = None
    omschrijving: Optional[str] = None
    uitvoeringsmethode: Optional[str] = None
    photo_url: Optional[str] = None
    photo_url_after: Optional[str] = None
    asset_id: Optional[str] = None
    order_index: Optional[int] = None
    status: Optional[str] = Field(default=None, pattern="^(gereed|restpunt|actiepunt|afgekeurd)$")


class OpleveringIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    project_id: Optional[str] = None
    locatie: Optional[str] = None
    datum_oplevering: Optional[datetime] = None
    opdrachtgever_naam: Optional[str] = None
    opdrachtgever_email: Optional[str] = None
    aannemer_naam: Optional[str] = None
    aannemer_email: Optional[str] = None
    extra_questions: Optional[dict] = None
    notes: Optional[str] = None
    status: str = Field(default="concept", pattern="^(concept|opgeleverd|aanvaard|afgewezen)$")


class OpleveringUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    project_id: Optional[str] = None
    locatie: Optional[str] = None
    datum_oplevering: Optional[datetime] = None
    opdrachtgever_naam: Optional[str] = None
    opdrachtgever_email: Optional[str] = None
    aannemer_naam: Optional[str] = None
    aannemer_email: Optional[str] = None
    extra_questions: Optional[dict] = None
    notes: Optional[str] = None
    status: Optional[str] = Field(default=None, pattern="^(concept|opgeleverd|aanvaard|afgewezen)$")


# ── Helpers ──────────────────────────────────────────────────────────

def _punt_to_dict(p: OpleveringPunt) -> dict:
    return {
        "id": p.id,
        "code": p.code,
        "omschrijving": p.omschrijving,
        "uitvoeringsmethode": p.uitvoeringsmethode,
        "photo_url": p.photo_url,
        "photo_url_after": p.photo_url_after,
        "asset_id": p.asset_id,
        "order_index": p.order_index,
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _bevestigde_punten(o: Oplevering) -> list:
    """De punten die meetellen: alles behalve wat nog een voorstel is.

    Een voorstel is door de camera gezien maar nog niet door een mens. Het
    telt niet mee in de restpuntenlijst, niet in de teller, niet in het PV en
    niet in de mail naar de aannemer -- pas als iemand het bevestigt. Zonder
    deze filter zou het scherm de belofte "niets gaat automatisch de lijst in"
    breken op elke plek waar de oplevering wordt getoond.
    """
    return [p for p in (o.punten or []) if p.status != "voorgesteld"]


def _oplevering_to_dict(o: Oplevering, *, include_punten: bool = False) -> dict:
    extra = None
    if o.extra_questions_json:
        try:
            extra = json.loads(o.extra_questions_json)
        except (json.JSONDecodeError, TypeError):
            extra = None
    out = {
        "id": o.id,
        "title": o.title,
        "description": o.description,
        "project_id": o.project_id,
        "project_name": o.project.name if o.project else None,
        "locatie": o.locatie,
        "datum_oplevering": o.datum_oplevering.isoformat() if o.datum_oplevering else None,
        "opdrachtgever_naam": o.opdrachtgever_naam,
        "opdrachtgever_email": o.opdrachtgever_email,
        "aannemer_naam": o.aannemer_naam,
        "aannemer_email": o.aannemer_email,
        "extra_questions": extra,
        "notes": o.notes,
        "status": o.status,
        "signed_off_at": o.signed_off_at.isoformat() if o.signed_off_at else None,
        "punten_count": len(_bevestigde_punten(o)),
        "created_by": o.created_by,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "updated_at": o.updated_at.isoformat() if o.updated_at else None,
    }
    if include_punten:
        out["punten"] = [_punt_to_dict(p) for p in _bevestigde_punten(o)]
    return out


def _get_oplevering_or_404(db: Session, oplevering_id: str, current_user: User) -> Oplevering:
    o = (db.query(Oplevering)
            .filter(Oplevering.id == oplevering_id,
                    Oplevering.organization_id == current_user.organization_id)
            .first())
    if not o:
        raise HTTPException(status_code=404, detail="Oplevering niet gevonden")
    return o


# ── Endpoints: opleveringen ─────────────────────────────────────────

@router.get("/")
def list_opleveringen(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Oplevering).filter(Oplevering.organization_id == current_user.organization_id)
    if project_id:
        q = q.filter(Oplevering.project_id == project_id)
    if status:
        q = q.filter(Oplevering.status == status)
    items = q.order_by(Oplevering.created_at.desc()).all()
    return [_oplevering_to_dict(o) for o in items]


@router.post("/")
def create_oplevering(
    payload: OpleveringIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    extra_json = json.dumps(payload.extra_questions) if payload.extra_questions else None
    o = Oplevering(
        organization_id=current_user.organization_id,
        project_id=payload.project_id,
        title=payload.title,
        description=payload.description,
        locatie=payload.locatie,
        datum_oplevering=payload.datum_oplevering,
        opdrachtgever_naam=payload.opdrachtgever_naam,
        opdrachtgever_email=payload.opdrachtgever_email,
        aannemer_naam=payload.aannemer_naam,
        aannemer_email=payload.aannemer_email,
        extra_questions_json=extra_json,
        notes=payload.notes,
        status=payload.status,
        created_by=current_user.id,
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    log_action(db, request, current_user, action="oplevering.create",
               entity_type="oplevering", entity_id=o.id,
               extra={"title": o.title, "status": o.status})
    return _oplevering_to_dict(o, include_punten=True)


# ─────────────────────────────────────────────────────────────────────────────
# OPLEVERRONDE MET CAMERA
# ─────────────────────────────────────────────────────────────────────────────
# Let op de volgorde: deze routes staan bewust vóór GET /{oplevering_id}.
# FastAPI kiest de eerst geregistreerde die past, en anders vangt die route
# "rondes" op als oplevering-id -- dan krijg je een 404 die nergens op slaat.


class RondeIn(BaseModel):
    soort: Optional[str] = Field(default="eerste", pattern="^(eerste|herkeuring)$")
    inspecteur_naam: Optional[str] = Field(default=None, max_length=120)
    weer: Optional[str] = Field(default=None, max_length=120)
    # De privacy-poort. Zonder expliciete bevestiging gaat er geen beeld naar
    # de verwerker; zie oplever_vision.analyseer_frame.
    privacy_bevestigd: bool = False


class FrameIn(BaseModel):
    image_data_url: str = Field(..., min_length=32)
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lng: Optional[float] = Field(default=None, ge=-180, le=180)
    plek: Optional[str] = Field(default=None, max_length=255)
    bewaar_beeld: bool = True


class HandmatigPuntIn(BaseModel):
    """Punt voor punt blijft gewoon kunnen -- de camera is een extra, geen dwang."""
    omschrijving: str = Field(..., min_length=1)
    restpunt_klasse: Optional[str] = None
    ernst: Optional[str] = Field(default="matig", pattern="^(licht|matig|zwaar)$")
    plek: Optional[str] = Field(default=None, max_length=255)
    code: Optional[str] = Field(default=None, max_length=64)
    photo_url: Optional[str] = None
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lng: Optional[float] = Field(default=None, ge=-180, le=180)


class BevestigIn(BaseModel):
    """Een voorstel bevestigen, bijstellen of weggooien."""
    besluit: str = Field(..., pattern="^(bevestigen|verwerpen)$")
    omschrijving: Optional[str] = None
    ernst: Optional[str] = Field(default=None, pattern="^(licht|matig|zwaar)$")
    plek: Optional[str] = Field(default=None, max_length=255)
    restpunt_klasse: Optional[str] = None


class HerstelIn(BaseModel):
    photo_url_after: str = Field(..., min_length=32)
    toelichting: Optional[str] = None


class VerificatieIn(BaseModel):
    besluit: str = Field(..., pattern="^(akkoord|afwijzen)$")
    reden: Optional[str] = None


def _ronde_of_404(db: Session, ronde_id: str, user: User) -> OpleverRonde:
    r = (db.query(OpleverRonde)
           .filter(OpleverRonde.id == ronde_id,
                   OpleverRonde.organization_id == user.organization_id)
           .first())
    if not r:
        raise HTTPException(status_code=404, detail="Ronde niet gevonden")
    return r


def _punt_of_404(db: Session, punt_id: str, user: User) -> OpleveringPunt:
    p = (db.query(OpleveringPunt)
           .filter(OpleveringPunt.id == punt_id,
                   OpleveringPunt.organization_id == user.organization_id)
           .first())
    if not p:
        raise HTTPException(status_code=404, detail="Punt niet gevonden")
    return p


def _naam(u: User) -> str:
    return " ".join(x for x in (u.first_name, u.last_name) if x).strip() or u.email


def _ronde_dict(r: OpleverRonde, *, punten: Optional[list] = None) -> dict:
    uit = {
        "id": r.id,
        "oplevering_id": r.oplevering_id,
        "nummer": r.nummer,
        "soort": r.soort,
        "status": r.status,
        "inspecteur_naam": r.inspecteur_naam,
        "privacy_bevestigd": r.privacy_bevestigd,
        "frames": r.frames,
        "frames_onbruikbaar": r.frames_onbruikbaar,
        "weer": r.weer,
        "opmerking": r.opmerking,
        "gestart_op": r.gestart_op.isoformat() if r.gestart_op else None,
        "afgerond_op": r.afgerond_op.isoformat() if r.afgerond_op else None,
        "ai_beschikbaar": ov.is_geconfigureerd(),
    }
    if punten is not None:
        uit["punten"] = punten
    return uit


def _restpunt_dict(p: OpleveringPunt) -> dict:
    """Zonder de foto's. Een ronde levert makkelijk dertig punten met elk een
    voor- en een nafoto op; die base64-blobs horen niet in een lijst. De
    inhoud haal je per punt op."""
    return {
        "id": p.id,
        "code": p.code,
        "omschrijving": p.omschrijving,
        "restpunt_klasse": p.restpunt_klasse,
        "restpunt_klasse_naam": (ov.KLASSEN_OP_CODE.get(p.restpunt_klasse) or {}).get("naam"),
        "ernst": p.ernst,
        "plek": p.plek,
        "status": p.status,
        "bron": p.bron,
        "zekerheid": p.zekerheid,
        "moet_nagekeken": ov.moet_nagekeken(p.zekerheid) if p.bron == "ai" else False,
        "lat": p.lat,
        "lng": p.lng,
        "heeft_foto": bool(p.photo_url),
        "heeft_herstelfoto": bool(p.photo_url_after),
        "hersteld_op": p.hersteld_op.isoformat() if p.hersteld_op else None,
        "geverifieerd_op": p.geverifieerd_op.isoformat() if p.geverifieerd_op else None,
        "afgewezen_reden": p.afgewezen_reden,
        "order_index": p.order_index,
    }


def _data_url_naar_bytes(data_url: str) -> tuple[bytes, str]:
    m = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", data_url or "", re.S)
    if not m:
        raise HTTPException(status_code=400,
                            detail="Verwacht een data-URL met een base64-afbeelding")
    try:
        return base64.b64decode(m.group(2)), m.group(1)
    except Exception:
        raise HTTPException(status_code=400, detail="Afbeelding is niet te lezen")


@router.get("/restpunt-klassen")
def restpunt_klassen(current_user: User = Depends(get_current_user)):
    """Wat de camera mag melden, plus de ernst-niveaus. Voedt het scherm."""
    return {
        "klassen": ov.klassen(),
        "ernst": [{"code": c, "label": lb} for c, lb in ov.ERNST_NIVEAUS.items()],
        "ai_beschikbaar": ov.is_geconfigureerd(),
        "drempel_nakijken": ov.DREMPEL_NAKIJKEN,
        "versie": ov.OPLEVER_VISION_VERSIE,
    }


@router.post("/{oplevering_id}/rondes")
def start_ronde(
    oplevering_id: str,
    payload: RondeIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start een ronde. De eerste levert de restpuntenlijst; een herkeuring
    loopt dezelfde route nadat er hersteld is."""
    o = _get_oplevering_or_404(db, oplevering_id, current_user)

    hoogste = (db.query(OpleverRonde.nummer)
                 .filter(OpleverRonde.oplevering_id == o.id)
                 .order_by(OpleverRonde.nummer.desc()).first())
    nummer = ((hoogste[0] or 0) + 1) if hoogste else 1

    r = OpleverRonde(
        oplevering_id=o.id,
        organization_id=current_user.organization_id,
        nummer=nummer,
        soort=payload.soort or ("herkeuring" if nummer > 1 else "eerste"),
        inspecteur_id=current_user.id,
        inspecteur_naam=(payload.inspecteur_naam or "").strip() or _naam(current_user),
        privacy_bevestigd=bool(payload.privacy_bevestigd),
        weer=payload.weer,
        created_by=current_user.id,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    log_action(db, request, current_user, action="oplevering.ronde.start",
               entity_type="oplever_ronde", entity_id=r.id,
               after={"oplevering_id": o.id, "nummer": r.nummer, "soort": r.soort,
                      "privacy_bevestigd": r.privacy_bevestigd})
    return _ronde_dict(r, punten=[])


@router.get("/{oplevering_id}/rondes")
def lijst_rondes(
    oplevering_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    o = _get_oplevering_or_404(db, oplevering_id, current_user)
    rijen = (db.query(OpleverRonde)
               .filter(OpleverRonde.oplevering_id == o.id)
               .order_by(OpleverRonde.nummer).all())
    return [_ronde_dict(r) for r in rijen]


@router.get("/{oplevering_id}/restpunten")
def restpunten(
    oplevering_id: str,
    alleen_open: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """De restpuntenlijst van deze oplevering.

    Voorstellen die nog niet zijn bevestigd staan er apart bij: die zijn door
    de camera gezien maar nog niet door een mens. Ze horen niet in de lijst die
    naar de aannemer gaat tot iemand ze heeft nagekeken.
    """
    o = _get_oplevering_or_404(db, oplevering_id, current_user)
    q = (db.query(OpleveringPunt)
           .filter(OpleveringPunt.oplevering_id == o.id)
           .order_by(OpleveringPunt.order_index, OpleveringPunt.created_at))
    alle = q.all()

    OPEN = ("restpunt", "actiepunt", "afgekeurd", "hersteld")
    voorstellen = [p for p in alle if p.status == "voorgesteld"]
    lijst = [p for p in alle if p.status != "voorgesteld"]
    if alleen_open:
        lijst = [p for p in lijst if p.status in OPEN]

    per_ernst = {"licht": 0, "matig": 0, "zwaar": 0}
    for p in lijst:
        if p.ernst in per_ernst and p.status in OPEN:
            per_ernst[p.ernst] += 1

    return {
        "oplevering_id": o.id,
        "restpunten": [_restpunt_dict(p) for p in lijst],
        "voorstellen": [_restpunt_dict(p) for p in voorstellen],
        "tellingen": {
            "totaal": len(lijst),
            "open": sum(1 for p in lijst if p.status in OPEN),
            "hersteld": sum(1 for p in lijst if p.status == "hersteld"),
            "geverifieerd": sum(1 for p in lijst if p.status == "geverifieerd"),
            "nog_te_bevestigen": len(voorstellen),
            "per_ernst_open": per_ernst,
        },
    }


@router.get("/rondes/{ronde_id}")
def ronde_detail(
    ronde_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    r = _ronde_of_404(db, ronde_id, current_user)
    punten = (db.query(OpleveringPunt)
                .filter(OpleveringPunt.ronde_id == r.id)
                .order_by(OpleveringPunt.created_at).all())
    return _ronde_dict(r, punten=[_restpunt_dict(p) for p in punten])


@router.post("/rondes/{ronde_id}/frame")
def ronde_frame(
    ronde_id: str,
    payload: FrameIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Eén beeld analyseren en de gevonden punten als voorstel vastleggen.

    Antwoordt met wat er in dít beeld is gezien, zodat het scherm het meteen
    kan tonen terwijl de inspecteur er nog staat. Alles komt binnen als
    'voorgesteld': niets gaat automatisch de restpuntenlijst in.
    """
    r = _ronde_of_404(db, ronde_id, current_user)
    if r.status != "bezig":
        raise HTTPException(status_code=409, detail="Deze ronde is al afgerond")

    beeld, media_type = _data_url_naar_bytes(payload.image_data_url)
    o = db.query(Oplevering).filter(Oplevering.id == r.oplevering_id).first()
    context = " · ".join(x for x in (o.title if o else None,
                                     o.locatie if o else None,
                                     payload.plek) if x) or None

    try:
        resultaat = ov.analyseer_frame(
            image_bytes=beeld, image_media_type=media_type,
            privacy_gecontroleerd=r.privacy_bevestigd,
            context=context)
    except ov.NietGecontroleerd as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    r.frames = (r.frames or 0) + 1
    if not resultaat.get("bruikbaar"):
        r.frames_onbruikbaar = (r.frames_onbruikbaar or 0) + 1

    volgende = (db.query(func.count(OpleveringPunt.id))
                  .filter(OpleveringPunt.oplevering_id == r.oplevering_id)
                  .scalar() or 0)

    nieuw: list[OpleveringPunt] = []
    for i, v in enumerate(resultaat.get("punten") or []):
        p = OpleveringPunt(
            oplevering_id=r.oplevering_id,
            organization_id=current_user.organization_id,
            ronde_id=r.id,
            code=f"RP-{volgende + i + 1:03d}",
            omschrijving=v.get("omschrijving") or v.get("klasse_naam") or "Restpunt",
            restpunt_klasse=v.get("klasse"),
            ernst=v.get("ernst"),
            plek=v.get("plek") or payload.plek,
            lat=payload.lat,
            lng=payload.lng,
            zekerheid=v.get("zekerheid"),
            bron="ai",
            status="voorgesteld",
            model_id=resultaat.get("_model_id"),
            vision_versie=resultaat.get("_versie"),
            photo_url=payload.image_data_url if payload.bewaar_beeld else None,
            order_index=volgende + i + 1,
        )
        db.add(p)
        nieuw.append(p)

    db.commit()
    db.refresh(r)
    return {
        "bruikbaar": resultaat.get("bruikbaar"),
        "reden_onbruikbaar": resultaat.get("reden_onbruikbaar"),
        "gevonden": [_restpunt_dict(p) for p in nieuw],
        "ronde": _ronde_dict(r),
    }


@router.post("/rondes/{ronde_id}/punt")
def handmatig_punt(
    ronde_id: str,
    payload: HandmatigPuntIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Zelf een restpunt vastleggen. Komt direct als restpunt binnen, niet als
    voorstel: een mens heeft het al gezien."""
    r = _ronde_of_404(db, ronde_id, current_user)
    if r.status != "bezig":
        raise HTTPException(status_code=409, detail="Deze ronde is al afgerond")
    if payload.restpunt_klasse and payload.restpunt_klasse not in ov.KLASSEN_OP_CODE:
        raise HTTPException(status_code=400, detail="Onbekende soort restpunt")

    volgende = (db.query(func.count(OpleveringPunt.id))
                  .filter(OpleveringPunt.oplevering_id == r.oplevering_id)
                  .scalar() or 0) + 1

    foto = payload.photo_url
    if foto:
        from photo_storage import maybe_offload
        foto = maybe_offload(foto, organization_id=current_user.organization_id,
                             kind="oplevering") or foto

    p = OpleveringPunt(
        oplevering_id=r.oplevering_id,
        organization_id=current_user.organization_id,
        ronde_id=r.id,
        code=(payload.code or "").strip() or f"RP-{volgende:03d}",
        omschrijving=payload.omschrijving.strip(),
        restpunt_klasse=payload.restpunt_klasse,
        ernst=payload.ernst or "matig",
        plek=payload.plek,
        lat=payload.lat, lng=payload.lng,
        bron="handmatig",
        status="restpunt",
        photo_url=foto,
        order_index=volgende,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    log_action(db, request, current_user, action="oplevering.restpunt.handmatig",
               entity_type="opleveringspunt", entity_id=p.id,
               after={"ronde_id": r.id, "code": p.code, "ernst": p.ernst})
    return _restpunt_dict(p)


@router.patch("/punten/{punt_id}/bevestigen")
def bevestig_punt(
    punt_id: str,
    payload: BevestigIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Een voorstel van de camera bevestigen of weggooien.

    Bijstellen mag: de omschrijving, de ernst, de plek en de soort. Wat het
    model zag blijft bewaard in zekerheid en model_id -- bij een geschil wil je
    kunnen laten zien wat de camera meldde en wat de inspecteur ervan maakte.
    """
    p = _punt_of_404(db, punt_id, current_user)
    if p.status != "voorgesteld":
        raise HTTPException(status_code=409,
                            detail="Dit punt is al beoordeeld en staat in de lijst")

    voor = {"status": p.status, "ernst": p.ernst, "omschrijving": p.omschrijving}

    if payload.besluit == "verwerpen":
        db.delete(p)
        db.commit()
        log_action(db, request, current_user, action="oplevering.voorstel.verworpen",
                   entity_type="opleveringspunt", entity_id=punt_id, before=voor)
        return {"verworpen": True}

    velden = payload.model_dump(exclude_unset=True)
    if velden.get("restpunt_klasse") and velden["restpunt_klasse"] not in ov.KLASSEN_OP_CODE:
        raise HTTPException(status_code=400, detail="Onbekende soort restpunt")
    for veld in ("omschrijving", "ernst", "plek", "restpunt_klasse"):
        if veld in velden and velden[veld] is not None:
            setattr(p, veld, velden[veld])

    p.status = "restpunt"
    p.bevestigd_op = datetime.now(timezone.utc)
    p.bevestigd_door = current_user.id
    db.commit()
    db.refresh(p)
    log_action(db, request, current_user, action="oplevering.voorstel.bevestigd",
               entity_type="opleveringspunt", entity_id=p.id, before=voor,
               after={"status": p.status, "ernst": p.ernst,
                      "zekerheid": p.zekerheid, "model_id": p.model_id})
    return _restpunt_dict(p)


@router.post("/punten/{punt_id}/herstel")
def meld_herstel(
    punt_id: str,
    payload: HerstelIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Melden dat een restpunt is hersteld, met de foto als bewijs.

    De foto is verplicht. Een restpunt afvinken zonder beeld is precies het
    soort afvinken waar deze module vanaf wil: bij een geschil is "hij zei dat
    het gemaakt was" geen onderbouwing.

    Het punt gaat naar 'hersteld', niet naar 'gereed'. Dicht is het pas als
    iemand anders het heeft nagekeken.
    """
    p = _punt_of_404(db, punt_id, current_user)
    if p.status == "voorgesteld":
        raise HTTPException(status_code=409,
                            detail="Bevestig dit punt eerst; het is nog een voorstel")
    if p.status == "geverifieerd":
        raise HTTPException(status_code=409, detail="Dit punt is al afgetekend")

    from photo_storage import maybe_offload
    p.photo_url_after = maybe_offload(
        payload.photo_url_after, organization_id=current_user.organization_id,
        kind="oplevering") or payload.photo_url_after
    p.hersteld_op = datetime.now(timezone.utc)
    p.hersteld_door = current_user.id
    p.hersteld_toelichting = payload.toelichting
    p.afgewezen_reden = None      # nieuwe poging: oude afwijzing hoort weg
    p.status = "hersteld"
    db.commit()
    db.refresh(p)
    log_action(db, request, current_user, action="oplevering.restpunt.hersteld",
               entity_type="opleveringspunt", entity_id=p.id,
               after={"code": p.code, "met_bewijs": True})
    return _restpunt_dict(p)


@router.post("/punten/{punt_id}/verifieren")
def verifieer_herstel(
    punt_id: str,
    payload: VerificatieIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Het herstel nakijken en aftekenen, of afwijzen met een reden.

    Afwijzen zonder reden mag niet: de aannemer moet weten wat er alsnog moet
    gebeuren, anders komt hetzelfde punt bij de volgende ronde weer terug.
    """
    p = _punt_of_404(db, punt_id, current_user)
    if p.status != "hersteld":
        raise HTTPException(status_code=409,
                            detail="Alleen een gemeld herstel kan worden nagekeken")

    reden = (payload.reden or "").strip()
    if payload.besluit == "afwijzen":
        if not reden:
            raise HTTPException(
                status_code=400,
                detail="Geef een reden op, anders weet de aannemer niet wat er moet gebeuren")
        p.status = "restpunt"
        p.afgewezen_reden = reden
        p.hersteld_op = None
        p.hersteld_door = None
    else:
        p.status = "geverifieerd"
        p.geverifieerd_op = datetime.now(timezone.utc)
        p.geverifieerd_door = current_user.id
        p.afgewezen_reden = None

    db.commit()
    db.refresh(p)
    log_action(db, request, current_user, action="oplevering.herstel.beoordeeld",
               entity_type="opleveringspunt", entity_id=p.id,
               after={"code": p.code, "besluit": payload.besluit,
                      "reden": reden or None})
    return _restpunt_dict(p)


@router.get("/punten/{punt_id}/fotos")
def punt_fotos(
    punt_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """De voor- en herstelfoto van één punt. Bewust apart: in een lijst van
    dertig punten horen zestig base64-blobs niet thuis."""
    p = _punt_of_404(db, punt_id, current_user)
    return {
        "id": p.id,
        "photo_url": p.photo_url,
        "photo_url_after": p.photo_url_after,
    }


@router.post("/rondes/{ronde_id}/afronden")
def rond_ronde_af(
    ronde_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """De ronde sluiten.

    Kan niet zolang er voorstellen open staan: dan zou een deel van wat de
    camera zag in het niets verdwijnen, en dat is precies het gat waar deze
    module voor bedoeld is.
    """
    r = _ronde_of_404(db, ronde_id, current_user)
    if r.status == "afgerond":
        raise HTTPException(status_code=409, detail="Deze ronde is al afgerond")

    open_voorstellen = (db.query(func.count(OpleveringPunt.id))
                          .filter(OpleveringPunt.ronde_id == r.id,
                                  OpleveringPunt.status == "voorgesteld")
                          .scalar() or 0)
    if open_voorstellen:
        raise HTTPException(
            status_code=400,
            detail=(f"Er staan nog {open_voorstellen} voorstellen open. Bevestig of "
                    "verwerp ze eerst, anders verdwijnt wat de camera zag."))

    r.status = "afgerond"
    r.afgerond_op = datetime.now(timezone.utc)
    db.commit()
    db.refresh(r)

    punten = (db.query(OpleveringPunt)
                .filter(OpleveringPunt.ronde_id == r.id)
                .order_by(OpleveringPunt.created_at).all())
    log_action(db, request, current_user, action="oplevering.ronde.afgerond",
               entity_type="oplever_ronde", entity_id=r.id,
               after={"frames": r.frames, "frames_onbruikbaar": r.frames_onbruikbaar,
                      "punten": len(punten)})
    return _ronde_dict(r, punten=[_restpunt_dict(p) for p in punten])


@router.get("/{oplevering_id}")
def get_oplevering(
    oplevering_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    o = _get_oplevering_or_404(db, oplevering_id, current_user)
    return _oplevering_to_dict(o, include_punten=True)


@router.patch("/{oplevering_id}")
def update_oplevering(
    oplevering_id: str,
    payload: OpleveringUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    o = _get_oplevering_or_404(db, oplevering_id, current_user)
    before_status = o.status
    data = payload.model_dump(exclude_unset=True)
    if "extra_questions" in data:
        eq = data.pop("extra_questions")
        o.extra_questions_json = json.dumps(eq) if eq else None
    for k, v in data.items():
        setattr(o, k, v)
    # Markeer signed_off_at als status overgaat naar aanvaard
    if before_status != "aanvaard" and o.status == "aanvaard":
        o.signed_off_at = datetime.utcnow()
    db.commit()
    db.refresh(o)
    log_action(db, request, current_user, action="oplevering.update",
               entity_type="oplevering", entity_id=o.id,
               extra={"before_status": before_status, "after_status": o.status})

    # Auto-trigger email bij status-overgang naar 'opgeleverd' (eenmalig).
    # Defensief in try/except: een email-fout mag de status-update niet blokkeren.
    if before_status != "opgeleverd" and o.status == "opgeleverd":
        try:
            recipients = _collect_recipients(o)
            if recipients:
                result = send_oplevering_email(o, recipients, trigger="auto_status_change")
                log_action(db, request, current_user, action="oplevering.email_sent",
                           entity_type="oplevering", entity_id=o.id,
                           extra={"trigger": "auto_status_change", **result})
        except Exception as e:
            import logging
            logging.exception("auto-email bij opleveren faalde: %s", e)

    # Werkdagboek: auto-entry bij status-wijziging naar opgeleverd of aanvaard
    if before_status != o.status and o.status in ("opgeleverd", "aanvaard"):
        from daybook_logger import log_daybook
        punten_count = len(_bevestigde_punten(o))
        log_daybook(
            db,
            user_id=current_user.id,
            organization_id=current_user.organization_id,
            entry_type="oplevering_completed",
            title=("Oplevering " + o.status + ": " + (o.project_naam or "(zonder project)")),
            description=(str(punten_count) + " opleverpunten · status " + before_status + " → " + o.status),
            source_type="oplevering",
            source_id=o.id,
            project_id=getattr(o, "project_id", None),
        )

    return _oplevering_to_dict(o, include_punten=True)


@router.delete("/{oplevering_id}")
def delete_oplevering(
    oplevering_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    o = _get_oplevering_or_404(db, oplevering_id, current_user)
    title = o.title
    db.delete(o)
    db.commit()
    log_action(db, request, current_user, action="oplevering.delete",
               entity_type="oplevering", entity_id=oplevering_id,
               extra={"title": title})
    return {"message": "Oplevering verwijderd"}


# ── Endpoints: punten ─────────────────────────────────────────────

@router.post("/{oplevering_id}/punten")
def add_punt(
    oplevering_id: str,
    payload: OpleveringPuntIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    o = _get_oplevering_or_404(db, oplevering_id, current_user)
    # Optionele asset-validatie (binnen eigen org)
    if payload.asset_id:
        asset = (db.query(Asset)
                   .filter(Asset.id == payload.asset_id,
                           Asset.organization_id == current_user.organization_id)
                   .first())
        if not asset:
            raise HTTPException(status_code=404, detail="Asset niet gevonden in jouw organisatie")
    # Auto order_index als 0 of niet gezet
    next_order = payload.order_index
    if not next_order:
        existing = db.query(OpleveringPunt).filter(OpleveringPunt.oplevering_id == o.id).count()
        next_order = existing + 1
    p = OpleveringPunt(
        oplevering_id=o.id,
        organization_id=current_user.organization_id,
        code=payload.code,
        omschrijving=payload.omschrijving,
        uitvoeringsmethode=payload.uitvoeringsmethode,
        photo_url=payload.photo_url,
        asset_id=payload.asset_id,
        order_index=next_order,
        status=payload.status,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    log_action(db, request, current_user, action="oplevering.punt_add",
               entity_type="oplevering_punt", entity_id=p.id,
               extra={"oplevering_id": o.id, "code": p.code})
    return _punt_to_dict(p)


@router.patch("/{oplevering_id}/punten/{punt_id}")
def update_punt(
    oplevering_id: str,
    punt_id: str,
    payload: OpleveringPuntUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    o = _get_oplevering_or_404(db, oplevering_id, current_user)
    p = (db.query(OpleveringPunt)
            .filter(OpleveringPunt.id == punt_id,
                    OpleveringPunt.oplevering_id == o.id)
            .first())
    if not p:
        raise HTTPException(status_code=404, detail="Punt niet gevonden")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    log_action(db, request, current_user, action="oplevering.punt_update",
               entity_type="oplevering_punt", entity_id=p.id,
               extra={"oplevering_id": o.id, "code": p.code})
    return _punt_to_dict(p)


@router.delete("/{oplevering_id}/punten/{punt_id}")
def delete_punt(
    oplevering_id: str,
    punt_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    o = _get_oplevering_or_404(db, oplevering_id, current_user)
    p = (db.query(OpleveringPunt)
            .filter(OpleveringPunt.id == punt_id,
                    OpleveringPunt.oplevering_id == o.id)
            .first())
    if not p:
        raise HTTPException(status_code=404, detail="Punt niet gevonden")
    code = p.code
    db.delete(p)
    db.commit()
    log_action(db, request, current_user, action="oplevering.punt_delete",
               entity_type="oplevering_punt", entity_id=punt_id,
               extra={"oplevering_id": o.id, "code": code})
    return {"message": "Punt verwijderd"}


# ── Email versturen (handmatig) ─────────────────────────────────────

class SendOpleveringRequest(BaseModel):
    recipients: Optional[List[str]] = None  # extra/override; default = opdrachtgever + aannemer


@router.post("/{oplevering_id}/send")
def send_oplevering(
    oplevering_id: str,
    payload: SendOpleveringRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verstuur het oplever-formulier handmatig per email.

    Standaard naar opdrachtgever + aannemer (gevuld in de oplevering).
    Caller kan custom recipient-lijst meesturen voor cc/extra ontvangers.
    """
    o = _get_oplevering_or_404(db, oplevering_id, current_user)
    recipients = payload.recipients or _collect_recipients(o)
    if not recipients:
        raise HTTPException(
            status_code=400,
            detail="Geen ontvangers — vul opdrachtgever-email of aannemer-email in (of geef recipients mee in de request).",
        )
    result = send_oplevering_email(o, recipients, trigger="manual")
    log_action(db, request, current_user, action="oplevering.email_sent",
               entity_type="oplevering", entity_id=o.id,
               extra={"trigger": "manual", "recipients": recipients, **result})
    return {
        "message": f"Email verstuurd naar {result['sent']} ontvanger(s)",
        "recipients": recipients,
        **result,
    }
