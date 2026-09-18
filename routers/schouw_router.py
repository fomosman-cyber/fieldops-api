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

import json
import math
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import crow_schouw as cs
import crow_wegschade as cw
import schouw_vision as sv
from audit import log_action
from auth import get_current_user
from database import get_db
from models import Melding, Project, Schouwrit, Schouwwaarneming, User
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
    schade = cw.zoek(w.crow_verharding, w.crow_schadebeeld) if w.crow_schadebeeld else None
    return {
        "id": w.id,
        "detectieklasse": w.detectieklasse,
        "drager": w.drager,
        "meetlat": w.meetlat,
        "naam": schade["naam"] if schade else (
            m["naam"] if m else (cs.DETECTIEKLASSEN.get(w.detectieklasse, {})
                                 .get("naam") or w.detectieklasse)),
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
        # Wegschade. `kader` is waar het scherm het rode vlak tekent.
        "wegschade": bool(w.crow_schadebeeld),
        "verharding": w.crow_verharding,
        "schadegroep": w.crow_schadegroep,
        "schadebeeld": w.crow_schadebeeld,
        "ernst": w.crow_ernst,
        "omvang": w.crow_omvang,
        "klasse_indicatie": cw.klasse_indicatie(w.crow_ernst, w.crow_omvang),
        "kader": _kader_lezen(w.kader),
        "keer_gezien": w.keer_gezien or 1,
        "melding_id": w.melding_id,
    }


# ── Dezelfde schade in opeenvolgende beelden ─────────────────────────
#
# Lopend met een beeld per paar seconden staat dezelfde kuil in twee, drie
# beelden achter elkaar. Dat is één kuil. De regel is bewust voorzichtig:
# liever een dubbele regel in de lijst dan een schade die wegvalt omdat hij
# met zijn buurman is samengevoegd. Twee kuilen tien meter uit elkaar mogen
# nooit één worden.
#
# Een nieuwe schade hoort bij een bestaande als alle drie waar zijn:
#   - zelfde verharding en zelfde schadebeeld, in dezelfde ronde;
#   - de bestaande was nog in beeld: laatst gezien hooguit DUBBEL_TIJD_S geleden;
#   - de plek klopt: binnen de GPS-straal, of er is geen GPS om te vergelijken.
# Schades uit één en hetzelfde beeld worden nooit samengevoegd: dat zijn er
# gewoon twee.

