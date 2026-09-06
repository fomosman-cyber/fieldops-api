"""Live schouw — camera aan, lopen, en zien wat er gevonden wordt.

Endpoints:
  GET    /api/schouw/catalogus              Detectieklassen, dragers, gebiedstypen
  GET    /api/schouw/drempels               Grenswaarden van deze organisatie
  PUT    /api/schouw/drempels               Grenswaarden vastleggen
  GET    /api/schouw/ritten                 Lijst van ritten
  POST   /api/schouw/ritten                 Rit starten
  GET    /api/schouw/ritten/{id}            Detail met waarnemingen en tussenstand
  POST   /api/schouw/ritten/{id}/frame      Eén live beeld + positie -> waarnemingen
  POST   /api/schouw/ritten/{id}/waarneming Handmatige waarneming toevoegen
  PATCH  /api/schouw/waarnemingen/{id}      Bevestigen, afwijzen of corrigeren
  POST   /api/schouw/ritten/{id}/afronden   Vastzetten en de beeldkwaliteit berekenen
  DELETE /api/schouw/ritten/{id}            Verwijderen

**Geen upload achteraf.** De inspecteur opent de camera in het portaal en het
scherm stuurt met een vast interval een beeld plus GPS-positie hierheen. Elk
beeld is een aparte aanroep die meteen antwoordt, zodat je ter plekke ziet wat
er gevonden is en het kunt corrigeren terwijl je er nog staat. Een videobestand
uploaden en achteraf verwerken zou betekenen dat je pas op kantoor merkt dat de
lens vies was.

**Kosten.** Elk beeld is een vision-aanroep. Het interval hoort dus ruim te
staan -- eens per paar seconden bij lopen, en bij rijden op afstand in plaats
van op tijd. De client bepaalt het tempo; de server telt alleen wat binnenkomt.

**De AI kijkt, jij beslist.** Een waarneming boven de zekerheidsdrempel telt
meteen mee; daaronder komt hij binnen als onbevestigd en telt hij pas na een
bevestiging. Afwijzen verwijdert niets: de waarneming blijft staan met
`afgewezen`, zodat het spoor van een gewijzigde score navolgbaar blijft.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import crow_schouw as cs
import schouw_vision as sv
from audit import log_action
from auth import get_current_user
from database import get_db
from models import Project, Schouwrit, Schouwwaarneming, User
from permissions import can_manage_toolbox, require_module

router = APIRouter(prefix="/api/schouw", tags=["Schouw"],
                   dependencies=[Depends(require_module("schouw"))])

# Alleen deze modus is gebouwd; zie de docstring van Schouwrit.
TOEGESTANE_PRIVACY_MODI = {"gericht"}


# ── Schemas ──────────────────────────────────────────────────────────

class RitIn(BaseModel):
    naam: Optional[str] = Field(default=None, max_length=255)
    gebied: Optional[str] = Field(default=None, max_length=255)
    gebiedstype: Optional[str] = None
    ambitie: Optional[str] = None
    project_id: Optional[str] = None
    privacy_modus: str = "gericht"


class FrameIn(BaseModel):
    """Eén beeld uit de live camera."""
    image_data_url: str = Field(..., min_length=32)
    lat: Optional[float] = None
    lng: Optional[float] = None
    nauwkeurigheid_m: Optional[float] = None
    straatnaam: Optional[str] = Field(default=None, max_length=255)
    # Bewaren van het beeld is optioneel: bij een lange rit is het veel data en
    # meestal is de waarneming genoeg. Bij een aandachtspunt wil je hem wel.
    bewaar_beeld: bool = False


class WaarnemingIn(BaseModel):
    detectieklasse: str
    drager: Optional[str] = None
    waarde: Optional[float] = None
    klasse_niveau: Optional[str] = Field(default=None, pattern=r"^(A\+|A|B|C|D)$")
    toelichting: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    straatnaam: Optional[str] = None
    photo_url: Optional[str] = None


class DrempelsIn(BaseModel):
    """Grenswaarden, per verschijnsel en optioneel per losse meetlat.

    Vorm: ``{"zwerfafval": {"A+": 0, "A": 2, "B": 5, "C": 10}}``. Een grens is de
    bovengrens van dat niveau; alles boven de laatste grens wordt D.
    """
    per_verschijnsel: dict[str, dict[str, float]] = Field(default_factory=dict)
    per_meetlat: dict[str, dict[str, float]] = Field(default_factory=dict)


class WaarnemingUpdate(BaseModel):
    bevestigd: Optional[bool] = None
    afgewezen: Optional[bool] = None
    drager: Optional[str] = None
    waarde: Optional[float] = None
    klasse_niveau: Optional[str] = Field(default=None, pattern=r"^(A\+|A|B|C|D)$")
    straatnaam: Optional[str] = None
    toelichting: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────

def _eis_beheer(current_user: User) -> None:
    if not can_manage_toolbox(current_user):
        raise HTTPException(
            status_code=403,
            detail="Alleen een beheerder of manager kan een schouw uitvoeren")


def _rit_of_404(db: Session, rit_id: str, current_user: User) -> Schouwrit:
    r = (db.query(Schouwrit)
           .filter(Schouwrit.id == rit_id,
                   Schouwrit.organization_id == current_user.organization_id)
           .first())
    if not r:
        raise HTTPException(status_code=404, detail="Schouwrit niet gevonden")
    return r


def _eis_bezig(r: Schouwrit) -> None:
    if r.status != "bezig":
        raise HTTPException(
            status_code=409,
            detail="Deze schouwrit is afgerond en kan niet meer worden gewijzigd")


def _telt_mee(w: Schouwwaarneming) -> bool:
    """Welke waarnemingen de score in gaan.

    Afgewezen nooit. Verder: bevestigd telt altijd, en onbevestigd alleen als de
    herkenning zeker genoeg was. Dat is dezelfde drempel als in schouw_vision,
    hier nog een keer toegepast omdat een waarneming na binnenkomst kan zijn
    bijgewerkt.
    """
    if w.afgewezen:
        return False
    if w.bevestigd:
        return True
    return (w.zekerheid or 0) >= sv.DREMPEL_AUTOMATISCH


def _drempels_voor(org) -> cs.Drempels:
    """Grenswaarden van de organisatie, uit haar eigen instellingen.

    Leeg bij een nieuwe organisatie, en dan komt elke meetlat terug onder
    `niet_beoordeeld`. Dat is bewust: geen verzonnen score is beter dan een
    score die niemand heeft afgesproken. Deze getallen staan in het bestek van
    de opdrachtgever en horen daar vandaan te komen.
    """
    import json
    ruw = getattr(org, "schouw_drempels", None)
    if not ruw:
        return cs.Drempels()
    try:
        data = json.loads(ruw)
    except Exception:  # noqa: BLE001 -- kapotte instelling mag de schouw niet slopen
        return cs.Drempels()
    return cs.Drempels(per_meetlat=data.get("per_meetlat") or {},
                       per_verschijnsel=data.get("per_verschijnsel") or {})


def _controleer_grenzen(blok: dict[str, dict[str, float]],
                        *, geldige_sleutels: set, wat: str) -> None:
    """Een reeks grenswaarden moet oplopend zijn van A+ naar D.

    Staat B lager dan A, dan is er geen enkele waarde die B oplevert en valt een
    heel niveau stilzwijgend weg. Beter meteen weigeren dan een schouw die
    maandenlang een niveau overslaat zonder dat iemand het merkt.
    """
    for sleutel, grenzen in (blok or {}).items():
        if sleutel not in geldige_sleutels:
            raise HTTPException(status_code=400,
                                detail=f"Onbekend {wat}: {sleutel}")
        vorige = None
        for klasse in cs.KLASSE_CODES:
            if klasse not in grenzen:
                continue
            waarde = grenzen[klasse]
            if waarde < 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"{sleutel}: grenswaarde voor {klasse} is negatief")
            if vorige is not None and waarde < vorige:
                raise HTTPException(
                    status_code=400,
                    detail=f"{sleutel}: de grens voor {klasse} ligt onder die van "
                           f"het strengere niveau. Dan is er geen enkele waarde "
                           f"die {klasse} oplevert.")
            vorige = waarde


def _tussenstand(r: Schouwrit) -> dict:
    tellend = [w for w in (r.waarnemingen or []) if _telt_mee(w)]
    waarden: dict[str, float] = {}
    niveaus: dict[str, str] = {}
    _rang = {"A+": 5, "A": 4, "B": 3, "C": 2, "D": 1}
    for w in tellend:
        if not w.meetlat:
            continue
        if w.waarde is not None:
            waarden[w.meetlat] = max(waarden.get(w.meetlat, 0.0), w.waarde)
        elif w.klasse_niveau:
            huidig = niveaus.get(w.meetlat)
            if huidig is None or _rang[w.klasse_niveau] < _rang[huidig]:
                niveaus[w.meetlat] = w.klasse_niveau

    return cs.beoordeel_vak(
        waarden, directe_klassen=niveaus,
        drempels=_drempels_voor(r.organization),
        gebiedstype=r.gebiedstype, ambitie=r.ambitie)


def _w_dict(w: Schouwwaarneming) -> dict:
    m = cs.meetlat(w.meetlat) if w.meetlat else None
    return {
        "id": w.id,
        "detectieklasse": w.detectieklasse,
        "drager": w.drager,
        "meetlat": w.meetlat,
        "naam": m["naam"] if m else (cs.DETECTIEKLASSEN.get(w.detectieklasse, {})
                                     .get("naam") or w.detectieklasse),
        "waarde": w.waarde,
        "klasse_niveau": w.klasse_niveau,
        "toelichting": w.toelichting,
        "zekerheid": w.zekerheid,
        "bron": w.bron,
        "bevestigd": w.bevestigd,
        "afgewezen": w.afgewezen,
        "telt_mee": _telt_mee(w),
        "lat": w.lat, "lng": w.lng, "straatnaam": w.straatnaam,
        "photo_url": w.photo_url,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


def _rit_dict(r: Schouwrit, *, detail: bool = False) -> dict:
    waarnemingen = list(r.waarnemingen or [])
    uit = {
        "id": r.id,
        "naam": r.naam,
        "gebied": r.gebied,
        "gebiedstype": r.gebiedstype,
        "gebiedstype_label": (cs.GEBIEDSTYPEN.get(r.gebiedstype or "") or {}).get("naam"),
        "ambitie": r.ambitie or cs.gangbare_ambitie(r.gebiedstype),
        "privacy_modus": r.privacy_modus,
        "status": r.status,
        "inspecteur_naam": r.inspecteur_naam,
        "frames": r.frames,
        "frames_onbruikbaar": r.frames_onbruikbaar,
        "waarnemingen_totaal": len(waarnemingen),
        "te_bevestigen": sum(1 for w in waarnemingen
                             if not w.bevestigd and not w.afgewezen
                             and (w.zekerheid or 0) < sv.DREMPEL_AUTOMATISCH),
        "beeldkwaliteit": r.beeldkwaliteit,
        "voldoet": r.voldoet,
        "gestart_op": r.gestart_op.isoformat() if r.gestart_op else None,
        "afgerond_op": r.afgerond_op.isoformat() if r.afgerond_op else None,
    }
    if r.status == "bezig":
        uit["tussenstand"] = _tussenstand(r)
    if detail:
        uit["waarnemingen"] = [_w_dict(w) for w in waarnemingen]
    return uit


def _data_url_naar_bytes(data_url: str) -> tuple[bytes, str]:
    import base64
    if not data_url.startswith("data:"):
        raise HTTPException(status_code=400, detail="Verwacht een data-URL")
    try:
        kop, payload = data_url.split(",", 1)
        media_type = kop.split(":", 1)[1].split(";", 1)[0] or "image/jpeg"
        return base64.b64decode(payload), media_type
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Beeld niet te lezen")


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/catalogus")
def catalogus():
    """Wat de client moet weten om een schouw te doen."""
    return {
        "versie": cs.SCHOUW_VERSION,
        "vision_versie": sv.SCHOUW_VISION_VERSION,
        "ai_beschikbaar": sv.is_geconfigureerd(),
        "gebiedstypen": cs.GEBIEDSTYPEN,
        "dragers": cs.DRAGERS,
        "detectieklassen": cs.detectieklassen(),
        "klassen": cs.KLASSE_CODES,
        "zekerheidsdrempel": sv.DREMPEL_AUTOMATISCH,
        "privacy_modi": sorted(TOEGESTANE_PRIVACY_MODI),
    }


@router.get("/drempels")
def drempels_lezen(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Wat deze organisatie heeft afgesproken, en wat er nog ontbreekt.

    `zonder_grenzen` is het nuttigste veld: die meetlatten leveren wel
    waarnemingen op maar geen score, en dat wil je zien voordat je het veld in
    gaat.
    """
    import json
    org = current_user.organization
    ruw = getattr(org, "schouw_drempels", None) if org else None
    data = {}
    if ruw:
        try:
            data = json.loads(ruw)
        except Exception:  # noqa: BLE001
            data = {}

    d = _drempels_voor(org)
    zonder = [m["code"] for m in cs.MEETLATTEN if not d.grenzen_voor(m["code"])]
    return {
        "per_verschijnsel": data.get("per_verschijnsel") or {},
        "per_meetlat": data.get("per_meetlat") or {},
        "verschijnselen": cs.VERSCHIJNSELEN,
        "klassen": cs.KLASSE_CODES,
        "meetlatten_totaal": len(cs.MEETLATTEN),
        "zonder_grenzen": zonder,
    }


