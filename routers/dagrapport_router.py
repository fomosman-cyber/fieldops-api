"""Projectdagrapport: dagboek, personeel, materieel, materiaal en afwijkingen.

Endpoints:
  GET    /api/dagrapport/config                 keuzelijsten en medewerkers
  GET    /api/dagrapport/dag                    alles van één project op één dag
  PUT    /api/dagrapport/dag                    weer, temperatuur en logboek
  POST   /api/dagrapport/personeel              iemand op de dag zetten
  POST   /api/dagrapport/personeel/kopieer      de ploeg van een andere dag overnemen
  PATCH  /api/dagrapport/personeel/{id}
  DELETE /api/dagrapport/personeel/{id}
  POST   /api/dagrapport/materiaal              aan- of afvoer
  PATCH  /api/dagrapport/materiaal/{id}
  DELETE /api/dagrapport/materiaal/{id}
  POST   /api/dagrapport/afwijkingen            meerwerk, stagnatie
  PATCH  /api/dagrapport/afwijkingen/{id}
  DELETE /api/dagrapport/afwijkingen/{id}
  GET    /api/dagrapport/week                   de week in tabellen
  GET    /api/dagrapport/week.pdf|.xlsx         weekrapport in de huisstijl

**Materieel** zet je op de dag via POST /api/materieel/inzet met project_id
en datum: dezelfde regel als in het werkdagboek, zodat CO2 op één plek wordt
berekend en nergens dubbel telt.

**Wie mag wat.** Het dagrapport is van het project, niet van één persoon.
Iedereen in de organisatie leest het; iedereen behalve een lezer vult het aan
en wijzigt het. Elke wijziging gaat met naam naar de auditlog. Verwijderen van
een regel mag wie hem maakte, en een beheerder of projectleider.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import dagrapport as dr
from audit import ACTION, log_action
from auth import get_current_user
from database import get_db
from models import (DagrapportAfwijking, DagrapportDag, DagrapportMateriaal, DagrapportPersoneel,
                    MaterieelInzet, Project, User, UserRole)
from permissions import can_manage_assets, label_for, require_module

router = APIRouter(prefix="/api/dagrapport", tags=["Dagrapport"],
                   dependencies=[Depends(require_module("dagboek"))])


# ── Schemas ──────────────────────────────────────────────────────────

class DagIn(BaseModel):
    project_id: str
    datum: date_type
    weer: Optional[str] = None
    temp_min: Optional[float] = Field(default=None, ge=-40, le=50)
    temp_max: Optional[float] = Field(default=None, ge=-40, le=50)
    log: Optional[str] = Field(default=None, max_length=20000)


class PersoneelIn(BaseModel):
    project_id: str
    datum: date_type
    user_id: Optional[str] = None
    naam: Optional[str] = Field(default=None, max_length=160)
    functie: Optional[str] = Field(default=None, max_length=80)
    bedrijf: Optional[str] = Field(default=None, max_length=160)
    uren: float = Field(..., ge=0, le=24)
    opmerking: Optional[str] = Field(default=None, max_length=2000)


class PersoneelPatch(BaseModel):
    naam: Optional[str] = Field(default=None, max_length=160)
    functie: Optional[str] = Field(default=None, max_length=80)
    bedrijf: Optional[str] = Field(default=None, max_length=160)
    uren: Optional[float] = Field(default=None, ge=0, le=24)
    opmerking: Optional[str] = Field(default=None, max_length=2000)


class KopieerIn(BaseModel):
    project_id: str
    van: date_type
    naar: date_type


class MateriaalIn(BaseModel):
    project_id: str
    datum: date_type
    leverancier: Optional[str] = Field(default=None, max_length=160)
    materiaal: str = Field(..., min_length=1, max_length=160)
    richting: str = "aanvoer"
    hoeveelheid: float = Field(..., ge=0, le=1_000_000)
    eenheid: str = "ton"
    opmerking: Optional[str] = Field(default=None, max_length=2000)


class MateriaalPatch(BaseModel):
    leverancier: Optional[str] = Field(default=None, max_length=160)
    materiaal: Optional[str] = Field(default=None, min_length=1, max_length=160)
    richting: Optional[str] = None
    hoeveelheid: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    eenheid: Optional[str] = None
    opmerking: Optional[str] = Field(default=None, max_length=2000)


class AfwijkingIn(BaseModel):
    project_id: str
    datum: date_type
    omschrijving: str = Field(..., min_length=1, max_length=5000)
    maatregel: Optional[str] = Field(default=None, max_length=5000)
    adres: Optional[str] = Field(default=None, max_length=255)
    stagnatie: str = "geen"
    duur_uren: Optional[float] = Field(default=None, ge=0, le=1000)
    soort: str = "geen"
    bedrag: Optional[float] = Field(default=None, ge=-10_000_000, le=10_000_000)


class AfwijkingPatch(BaseModel):
    omschrijving: Optional[str] = Field(default=None, min_length=1, max_length=5000)
    maatregel: Optional[str] = Field(default=None, max_length=5000)
    adres: Optional[str] = Field(default=None, max_length=255)
    stagnatie: Optional[str] = None
    duur_uren: Optional[float] = Field(default=None, ge=0, le=1000)
    soort: Optional[str] = None
    bedrag: Optional[float] = Field(default=None, ge=-10_000_000, le=10_000_000)


# ── Helpers ──────────────────────────────────────────────────────────

def _eis_schrijven(user: User) -> None:
    if user.role == UserRole.VIEWER and not user.is_org_admin:
        raise HTTPException(403, "Met een leesaccount kun je het dagrapport niet aanvullen")


def _project(db: Session, project_id: str, user: User) -> Project:
    p = db.query(Project).filter(Project.id == project_id,
                                 Project.organization_id == user.organization_id).first()
    if not p:
        raise HTTPException(404, "Project niet gevonden")
    return p


def _keuze(waarde: Optional[str], lijst: dict, wat: str) -> None:
    if waarde is not None and waarde not in lijst:
        raise HTTPException(400, f"Onbekende {wat}: {waarde}. Kies uit: {', '.join(lijst)}")


def _tekst(waarde: Optional[str]) -> Optional[str]:
    if waarde is None:
        return None
    waarde = waarde.strip()
    return waarde or None


def _naam(u: Optional[User]) -> str:
    if u is None:
        return ""
    return ((u.first_name or "") + " " + (u.last_name or "")).strip() or u.email


def _regel(db: Session, model, regel_id: str, user: User):
    r = db.query(model).filter(model.id == regel_id,
                               model.organization_id == user.organization_id).first()
    if not r:
        raise HTTPException(404, "Regel niet gevonden")
    return r


def _mag_verwijderen(r, user: User) -> None:
    if r.created_by != user.id and not can_manage_assets(user):
        raise HTTPException(403, "Alleen wie de regel maakte, of een beheerder of projectleider, "
                                 "kan hem verwijderen")


def _gebruikers(db: Session, ids: set) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()}


def _toon_dag(d: Optional[DagrapportDag], datum: date_type, users: dict) -> dict:
    if d is None:
        return {"id": None, "datum": datum.isoformat(), "weer": None, "weer_naam": None,
                "temp_min": None, "temp_max": None, "log": None, "bijgewerkt_door": None,
                "bijgewerkt_op": None}
    return {
        "id": d.id, "datum": d.datum.isoformat(), "weer": d.weer,
        "weer_naam": dr.WEER.get(d.weer or "", d.weer), "temp_min": d.temp_min,
        "temp_max": d.temp_max, "log": d.log,
        "bijgewerkt_door": _naam(users.get(d.updated_by or d.created_by)),
        "bijgewerkt_op": d.updated_at.isoformat() if d.updated_at else None,
    }


def _toon_personeel(p: DagrapportPersoneel, users: dict) -> dict:
    medewerker = users.get(p.user_id)
    naam = p.naam or (_naam(medewerker) if medewerker else "")
    return {
        "id": p.id, "datum": p.datum.isoformat(), "user_id": p.user_id,
        "naam": p.naam, "naam_toon": naam, "functie": p.functie,
        "bedrijf": p.bedrijf, "eigen": bool(p.user_id), "uren": p.uren,
        "opmerking": p.opmerking, "created_by": p.created_by,
        "ingevuld_door": _naam(users.get(p.created_by)),
    }


def _toon_materiaal(m: DagrapportMateriaal, users: dict) -> dict:
    return {
        "id": m.id, "datum": m.datum.isoformat(), "leverancier": m.leverancier,
        "materiaal": m.materiaal, "richting": m.richting,
        "richting_naam": dr.RICHTINGEN.get(m.richting, m.richting),
        "hoeveelheid": m.hoeveelheid, "eenheid": m.eenheid,
        "eenheid_naam": dr.EENHEDEN.get(m.eenheid, m.eenheid),
        "opmerking": m.opmerking, "created_by": m.created_by,
        "ingevuld_door": _naam(users.get(m.created_by)),
    }


def _toon_afwijking(a: DagrapportAfwijking, users: dict) -> dict:
    return {
        "id": a.id, "datum": a.datum.isoformat(), "omschrijving": a.omschrijving,
        "maatregel": a.maatregel, "adres": a.adres, "stagnatie": a.stagnatie,
        "stagnatie_naam": dr.STAGNATIE.get(a.stagnatie, a.stagnatie),
        "duur_uren": a.duur_uren, "soort": a.soort,
        "soort_naam": dr.AFWIJKING_SOORTEN.get(a.soort, a.soort), "bedrag": a.bedrag,
        "created_by": a.created_by, "ingevuld_door": _naam(users.get(a.created_by)),
    }


def _materieel(db: Session, user: User, project_id: str, van: date_type, tot: date_type) -> list[dict]:
    """Het materieel van het project in de periode, van iedereen.

    Dezelfde regels als in het werkdagboek (`materieel_inzet`), maar hier niet
    beperkt tot de eigen regels: het dagrapport gaat over het project.
    """
    from routers.materieel_router import _eigen_factoren, _namen, _toon
    regels = (db.query(MaterieelInzet)
                .filter(MaterieelInzet.organization_id == user.organization_id,
                        MaterieelInzet.project_id == project_id,
                        MaterieelInzet.datum >= van, MaterieelInzet.datum <= tot,
                        MaterieelInzet.deleted_at.is_(None))
                .order_by(MaterieelInzet.datum, MaterieelInzet.created_at).all())
    users, projecten = _namen(db, regels)
    eigen = _eigen_factoren(user.organization)
    return [_toon(r, users, projecten, eigen) for r in regels]


def _periode_data(db: Session, user: User, project_id: str, van: date_type, tot: date_type) -> dict:
    def query(model):
        return (db.query(model)
                  .filter(model.organization_id == user.organization_id,
                          model.project_id == project_id,
                          model.datum >= van, model.datum <= tot)
                  .order_by(model.datum, model.created_at).all())

    dagen = query(DagrapportDag)
    personeel = query(DagrapportPersoneel)
    materiaal = query(DagrapportMateriaal)
    afwijkingen = query(DagrapportAfwijking)
    users = _gebruikers(db, {d.created_by for d in dagen} | {d.updated_by for d in dagen}
                        | {p.user_id for p in personeel} | {p.created_by for p in personeel}
                        | {m.created_by for m in materiaal} | {a.created_by for a in afwijkingen})
    return {
        "dagen": dagen, "users": users,
        "personeel": [_toon_personeel(p, users) for p in personeel],
        "materiaal": [_toon_materiaal(m, users) for m in materiaal],
        "afwijkingen": [_toon_afwijking(a, users) for a in afwijkingen],
        "materieel": _materieel(db, user, project_id, van, tot),
    }


def _co2(materieel: list[dict]) -> dict:
    return {
        "uitstoot_kg": round(sum(r["co2_kg"] for r in materieel if r.get("co2_kg") is not None), 2),
        "waarvan_geschat_kg": round(sum(r["co2_kg"] for r in materieel
                                        if r.get("co2_kg") is not None
                                        and r.get("co2_methode") == "geschat"), 2),
        "reductie_kg": round(sum(r["reductie_kg"] for r in materieel if r.get("reductie_kg")), 2),
        "regels_zonder_getal": sum(1 for r in materieel if r.get("co2_kg") is None),
    }


def _audit(db, request, user, actie: str, entity_type: str, entity_id: str, **kw) -> None:
    log_action(db, request, user, action=actie, entity_type=entity_type, entity_id=entity_id, **kw)


# ── Config en dag ────────────────────────────────────────────────────

@router.get("/config")
def config(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Keuzelijsten, en de medewerkers van de organisatie voor de personeelslijst."""
    mensen = (db.query(User)
                .filter(User.organization_id == current_user.organization_id,
                        User.is_active.isnot(False))
                .order_by(User.first_name, User.last_name).all())
    return {
        "weer": [{"code": k, "naam": v} for k, v in dr.WEER.items()],
        "stagnatie": [{"code": k, "naam": v} for k, v in dr.STAGNATIE.items()],
        "afwijking_soorten": [{"code": k, "naam": v} for k, v in dr.AFWIJKING_SOORTEN.items()],
        "richtingen": [{"code": k, "naam": v} for k, v in dr.RICHTINGEN.items()],
        "eenheden": [{"code": k, "naam": v} for k, v in dr.EENHEDEN.items()],
        "functies": dr.FUNCTIES,
        # Alleen naam en rol: de personeelslijst hoeft geen e-mailadressen.
        "medewerkers": [{"id": u.id, "naam": _naam(u) if (u.first_name or u.last_name) else "(geen naam)",
                         "rol": label_for(u.role)} for u in mensen],
        "mag_schrijven": not (current_user.role == UserRole.VIEWER and not current_user.is_org_admin),
    }