DUBBEL_TIJD_S = 15
DUBBEL_STRAAL_M = 6.0            # als de nauwkeurigheid onbekend is
DUBBEL_STRAAL_MIN_M = 4.0
DUBBEL_STRAAL_MAX_M = 12.0       # daarboven is GPS te grof om op te vertrouwen


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _afstand_m(lat1, lng1, lat2, lng2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _straal_m(n1: Optional[float], n2: Optional[float]) -> float:
    bekend = [n for n in (n1, n2) if n]
    if not bekend:
        return DUBBEL_STRAAL_M
    return min(DUBBEL_STRAAL_MAX_M, max(DUBBEL_STRAAL_MIN_M, max(bekend)))


def _kandidaten(db: Session, r: Schouwrit, schadebeelden: set[str],
                nu: datetime) -> list[Schouwwaarneming]:
    """Wegschade in deze ronde die nog in beeld kan zijn. Opgehaald vóór het
    verwerken van het nieuwe beeld, zodat schades uit hetzelfde beeld elkaar
    niet kunnen vinden."""
    if not schadebeelden:
        return []
    rijen = (db.query(Schouwwaarneming)
               .filter(Schouwwaarneming.schouwrit_id == r.id,
                       Schouwwaarneming.crow_schadebeeld.in_(schadebeelden))
               .all())
    uit = []
    for w in rijen:
        laatst = _utc(w.laatst_gezien_op) or _utc(w.created_at)
        if laatst and (nu - laatst).total_seconds() <= DUBBEL_TIJD_S:
            uit.append(w)
    return uit


def _zoek_dubbel(kandidaten: list[Schouwwaarneming], w: dict, payload,
                 gebruikt: set[str]) -> Optional[Schouwwaarneming]:
    beste, beste_afstand = None, None
    for k in kandidaten:
        if k.id in gebruikt:
            continue
        if (k.crow_verharding, k.crow_schadebeeld) != (w.get("verharding"), w.get("schadebeeld")):
            continue
        heeft_gps = None not in (k.lat, k.lng, payload.lat, payload.lng)
        if heeft_gps:
            afstand = _afstand_m(k.lat, k.lng, payload.lat, payload.lng)
            if afstand > _straal_m(k.nauwkeurigheid_m, payload.nauwkeurigheid_m):
                continue
        else:
            afstand = 0.0
        if beste is None or afstand < beste_afstand:
            beste, beste_afstand = k, afstand
    return beste


def _voeg_samen(bestaand: Schouwwaarneming, w: dict, payload, foto, resultaat: dict,
                nu: datetime) -> bool:
    """Tel het nieuwe beeld bij de bestaande schade. Geeft True als het nieuwe
    beeld het bewijs wordt (en het scherm het dus mag onthouden).

    Het beeld waar het model het zekerst was, wordt het bewijs: ernst, kader en
    foto komen daar samen vandaan, zodat ze bij elkaar passen. Heeft een mens
    de schade al bevestigd of afgewezen, dan verandert de camera er niets meer
    aan -- alleen de teller loopt op.
    """
    bestaand.keer_gezien = (bestaand.keer_gezien or 1) + 1
    bestaand.laatst_gezien_op = nu
    if bestaand.bevestigd or bestaand.afgewezen:
        return False
    if (w.get("zekerheid") or 0) <= (bestaand.zekerheid or 0):
        return False
    bestaand.crow_ernst = w.get("ernst")
    bestaand.crow_omvang = w.get("omvang")
    bestaand.klasse_niveau = w.get("klasse_niveau")
    bestaand.kader = json.dumps(w["kader"]) if w.get("kader") else None
    bestaand.zekerheid = w.get("zekerheid")
    bestaand.toelichting = w.get("toelichting")
    bestaand.photo_url = foto()
    if payload.lat is not None and payload.lng is not None:
        bestaand.lat, bestaand.lng = payload.lat, payload.lng
        bestaand.nauwkeurigheid_m = payload.nauwkeurigheid_m
    bestaand.model_id = resultaat.get("_model_id")
    bestaand.vision_versie = resultaat.get("_versie")
    return True


def _kader_lezen(tekst: Optional[str]) -> Optional[list[float]]:
    if not tekst:
        return None
    try:
        k = json.loads(tekst)
    except (TypeError, ValueError):
        return None
    return k if isinstance(k, list) and len(k) == 4 else None


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
        "wegschade": {
            "versie": cw.WEGSCHADE_VERSIE,
            "verhardingen": {k: v["naam"] for k, v in cw.VERHARDINGEN.items()},
            "ernst": cw.ERNST,
            "schadebeelden": [
                {k: sb[k] for k in ("verharding", "schadegroep", "schadebeeld", "naam")}
                for sb in cw.schadebeelden()],
        },
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

    # De zekerste eerst: die mag als eerste een bestaande schade claimen.
    wegschade = sorted(resultaat.get("wegschade") or [],
                       key=lambda w: -(w.get("zekerheid") or 0))
    nu = datetime.now(timezone.utc)

    # Een beeld met schade erop is bewijs, en zonder beeld kan het scherm het
    # rode vak later niet meer laten zien. Dan bewaren we hem -- één keer, via
    # de opslag voor foto's, en pas als er echt iets is dat hem gebruikt.
    _foto: dict = {}

    def foto() -> Optional[str]:
        if "url" not in _foto:
            from photo_storage import maybe_offload
            _foto["url"] = maybe_offload(payload.image_data_url,
                                         organization_id=current_user.organization_id,
                                         kind="schouw") or payload.image_data_url
        return _foto["url"]

    kandidaten = _kandidaten(db, r, {w.get("schadebeeld") for w in wegschade}, nu)
    gebruikt: set[str] = set()
    samengevoegd: list[tuple[Schouwwaarneming, bool, dict]] = []
    nieuwe_schade: list[dict] = []
    for w in wegschade:
        dubbel = _zoek_dubbel(kandidaten, w, payload, gebruikt)
        if dubbel is None:
            nieuwe_schade.append(w)
            continue
        gebruikt.add(dubbel.id)
        samengevoegd.append((dubbel, _voeg_samen(dubbel, w, payload, foto, resultaat, nu), w))

    nieuw: list[Schouwwaarneming] = []
    in_beeld_nieuw: list[tuple[Schouwwaarneming, dict]] = []
    for w in (resultaat.get("gebied") or []) + nieuwe_schade:
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
            photo_url=foto() if (payload.bewaar_beeld or wegschade) else None,
            model_id=resultaat.get("_model_id"),
            vision_versie=resultaat.get("_versie"),
            crow_verharding=w.get("verharding"),
            crow_schadegroep=w.get("schadegroep"),
            crow_schadebeeld=w.get("schadebeeld"),
            crow_ernst=w.get("ernst"),
            crow_omvang=w.get("omvang"),
            kader=json.dumps(w["kader"]) if w.get("kader") else None,
            keer_gezien=1,
            laatst_gezien_op=nu,
        )
        db.add(rij)
        nieuw.append(rij)
        if w.get("schadebeeld"):
            in_beeld_nieuw.append((rij, w))

    db.commit()
    db.refresh(r)

    # Voor het scherm: alles wat in DIT beeld rood moet worden, met het kader
    # uit dit beeld -- ook bij een samengevoegde schade, waarvan het bewaarde
    # kader bij een ander beeld hoort.
    in_beeld = [
        {"id": rij.id, "naam": _w_dict(rij)["naam"], "ernst": w.get("ernst"),
         "kader": w.get("kader")}
        for rij, w in in_beeld_nieuw + [(d, w) for d, _, w in samengevoegd]
        if w.get("kader")
    ]
    gevonden = [dict(_w_dict(w), samengevoegd=False, beeld_vervangen=True) for w in nieuw]
    gevonden += [dict(_w_dict(d), samengevoegd=True, beeld_vervangen=v)
                 for d, v, _ in samengevoegd]
    return {
        "bruikbaar": resultaat.get("bruikbaar"),
        "reden_onbruikbaar": resultaat.get("reden_onbruikbaar"),
        "gevonden": gevonden,
        "in_beeld": in_beeld,
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


# Hoe een schade als melding binnenkomt. Kritiek laten we aan een mens: dat
# is een oordeel over gevaar, en dat ziet de camera niet.
_ERNST_NAAR_PRIORITEIT = {"E": "hoog", "M": "normaal", "L": "laag"}
_CATEGORIE = {"asfalt": "Wegdek", "beton": "Wegdek", "elementen": "Bestrating"}


@router.post("/waarnemingen/{waarneming_id}/melding")
def maak_melding(
    waarneming_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Een schade uit de schouw doorzetten als melding.

    Op de knop drukken is zelf een menselijk oordeel, dus de waarneming wordt
    daarmee ook bevestigd. De melding loopt via dezelfde route als elke andere
    melding: foto-opslag, koppeling aan het dichtstbijzijnde object, audit en
    werkdagboek. Twee keer drukken levert geen tweede melding op.

    Ook na het afronden van de ronde: nakijken op kantoor is het normale moment.
    """
    _eis_beheer(current_user)
    w = (db.query(Schouwwaarneming)
           .filter(Schouwwaarneming.id == waarneming_id,
                   Schouwwaarneming.organization_id == current_user.organization_id)
           .first())
    if not w:
        raise HTTPException(status_code=404, detail="Waarneming niet gevonden")
    if w.melding_id:
        bestaat = (db.query(Melding.id)
                     .filter(Melding.id == w.melding_id,
                             Melding.organization_id == current_user.organization_id)
                     .first())
        if bestaat:
            return {"waarneming": _w_dict(w), "melding_id": w.melding_id, "bestond_al": True}
    if w.afgewezen:
        raise HTTPException(status_code=409,
                            detail="Deze schade is afgewezen; zet hem eerst terug als je er een melding van wilt")
    schade = cw.zoek(w.crow_verharding, w.crow_schadebeeld)
    if not schade:
        raise HTTPException(status_code=400,
                            detail="Alleen schade aan de verharding kan vanuit de schouw een melding worden")

    r = w.rit
    plek = w.straatnaam or (r.gebied if r else None)
    datum = (_utc(w.created_at) or datetime.now(timezone.utc)).strftime("%d-%m-%Y")
    delen = [f"{schade['naam']} ({schade['verharding_naam'].lower()})"
             + (f", ernst {w.crow_ernst}" if w.crow_ernst else "") + "."]
    if w.toelichting:
        delen.append(w.toelichting.rstrip(".") + ".")
    delen.append(f"Vastgelegd met de schouwcamera op {datum}"
                 + (f" tijdens de ronde '{r.gebied}'" if r and r.gebied else "")
                 + (f", {w.keer_gezien} keer in beeld" if (w.keer_gezien or 1) > 1 else "")
                 + ". De plek staat met een rood vak in de schouwronde.")

    from routers.meldingen_router import create_melding
    from schemas import MeldingCreate

    # Omvang laten we leeg: de camera zag een stuk van het vak, niet het vak.
    # De klasse (M2 enz.) vult de inspecteur in de melding aan.
    data = MeldingCreate(
        title=(schade["naam"] + (f" \u2014 {plek}" if plek else ""))[:255],
        description=" ".join(delen),
        category=_CATEGORIE.get(w.crow_verharding),
        priority=_ERNST_NAAR_PRIORITEIT.get(w.crow_ernst, "normaal"),
        lat=w.lat, lng=w.lng,
        photo_url=w.photo_url,
        asset_id=w.asset_id,
        project_id=r.project_id if r else None,
        crow_schadegroep=w.crow_schadegroep,
        crow_schadebeeld=w.crow_schadebeeld,
        crow_ernst=w.crow_ernst,
    )
    melding = create_melding(data, request, current_user, db)
    melding_id = melding["id"] if isinstance(melding, dict) else melding.id

    w.melding_id = melding_id
    w.bevestigd = True
    w.afgewezen = False
    w.bevestigd_door_id = current_user.id
    db.commit()
    db.refresh(w)
    log_action(db, request, current_user, action="schouw.melding",
               entity_type="schouwwaarneming", entity_id=w.id,
               after={"melding_id": melding_id, "schadebeeld": w.crow_schadebeeld,
                      "ernst": w.crow_ernst})
    return {"waarneming": _w_dict(w), "melding_id": melding_id, "bestond_al": False}


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