@router.put("/drempels")
def drempels_vastleggen(
    payload: DrempelsIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Grenswaarden vastleggen. Alleen de org-admin.

    Deze getallen bepalen waar een aannemer op wordt afgerekend, dus ze horen
    niet bij iedereen die kan schouwen te liggen.
    """
    import json
    if not current_user.is_org_admin:
        raise HTTPException(
            status_code=403,
            detail="Alleen een beheerder kan de grenswaarden vastleggen")
    org = current_user.organization
    if org is None:
        raise HTTPException(status_code=404, detail="Geen organisatie gevonden")

    _controleer_grenzen(payload.per_verschijnsel,
                        geldige_sleutels=set(cs.VERSCHIJNSELEN), wat="verschijnsel")
    _controleer_grenzen(payload.per_meetlat,
                        geldige_sleutels={m["code"] for m in cs.MEETLATTEN},
                        wat="meetlat")

    org.schouw_drempels = json.dumps({
        "per_verschijnsel": payload.per_verschijnsel,
        "per_meetlat": payload.per_meetlat,
    })
    db.commit()
    log_action(db, request, current_user, action="schouw.drempels",
               entity_type="organization", entity_id=org.id,
               after={"verschijnselen": sorted(payload.per_verschijnsel),
                      "meetlatten": sorted(payload.per_meetlat)})
    return drempels_lezen(current_user=current_user, db=db)


@router.get("/ritten")
def lijst(
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Schouwrit).filter(
        Schouwrit.organization_id == current_user.organization_id)
    if status:
        q = q.filter(Schouwrit.status == status)
    return [_rit_dict(r) for r in
            q.order_by(Schouwrit.gestart_op.desc()).limit(200).all()]


@router.post("/ritten")
def start_rit(
    payload: RitIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)

    if payload.privacy_modus not in TOEGESTANE_PRIVACY_MODI:
        raise HTTPException(
            status_code=400,
            detail="Alleen gericht schouwen is beschikbaar. Doorlopend opnemen "
                   "vanuit een voertuig vraagt automatisch blurren van "
                   "gezichten en kentekens, en dat is nog niet gebouwd.")
    if payload.gebiedstype and payload.gebiedstype not in cs.GEBIEDSTYPEN:
        raise HTTPException(
            status_code=400,
            detail=f"Onbekend gebiedstype. Kies uit: {', '.join(cs.GEBIEDSTYPEN)}")
    if payload.ambitie and payload.ambitie not in cs.KLASSE_CODES:
        raise HTTPException(status_code=400, detail="Onbekend ambitieniveau")
    if payload.project_id:
        project = (db.query(Project)
                     .filter(Project.id == payload.project_id,
                             Project.organization_id == current_user.organization_id)
                     .first())
        if not project:
            raise HTTPException(status_code=404, detail="Project niet gevonden")

    naam = " ".join(x for x in (current_user.first_name, current_user.last_name) if x).strip()
    r = Schouwrit(
        organization_id=current_user.organization_id,
        project_id=payload.project_id,
        naam=payload.naam,
        gebied=payload.gebied,
        gebiedstype=payload.gebiedstype,
        ambitie=payload.ambitie,
        privacy_modus=payload.privacy_modus,
        inspecteur_id=current_user.id,
        inspecteur_naam=naam or current_user.email,
        status="bezig",
        created_by=current_user.id,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    log_action(db, request, current_user, action="schouw.start",
               entity_type="schouwrit", entity_id=r.id,
               after={"gebied": r.gebied, "gebiedstype": r.gebiedstype})
    return _rit_dict(r, detail=True)


@router.get("/ritten/{rit_id}")
def detail(
    rit_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _rit_dict(_rit_of_404(db, rit_id, current_user), detail=True)


@router.post("/ritten/{rit_id}/frame")
def frame(
    rit_id: str,
    payload: FrameIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Eén live beeld analyseren en de waarnemingen teruggeven.

    Antwoordt met wat er in dít beeld is gezien, plus de tussenstand van de rit.
    Zo kan het scherm meteen tonen wat er gevonden is terwijl de inspecteur er
    nog staat.
    """
    _eis_beheer(current_user)
    r = _rit_of_404(db, rit_id, current_user)
    _eis_bezig(r)

    beeld, media_type = _data_url_naar_bytes(payload.image_data_url)

    # De privacy-poort van schouw_vision. In de modus `gericht` bepaalt de
    # inspecteur zelf wat er in beeld komt; die verantwoordelijkheid is bij het
    # starten van de rit vastgelegd. Voor `rijdend` komt hier straks de blur.
    resultaat = sv.analyseer_frame(
        image_bytes=beeld, image_media_type=media_type,
        privacy_gecontroleerd=(r.privacy_modus == "gericht"),
        context=(f"Gebied: {r.gebied}" if r.gebied else None))

    r.frames = (r.frames or 0) + 1
    if not resultaat.get("bruikbaar"):
        r.frames_onbruikbaar = (r.frames_onbruikbaar or 0) + 1

    nieuw: list[Schouwwaarneming] = []
    for w in (resultaat.get("gebied") or []):
        rij = Schouwwaarneming(
            schouwrit_id=r.id,
            organization_id=current_user.organization_id,
            lat=payload.lat, lng=payload.lng,
            nauwkeurigheid_m=payload.nauwkeurigheid_m,
            straatnaam=payload.straatnaam,
            detectieklasse=w.get("klasse"),
            drager=w.get("drager"),
            meetlat=w.get("meetlat"),
            waarde=w.get("waarde"),
            klasse_niveau=w.get("klasse_niveau"),
            toelichting=w.get("toelichting"),
            zekerheid=w.get("zekerheid"),
            bron="ai",
            photo_url=payload.image_data_url if payload.bewaar_beeld else None,
            model_id=resultaat.get("_model_id"),
            vision_versie=resultaat.get("_versie"),
        )
        db.add(rij)
        nieuw.append(rij)

    db.commit()
    db.refresh(r)
    return {
        "bruikbaar": resultaat.get("bruikbaar"),
        "reden_onbruikbaar": resultaat.get("reden_onbruikbaar"),
        "gevonden": [_w_dict(w) for w in nieuw],
        "objecten": resultaat.get("objecten") or [],
        "rit": _rit_dict(r),
    }


@router.post("/ritten/{rit_id}/waarneming")
def handmatige_waarneming(
    rit_id: str,
    payload: WaarnemingIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Zelf iets vastleggen wat de camera niet ziet.

    Uitwerpselen bijvoorbeeld, of iets waar de AI overheen keek. Komt binnen als
    `bron = mens` en meteen bevestigd -- een inspecteur die iets intikt hoeft
    zichzelf niet te bevestigen.
    """
    _eis_beheer(current_user)
    r = _rit_of_404(db, rit_id, current_user)
    _eis_bezig(r)

    if payload.detectieklasse not in cs.DETECTIEKLASSEN:
        raise HTTPException(
            status_code=400,
            detail=f"Onbekende detectieklasse: {payload.detectieklasse}")
    try:
        code = cs.meetlat_voor(payload.detectieklasse, payload.drager)
    except cs.OnbekendeDrager as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    w = Schouwwaarneming(
        schouwrit_id=r.id,
        organization_id=current_user.organization_id,
        lat=payload.lat, lng=payload.lng, straatnaam=payload.straatnaam,
        detectieklasse=payload.detectieklasse,
        drager=payload.drager,
        meetlat=code,
        waarde=payload.waarde,
        klasse_niveau=payload.klasse_niveau,
        toelichting=payload.toelichting,
        zekerheid=1.0,
        bron="mens",
        bevestigd=True,
        bevestigd_door_id=current_user.id,
        photo_url=payload.photo_url,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return _w_dict(w)


@router.patch("/waarnemingen/{waarneming_id}")
def waarneming_bijwerken(
    waarneming_id: str,
    payload: WaarnemingUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bevestigen, afwijzen of corrigeren.

    Vult iemand een ontbrekende drager in, dan wordt de meetlat opnieuw bepaald:
    dat is precies het geval waarvoor de beoordeellijst bestaat.
    """
    _eis_beheer(current_user)
    w = (db.query(Schouwwaarneming)
           .filter(Schouwwaarneming.id == waarneming_id,
                   Schouwwaarneming.organization_id == current_user.organization_id)
           .first())
    if not w:
        raise HTTPException(status_code=404, detail="Waarneming niet gevonden")
    _eis_bezig(w.rit)

    velden = payload.model_dump(exclude_unset=True)
    if "drager" in velden and w.detectieklasse:
        try:
            w.meetlat = cs.meetlat_voor(w.detectieklasse, velden["drager"])
        except cs.OnbekendeDrager as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    for k, v in velden.items():
        setattr(w, k, v)
    if velden.get("bevestigd"):
        w.afgewezen = False
        w.bevestigd_door_id = current_user.id
    if velden.get("afgewezen"):
        w.bevestigd = False

    db.commit()
    db.refresh(w)
    return _w_dict(w)


@router.post("/ritten/{rit_id}/afronden")
def afronden(
    rit_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    r = _rit_of_404(db, rit_id, current_user)
    _eis_bezig(r)

    uitslag = _tussenstand(r)
    r.beeldkwaliteit = uitslag.get("beeldkwaliteit")
    r.voldoet = uitslag.get("voldoet")
    r.schouw_versie = uitslag.get("versie")
    r.status = "afgerond"
    r.afgerond_op = datetime.now(timezone.utc)
    db.commit()
    db.refresh(r)

    log_action(db, request, current_user, action="schouw.afronden",
               entity_type="schouwrit", entity_id=r.id,
               after={"beeldkwaliteit": r.beeldkwaliteit,
                      "frames": r.frames,
                      "waarnemingen": len(r.waarnemingen or [])})
    return {**_rit_dict(r, detail=True), "uitslag": uitslag}


@router.delete("/ritten/{rit_id}")
def verwijderen(
    rit_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    r = _rit_of_404(db, rit_id, current_user)
    db.delete(r)
    db.commit()
    log_action(db, request, current_user, action="schouw.delete",
               entity_type="schouwrit", entity_id=rit_id)
    return {"status": "verwijderd"}
