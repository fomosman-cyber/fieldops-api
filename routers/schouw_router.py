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

import base64
import json
import math
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import crow_schouw as cs
import crow_wegschade as cw
import schouw_instellingen as si
import schouw_leren
import schouw_vision as sv
from audit import log_action
from auth import get_current_user
from database import get_db
from models import Melding, Project, SchouwBeeld, SchouwOpname, Schouwrit, Schouwwaarneming, User
from models import generate_uuid
from permissions import can_manage_toolbox, is_org_admin, require_module

router = APIRouter(prefix="/api/schouw", tags=["Schouw"],
                   dependencies=[Depends(require_module("schouw"))])

# Zie de docstring van Schouwrit. Rijdend kan sinds het toestel mensen en
# voertuigen verpixelt; een rijdende schouw neemt op en analyseert daarna.
TOEGESTANE_PRIVACY_MODI = {"gericht", "rijdend"}


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
    # Het toestel heeft mensen en voertuigen verpixeld vóór het versturen.
    # Alleen zulke beelden worden lesmateriaal.
    geanonimiseerd: bool = False
    verpixeld: Optional[int] = Field(default=None, ge=0, le=500)
    breedte: Optional[int] = Field(default=None, ge=1, le=10000)
    hoogte: Optional[int] = Field(default=None, ge=1, le=10000)


class OpnameIn(BaseModel):
    """Eén beeld uit een rijdende schouw: alleen opnemen, analyseren doet de
    server daarna."""
    image_data_url: str = Field(..., min_length=32)
    volgnummer: int = Field(..., ge=0, le=1_000_000)
    gemaakt_op: Optional[datetime] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    nauwkeurigheid_m: Optional[float] = None
    koers: Optional[float] = Field(default=None, ge=0, le=360)
    snelheid_ms: Optional[float] = Field(default=None, ge=0, le=100)
    breedte: Optional[int] = Field(default=None, ge=1, le=10000)
    hoogte: Optional[int] = Field(default=None, ge=1, le=10000)
    verpixeld: Optional[int] = Field(default=None, ge=0, le=500)
    geanonimiseerd: bool = False
    # Waar in het beeld het wegdek begint; de server snijdt het daar uit.
    wegdek_boven: float = Field(default=0.4, ge=0.0, le=0.9)


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
    # Waarom het geen schade was (schouw_leren.AFWIJS_REDENEN).
    afwijs_reden: Optional[str] = None
    # Een ander schadebeeld of een andere ernst dan de herkenning zei.
    verharding: Optional[str] = None
    schadebeeld: Optional[str] = None
    ernst: Optional[str] = Field(default=None, pattern=r"^(L|M|E)$")
    omvang: Optional[str] = Field(default=None, pattern=r"^(1|2|3)$")
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


def _drempel(org) -> float:
    """Vanaf welke zekerheid een waarneming vanzelf meetelt, voor deze organisatie."""
    return si.lees(org)["drempel_automatisch"]


def _telt_mee(w: Schouwwaarneming, drempel: Optional[float] = None) -> bool:
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
    if drempel is None:
        drempel = _drempel(w.rit.organization if w.rit else None)
    return (w.zekerheid or 0) >= drempel


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
    drempel = _drempel(r.organization)
    tellend = [w for w in (r.waarnemingen or []) if _telt_mee(w, drempel)]
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
        "afwijs_reden": w.afwijs_reden,
        "oorspronkelijk_schadebeeld": w.oorspronkelijk_schadebeeld,
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
        # abs(): bij een rijdende schouw worden beelden na elkaar geanalyseerd,
        # niet per se in de volgorde waarin ze zijn gemaakt.
        if laatst and abs((nu - laatst).total_seconds()) <= DUBBEL_TIJD_S:
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
        lat, lng = _plek(w, payload)
        heeft_gps = None not in (k.lat, k.lng, lat, lng)
        if heeft_gps:
            afstand = _afstand_m(k.lat, k.lng, lat, lng)
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
    vorige = _utc(bestaand.laatst_gezien_op)
    bestaand.laatst_gezien_op = nu if vorige is None or nu > vorige else vorige
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
    lat, lng = _plek(w, payload)
    if lat is not None and lng is not None:
        bestaand.lat, bestaand.lng = lat, lng
        bestaand.nauwkeurigheid_m = payload.nauwkeurigheid_m
    bestaand.model_id = resultaat.get("_model_id")
    bestaand.vision_versie = resultaat.get("_versie")
    return True


def _plek(w: dict, payload) -> tuple[Optional[float], Optional[float]]:
    """Waar een schade ligt: de geschatte plek op de weg als die er is (rijdend,
    zie _projecteer), anders de plek van het toestel."""
    if w.get("_lat") is not None and w.get("_lng") is not None:
        return w["_lat"], w["_lng"]
    return payload.lat, payload.lng


# Een schade in beeld ligt niet waar de auto is, maar een stukje ervoor. Hoe
# ver, volgt uit hoe laag hij in het beeld staat: onderaan is dichtbij. Een
# schatting met een camera op ~1,3 m hoogte en ~50 graden beeldhoek in de
# hoogte, met de horizon bij de wegdeklijn. Grof, maar een stuk beter dan
# elke schade op de plek van de auto: daar lag hij juist niet.
CAMERA_HOOGTE_M = 1.3
BEELDHOEK_HOOGTE_GRADEN = 50.0
MAX_VOORUIT_M = 30.0