@router.get("/dag")
def dag(project_id: str = Query(...), datum: date_type = Query(...),
        current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, current_user)
    data = _periode_data(db, current_user, project_id, datum, datum)
    kop = data["dagen"][0] if data["dagen"] else None
    return {
        "project": {"id": project.id, "naam": project.name},
        "datum": datum.isoformat(),
        "week": dr.weeknummer(datum),
        "dag": _toon_dag(kop, datum, data["users"]),
        "personeel": data["personeel"],
        "materieel": data["materieel"],
        "materiaal": data["materiaal"],
        "afwijkingen": data["afwijkingen"],
        "co2": _co2(data["materieel"]),
        "uren_personeel": round(sum(p["uren"] or 0 for p in data["personeel"]), 2),
    }


@router.put("/dag")
def zet_dag(body: DagIn, request: Request, current_user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    _eis_schrijven(current_user)
    _project(db, body.project_id, current_user)
    _keuze(body.weer, dr.WEER, "weersoort")
    if body.temp_min is not None and body.temp_max is not None and body.temp_min > body.temp_max:
        raise HTTPException(400, "De minimumtemperatuur ligt boven de maximumtemperatuur")

    d = (db.query(DagrapportDag)
           .filter(DagrapportDag.project_id == body.project_id, DagrapportDag.datum == body.datum,
                   DagrapportDag.organization_id == current_user.organization_id).first())
    voor = None
    if d is None:
        d = DagrapportDag(organization_id=current_user.organization_id, project_id=body.project_id,
                          datum=body.datum, created_by=current_user.id)
        db.add(d)
    else:
        voor = {"weer": d.weer, "temp_min": d.temp_min, "temp_max": d.temp_max,
                "log_lengte": len(d.log or "")}
    d.weer = body.weer
    d.temp_min, d.temp_max = body.temp_min, body.temp_max
    d.log = _tekst(body.log)
    d.updated_by = current_user.id
    db.commit()
    db.refresh(d)
    _audit(db, request, current_user, ACTION.DAGRAPPORT_DAG, "dagrapport_dag", d.id, before=voor,
           after={"datum": d.datum.isoformat(), "weer": d.weer, "log_lengte": len(d.log or "")})
    return _toon_dag(d, d.datum, _gebruikers(db, {d.created_by, d.updated_by}))


# ── Personeel ────────────────────────────────────────────────────────

@router.post("/personeel", status_code=201)
def nieuw_personeel(body: PersoneelIn, request: Request,
                    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _eis_schrijven(current_user)
    _project(db, body.project_id, current_user)
    medewerker = None
    if body.user_id:
        medewerker = db.query(User).filter(User.id == body.user_id,
                                           User.organization_id == current_user.organization_id).first()
        if not medewerker:
            raise HTTPException(404, "Medewerker niet gevonden")
    naam, functie, bedrijf = _tekst(body.naam), _tekst(body.functie), _tekst(body.bedrijf)
    if not (medewerker or naam or functie or bedrijf):
        raise HTTPException(400, "Kies een medewerker, of vul een naam, functie of bedrijf in")
    p = DagrapportPersoneel(
        organization_id=current_user.organization_id, project_id=body.project_id, datum=body.datum,
        user_id=medewerker.id if medewerker else None,
        naam=naam, functie=functie,
        bedrijf=bedrijf or (current_user.organization.name if medewerker and current_user.organization else None),
        uren=body.uren, opmerking=_tekst(body.opmerking), created_by=current_user.id)
    db.add(p)
    db.commit()
    db.refresh(p)
    _audit(db, request, current_user, ACTION.DAGRAPPORT_PERSONEEL_CREATE, "dagrapport_personeel", p.id,
           after={"datum": p.datum.isoformat(), "naam": naam or _naam(medewerker), "uren": p.uren})
    return _toon_personeel(p, _gebruikers(db, {p.user_id, p.created_by}))


@router.post("/personeel/kopieer")
def kopieer_personeel(body: KopieerIn, request: Request,
                      current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """De ploeg van een andere dag overnemen, met dezelfde uren.

    Wie er al staat op de doeldag (zelfde medewerker, of zelfde naam, functie
    en bedrijf) wordt overgeslagen: twee keer op "ploeg van gisteren" tikken
    mag geen dubbele uren opleveren.
    """
    _eis_schrijven(current_user)
    _project(db, body.project_id, current_user)
    if body.van == body.naar:
        raise HTTPException(400, "Kies een andere dag om van over te nemen")

    def rijen(d):
        return (db.query(DagrapportPersoneel)
                  .filter(DagrapportPersoneel.organization_id == current_user.organization_id,
                          DagrapportPersoneel.project_id == body.project_id,
                          DagrapportPersoneel.datum == d)
                  .order_by(DagrapportPersoneel.created_at).all())

    def sleutel(p):
        return p.user_id or ("", (p.naam or "").lower(), (p.functie or "").lower(), (p.bedrijf or "").lower())

    bron = rijen(body.van)
    if not bron:
        raise HTTPException(404, "Op die dag staat niemand op dit project")
    al = {sleutel(p) for p in rijen(body.naar)}
    nieuw = []
    for p in bron:
        if sleutel(p) in al:
            continue
        al.add(sleutel(p))
        kopie = DagrapportPersoneel(
            organization_id=current_user.organization_id, project_id=body.project_id,
            datum=body.naar, user_id=p.user_id, naam=p.naam, functie=p.functie,
            bedrijf=p.bedrijf, uren=p.uren, created_by=current_user.id)
        db.add(kopie)
        nieuw.append(kopie)
    db.commit()
    _audit(db, request, current_user, ACTION.DAGRAPPORT_PERSONEEL_KOPIEER, "project", body.project_id,
           after={"van": body.van.isoformat(), "naar": body.naar.isoformat(), "regels": len(nieuw)})
    users = _gebruikers(db, {p.user_id for p in nieuw} | {current_user.id})
    return {"toegevoegd": len(nieuw), "overgeslagen": len(bron) - len(nieuw),
            "personeel": [_toon_personeel(p, users) for p in nieuw]}


@router.patch("/personeel/{regel_id}")
def wijzig_personeel(regel_id: str, body: PersoneelPatch, request: Request,
                     current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _eis_schrijven(current_user)
    p = _regel(db, DagrapportPersoneel, regel_id, current_user)
    velden = body.model_dump(exclude_unset=True)
    if "uren" in velden and velden["uren"] is None:
        raise HTTPException(400, "Uren mogen niet leeg zijn; vul 0 in")
    voor = {"uren": p.uren, "functie": p.functie}
    for veld, waarde in velden.items():
        setattr(p, veld, _tekst(waarde) if isinstance(waarde, str) else waarde)
    if not (p.user_id or p.naam or p.functie or p.bedrijf):
        raise HTTPException(400, "Een regel zonder medewerker heeft een naam, functie of bedrijf nodig")
    db.commit()
    db.refresh(p)
    _audit(db, request, current_user, ACTION.DAGRAPPORT_PERSONEEL_UPDATE, "dagrapport_personeel", p.id,
           before=voor, after={"uren": p.uren, "functie": p.functie})
    return _toon_personeel(p, _gebruikers(db, {p.user_id, p.created_by}))


@router.delete("/personeel/{regel_id}")
def verwijder_personeel(regel_id: str, request: Request,
                        current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _eis_schrijven(current_user)
    p = _regel(db, DagrapportPersoneel, regel_id, current_user)
    _mag_verwijderen(p, current_user)
    spoor = {"datum": p.datum.isoformat(), "naam": p.naam, "user_id": p.user_id, "uren": p.uren}
    db.delete(p)
    db.commit()
    _audit(db, request, current_user, ACTION.DAGRAPPORT_PERSONEEL_DELETE, "dagrapport_personeel", regel_id, before=spoor)
    return {"verwijderd": True}


# ── Materiaal ────────────────────────────────────────────────────────

@router.post("/materiaal", status_code=201)
def nieuw_materiaal(body: MateriaalIn, request: Request,
                    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _eis_schrijven(current_user)
    _project(db, body.project_id, current_user)
    _keuze(body.richting, dr.RICHTINGEN, "richting")
    _keuze(body.eenheid, dr.EENHEDEN, "eenheid")
    materiaal = _tekst(body.materiaal)
    if not materiaal:
        raise HTTPException(400, "Vul het materiaal in")
    m = DagrapportMateriaal(
        organization_id=current_user.organization_id, project_id=body.project_id, datum=body.datum,
        leverancier=_tekst(body.leverancier), materiaal=materiaal, richting=body.richting,
        hoeveelheid=body.hoeveelheid, eenheid=body.eenheid, opmerking=_tekst(body.opmerking),
        created_by=current_user.id)
    db.add(m)
    db.commit()
    db.refresh(m)
    _audit(db, request, current_user, ACTION.DAGRAPPORT_MATERIAAL_CREATE, "dagrapport_materiaal", m.id,
           after={"datum": m.datum.isoformat(), "materiaal": m.materiaal,
                  "hoeveelheid": m.hoeveelheid, "eenheid": m.eenheid})
    return _toon_materiaal(m, _gebruikers(db, {m.created_by}))


@router.patch("/materiaal/{regel_id}")
def wijzig_materiaal(regel_id: str, body: MateriaalPatch, request: Request,
                     current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _eis_schrijven(current_user)
    m = _regel(db, DagrapportMateriaal, regel_id, current_user)
    velden = body.model_dump(exclude_unset=True)
    _keuze(velden.get("richting"), dr.RICHTINGEN, "richting")
    _keuze(velden.get("eenheid"), dr.EENHEDEN, "eenheid")
    for verplicht in ("materiaal", "hoeveelheid", "richting", "eenheid"):
        if verplicht in velden and velden[verplicht] is None:
            raise HTTPException(400, f"{verplicht} mag niet leeg zijn")
    voor = {"hoeveelheid": m.hoeveelheid, "materiaal": m.materiaal}
    for veld, waarde in velden.items():
        setattr(m, veld, _tekst(waarde) if isinstance(waarde, str) else waarde)
    if not m.materiaal:
        raise HTTPException(400, "Vul het materiaal in")
    db.commit()
    db.refresh(m)
    _audit(db, request, current_user, ACTION.DAGRAPPORT_MATERIAAL_UPDATE, "dagrapport_materiaal", m.id,
           before=voor, after={"hoeveelheid": m.hoeveelheid, "materiaal": m.materiaal})
    return _toon_materiaal(m, _gebruikers(db, {m.created_by}))


@router.delete("/materiaal/{regel_id}")
def verwijder_materiaal(regel_id: str, request: Request,
                        current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _eis_schrijven(current_user)
    m = _regel(db, DagrapportMateriaal, regel_id, current_user)
    _mag_verwijderen(m, current_user)
    spoor = {"datum": m.datum.isoformat(), "materiaal": m.materiaal, "hoeveelheid": m.hoeveelheid}
    db.delete(m)
    db.commit()
    _audit(db, request, current_user, ACTION.DAGRAPPORT_MATERIAAL_DELETE, "dagrapport_materiaal", regel_id, before=spoor)
    return {"verwijderd": True}


# ── Afwijkingen ──────────────────────────────────────────────────────

@router.post("/afwijkingen", status_code=201)
def nieuwe_afwijking(body: AfwijkingIn, request: Request,
                     current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _eis_schrijven(current_user)
    _project(db, body.project_id, current_user)
    _keuze(body.stagnatie, dr.STAGNATIE, "stagnatie")
    _keuze(body.soort, dr.AFWIJKING_SOORTEN, "soort afwijking")
    omschrijving = _tekst(body.omschrijving)
    if not omschrijving:
        raise HTTPException(400, "Beschrijf wat er afweek")
    a = DagrapportAfwijking(
        organization_id=current_user.organization_id, project_id=body.project_id, datum=body.datum,
        omschrijving=omschrijving, maatregel=_tekst(body.maatregel), adres=_tekst(body.adres),
        stagnatie=body.stagnatie, duur_uren=body.duur_uren, soort=body.soort, bedrag=body.bedrag,
        created_by=current_user.id)
    db.add(a)
    db.commit()
    db.refresh(a)
    _audit(db, request, current_user, ACTION.DAGRAPPORT_AFWIJKING_CREATE, "dagrapport_afwijking", a.id,
           after={"datum": a.datum.isoformat(), "soort": a.soort, "bedrag": a.bedrag,
                  "stagnatie": a.stagnatie})
    return _toon_afwijking(a, _gebruikers(db, {a.created_by}))


@router.patch("/afwijkingen/{regel_id}")
def wijzig_afwijking(regel_id: str, body: AfwijkingPatch, request: Request,
                     current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _eis_schrijven(current_user)
    a = _regel(db, DagrapportAfwijking, regel_id, current_user)
    velden = body.model_dump(exclude_unset=True)
    _keuze(velden.get("stagnatie"), dr.STAGNATIE, "stagnatie")
    _keuze(velden.get("soort"), dr.AFWIJKING_SOORTEN, "soort afwijking")
    for verplicht in ("omschrijving", "stagnatie", "soort"):
        if verplicht in velden and velden[verplicht] is None:
            raise HTTPException(400, f"{verplicht} mag niet leeg zijn")
    voor = {"soort": a.soort, "bedrag": a.bedrag, "stagnatie": a.stagnatie}
    for veld, waarde in velden.items():
        setattr(a, veld, _tekst(waarde) if isinstance(waarde, str) else waarde)
    if not a.omschrijving:
        raise HTTPException(400, "Beschrijf wat er afweek")
    db.commit()
    db.refresh(a)
    _audit(db, request, current_user, ACTION.DAGRAPPORT_AFWIJKING_UPDATE, "dagrapport_afwijking", a.id,
           before=voor, after={"soort": a.soort, "bedrag": a.bedrag, "stagnatie": a.stagnatie})
    return _toon_afwijking(a, _gebruikers(db, {a.created_by}))


@router.delete("/afwijkingen/{regel_id}")
def verwijder_afwijking(regel_id: str, request: Request,
                        current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _eis_schrijven(current_user)
    a = _regel(db, DagrapportAfwijking, regel_id, current_user)
    _mag_verwijderen(a, current_user)
    spoor = {"datum": a.datum.isoformat(), "omschrijving": a.omschrijving[:120], "bedrag": a.bedrag}
    db.delete(a)
    db.commit()
    _audit(db, request, current_user, ACTION.DAGRAPPORT_AFWIJKING_DELETE, "dagrapport_afwijking", regel_id, before=spoor)
    return {"verwijderd": True}


# ── Week ─────────────────────────────────────────────────────────────

def _week(db: Session, user: User, project_id: str, datum: date_type) -> tuple[dict, Project, str]:
    project = _project(db, project_id, user)
    maandag, zondag = dr.week_van(datum)
    data = _periode_data(db, user, project_id, maandag, zondag)
    users = data["users"]
    dagen = [_toon_dag(d, d.datum, users) for d in data["dagen"]]

    def met_datum(rijen):
        return [dict(r, _datum=date_type.fromisoformat(r["datum"])) for r in rijen]

    week = dr.weekopbouw(maandag=maandag, dagen=dagen,
                         personeel=met_datum(data["personeel"]),
                         materieel=met_datum(data["materieel"]),
                         materiaal=met_datum(data["materiaal"]),
                         afwijkingen=data["afwijkingen"])
    opstellers = sorted({_naam(users.get(d.updated_by or d.created_by)) for d in data["dagen"]} - {""})
    return week, project, ", ".join(opstellers) or _naam(user)


def _schoon(week: dict) -> dict:
    """Zonder de hulpvelden (_datum) die niet in JSON horen."""
    def zonder(r):
        return {k: v for k, v in r.items() if not k.startswith("_")}
    uit = dict(week)
    for sleutel in ("personeel", "materieel", "materiaal"):
        uit[sleutel] = [dict(g, voorbeeld=zonder(g["voorbeeld"]), sleutel=None) for g in week[sleutel]]
    return uit


@router.get("/week")
def week(project_id: str = Query(...), datum: date_type = Query(..., description="Een dag in de week"),
         current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    w, project, opstellers = _week(db, current_user, project_id, datum)
    return dict(_schoon(w), project={"id": project.id, "naam": project.name}, opstellers=opstellers)


@router.get("/week.pdf")
def week_pdf(project_id: str = Query(...), datum: date_type = Query(...),
             current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from export_huisstijl import bestandsnaam, klant_van, pdf_antwoord
    w, project, opstellers = _week(db, current_user, project_id, datum)
    inhoud = dr.pdf(w, klant=klant_van(current_user.organization), project_naam=project.name,
                    opstellers=opstellers)
    return pdf_antwoord(inhoud, bestandsnaam("Weekrapport", project.name, "week", w["week"],
                                             ext="pdf", met_datum=False))


@router.get("/week.xlsx")
def week_excel(project_id: str = Query(...), datum: date_type = Query(...),
               current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from export_huisstijl import bestandsnaam, excel_antwoord, excel_van, klant_van
    w, project, opstellers = _week(db, current_user, project_id, datum)
    co2 = w["co2"]
    ondertitel = (f"{project.name} · week {w['week']} · opsteller {opstellers} · "
                  f"CO2 materieel {co2['uitstoot_kg']:g} kg, reductie {co2['reductie_kg']:g} kg")
    inhoud = excel_van(klant_van(current_user.organization), "Weekrapport", dr.bladen(w),
                       ondertitel=ondertitel)
    return excel_antwoord(inhoud, bestandsnaam("Weekrapport", project.name, "week", w["week"],
                                               ext="xlsx", met_datum=False))