def _projecteer(lat: Optional[float], lng: Optional[float], koers: Optional[float],
                snelheid_ms: Optional[float], kader: Optional[list[float]],
                wegdek_boven: float) -> tuple[Optional[float], Optional[float]]:
    if None in (lat, lng, koers) or not kader or (snelheid_ms or 0) < 1.5:
        return lat, lng          # stilstaand zegt de koers niets
    midden = (kader[1] + kader[3]) / 2
    hoek = (midden - wegdek_boven) * BEELDHOEK_HOOGTE_GRADEN
    afstand = MAX_VOORUIT_M if hoek <= 2.5 else min(
        MAX_VOORUIT_M, CAMERA_HOOGTE_M / math.tan(math.radians(hoek)))
    richting = math.radians(koers)
    dlat = afstand * math.cos(richting) / 111_320.0
    dlng = afstand * math.sin(richting) / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
    return round(lat + dlat, 7), round(lng + dlng, 7)


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
        "frames_geanonimiseerd": r.frames_geanonimiseerd or 0,
        "waarnemingen_totaal": len(waarnemingen),
        "te_bevestigen": sum(1 for w in waarnemingen
                             if not w.bevestigd and not w.afgewezen
                             and (w.zekerheid or 0) < _drempel(r.organization)),
        # Wegschade waar nog geen mens naar heeft gekeken: het beoordeelscherm.
        "schades_te_beoordelen": sum(1 for w in waarnemingen
                                     if w.crow_schadebeeld and not w.bevestigd and not w.afgewezen),
        "beeldkwaliteit": r.beeldkwaliteit,
        "voldoet": r.voldoet,
        "gestart_op": r.gestart_op.isoformat() if r.gestart_op else None,
        "afgerond_op": r.afgerond_op.isoformat() if r.afgerond_op else None,
    }
    if r.privacy_modus == "rijdend":
        opnames = list(r.opnames or [])
        uit["opnames"] = {
            "totaal": len(opnames),
            "wacht": sum(1 for o in opnames if o.status in ("wacht", "bezig")),
            "klaar": sum(1 for o in opnames if o.status == "klaar"),
            "mislukt": sum(1 for o in opnames if o.status == "mislukt"),
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
        "afwijs_redenen": schouw_leren.AFWIJS_REDENEN,
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


def _verwerk_resultaat(db: Session, r: Schouwrit, resultaat: dict, payload, foto,
                       organization_id: str, nu: datetime):
    """Eén beoordeeld beeld verwerken: tellers, dezelfde schade samenvoegen,
    nieuwe waarnemingen, wat er in beeld is, en lesmateriaal. Gedeeld door de
    live schouw en de analyse van opnames, zodat beide hetzelfde doen.

    `payload` levert plek en beeldgegevens (lat, lng, nauwkeurigheid_m,
    straatnaam, bewaar_beeld, geanonimiseerd, breedte, hoogte, verpixeld);
    `foto()` geeft de URL van het beeld; `nu` is het moment van het beeld.
    """
    r.frames = (r.frames or 0) + 1
    if not resultaat.get("bruikbaar"):
        r.frames_onbruikbaar = (r.frames_onbruikbaar or 0) + 1
    if payload.geanonimiseerd:
        r.frames_geanonimiseerd = (r.frames_geanonimiseerd or 0) + 1

    # De zekerste eerst: die mag als eerste een bestaande schade claimen.
    wegschade = sorted(resultaat.get("wegschade") or [],
                       key=lambda w: -(w.get("zekerheid") or 0))

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
    gebied_in_beeld: list[tuple[Schouwwaarneming, dict]] = []
    for w in (resultaat.get("gebied") or []) + nieuwe_schade:
        rij = Schouwwaarneming(
            schouwrit_id=r.id,
            organization_id=organization_id,
            lat=_plek(w, payload)[0], lng=_plek(w, payload)[1],
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
        elif w.get("kader"):
            gebied_in_beeld.append((rij, w))

    db.flush()      # ids voor de nieuwe waarnemingen

    # Voor het scherm: alles wat in DIT beeld een kader krijgt, met het kader
    # uit dit beeld -- ook bij een samengevoegde schade, waarvan het bewaarde
    # kader bij een ander beeld hoort. Schade wordt rood; de rest krijgt een
    # rand in de kleur van het niveau.
    in_beeld = [
        {"soort": "schade", "id": rij.id, "label": w.get("schadebeeld"),
         "naam": _w_dict(rij)["naam"], "ernst": w.get("ernst"),
         "kader": w.get("kader"), "zekerheid": w.get("zekerheid")}
        for rij, w in in_beeld_nieuw + [(d, w) for d, _, w in samengevoegd]
        if w.get("kader")
    ]
    in_beeld += [
        {"soort": "gebied", "id": rij.id, "label": w.get("klasse"),
         "naam": cs.DETECTIEKLASSEN.get(w.get("klasse"), {}).get("naam") or w.get("klasse"),
         "niveau": w.get("klasse_niveau"), "waarde": w.get("waarde"),
         "kader": w.get("kader"), "zekerheid": w.get("zekerheid")}
        for rij, w in gebied_in_beeld
    ]
    in_beeld += [
        {"soort": "object", "id": None, "label": o.get("type"), "naam": o.get("naam"),
         "niveau": o.get("niveau"), "kader": o.get("kader"), "zekerheid": o.get("zekerheid")}
        for o in (resultaat.get("objecten") or []) if o.get("kader")
    ]

    # Lesmateriaal: alleen geanonimiseerde beelden, en dan elk beeld met
    # schade (dat bewaren we toch al als bewijs) plus een steekproef van de
    # rest, zodat het model ook leert hoe een straat zonder schade eruitziet.
    if payload.geanonimiseerd and in_beeld and (
            wegschade or payload.bewaar_beeld or r.frames % LEERBEELD_ELKE == 0):
        beeld = SchouwBeeld(
            id=generate_uuid(), schouwrit_id=r.id,
            organization_id=organization_id,
            photo_url=foto(), breedte=payload.breedte, hoogte=payload.hoogte,
            verpixeld=payload.verpixeld or 0, lat=payload.lat, lng=payload.lng,
            kaders=json.dumps([
                {k: i[k] for k in ("soort", "label", "naam", "niveau", "ernst",
                                   "kader", "zekerheid") if i.get(k) is not None}
                | ({"waarneming_id": i["id"]} if i.get("id") else {})
                for i in in_beeld]),
            model_id=resultaat.get("_model_id"), vision_versie=resultaat.get("_versie"))
        db.add(beeld)
        for rij in nieuw:
            rij.beeld_id = beeld.id
        for d, vervangen, _ in samengevoegd:
            if vervangen:
                d.beeld_id = beeld.id

    return nieuw, samengevoegd, in_beeld


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
    if r.privacy_modus == "rijdend":
        raise HTTPException(status_code=400, detail=(
            "Een rijdende schouw neemt op en analyseert daarna; stuur beelden naar /opnames"))

    beeld, media_type = _data_url_naar_bytes(payload.image_data_url)

    # De privacy-poort van schouw_vision. In de modus `gericht` bepaalt de
    # inspecteur zelf wat er in beeld komt; die verantwoordelijkheid is bij het
    # starten van de rit vastgelegd. Een rijdende schouw komt hier niet (zie
    # /opnames): die verstuurt alleen op het toestel verpixelde beelden.
    resultaat = sv.analyseer_frame(
        image_bytes=beeld, image_media_type=media_type,
        privacy_gecontroleerd=(r.privacy_modus == "gericht"),
        context=(f"Gebied: {r.gebied}" if r.gebied else None),
        instellingen=si.lees(r.organization),
        voorbeelden=schouw_leren.voorbeelden(db, current_user.organization_id))

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

    nieuw, samengevoegd, in_beeld = _verwerk_resultaat(
        db, r, resultaat, payload, foto, current_user.organization_id,
        datetime.now(timezone.utc))

    db.commit()
    db.refresh(r)
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


MAX_OPNAME_BYTES = 4_000_000


@router.post("/ritten/{rit_id}/opnames")
def opname(
    rit_id: str,
    payload: OpnameIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Eén beeld uit een rijdende schouw opnemen. Antwoordt meteen; de
    analyse volgt op de achtergrond (schouw_analyse). Een beeld dat na een
    haperende verbinding opnieuw binnenkomt, telt niet dubbel."""
    import photo_storage
    import schouw_analyse

    _eis_beheer(current_user)
    r = _rit_of_404(db, rit_id, current_user)
    if r.privacy_modus != "rijdend":
        raise HTTPException(status_code=400, detail="Opnames horen bij een rijdende schouw")
    # Ook na afronden: wat nog onderweg was, hoort bij deze ronde.
    if r.status not in ("bezig", "afgerond"):
        raise HTTPException(status_code=409, detail="Deze schouwrit neemt geen beelden meer aan")
    if not payload.geanonimiseerd:
        raise HTTPException(status_code=400, detail=(
            "Een rijdende schouw verstuurt alleen beelden waarin mensen en voertuigen "
            "op het toestel zijn verpixeld"))
    bestaand = (db.query(SchouwOpname)
                  .filter(SchouwOpname.schouwrit_id == r.id,
                          SchouwOpname.volgnummer == payload.volgnummer)
                  .first())
    if bestaand:
        return {"id": bestaand.id, "status": bestaand.status, "dubbel": True}

    beeld, _media = _data_url_naar_bytes(payload.image_data_url)
    if len(beeld) > MAX_OPNAME_BYTES:
        raise HTTPException(status_code=413, detail="Beeld te groot (max 4 MB)")
    foto = photo_storage.maybe_offload(payload.image_data_url,
                                       organization_id=current_user.organization_id,
                                       kind="schouw") or payload.image_data_url
    o = SchouwOpname(
        schouwrit_id=r.id, organization_id=current_user.organization_id,
        volgnummer=payload.volgnummer,
        gemaakt_op=_utc(payload.gemaakt_op) or datetime.now(timezone.utc),
        lat=payload.lat, lng=payload.lng, nauwkeurigheid_m=payload.nauwkeurigheid_m,
        koers=payload.koers, snelheid_ms=payload.snelheid_ms,
        photo_url=foto, breedte=payload.breedte, hoogte=payload.hoogte,
        verpixeld=payload.verpixeld or 0, wegdek_boven=payload.wegdek_boven,
        status="wacht")
    db.add(o)
    db.commit()
    schouw_analyse.aanbieden(o.id, beeld)
    db.refresh(o)
    return {"id": o.id, "status": o.status, "dubbel": False}


@router.get("/ritten/{rit_id}/opnames")
def opnames_stand(
    rit_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Hoe ver de analyse is, voor het scherm tijdens en na de rit."""
    from sqlalchemy import func

    r = _rit_of_404(db, rit_id, current_user)
    tellers = dict(db.query(SchouwOpname.status, func.count(SchouwOpname.id))
                     .filter(SchouwOpname.schouwrit_id == r.id)
                     .group_by(SchouwOpname.status).all())
    gemiddeld = (db.query(func.avg(SchouwOpname.duur_ms))
                   .filter(SchouwOpname.schouwrit_id == r.id,
                           SchouwOpname.status == "klaar").scalar())
    schades = [w for w in (r.waarnemingen or []) if w.crow_schadebeeld]
    schades.sort(key=lambda w: _utc(w.created_at) or datetime.min.replace(tzinfo=timezone.utc),
                 reverse=True)
    return {
        "totaal": sum(tellers.values()),
        "wacht": tellers.get("wacht", 0) + tellers.get("bezig", 0),
        "klaar": tellers.get("klaar", 0),
        "mislukt": tellers.get("mislukt", 0),
        "gemiddelde_analyse_s": round(gemiddeld / 1000, 1) if gemiddeld else None,
        "schades": len(schades),
        "laatste_schades": [_w_dict(w) for w in schades[:8]],
        "rit": _rit_dict(r),
    }


# Het dichtstbijzijnde stuk wegdek, waar een haarscheur nog een paar pixels
# breed is. Dat gaat er als tweede beeld naast, op volle scherpte: in het hele
# wegdekbeeld verdwijnt zo'n scheur in het verkleinen.
DICHTBIJ_DEEL = 0.30           # onderste deel van het hele beeld
MODEL_MAX_ZIJDE = 1568         # groter maakt het model zelf toch kleiner


def _wegdek_uitsnede(beeld: bytes, boven: Optional[float]) -> tuple[bytes, list[bytes], float]:
    """Het wegdek uit het beeld snijden, plus een scherpe uitsnede van het
    stuk vlak voor de auto. Lucht en gevels zeggen niets over het wegdek en
    kosten pixels."""
    import io as _io

    from PIL import Image

    boven = min(0.9, max(0.0, boven if boven is not None else 0.4))

    def als_jpeg(im) -> bytes:
        buf = _io.BytesIO()
        im.save(buf, format="JPEG", quality=92)
        return buf.getvalue()

    with Image.open(_io.BytesIO(beeld)) as im:
        im = im.convert("RGB")
        b, h = im.size
        wegdek = im.crop((0, int(h * boven), b, h))
        wegdek.thumbnail((MODEL_MAX_ZIJDE, MODEL_MAX_ZIJDE))
        extra = []
        dichtbij_top = int(h * max(boven, 1.0 - DICHTBIJ_DEEL))
        if h - dichtbij_top > 60 and dichtbij_top > int(h * boven) + 20:
            dichtbij = im.crop((0, dichtbij_top, b, h))
            dichtbij.thumbnail((MODEL_MAX_ZIJDE, MODEL_MAX_ZIJDE))
            extra.append(als_jpeg(dichtbij))
        return als_jpeg(wegdek), extra, boven


def _naar_heel_beeld(kader: Optional[list[float]], boven: float) -> Optional[list[float]]:
    """Een kader in de uitsnede terug naar het hele beeld, zodat het rode vak
    op de bewaarde foto op de goede plek staat."""
    if not kader:
        return kader
    x0, y0, x1, y1 = kader
    schaal = 1.0 - boven
    return [x0, round(boven + y0 * schaal, 4), x1, round(boven + y1 * schaal, 4)]


def analyseer_opname(db: Session, o: SchouwOpname, beeld: Optional[bytes] = None) -> None:
    """Eén opname beoordelen en verwerken. Aangeroepen door schouw_analyse,
    met de opname al op `bezig`. Een mislukte aanroep van het model gooit een
    fout, zodat de wachtrij het later opnieuw probeert in plaats van het stuk
    weg als 'niet te beoordelen' te boeken."""
    import photo_storage

    r = db.query(Schouwrit).filter(Schouwrit.id == o.schouwrit_id).first()
    if r is None:
        o.status, o.fout = "mislukt", "schouwrit bestaat niet meer"
        db.commit()
        return
    data = beeld or photo_storage.lees_foto(o.photo_url)
    if not data:
        raise RuntimeError("beeld niet te lezen")
    uitsnede, extra, boven = _wegdek_uitsnede(data, o.wegdek_boven)
    resultaat = sv.analyseer_frame(
        image_bytes=uitsnede, extra_beelden=extra, image_media_type="image/jpeg",
        privacy_gecontroleerd=True,        # alleen verpixelde beelden komen hier
        context=(f"Gebied: {r.gebied}" if r.gebied else None),
        instellingen=si.lees(r.organization), stand="wegdek",
        voorbeelden=schouw_leren.voorbeelden(db, o.organization_id))
    reden = resultaat.get("reden_onbruikbaar") or ""
    if reden.startswith("analyse mislukt"):
        raise RuntimeError(reden)

    for w in resultaat.get("wegschade") or []:
        w["kader"] = _naar_heel_beeld(w.get("kader"), boven)
        w["_lat"], w["_lng"] = _projecteer(o.lat, o.lng, o.koers, o.snelheid_ms,
                                           w.get("kader"), boven)

    # Eén opname tegelijk per rit: twee beelden van dezelfde kuil die tegelijk
    # klaar zijn, mogen niet allebei denken dat ze de eerste zijn.
    r = db.query(Schouwrit).filter(Schouwrit.id == r.id).with_for_update().first()
    meta = SimpleNamespace(lat=o.lat, lng=o.lng, nauwkeurigheid_m=o.nauwkeurigheid_m,
                           straatnaam=None, bewaar_beeld=False, geanonimiseerd=True,
                           breedte=o.breedte, hoogte=o.hoogte, verpixeld=o.verpixeld)
    _verwerk_resultaat(db, r, resultaat, meta, lambda: o.photo_url, o.organization_id,
                       _utc(o.gemaakt_op) or datetime.now(timezone.utc))
    o.status = "klaar"
    o.fout = None if resultaat.get("bruikbaar") else (reden or "niet te beoordelen")[:300]
    o.schades = len(resultaat.get("wegschade") or [])
    o.duur_ms = resultaat.get("_duur_ms")
    o.model_id = resultaat.get("_model_id")
    o.geanalyseerd_op = datetime.now(timezone.utc)
    if r.status == "afgerond":
        # Wat na het afronden nog binnenkwam, telt mee in de uitslag.
        db.flush()
        db.refresh(r)
        uitslag = _tussenstand(r)
        r.beeldkwaliteit = uitslag.get("beeldkwaliteit")
        r.voldoet = uitslag.get("voldoet")
    db.commit()


# ── Rapport: de hele ronde als PDF of Excel, met de foto's erbij ─────
#
# "Met foto's erbij" is de kern: een schade zonder beeld is een bewering. In
# de tabel staat een duimnagel met het rode vak erop; in Excel zit hetzelfde
# plaatje in de cel, zodat het bestand op zichzelf staat.
MAX_FOTOS_IN_RAPPORT = 200


def _rapport_bladen(db: Session, r: Schouwrit) -> tuple[list, str]:
    from export_huisstijl import Blad, Kolom, foto_bytes

    waarnemingen = sorted((r.waarnemingen or []),
                          key=lambda w: _utc(w.created_at) or datetime.min.replace(tzinfo=timezone.utc))
    schades = [w for w in waarnemingen if w.crow_schadebeeld]
    overig = [w for w in waarnemingen if not w.crow_schadebeeld]
    uitslag = _tussenstand(r)
    dek = dekking(r.id, _SysteemGebruiker(r), db) if r.privacy_modus == "rijdend" else None

    def oordeel(w) -> str:
        if w.afgewezen:
            reden = schouw_leren.AFWIJS_REDENEN.get(w.afwijs_reden or "")
            return "afgewezen" + (f" ({reden.lower()})" if reden else "")
        return "bevestigd" if w.bevestigd else ("telt mee" if _telt_mee(w) else "nog te beoordelen")

    samenvatting = [
        ["Gebied", r.gebied or "-"],
        ["Soort schouw", "rijdend (wegdek)" if r.privacy_modus == "rijdend" else "lopend"],
        ["Gebiedstype", (cs.GEBIEDSTYPEN.get(r.gebiedstype or "") or {}).get("naam") or "-"],
        ["Ambitie", r.ambitie or cs.gangbare_ambitie(r.gebiedstype) or "-"],
        ["Inspecteur", r.inspecteur_naam or "-"],
        ["Gestart", _utc(r.gestart_op)],
        ["Afgerond", _utc(r.afgerond_op) if r.afgerond_op else "loopt nog"],
        ["Beeldkwaliteit", r.beeldkwaliteit or uitslag.get("beeldkwaliteit") or "nog geen oordeel"],
        ["Voldoet aan de ambitie", ("ja" if (r.voldoet if r.voldoet is not None else uitslag.get("voldoet")) else "nee")
         if (r.voldoet if r.voldoet is not None else uitslag.get("voldoet")) is not None else "-"],
        ["Beelden beoordeeld", r.frames or 0],
        ["Beelden niet te beoordelen", r.frames_onbruikbaar or 0],
        ["Schades gevonden", len(schades)],
        ["Waarvan bevestigd", sum(1 for w in schades if w.bevestigd)],
        ["Waarvan afgewezen", sum(1 for w in schades if w.afgewezen)],
        ["Nog te beoordelen", sum(1 for w in schades if not w.bevestigd and not w.afgewezen)],
    ]
    if dek:
        samenvatting += [["Gereden", f"{dek['km_gereden']:.2f} km".replace(".", ",")],
                         ["Waarvan beoordeeld", f"{dek['km']['beoordeeld']:.2f} km".replace(".", ",")],
                         ["Niet te beoordelen", f"{dek['km']['onbruikbaar']:.2f} km".replace(".", ",")],
                         ["Niet bekeken", f"{dek['km']['niet_bekeken']:.2f} km".replace(".", ",")]]
    samenvatting.append(["Herkenning", sv.SCHOUW_VISION_VERSION])

    bladen = [Blad("Samenvatting", [Kolom("Onderdeel"), Kolom("Waarde")],
                   [[k, ("" if v is None else v)] for k, v in samenvatting], liggend=False)]

    kolommen = [Kolom("Nr", "heel", breedte=None), Kolom("Foto", "foto"), Kolom("Schade"),
                Kolom("Verharding"), Kolom("Ernst"), Kolom("Omvang"), Kolom("Klasse"),
                Kolom("Zekerheid", "procent"), Kolom("Oordeel"), Kolom("Gezien", "heel"),
                Kolom("Straat"), Kolom("Breedtegraad", "getal", 6), Kolom("Lengtegraad", "getal", 6),
                Kolom("Toelichting")]
    rijen = []
    for i, w in enumerate(schades, start=1):
        foto = (foto_bytes(w.photo_url, kader=_kader_lezen(w.kader))
                if w.photo_url and i <= MAX_FOTOS_IN_RAPPORT else None)
        d = _w_dict(w)
        rijen.append([i, foto, d["naam"], w.crow_verharding, w.crow_ernst, w.crow_omvang,
                      d["klasse_indicatie"], w.zekerheid, oordeel(w), w.keer_gezien or 1,
                      w.straatnaam, w.lat, w.lng, w.toelichting])
    bladen.append(Blad("Schades", kolommen, rijen, liggend=True,
                       toelichting=["Het rode vak op de foto is waar de herkenning de schade zag.",
                                    "Klasse is een indicatie uit ernst en omvang (CROW 146), geen meting."]))

    if overig:
        bladen.append(Blad(
            "Overige waarnemingen",
            [Kolom("Nr", "heel"), Kolom("Foto", "foto"), Kolom("Waarneming"), Kolom("Drager"),
             Kolom("Waarde", "getal", 1), Kolom("Niveau"), Kolom("Zekerheid", "procent"),
             Kolom("Oordeel"), Kolom("Straat"), Kolom("Toelichting")],
            [[i, (foto_bytes(w.photo_url) if w.photo_url and i <= MAX_FOTOS_IN_RAPPORT else None),
              _w_dict(w)["naam"], w.drager, w.waarde, w.klasse_niveau, w.zekerheid,
              oordeel(w), w.straatnaam, w.toelichting]
             for i, w in enumerate(overig, start=1)], liggend=True))

    if dek and dek["punten"]:
        # Zonder GPS valt er niets over dekking te zeggen; de beelden zelf
        # horen er wel altijd bij.
        bladen.append(Blad("Dekking", [Kolom("Wat"), Kolom("Kilometer", "getal", 2)],
                           [["Gereden", dek["km_gereden"]],
                            ["Beoordeeld", dek["km"]["beoordeeld"]],
                            ["Niet te beoordelen", dek["km"]["onbruikbaar"]],
                            ["Niet bekeken (gat groter dan 60 m)", dek["km"]["niet_bekeken"]],
                            ["Nog in beoordeling", dek["km"]["bezig"]]],
                           toelichting=["Niet bekeken betekent: daar is geen beeld van. "
                                        "Dat is iets anders dan: daar is geen schade."]))
    if r.privacy_modus == "rijdend":
        opnames = (db.query(SchouwOpname)
                     .filter(SchouwOpname.schouwrit_id == r.id)
                     .order_by(SchouwOpname.volgnummer).all())
        bladen.append(Blad(
            "Beelden",
            [Kolom("Nr", "heel"), Kolom("Tijd", "datumtijd"), Kolom("Status"), Kolom("Schades", "heel"),
             Kolom("Breedtegraad", "getal", 6), Kolom("Lengtegraad", "getal", 6), Kolom("Opmerking")],
            [[o.volgnummer, _utc(o.gemaakt_op), _dek_status(o), o.schades or 0, o.lat, o.lng, o.fout]
             for o in opnames], liggend=True))

    ondertitel = " · ".join(x for x in (
        r.gebied, r.inspecteur_naam,
        (_utc(r.gestart_op).strftime("%d-%m-%Y") if r.gestart_op else None)) if x)
    return bladen, ondertitel


class _SysteemGebruiker:
    """Zodat het rapport de dekking kan hergebruiken zonder de route na te bouwen."""

    def __init__(self, r: Schouwrit):
        self.organization_id = r.organization_id


MAX_GROTE_FOTOS = 60
FOTO_BIJLAGE_MM = 120.0


def _fotobijlage(pdf, db: Session, r: Schouwrit) -> None:
    """Elke schade nog een keer, groot genoeg om te zien wat het is."""
    import io as _io

    from export_huisstijl import GRIJS, INKT, foto_bytes
    from export_huisstijl import rgb as _rgb

    schades = [w for w in sorted((r.waarnemingen or []),
                                 key=lambda w: _utc(w.created_at) or datetime.min.replace(tzinfo=timezone.utc))
               if w.crow_schadebeeld and w.photo_url][:MAX_GROTE_FOTOS]
    if not schades:
        return
    pdf.sectie("Foto's bij de schades")
    # Twee naast elkaar op een liggende pagina: groot genoeg om te zien wat het
    # is, zonder een pagina per foto.
    breed = (pdf.w - 2 * pdf.MARGE - 10) / 2 if pdf.w > pdf.h else FOTO_BIJLAGE_MM
    hoogte = breed * 0.62                       # de meeste beelden zijn 16:9
    kolom, y = 0, pdf.get_y()
    for i, w in enumerate(schades, start=1):
        beeld = foto_bytes(w.photo_url, breedte=1100, kader=_kader_lezen(w.kader))
        if not beeld:
            continue
        if kolom == 0:
            pdf.set_y(y)
            pdf.ruimte_nodig(hoogte + 18)      # past het niet meer: volgende pagina
            y = pdf.get_y()
        x = pdf.MARGE + kolom * (breed + 10)
        d = _w_dict(w)
        regel = f"{i}. {d['naam']}"
        for stuk in (w.crow_verharding, f"ernst {w.crow_ernst}" if w.crow_ernst else None,
                     f"klasse {d['klasse_indicatie']}" if d["klasse_indicatie"] else None,
                     w.straatnaam,
                     "bevestigd" if w.bevestigd else ("afgewezen" if w.afgewezen else None)):
            if stuk:
                regel += f" · {stuk}"
        pdf.set_xy(x, y)
        pdf.set_font(pdf.font_family, "B", 9.5)
        pdf.cell(breed, 5, regel)
        try:
            pdf.image(_io.BytesIO(beeld), x=x, y=y + 5.5, w=breed)
        except Exception:  # noqa: BLE001 — een kapot beeld stopt het rapport niet
            continue
        if w.toelichting:
            pdf.set_xy(x, y + hoogte + 7)
            pdf.set_font(pdf.font_family, "", 8)
            pdf.set_text_color(*_rgb(GRIJS))
            pdf.cell(breed, 4, w.toelichting[:90])
            pdf.set_text_color(*_rgb(INKT))
        kolom = (kolom + 1) % 2
        if kolom == 0:
            y += hoogte + 18
    pdf.set_xy(pdf.MARGE, y + (hoogte + 18 if kolom else 0))


@router.get("/ritten/{rit_id}/rapport.pdf")
def rapport_pdf(
    rit_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """De hele ronde als PDF, met de foto's van de schades erbij."""
    from export_huisstijl import HuisstijlPDF, bestandsnaam, klant_van, pdf_antwoord

    r = _rit_of_404(db, rit_id, current_user)
    bladen, ondertitel = _rapport_bladen(db, r)
    pdf = HuisstijlPDF(klant_van(current_user.organization), "Schouwrapport",
                       ondertitel=ondertitel, liggend=True)
    pdf.add_page()
    pdf.titelblok()
    for blad in bladen:
        pdf.blad(blad)
    _fotobijlage(pdf, db, r)
    return pdf_antwoord(pdf.uitvoer(), bestandsnaam("Schouw", r.gebied, ext="pdf"))


@router.get("/ritten/{rit_id}/rapport.xlsx")
def rapport_xlsx(
    rit_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dezelfde ronde als Excel: zelfde kolommen, met de foto's in de cel."""
    from export_huisstijl import bestandsnaam, excel_antwoord, excel_van, klant_van

    r = _rit_of_404(db, rit_id, current_user)
    bladen, ondertitel = _rapport_bladen(db, r)
    inhoud = excel_van(klant_van(current_user.organization), "Schouwrapport", bladen,
                       ondertitel=ondertitel)
    return excel_antwoord(inhoud, bestandsnaam("Schouw", r.gebied, ext="xlsx"))


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
    r = w.rit
    # Een rijdende schouw wordt na de rit beoordeeld: de schades komen pas
    # binnen als de beelden zijn geanalyseerd. Die mag je na afronden nog
    # beoordelen; de uitslag rekent dan mee.
    if not (r.privacy_modus == "rijdend" and r.status == "afgerond"):
        _eis_bezig(r)

    velden = payload.model_dump(exclude_unset=True)
    schade = {k: velden.pop(k) for k in ("verharding", "schadebeeld", "ernst", "omvang") if k in velden}
    if schade:
        _schade_verbeteren(w, schade)
    if velden.get("afwijs_reden") is not None and velden["afwijs_reden"] not in schouw_leren.AFWIJS_REDENEN:
        raise HTTPException(status_code=400, detail="Onbekende reden. Kies uit: "
                            + ", ".join(schouw_leren.AFWIJS_REDENEN))
    if "drager" in velden and w.detectieklasse:
        try:
            w.meetlat = cs.meetlat_voor(w.detectieklasse, velden["drager"])
        except cs.OnbekendeDrager as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    for k, v in velden.items():
        setattr(w, k, v)
    if velden.get("bevestigd"):
        w.afgewezen = False
        w.afwijs_reden = None
        w.bevestigd_door_id = current_user.id
    if velden.get("afgewezen"):
        w.bevestigd = False
    if velden.get("bevestigd") or velden.get("afgewezen"):
        w.beoordeeld_op = datetime.now(timezone.utc)

    db.commit()
    if r.status == "afgerond":
        db.refresh(r)
        uitslag = _tussenstand(r)
        r.beeldkwaliteit = uitslag.get("beeldkwaliteit")
        r.voldoet = uitslag.get("voldoet")
        db.commit()
    schouw_leren.vergeet(w.organization_id)
    db.refresh(w)
    return _w_dict(w)


def _schade_verbeteren(w: Schouwwaarneming, velden: dict) -> None:
    """Een ander schadebeeld, een andere ernst of omvang. Wat de herkenning
    eerst zei, blijft bewaard: dat is precies wat het model moet leren."""
    if not w.crow_schadebeeld:
        raise HTTPException(status_code=400, detail="Alleen wegschade heeft een schadebeeld")
    verharding = velden.get("verharding") or w.crow_verharding
    schadebeeld = velden.get("schadebeeld") or w.crow_schadebeeld
    s = cw.zoek(verharding, schadebeeld)
    if not s:
        raise HTTPException(status_code=400, detail="Dat schadebeeld hoort niet bij deze verharding")
    if (s["verharding"], s["schadebeeld"]) != (w.crow_verharding, w.crow_schadebeeld):
        if not w.oorspronkelijk_schadebeeld:
            w.oorspronkelijk_schadebeeld = w.crow_schadebeeld
        w.crow_verharding, w.crow_schadebeeld = s["verharding"], s["schadebeeld"]
        w.crow_schadegroep = s["schadegroep"]
        w.drager = cw.VERHARDINGEN[s["verharding"]]["schouw_drager"]
        w.meetlat = cs.meetlat_voor("verharding", w.drager)
    if velden.get("ernst"):
        w.crow_ernst = velden["ernst"]
        w.klasse_niveau = cw.ERNST_NAAR_NIVEAU.get(velden["ernst"])
    if velden.get("omvang"):
        w.crow_omvang = velden["omvang"]


# ── Dekking: welk stuk weg is bekeken ────────────────────────────────
# Rijdend is elk beeld het stuk weg vóór de auto tot het volgende beeld. Ligt
# er meer dan DEKKING_GAT_M tussen twee beelden, dan is daar niets bekeken;
# boven DEKKING_PAUZE_M is het een pauze of een GPS-sprong en telt het niet mee.
DEKKING_GAT_M = 60.0
DEKKING_PAUZE_M = 2000.0


def _dek_status(o: SchouwOpname) -> str:
    if o.status in ("wacht", "bezig"):
        return "bezig"
    if o.status == "mislukt":
        return "onbruikbaar"
    if o.fout:
        return "onbruikbaar"
    return "schade" if (o.schades or 0) > 0 else "schoon"


@router.get("/ritten/{rit_id}/dekking")
def dekking(
    rit_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Welke stukken weg zijn bekeken, niet te beoordelen, of overgeslagen --
    met de schades erbij. Niets gemeten is iets anders dan niets gevonden."""
    r = _rit_of_404(db, rit_id, current_user)
    opnames = (db.query(SchouwOpname)
                 .filter(SchouwOpname.schouwrit_id == r.id,
                         SchouwOpname.lat.isnot(None), SchouwOpname.lng.isnot(None))
                 .order_by(SchouwOpname.volgnummer).all())
    punten = [{"lat": o.lat, "lng": o.lng, "status": _dek_status(o), "schades": o.schades or 0}
              for o in opnames]
    totalen = {"beoordeeld": 0.0, "onbruikbaar": 0.0, "bezig": 0.0, "niet_bekeken": 0.0}
    stukken = []
    for a, b in zip(punten, punten[1:]):
        lengte = _afstand_m(a["lat"], a["lng"], b["lat"], b["lng"])
        if lengte > DEKKING_PAUZE_M:
            continue
        if lengte > DEKKING_GAT_M:
            status = "niet_bekeken"
        else:
            status = {"schade": "beoordeeld", "schoon": "beoordeeld"}.get(a["status"], a["status"])
        totalen[status] += lengte
        stukken.append({"van": [a["lat"], a["lng"]], "naar": [b["lat"], b["lng"]], "status": status})
    schades = [{"id": w.id, "lat": w.lat, "lng": w.lng, "naam": _w_dict(w)["naam"],
                "ernst": w.crow_ernst, "bevestigd": w.bevestigd, "afgewezen": w.afgewezen}
               for w in (r.waarnemingen or []) if w.crow_schadebeeld and w.lat is not None]
    return {
        "punten": punten,
        "stukken": stukken,
        "km": {k: round(v / 1000, 2) for k, v in totalen.items()},
        "km_gereden": round(sum(totalen.values()) / 1000, 2),
        "schades": schades,
    }


# ── Instellingen van de herkenning ───────────────────────────────────

def _instellingen_antwoord(current_user: User) -> dict:
    uit = si.beschrijving()
    uit["instellingen"] = si.lees(current_user.organization)
    uit["kan_wijzigen"] = bool(is_org_admin(current_user))
    return uit


@router.get("/instellingen")
def instellingen_lezen(current_user: User = Depends(get_current_user)):
    """Wat de camera herkent en hoe streng. Iedereen die schouwt mag het zien:
    een inspecteur moet weten waarom de camera iets niet meldt."""
    return _instellingen_antwoord(current_user)


@router.put("/instellingen")
def instellingen_vastleggen(
    payload: dict,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alleen de beheerder: dit bepaalt voor de hele organisatie wat er gezien
    en meegeteld wordt."""
    if not is_org_admin(current_user):
        raise HTTPException(status_code=403,
                            detail="Alleen een beheerder kan de herkenning instellen")
    org = current_user.organization
    if org is None:
        raise HTTPException(status_code=404, detail="Geen organisatie gevonden")
    try:
        nieuw = si.valideer(payload)
    except si.OngeldigeInstelling as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    oud = si.lees(org)
    org.schouw_instellingen = json.dumps(nieuw)
    db.commit()
    log_action(db, request, current_user, action="schouw.instellingen",
               entity_type="organization", entity_id=org.id,
               before=oud, after=nieuw)
    return _instellingen_antwoord(current_user)


class ProefIn(BaseModel):
    image_data_url: str = Field(..., min_length=32)
    # "wegdek" loopt precies zoals de rijstand: uitsnede van het wegdek plus
    # het scherpe stuk vlak voor de auto. Zo test je met een foto van je eigen
    # rit wat de herkenning ervan maakt.
    stand: str = Field(default="alles", pattern="^(alles|wegdek)$")
    wegdek_boven: float = Field(default=0.4, ge=0.0, le=0.9)


@router.post("/proefbeeld")
def proefbeeld(
    payload: ProefIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Eén beeld beoordelen met de huidige instellingen, zonder iets op te slaan.

    Om instellingen te vergelijken op je eigen straat: hoe lang duurde het,
    wat kwam eruit. Zelfde privacyregel als de schouw: de inspecteur richt.
    """
    _eis_beheer(current_user)
    inst = si.lees(current_user.organization)
    beeld, media_type = _data_url_naar_bytes(payload.image_data_url)
    extra: list[bytes] = []
    getoond = payload.image_data_url
    if payload.stand == "wegdek":
        beeld, extra, _boven = _wegdek_uitsnede(beeld, payload.wegdek_boven)
        media_type = "image/jpeg"
        getoond = "data:image/jpeg;base64," + base64.b64encode(beeld).decode("ascii")
    start = time.perf_counter()
    uit = sv.analyseer_frame(image_bytes=beeld, extra_beelden=extra, image_media_type=media_type,
                             privacy_gecontroleerd=True, instellingen=inst, stand=payload.stand,
                             voorbeelden=schouw_leren.voorbeelden(db, current_user.organization_id))
    duur_ms = int((time.perf_counter() - start) * 1000)

    items = [{"soort": "schade", "naam": w.get("naam"), "ernst": w.get("ernst"),
              "kader": w.get("kader"), "zekerheid": w.get("zekerheid")}
             for w in (uit.get("wegschade") or [])]
    items += [{"soort": "gebied",
               "naam": cs.DETECTIEKLASSEN.get(w.get("klasse"), {}).get("naam") or w.get("klasse"),
               "niveau": w.get("klasse_niveau"), "waarde": w.get("waarde"),
               "kader": w.get("kader"), "zekerheid": w.get("zekerheid")}
              for w in (uit.get("gebied") or [])]
    items += [{"soort": "object", "naam": o.get("naam"), "niveau": o.get("niveau"),
               "kader": o.get("kader"), "zekerheid": o.get("zekerheid")}
              for o in (uit.get("objecten") or [])]
    return {
        "bruikbaar": uit.get("bruikbaar"),
        "reden_onbruikbaar": uit.get("reden_onbruikbaar"),
        "duur_ms": duur_ms,
        "model_id": uit.get("_model_id"),
        "grondigheid": inst["grondigheid"],
        "stand": payload.stand,
        # Het beeld zoals het model het zag; bij "wegdek" dus de uitsnede.
        "beeld": getoond,
        "items": items,
    }


# ── Lesmateriaal ─────────────────────────────────────────────────────
#
# Een steekproef van één op de zoveel beelden gaat ook zonder schade de
# leerset in: een model dat alleen schade heeft gezien, ziet overal schade.
LEERBEELD_ELKE = 10


def _eis_org_admin(current_user: User) -> None:
    if not is_org_admin(current_user):
        raise HTTPException(status_code=403,
                            detail="Alleen een beheerder kan de leerset ophalen")


def _status(w: Optional[Schouwwaarneming]) -> str:
    if w is None:
        return "voorstel"
    if w.afgewezen:
        return "afgewezen"
    return "bevestigd" if w.bevestigd else "voorstel"


@router.get("/leerset")
def leerset(
    limit: int = Query(default=500, ge=1, le=5000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """De bewaarde schouwbeelden met hun kaders, in COCO-formaat.

    COCO is wat vrijwel elk trainingsgereedschap inleest. Kaders staan in
    pixels ([x, y, breedte, hoogte]); per kader staat erbij of het een
    voorstel van het model is, of door een inspecteur is bevestigd of
    afgewezen. Afgewezen kaders zijn ook lesmateriaal: ze leren het model wat
    het níet is.
    """
    _eis_org_admin(current_user)
    beelden = (db.query(SchouwBeeld)
                 .filter(SchouwBeeld.organization_id == current_user.organization_id)
                 .order_by(SchouwBeeld.created_at.desc()).limit(limit).all())
    ids = [wid for b in beelden for k in json.loads(b.kaders or "[]")
           if (wid := k.get("waarneming_id"))]
    oordeel = {w.id: w for w in (db.query(Schouwwaarneming)
                                   .filter(Schouwwaarneming.id.in_(ids)).all() if ids else [])}

    categorieen: dict[tuple[str, str], int] = {}
    images, annotations = [], []
    for b in beelden:
        breedte, hoogte = b.breedte or 0, b.hoogte or 0
        images.append({
            "id": b.id,
            "file_name": (b.photo_url if b.photo_url and not b.photo_url.startswith("data:")
                          else f"/api/schouw/leerset/beeld/{b.id}"),
            "width": breedte, "height": hoogte,
            "date_captured": b.created_at.isoformat() if b.created_at else None,
            "schouwrit_id": b.schouwrit_id,
        })
        for i, k in enumerate(json.loads(b.kaders or "[]")):
            if not k.get("kader") or not k.get("label"):
                continue
            sleutel = (k.get("soort") or "object", k["label"])
            cat = categorieen.setdefault(sleutel, len(categorieen) + 1)
            x0, y0, x1, y1 = k["kader"]
            bw, bh = (x1 - x0) * breedte, (y1 - y0) * hoogte
            annotations.append({
                "id": f"{b.id}:{i}", "image_id": b.id, "category_id": cat,
                "bbox": [round(x0 * breedte, 1), round(y0 * hoogte, 1),
                         round(bw, 1), round(bh, 1)],
                "area": round(bw * bh, 1), "iscrowd": 0,
                "niveau": k.get("niveau"), "ernst": k.get("ernst"),
                "zekerheid": k.get("zekerheid"),
                "status": _status(oordeel.get(k.get("waarneming_id"))),
            })
    return {
        "info": {"description": "FieldOps schouw-leerset",
                 "versie": sv.SCHOUW_VISION_VERSION,
                 "gemaakt_op": datetime.now(timezone.utc).isoformat()},
        "images": images,
        "annotations": annotations,
        "categories": [{"id": c, "name": label, "supercategory": soort}
                       for (soort, label), c in sorted(categorieen.items(), key=lambda x: x[1])],
    }


@router.get("/leerset/beeld/{beeld_id}")
def leerset_beeld(
    beeld_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Het beeld zelf, voor wie de leerset downloadt."""
    _eis_org_admin(current_user)
    b = (db.query(SchouwBeeld)
           .filter(SchouwBeeld.id == beeld_id,
                   SchouwBeeld.organization_id == current_user.organization_id)
           .first())
    if not b or not b.photo_url:
        raise HTTPException(status_code=404, detail="Beeld niet gevonden")
    if not b.photo_url.startswith("data:"):
        return RedirectResponse(b.photo_url)
    data, media_type = _data_url_naar_bytes(b.photo_url)
    return Response(content=data, media_type=media_type)


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
    # Lesbeelden expliciet mee: SQLite handhaaft ON DELETE CASCADE niet altijd.
    db.query(SchouwBeeld).filter(SchouwBeeld.schouwrit_id == r.id).delete(
        synchronize_session=False)
    db.query(SchouwOpname).filter(SchouwOpname.schouwrit_id == r.id).delete(
        synchronize_session=False)
    db.delete(r)
    db.commit()
    log_action(db, request, current_user, action="schouw.delete",
               entity_type="schouwrit", entity_id=rit_id)
    return {"status": "verwijderd"}
