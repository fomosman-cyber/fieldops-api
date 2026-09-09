"""Kwaliteit & keuringen — keuring opstellen, uitvoeren en beoordelen.

Endpoints:
  GET    /api/kwaliteit/config                          veldtypen, werksoorten, frequenties
  GET    /api/kwaliteit/templates                        standaardkeuringen
  GET    /api/kwaliteit/keuringen                        lijst met voortgang
  POST   /api/kwaliteit/keuringen                        aanmaken, los of vanaf sjabloon
  GET    /api/kwaliteit/keuringen/{id}                   detail met velden en eisen
  PATCH  /api/kwaliteit/keuringen/{id}
  DELETE /api/kwaliteit/keuringen/{id}
  POST   /api/kwaliteit/keuringen/{id}/velden            builder
  PATCH  /api/kwaliteit/keuringen/{id}/velden/{vid}
  DELETE /api/kwaliteit/keuringen/{id}/velden/{vid}
  POST   /api/kwaliteit/keuringen/{id}/eisen
  PATCH  /api/kwaliteit/keuringen/{id}/eisen/{eid}
  DELETE /api/kwaliteit/keuringen/{id}/eisen/{eid}
  POST   /api/kwaliteit/keuringen/{id}/registraties      registratie starten
  GET    /api/kwaliteit/registraties                     lijst met filters
  GET    /api/kwaliteit/registraties/{id}                detail met antwoorden en bewijs
  PATCH  /api/kwaliteit/registraties/{id}
  PATCH  /api/kwaliteit/registraties/{id}/antwoorden/{aid}
  POST   /api/kwaliteit/registraties/{id}/bewijs
  GET    /api/kwaliteit/registraties/{id}/bewijs/{bid}   inclusief de inhoud
  DELETE /api/kwaliteit/registraties/{id}/bewijs/{bid}
  POST   /api/kwaliteit/registraties/{id}/indienen
  POST   /api/kwaliteit/registraties/{id}/beoordelen
  DELETE /api/kwaliteit/registraties/{id}

**Een keuring is niet een registratie.** De keuring is wat je afspreekt te
controleren; de registratie is een keer buiten staan en het invullen. Wie die
twee samenvoegt krijgt een formulier dat je maar een keer kunt gebruiken, en
dan wordt er per werkvak een nieuwe keuring aangemaakt en is er geen voortgang
meer te tellen.

**Lijsten sturen nooit foto's mee.** Bewijsmateriaal is base64 zolang S3 niet
is geconfigureerd, en een lijst van vijftig registraties met foto's erin heeft
deze API eerder omvergetrokken. De lijst geeft aantallen; de inhoud haal je op
per bewijsstuk.

**Zonder norm geen oordeel.** Een meetwaarde zonder ingevulde grenzen komt terug
als `niet_beoordeeld` en niet als akkoord. Zie ``kwaliteit.beoordeel_meetwaarde``
voor het waarom.
"""

from datetime import datetime, timezone
from typing import Any, Optional

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import kwaliteit as kw
from audit import ACTION, log_action
from auth import get_current_user
from database import get_db
from models import (Asset, Project, QualityAnswer, QualityEvidence, QualityField,
                    QualityInspection, QualityRegistration, QualityRequirement,
                    User, UserRole)
from permissions import can_manage_toolbox, is_org_admin, require_module

router = APIRouter(prefix="/api/kwaliteit", tags=["Kwaliteit"],
                   dependencies=[Depends(require_module("kwaliteit"))])


# ── Schemas ──────────────────────────────────────────────────────────

class KeuringIn(BaseModel):
    naam: Optional[str] = Field(default=None, max_length=255)
    keuringsnummer: Optional[str] = Field(default=None, max_length=60)
    omschrijving: Optional[str] = None
    werksoort: Optional[str] = None
    project_id: Optional[str] = None
    asset_id: Optional[str] = None
    werkvak: Optional[str] = Field(default=None, max_length=160)
    locatie: Optional[str] = Field(default=None, max_length=255)
    activiteit: Optional[str] = Field(default=None, max_length=160)
    verantwoordelijke_id: Optional[str] = None
    uitvoerder_id: Optional[str] = None
    controleur_id: Optional[str] = None
    frequentie: Optional[str] = None
    frequentie_vrij: Optional[str] = Field(default=None, max_length=160)
    verwacht_aantal: Optional[int] = Field(default=None, ge=0, le=100000)
    prioriteit: Optional[str] = None
    # Sjabloon uit de bibliotheek. Vult naam, velden en eisen voor.
    template_code: Optional[str] = None


class KeuringUpdate(KeuringIn):
    status: Optional[str] = None


class VeldIn(BaseModel):
    code: Optional[str] = Field(default=None, max_length=60)
    label: str = Field(..., min_length=1, max_length=255)
    veldtype: str
    toelichting: Optional[str] = None
    verplicht: bool = False
    eenheid: Optional[str] = Field(default=None, max_length=20)
    norm_min: Optional[float] = None
    norm_max: Optional[float] = None
    tolerantie: Optional[float] = Field(default=None, ge=0)
    opties: Optional[list[str]] = None
    eis_id: Optional[str] = None
    order_index: Optional[int] = None


class VeldUpdate(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=255)
    veldtype: Optional[str] = None
    toelichting: Optional[str] = None
    verplicht: Optional[bool] = None
    eenheid: Optional[str] = Field(default=None, max_length=20)
    norm_min: Optional[float] = None
    norm_max: Optional[float] = None
    tolerantie: Optional[float] = Field(default=None, ge=0)
    opties: Optional[list[str]] = None
    eis_id: Optional[str] = None
    order_index: Optional[int] = None


class EisIn(BaseModel):
    eisnummer: Optional[str] = Field(default=None, max_length=20)
    titel: str = Field(..., min_length=1, max_length=255)
    omschrijving: Optional[str] = None
    norm: Optional[str] = Field(default=None, max_length=255)
    bron: Optional[str] = Field(default=None, max_length=255)
    referentie: Optional[str] = Field(default=None, max_length=255)
    verplichting: Optional[str] = None
    tolerantie: Optional[str] = Field(default=None, max_length=120)
    meetmethode: Optional[str] = Field(default=None, max_length=255)
    bewijs_vereist: bool = False
    order_index: Optional[int] = None


class EisUpdate(BaseModel):
    eisnummer: Optional[str] = Field(default=None, max_length=20)
    titel: Optional[str] = Field(default=None, min_length=1, max_length=255)
    omschrijving: Optional[str] = None
    norm: Optional[str] = Field(default=None, max_length=255)
    bron: Optional[str] = Field(default=None, max_length=255)
    referentie: Optional[str] = Field(default=None, max_length=255)
    verplichting: Optional[str] = None
    tolerantie: Optional[str] = Field(default=None, max_length=120)
    meetmethode: Optional[str] = Field(default=None, max_length=255)
    bewijs_vereist: Optional[bool] = None
    order_index: Optional[int] = None


class RegistratieIn(BaseModel):
    datum: Optional[datetime] = None
    werkvak: Optional[str] = Field(default=None, max_length=160)
    locatie: Optional[str] = Field(default=None, max_length=255)
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lng: Optional[float] = Field(default=None, ge=-180, le=180)
    uitvoerder_naam: Optional[str] = Field(default=None, max_length=120)


class RegistratieUpdate(RegistratieIn):
    opmerking: Optional[str] = None
    handtekening_data_url: Optional[str] = None
    handtekening_naam: Optional[str] = Field(default=None, max_length=120)


class AntwoordIn(BaseModel):
    # Vorm hangt af van het veldtype: getal, tekst, bool of lijst. De router
    # zet hem in de juiste kolom -- zie _zet_antwoord.
    waarde: Optional[Any] = None
    toelichting: Optional[str] = None
    photo_url: Optional[str] = None


class BewijsIn(BaseModel):
    soort: Optional[str] = None
    titel: Optional[str] = Field(default=None, max_length=255)
    omschrijving: Optional[str] = None
    url: str = Field(..., min_length=1)
    mime: Optional[str] = Field(default=None, max_length=80)
    bestandsnaam: Optional[str] = Field(default=None, max_length=255)
    eis_id: Optional[str] = None
    veld_id: Optional[str] = None
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lng: Optional[float] = Field(default=None, ge=-180, le=180)
    vastgelegd_op: Optional[datetime] = None


class BeoordelingIn(BaseModel):
    besluit: str  # goedkeuren | afkeuren | terugsturen
    reden: Optional[str] = None


BEWIJS_SOORTEN: frozenset[str] = frozenset({
    "foto", "video", "pdf", "certificaat", "meetrapport", "tekening", "bon",
    "leveringsdocument", "testresultaat", "handtekening", "locatie", "overig",
})

PRIORITEITEN: frozenset[str] = frozenset({"laag", "normaal", "hoog", "kritiek"})
KEURING_STATUSSEN: frozenset[str] = frozenset({"actief", "concept", "gereed", "gearchiveerd"})
VERPLICHTINGEN: frozenset[str] = frozenset({"eis", "inspanning", "advies"})

# Wie mag beoordelen. De toezichthouder is de controleur uit het werkproces;
# admin en manager mogen het ook omdat een klein bureau die rol niet apart
# bezet heeft.
_MAG_BEOORDELEN: frozenset[UserRole] = frozenset({
    UserRole.ADMIN, UserRole.MANAGER, UserRole.INSPECTOR,
})


# ── Helpers ──────────────────────────────────────────────────────────

def _eis_beheer(current_user: User) -> None:
    """Een keuring opstellen of wijzigen is beheerwerk.

    Deelt bewust dezelfde rollenset als assets en toolboxen: het is dezelfde
    groep "wie mag hier vastleggen waar de rest zich aan houdt". Uitvoeren mag
    daarna iedereen die buiten staat.
    """
    if not can_manage_toolbox(current_user):
        raise HTTPException(
            status_code=403,
            detail="Alleen een beheerder of projectleider kan een keuring opstellen")


def _eis_registreren(current_user: User) -> None:
    """Registreren mag iedereen behalve de meekijker.

    De opdrachtgever heeft leesrechten en hoort geen keuringen in te vullen --
    dat zou het dossier onbetrouwbaar maken. Al het andere veldpersoneel mag,
    want een keuring die alleen de uitvoerder mag invullen wordt niet gedaan
    als hij een dag ziek is.
    """
    if is_org_admin(current_user):
        return
    if current_user.role == UserRole.VIEWER or current_user.role is None:
        raise HTTPException(
            status_code=403,
            detail="Geen rechten om een registratie in te vullen")


def _eis_beoordelen(current_user: User) -> None:
    if is_org_admin(current_user):
        return
    if current_user.role not in _MAG_BEOORDELEN:
        raise HTTPException(
            status_code=403,
            detail="Alleen een toezichthouder, projectleider of beheerder kan beoordelen")


def _keuring_of_404(db: Session, keuring_id: str, user: User) -> QualityInspection:
    k = (db.query(QualityInspection)
           .filter(QualityInspection.id == keuring_id,
                   QualityInspection.organization_id == user.organization_id)
           .first())
    if not k:
        raise HTTPException(status_code=404, detail="Keuring niet gevonden")
    return k


def _registratie_of_404(db: Session, reg_id: str, user: User) -> QualityRegistration:
    r = (db.query(QualityRegistration)
           .filter(QualityRegistration.id == reg_id,
                   QualityRegistration.organization_id == user.organization_id)
           .first())
    if not r:
        raise HTTPException(status_code=404, detail="Registratie niet gevonden")
    return r


def _volledige_naam(user: User) -> str:
    naam = " ".join(x for x in (user.first_name, user.last_name) if x).strip()
    return naam or user.email


def _uniek_veldcode(bestaand: set[str], gewenst: str) -> str:
    """Maak de code uniek binnen de keuring.

    De code is waar het antwoord aan hangt in exports en rapportage. Twee velden
    met dezelfde code leveren een kolom op waarin twee metingen door elkaar
    lopen, en dat merk je pas als het rapport eruit rolt.
    """
    basis = (gewenst or "veld").strip().lower().replace(" ", "_")[:50] or "veld"
    if basis not in bestaand:
        return basis
    n = 2
    while f"{basis}_{n}" in bestaand:
        n += 1
    return f"{basis}_{n}"


def _valideer_veld(veldtype: str, opties: Optional[list[str]]) -> None:
    if veldtype not in kw.VELDTYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Onbekend veldtype '{veldtype}'. Kies uit: {', '.join(sorted(kw.VELDTYPES))}")
    if veldtype in kw.OPTIE_VELDTYPES and not opties:
        raise HTTPException(
            status_code=400,
            detail=f"Een veld van het type '{veldtype}' heeft keuze-opties nodig")


def _veld_to_dict(v: QualityField) -> dict:
    return {
        "id": v.id,
        "code": v.code,
        "label": v.label,
        "veldtype": v.veldtype,
        "veldtype_label": kw.VELDTYPES.get(v.veldtype, {}).get("label", v.veldtype),
        "toelichting": v.toelichting,
        "verplicht": v.verplicht,
        "eenheid": v.eenheid,
        "norm_min": v.norm_min,
        "norm_max": v.norm_max,
        "tolerantie": v.tolerantie,
        "opties": v.opties_lijst(),
        "eis_id": v.eis_id,
        "order_index": v.order_index,
        "is_bewijs": v.veldtype in kw.BEWIJS_VELDTYPES,
    }


def _eis_to_dict(e: QualityRequirement) -> dict:
    return {
        "id": e.id,
        "eisnummer": e.eisnummer,
        "titel": e.titel,
        "omschrijving": e.omschrijving,
        "norm": e.norm,
        "bron": e.bron,
        "referentie": e.referentie,
        "verplichting": e.verplichting,
        "tolerantie": e.tolerantie,
        "meetmethode": e.meetmethode,
        "bewijs_vereist": e.bewijs_vereist,
        "order_index": e.order_index,
    }


def _keuring_tellingen(db: Session, keuring_id: str) -> dict:
    """Aantallen per status. Een query, geen loop over registraties."""
    rijen = (db.query(QualityRegistration.status, QualityRegistration.resultaat)
               .filter(QualityRegistration.keuring_id == keuring_id)
               .all())
    afgerond = sum(1 for s, _ in rijen if s in ("ingediend", "in_beoordeling",
                                                "goedgekeurd", "afgekeurd"))
    return {
        "totaal": len(rijen),
        "concept": sum(1 for s, _ in rijen if s in ("concept", "in_uitvoering")),
        "ingediend": sum(1 for s, _ in rijen if s in ("ingediend", "in_beoordeling")),
        "goedgekeurd": sum(1 for s, _ in rijen if s == "goedgekeurd"),
        "afgekeurd": sum(1 for s, _ in rijen if s == "afgekeurd"),
        "afwijkend": sum(1 for _, r in rijen if r == "niet_akkoord"),
        "uitgevoerd": afgerond,
    }


def _keuring_to_dict(db: Session, k: QualityInspection, *, detail: bool = False) -> dict:
    tel = _keuring_tellingen(db, k.id)
    uit = {
        "id": k.id,
        "naam": k.naam,
        "keuringsnummer": k.keuringsnummer,
        "omschrijving": k.omschrijving,
        "werksoort": k.werksoort,
        "werksoort_label": kw.WERKSOORTEN.get(k.werksoort, k.werksoort),
        "project_id": k.project_id,
        "project_naam": k.project.name if k.project else None,
        "asset_id": k.asset_id,
        "werkvak": k.werkvak,
        "locatie": k.locatie,
        "activiteit": k.activiteit,
        "verantwoordelijke_id": k.verantwoordelijke_id,
        "verantwoordelijke_naam": _volledige_naam(k.verantwoordelijke) if k.verantwoordelijke else None,
        "uitvoerder_id": k.uitvoerder_id,
        "uitvoerder_naam": _volledige_naam(k.uitvoerder) if k.uitvoerder else None,
        "controleur_id": k.controleur_id,
        "controleur_naam": _volledige_naam(k.controleur) if k.controleur else None,
        "frequentie": k.frequentie,
        "frequentie_label": (k.frequentie_vrij if k.frequentie == "vrij"
                             else kw.FREQUENTIES.get(k.frequentie, k.frequentie)),
        "verwacht_aantal": k.verwacht_aantal,
        "prioriteit": k.prioriteit,
        "status": k.status,
        "template_code": k.template_code,
        "aantal_velden": len(k.velden or []),
        "aantal_eisen": len(k.eisen or []),
        "registraties": tel,
        "voortgang": kw.voortgang(tel["uitgevoerd"], k.verwacht_aantal),
        "created_at": k.created_at.isoformat() if k.created_at else None,
        "updated_at": k.updated_at.isoformat() if k.updated_at else None,
    }
    if detail:
        uit["velden"] = [_veld_to_dict(v) for v in (k.velden or [])]
        uit["eisen"] = [_eis_to_dict(e) for e in (k.eisen or [])]
    return uit


def _antwoord_waarde(a: QualityAnswer) -> Any:
    """Haal het antwoord uit de kolom die bij het veldtype hoort."""
    kolom = kw.VELDTYPES.get(a.veldtype_snapshot, {}).get("waarde_kolom", "answer_text")
    if kolom == "answer_number":
        return a.answer_number
    if kolom == "answer_bool":
        return a.answer_bool
    if kolom == "answer_date":
        return a.answer_date.isoformat() if a.answer_date else None
    if kolom == "answer_json":
        if not a.answer_json:
            return None
        try:
            return json.loads(a.answer_json)
        except (ValueError, TypeError):
            return None
    return a.answer_text


def _antwoord_to_dict(a: QualityAnswer) -> dict:
    return {
        "id": a.id,
        "veld_id": a.veld_id,
        "veld_code": a.veld_code,
        "label": a.label_snapshot,
        "veldtype": a.veldtype_snapshot,
        "eenheid": a.eenheid_snapshot,
        "waarde": _antwoord_waarde(a),
        "norm_min": a.norm_min_snapshot,
        "norm_max": a.norm_max_snapshot,
        "tolerantie": a.tolerantie_snapshot,
        "oordeel": a.oordeel,
        "toelichting": a.toelichting,
        "heeft_foto": bool(a.photo_url),
        "photo_url": a.photo_url,
        "beantwoord_op": a.beantwoord_op.isoformat() if a.beantwoord_op else None,
        "order_index": a.order_index,
    }


def _bewijs_to_dict(b: QualityEvidence, *, met_inhoud: bool = False) -> dict:
    """Bewijsstuk zonder de inhoud.

    `url` kan een base64 data-URL van een megabyte zijn zolang S3 niet is
    ingericht. Die hoort niet in een lijst thuis -- alleen in het detail van een
    stuk dat je opvraagt.
    """
    uit = {
        "id": b.id,
        "soort": b.soort,
        "titel": b.titel,
        "omschrijving": b.omschrijving,
        "mime": b.mime,
        "bestandsnaam": b.bestandsnaam,
        "bytes_grootte": b.bytes_grootte,
        "eis_id": b.eis_id,
        "veld_id": b.veld_id,
        "lat": b.lat,
        "lng": b.lng,
        "vastgelegd_op": b.vastgelegd_op.isoformat() if b.vastgelegd_op else None,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "created_by": b.created_by,
        "aangeleverd_door": _volledige_naam(b.creator) if b.creator else None,
    }
    if met_inhoud:
        uit["url"] = b.url
    return uit


def _registratie_to_dict(db: Session, r: QualityRegistration, *, detail: bool = False) -> dict:
    k = r.keuring
    antwoorden = list(r.antwoorden or [])
    bewijs = list(r.bewijs or [])
    ingevuld = sum(1 for a in antwoorden if a.beantwoord_op is not None)
    uit = {
        "id": r.id,
        "keuring_id": r.keuring_id,
        "keuring_naam": k.naam if k else None,
        "werksoort": k.werksoort if k else None,
        "project_id": k.project_id if k else None,
        "project_naam": k.project.name if k and k.project else None,
        "volgnummer": r.volgnummer,
        "datum": r.datum.isoformat() if r.datum else None,
        "werkvak": r.werkvak,
        "locatie": r.locatie,
        "lat": r.lat,
        "lng": r.lng,
        "uitvoerder_id": r.uitvoerder_id,
        "uitvoerder_naam": r.uitvoerder_naam,
        "status": r.status,
        "resultaat": r.resultaat,
        "resultaat_label": kw.RESULTATEN.get(r.resultaat) if r.resultaat else None,
        "afwijkend": r.afwijkend,
        "opmerking": r.opmerking,
        "heeft_handtekening": bool(r.handtekening_data_url),
        "handtekening_naam": r.handtekening_naam,
        "aantal_velden": len(antwoorden),
        "aantal_ingevuld": ingevuld,
        "aantal_bewijs": len(bewijs),
        "ingediend_op": r.ingediend_op.isoformat() if r.ingediend_op else None,
        "beoordeeld_op": r.beoordeeld_op.isoformat() if r.beoordeeld_op else None,
        "beoordelaar_naam": _volledige_naam(r.beoordelaar) if r.beoordelaar else None,
        "beoordeling_reden": r.beoordeling_reden,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
    if detail:
        uit["antwoorden"] = [_antwoord_to_dict(a) for a in antwoorden]
        uit["bewijs"] = [_bewijs_to_dict(b) for b in bewijs]
        uit["eisen"] = [_eis_to_dict(e) for e in (k.eisen or [])] if k else []
        uit["handtekening_data_url"] = r.handtekening_data_url
    return uit


def _oordeel_voor(veldtype: str, waarde: Any, veld_norm: dict) -> Optional[str]:
    """Bepaal het oordeel bij een antwoord.

    Alleen velden die iets beweren over kwaliteit krijgen een oordeel. Een
    foto, een datum of een opmerking is bewijs of context, geen goedkeuring --
    die tellen dus ook niet mee in het eindresultaat van de registratie.
    """
    if waarde is None or waarde == "":
        return None

    if veldtype == "meetwaarde":
        try:
            getal = float(waarde)
        except (TypeError, ValueError):
            return None
        return kw.beoordeel_meetwaarde(
            getal,
            norm_min=veld_norm.get("norm_min"),
            norm_max=veld_norm.get("norm_max"),
            tolerantie=veld_norm.get("tolerantie"))

    if veldtype == "getal":
        if veld_norm.get("norm_min") is None and veld_norm.get("norm_max") is None:
            return None
        try:
            getal = float(waarde)
        except (TypeError, ValueError):
            return None
        return kw.beoordeel_meetwaarde(
            getal,
            norm_min=veld_norm.get("norm_min"),
            norm_max=veld_norm.get("norm_max"),
            tolerantie=veld_norm.get("tolerantie"))

    if veldtype == "ja_nee":
        return "akkoord" if bool(waarde) else "niet_akkoord"

    if veldtype == "ja_nee_nvt":
        if waarde == "nvt":
            return None
        return "akkoord" if waarde == "ja" else "niet_akkoord"

    if veldtype == "akkoord":
        if waarde == "nvt":
            return None
        return "akkoord" if waarde == "akkoord" else "niet_akkoord"

    if veldtype == "keuring":
        if waarde == "nvt":
            return None
        return "akkoord" if waarde == "goed" else "niet_akkoord"

    return None


def _zet_antwoord(a: QualityAnswer, veldtype: str, waarde: Any) -> None:
    """Schrijf de waarde in de kolom die bij het veldtype hoort."""
    a.answer_number = None
    a.answer_bool = None
    a.answer_text = None
    a.answer_date = None
    a.answer_json = None

    if waarde is None:
        return

    kolom = kw.VELDTYPES.get(veldtype, {}).get("waarde_kolom", "answer_text")

    if kolom == "answer_number":
        try:
            a.answer_number = float(waarde)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail=f"'{a.label_snapshot}' verwacht een getal")
    elif kolom == "answer_bool":
        a.answer_bool = bool(waarde)
    elif kolom == "answer_date":
        if isinstance(waarde, datetime):
            a.answer_date = waarde
        else:
            try:
                a.answer_date = datetime.fromisoformat(str(waarde).replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(status_code=400,
                                    detail=f"'{a.label_snapshot}' verwacht een datum")
    elif kolom == "answer_json":
        if not isinstance(waarde, (list, dict)):
            raise HTTPException(status_code=400,
                                detail=f"'{a.label_snapshot}' verwacht een lijst met keuzes")
        a.answer_json = json.dumps(waarde)
    else:
        a.answer_text = str(waarde)


def _herbereken(db: Session, r: QualityRegistration) -> None:
    """Rol de veldoordelen op naar het resultaat van de registratie."""
    oordelen = [a.oordeel for a in (r.antwoorden or []) if a.oordeel]
    r.resultaat = kw.beoordeel_registratie(oordelen)
    r.afwijkend = any(o == "niet_akkoord" for o in oordelen)
    if r.status == "concept" and any(a.beantwoord_op for a in (r.antwoorden or [])):
        r.status = "in_uitvoering"


# ── Config en sjablonen ──────────────────────────────────────────────

@router.get("/config")
def config(current_user: User = Depends(get_current_user)):
    """Alles wat de front-end nodig heeft om de builder te tekenen."""
    return {
        "versie": kw.KWALITEIT_VERSION,
        "veldtypes": [{"code": c, "label": v["label"],
                       "is_bewijs": c in kw.BEWIJS_VELDTYPES,
                       "heeft_opties": c in kw.OPTIE_VELDTYPES,
                       "heeft_norm": c in ("meetwaarde", "getal")}
                      for c, v in kw.VELDTYPES.items()],
        "werksoorten": [{"code": c, "label": lb} for c, lb in kw.WERKSOORTEN.items()],
        "frequenties": [{"code": c, "label": lb} for c, lb in kw.FREQUENTIES.items()],
        "resultaten": [{"code": c, "label": lb} for c, lb in kw.RESULTATEN.items()],
        "bewijs_soorten": sorted(BEWIJS_SOORTEN),
        "prioriteiten": sorted(PRIORITEITEN),
        "akkoord_waarden": list(kw.AKKOORD_WAARDEN),
        "keuring_waarden": list(kw.KEURING_WAARDEN),
        "ja_nee_nvt_waarden": list(kw.JA_NEE_NVT_WAARDEN),
    }


@router.get("/templates")
def templates(
    werksoort: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
):
    """De standaardkeuringen. Kopieer er een naar een eigen keuring.

    Zonder deze bibliotheek moet iedere aannemer eerst een half uur een
    formulier bouwen voordat hij iets kan controleren, en dan gebeurt het niet.
    """
    if werksoort and werksoort not in kw.WERKSOORTEN:
        raise HTTPException(status_code=400, detail="Onbekende werksoort")
    return [{
        "code": t["code"],
        "werksoort": t["werksoort"],
        "werksoort_label": kw.WERKSOORTEN.get(t["werksoort"], t["werksoort"]),
        "naam": t["naam"],
        "omschrijving": t.get("omschrijving"),
        "frequentie": t.get("frequentie"),
        "aantal_velden": len(t.get("velden") or []),
        "aantal_eisen": len(t.get("eisen") or []),
        "velden": t.get("velden") or [],
        "eisen": t.get("eisen") or [],
    } for t in kw.templates_voor(werksoort)]


# ── Keuringen ────────────────────────────────────────────────────────

@router.get("/keuringen")
def lijst_keuringen(
    project_id: Optional[str] = Query(default=None),
    werksoort: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    zoek: Optional[str] = Query(default=None, max_length=120),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = (db.query(QualityInspection)
           .filter(QualityInspection.organization_id == current_user.organization_id))
    if project_id:
        q = q.filter(QualityInspection.project_id == project_id)
    if werksoort:
        q = q.filter(QualityInspection.werksoort == werksoort)
    if status:
        q = q.filter(QualityInspection.status == status)
    if zoek:
        naald = f"%{zoek.strip()}%"
        q = q.filter(QualityInspection.naam.ilike(naald))
    totaal = q.count()
    rijen = (q.order_by(QualityInspection.created_at.desc())
              .offset(offset).limit(limit).all())
    return {
        "totaal": totaal,
        "limit": limit,
        "offset": offset,
        "keuringen": [_keuring_to_dict(db, k) for k in rijen],
    }


@router.post("/keuringen")
def maak_keuring(
    payload: KeuringIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Maak een keuring, los of vanaf een sjabloon.

    Met een `template_code` komen naam, velden en eisen mee uit de bibliotheek.
    Vanaf dat moment is het een eigen keuring van de organisatie: aanpassen mag,
    en het sjabloon verandert er niet van mee.
    """
    _eis_beheer(current_user)

    sjabloon = kw.template(payload.template_code) if payload.template_code else None
    if payload.template_code and not sjabloon:
        raise HTTPException(status_code=400, detail="Onbekend sjabloon")

    naam = (payload.naam or "").strip() or (sjabloon["naam"] if sjabloon else "")
    if not naam:
        raise HTTPException(status_code=400, detail="Geef de keuring een naam")

    werksoort = payload.werksoort or (sjabloon["werksoort"] if sjabloon else "algemeen")
    if werksoort not in kw.WERKSOORTEN:
        raise HTTPException(
            status_code=400,
            detail=f"Onbekende werksoort. Kies uit: {', '.join(kw.WERKSOORTEN)}")

    frequentie = payload.frequentie or (sjabloon.get("frequentie") if sjabloon else "eenmalig")
    if frequentie not in kw.FREQUENTIES:
        raise HTTPException(
            status_code=400,
            detail=f"Onbekende frequentie. Kies uit: {', '.join(kw.FREQUENTIES)}")
    if frequentie == "vrij" and not (payload.frequentie_vrij or "").strip():
        raise HTTPException(status_code=400,
                            detail="Omschrijf de frequentie als je 'vrij' kiest")

    prioriteit = payload.prioriteit or "normaal"
    if prioriteit not in PRIORITEITEN:
        raise HTTPException(status_code=400, detail="Onbekende prioriteit")

    if payload.project_id:
        project = (db.query(Project)
                     .filter(Project.id == payload.project_id,
                             Project.organization_id == current_user.organization_id)
                     .first())
        if not project:
            raise HTTPException(status_code=404, detail="Project niet gevonden")
    if payload.asset_id:
        asset = (db.query(Asset)
                   .filter(Asset.id == payload.asset_id,
                           Asset.organization_id == current_user.organization_id)
                   .first())
        if not asset:
            raise HTTPException(status_code=404, detail="Asset niet gevonden")

    k = QualityInspection(
        organization_id=current_user.organization_id,
        naam=naam[:255],
        keuringsnummer=(payload.keuringsnummer or "").strip() or None,
        omschrijving=payload.omschrijving or (sjabloon.get("omschrijving") if sjabloon else None),
        werksoort=werksoort,
        project_id=payload.project_id,
        asset_id=payload.asset_id,
        werkvak=(payload.werkvak or "").strip() or None,
        locatie=(payload.locatie or "").strip() or None,
        activiteit=(payload.activiteit or "").strip() or None,
        verantwoordelijke_id=payload.verantwoordelijke_id or current_user.id,
        uitvoerder_id=payload.uitvoerder_id,
        controleur_id=payload.controleur_id,
        frequentie=frequentie,
        frequentie_vrij=(payload.frequentie_vrij or "").strip() or None,
        verwacht_aantal=payload.verwacht_aantal,
        prioriteit=prioriteit,
        status="actief",
        template_code=payload.template_code,
        template_versie=kw.KWALITEIT_VERSION if sjabloon else None,
        created_by=current_user.id,
    )
    db.add(k)
    db.flush()

    # Eerst de eisen, dan de velden -- een veld kan naar een eis verwijzen.
    eis_op_nummer: dict[str, str] = {}
    if sjabloon:
        for i, e in enumerate(sjabloon.get("eisen") or []):
            eis = QualityRequirement(
                keuring_id=k.id,
                organization_id=current_user.organization_id,
                eisnummer=e["eisnummer"],
                titel=e["titel"][:255],
                omschrijving=e.get("omschrijving"),
                norm=e.get("norm"),
                bron=e.get("bron"),
                meetmethode=e.get("meetmethode"),
                tolerantie=e.get("tolerantie"),
                bewijs_vereist=bool(e.get("bewijs_vereist")),
                order_index=i,
            )
            db.add(eis)
            db.flush()
            eis_op_nummer[e["eisnummer"]] = eis.id

        for i, v in enumerate(sjabloon.get("velden") or []):
            db.add(QualityField(
                keuring_id=k.id,
                organization_id=current_user.organization_id,
                code=v["code"],
                label=v["label"][:255],
                veldtype=v["veldtype"],
                toelichting=v.get("toelichting"),
                verplicht=bool(v.get("verplicht")),
                eenheid=v.get("eenheid"),
                norm_min=v.get("norm_min"),
                norm_max=v.get("norm_max"),
                tolerantie=v.get("tolerantie"),
                opties=json.dumps(v["opties"]) if v.get("opties") else None,
                order_index=i,
            ))

    db.commit()
    db.refresh(k)
    log_action(db, request, current_user, action=ACTION.KWALITEIT_KEURING_CREATE,
               entity_type="quality_inspection", entity_id=k.id,
               after={"naam": k.naam, "werksoort": k.werksoort,
                      "template": k.template_code,
                      "velden": len(k.velden or []), "eisen": len(k.eisen or [])})
    return _keuring_to_dict(db, k, detail=True)


@router.get("/keuringen/{keuring_id}")
def keuring_detail(
    keuring_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    k = _keuring_of_404(db, keuring_id, current_user)
    return _keuring_to_dict(db, k, detail=True)


@router.patch("/keuringen/{keuring_id}")
def wijzig_keuring(
    keuring_id: str,
    payload: KeuringUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    k = _keuring_of_404(db, keuring_id, current_user)
    voor = {"naam": k.naam, "status": k.status, "verwacht_aantal": k.verwacht_aantal}

    velden = payload.model_dump(exclude_unset=True)
    if "werksoort" in velden and velden["werksoort"] not in kw.WERKSOORTEN:
        raise HTTPException(status_code=400, detail="Onbekende werksoort")
    if "frequentie" in velden and velden["frequentie"] not in kw.FREQUENTIES:
        raise HTTPException(status_code=400, detail="Onbekende frequentie")
    if "prioriteit" in velden and velden["prioriteit"] not in PRIORITEITEN:
        raise HTTPException(status_code=400, detail="Onbekende prioriteit")
    if "status" in velden and velden["status"] not in KEURING_STATUSSEN:
        raise HTTPException(status_code=400, detail="Onbekende status")

    for naam_veld, waarde in velden.items():
        if naam_veld == "template_code":
            continue  # herkomst blijft staan
        if hasattr(k, naam_veld):
            setattr(k, naam_veld, waarde)

    db.commit()
    db.refresh(k)
    log_action(db, request, current_user, action=ACTION.KWALITEIT_KEURING_UPDATE,
               entity_type="quality_inspection", entity_id=k.id,
               before=voor, after={"naam": k.naam, "status": k.status,
                                   "verwacht_aantal": k.verwacht_aantal})
    return _keuring_to_dict(db, k, detail=True)


@router.delete("/keuringen/{keuring_id}")
def verwijder_keuring(
    keuring_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verwijderen mag alleen zolang er niets is ingediend.

    Een keuring met ingediende registraties is onderdeel van het dossier. Die
    weggooien wist bewijs dat iemand ooit nodig heeft; archiveren kan wel.
    """
    _eis_beheer(current_user)
    k = _keuring_of_404(db, keuring_id, current_user)

    ingediend = (db.query(QualityRegistration)
                   .filter(QualityRegistration.keuring_id == k.id,
                           QualityRegistration.status.in_(
                               ["ingediend", "in_beoordeling", "goedgekeurd", "afgekeurd"]))
                   .count())
    if ingediend:
        raise HTTPException(
            status_code=409,
            detail=(f"Deze keuring heeft {ingediend} ingediende registratie(s) en hoort bij "
                    "het dossier. Zet hem op gearchiveerd in plaats van verwijderen."))

    naam = k.naam
    db.delete(k)
    db.commit()
    log_action(db, request, current_user, action=ACTION.KWALITEIT_KEURING_DELETE,
               entity_type="quality_inspection", entity_id=keuring_id,
               before={"naam": naam})
    return {"verwijderd": True}


# ── Velden (de builder) ──────────────────────────────────────────────

@router.post("/keuringen/{keuring_id}/velden")
def voeg_veld_toe(
    keuring_id: str,
    payload: VeldIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    k = _keuring_of_404(db, keuring_id, current_user)
    _valideer_veld(payload.veldtype, payload.opties)

    if payload.eis_id:
        eis = (db.query(QualityRequirement)
                 .filter(QualityRequirement.id == payload.eis_id,
                         QualityRequirement.keuring_id == k.id)
                 .first())
        if not eis:
            raise HTTPException(status_code=404, detail="Eis niet gevonden bij deze keuring")

    bestaand = {v.code for v in (k.velden or [])}
    code = _uniek_veldcode(bestaand, payload.code or payload.label)
    volgende = max((v.order_index for v in (k.velden or [])), default=-1) + 1

    v = QualityField(
        keuring_id=k.id,
        organization_id=current_user.organization_id,
        code=code,
        label=payload.label[:255],
        veldtype=payload.veldtype,
        toelichting=payload.toelichting,
        verplicht=payload.verplicht,
        eenheid=payload.eenheid,
        norm_min=payload.norm_min,
        norm_max=payload.norm_max,
        tolerantie=payload.tolerantie,
        opties=json.dumps(payload.opties) if payload.opties else None,
        eis_id=payload.eis_id,
        order_index=payload.order_index if payload.order_index is not None else volgende,
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    log_action(db, request, current_user, action=ACTION.KWALITEIT_VELD_CREATE,
               entity_type="quality_field", entity_id=v.id,
               after={"keuring_id": k.id, "code": v.code, "veldtype": v.veldtype})
    return _veld_to_dict(v)


@router.patch("/keuringen/{keuring_id}/velden/{veld_id}")
def wijzig_veld(
    keuring_id: str,
    veld_id: str,
    payload: VeldUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    k = _keuring_of_404(db, keuring_id, current_user)
    v = (db.query(QualityField)
           .filter(QualityField.id == veld_id, QualityField.keuring_id == k.id)
           .first())
    if not v:
        raise HTTPException(status_code=404, detail="Veld niet gevonden")

    velden = payload.model_dump(exclude_unset=True)
    nieuw_type = velden.get("veldtype", v.veldtype)
    nieuwe_opties = velden.get("opties", v.opties_lijst())
    _valideer_veld(nieuw_type, nieuwe_opties)

    voor = {"label": v.label, "veldtype": v.veldtype,
            "norm_min": v.norm_min, "norm_max": v.norm_max, "tolerantie": v.tolerantie}

    for naam_veld, waarde in velden.items():
        if naam_veld == "opties":
            v.opties = json.dumps(waarde) if waarde else None
        elif hasattr(v, naam_veld):
            setattr(v, naam_veld, waarde)

    db.commit()
    db.refresh(v)
    log_action(db, request, current_user, action=ACTION.KWALITEIT_VELD_UPDATE,
               entity_type="quality_field", entity_id=v.id,
               before=voor,
               after={"label": v.label, "veldtype": v.veldtype,
                      "norm_min": v.norm_min, "norm_max": v.norm_max,
                      "tolerantie": v.tolerantie})
    return _veld_to_dict(v)


@router.delete("/keuringen/{keuring_id}/velden/{veld_id}")
def verwijder_veld(
    keuring_id: str,
    veld_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Een veld weghalen wist ook de antwoorden erop.

    Daarom kan het niet meer zodra er een registratie is ingediend: dan zou een
    goedgekeurde registratie achteraf een vraag minder hebben.
    """
    _eis_beheer(current_user)
    k = _keuring_of_404(db, keuring_id, current_user)
    v = (db.query(QualityField)
           .filter(QualityField.id == veld_id, QualityField.keuring_id == k.id)
           .first())
    if not v:
        raise HTTPException(status_code=404, detail="Veld niet gevonden")

    ingediend = (db.query(QualityRegistration)
                   .filter(QualityRegistration.keuring_id == k.id,
                           QualityRegistration.status.in_(
                               ["ingediend", "in_beoordeling", "goedgekeurd", "afgekeurd"]))
                   .count())
    if ingediend:
        raise HTTPException(
            status_code=409,
            detail=("Er zijn al registraties ingediend met dit veld. Verwijderen zou het "
                    "dossier veranderen; maak een nieuwe keuring als de opzet moet wijzigen."))

    code = v.code
    db.delete(v)
    db.commit()
    log_action(db, request, current_user, action=ACTION.KWALITEIT_VELD_DELETE,
               entity_type="quality_field", entity_id=veld_id,
               before={"keuring_id": k.id, "code": code})
    return {"verwijderd": True}


# ── Eisen ────────────────────────────────────────────────────────────

@router.post("/keuringen/{keuring_id}/eisen")
def voeg_eis_toe(
    keuring_id: str,
    payload: EisIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    k = _keuring_of_404(db, keuring_id, current_user)

    verplichting = payload.verplichting or "eis"
    if verplichting not in VERPLICHTINGEN:
        raise HTTPException(
            status_code=400,
            detail=f"Onbekende verplichting. Kies uit: {', '.join(sorted(VERPLICHTINGEN))}")

    volgende = max((e.order_index for e in (k.eisen or [])), default=-1) + 1
    nummer = (payload.eisnummer or "").strip() or f"{volgende + 1:02d}"

    e = QualityRequirement(
        keuring_id=k.id,
        organization_id=current_user.organization_id,
        eisnummer=nummer[:20],
        titel=payload.titel[:255],
        omschrijving=payload.omschrijving,
        norm=payload.norm,
        bron=payload.bron,
        referentie=payload.referentie,
        verplichting=verplichting,
        tolerantie=payload.tolerantie,
        meetmethode=payload.meetmethode,
        bewijs_vereist=payload.bewijs_vereist,
        order_index=payload.order_index if payload.order_index is not None else volgende,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    log_action(db, request, current_user, action=ACTION.KWALITEIT_EIS_CREATE,
               entity_type="quality_requirement", entity_id=e.id,
               after={"keuring_id": k.id, "eisnummer": e.eisnummer, "titel": e.titel,
                      "bewijs_vereist": e.bewijs_vereist})
    return _eis_to_dict(e)


@router.patch("/keuringen/{keuring_id}/eisen/{eis_id}")
def wijzig_eis(
    keuring_id: str,
    eis_id: str,
    payload: EisUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    k = _keuring_of_404(db, keuring_id, current_user)
    e = (db.query(QualityRequirement)
           .filter(QualityRequirement.id == eis_id, QualityRequirement.keuring_id == k.id)
           .first())
    if not e:
        raise HTTPException(status_code=404, detail="Eis niet gevonden")

    velden = payload.model_dump(exclude_unset=True)
    if "verplichting" in velden and velden["verplichting"] not in VERPLICHTINGEN:
        raise HTTPException(status_code=400, detail="Onbekende verplichting")

    voor = {"titel": e.titel, "norm": e.norm, "bewijs_vereist": e.bewijs_vereist}
    for naam_veld, waarde in velden.items():
        if hasattr(e, naam_veld):
            setattr(e, naam_veld, waarde)

    db.commit()
    db.refresh(e)
    log_action(db, request, current_user, action=ACTION.KWALITEIT_EIS_UPDATE,
               entity_type="quality_requirement", entity_id=e.id,
               before=voor,
               after={"titel": e.titel, "norm": e.norm, "bewijs_vereist": e.bewijs_vereist})
    return _eis_to_dict(e)


@router.delete("/keuringen/{keuring_id}/eisen/{eis_id}")
def verwijder_eis(
    keuring_id: str,
    eis_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    k = _keuring_of_404(db, keuring_id, current_user)
    e = (db.query(QualityRequirement)
           .filter(QualityRequirement.id == eis_id, QualityRequirement.keuring_id == k.id)
           .first())
    if not e:
        raise HTTPException(status_code=404, detail="Eis niet gevonden")

    nummer = e.eisnummer
    db.delete(e)
    db.commit()
    log_action(db, request, current_user, action=ACTION.KWALITEIT_EIS_DELETE,
               entity_type="quality_requirement", entity_id=eis_id,
               before={"keuring_id": k.id, "eisnummer": nummer})
    return {"verwijderd": True}


# ── Registraties ─────────────────────────────────────────────────────

@router.post("/keuringen/{keuring_id}/registraties")
def start_registratie(
    keuring_id: str,
    payload: RegistratieIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start een registratie. Alle antwoordrijen worden meteen aangemaakt.

    Bewust vooraf en niet gaandeweg: dan staat de complete lijst op je scherm en
    zie je wat je nog niet gehad hebt. Zelfde keuze als bij de LMRA en de
    BOEI-opname.
    """
    _eis_registreren(current_user)
    k = _keuring_of_404(db, keuring_id, current_user)

    if k.status == "gearchiveerd":
        raise HTTPException(status_code=409,
                            detail="Deze keuring is gearchiveerd en kan niet meer worden ingevuld")
    if not (k.velden or []):
        raise HTTPException(
            status_code=409,
            detail="Deze keuring heeft nog geen velden. Voeg er eerst een toe.")

    hoogste = (db.query(QualityRegistration.volgnummer)
                 .filter(QualityRegistration.keuring_id == k.id)
                 .order_by(QualityRegistration.volgnummer.desc())
                 .first())
    volgnummer = ((hoogste[0] or 0) + 1) if hoogste else 1

    r = QualityRegistration(
        keuring_id=k.id,
        organization_id=current_user.organization_id,
        volgnummer=volgnummer,
        datum=payload.datum or datetime.now(timezone.utc),
        werkvak=(payload.werkvak or "").strip() or k.werkvak,
        locatie=(payload.locatie or "").strip() or k.locatie,
        lat=payload.lat,
        lng=payload.lng,
        uitvoerder_id=current_user.id,
        uitvoerder_naam=(payload.uitvoerder_naam or "").strip() or _volledige_naam(current_user),
        status="concept",
        resultaat="niet_beoordeeld",
        created_by=current_user.id,
    )
    db.add(r)
    db.flush()

    for v in (k.velden or []):
        db.add(QualityAnswer(
            registratie_id=r.id,
            organization_id=current_user.organization_id,
            veld_id=v.id,
            veld_code=v.code,
            label_snapshot=v.label[:255],
            veldtype_snapshot=v.veldtype,
            eenheid_snapshot=v.eenheid,
            norm_min_snapshot=v.norm_min,
            norm_max_snapshot=v.norm_max,
            tolerantie_snapshot=v.tolerantie,
            order_index=v.order_index,
        ))

    db.commit()
    db.refresh(r)
    log_action(db, request, current_user, action=ACTION.KWALITEIT_REG_CREATE,
               entity_type="quality_registration", entity_id=r.id,
               after={"keuring_id": k.id, "keuring": k.naam, "volgnummer": r.volgnummer,
                      "werkvak": r.werkvak, "velden": len(k.velden or [])})
    return _registratie_to_dict(db, r, detail=True)


@router.get("/registraties")
def lijst_registraties(
    keuring_id: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    resultaat: Optional[str] = Query(default=None),
    alleen_afwijkend: bool = Query(default=False),
    uitvoerder_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Registraties, gepagineerd en zonder bewijsmateriaal in de rijen."""
    q = (db.query(QualityRegistration)
           .filter(QualityRegistration.organization_id == current_user.organization_id))
    if keuring_id:
        q = q.filter(QualityRegistration.keuring_id == keuring_id)
    if project_id:
        q = (q.join(QualityInspection,
                    QualityInspection.id == QualityRegistration.keuring_id)
              .filter(QualityInspection.project_id == project_id))
    if status:
        q = q.filter(QualityRegistration.status == status)
    if resultaat:
        q = q.filter(QualityRegistration.resultaat == resultaat)
    if alleen_afwijkend:
        q = q.filter(QualityRegistration.afwijkend.is_(True))
    if uitvoerder_id:
        q = q.filter(QualityRegistration.uitvoerder_id == uitvoerder_id)

    totaal = q.count()
    rijen = (q.order_by(QualityRegistration.datum.desc())
              .offset(offset).limit(limit).all())
    return {
        "totaal": totaal,
        "limit": limit,
        "offset": offset,
        "registraties": [_registratie_to_dict(db, r) for r in rijen],
    }


@router.get("/registraties/{registratie_id}")
def registratie_detail(
    registratie_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    r = _registratie_of_404(db, registratie_id, current_user)
    return _registratie_to_dict(db, r, detail=True)


@router.patch("/registraties/{registratie_id}")
def wijzig_registratie(
    registratie_id: str,
    payload: RegistratieUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_registreren(current_user)
    r = _registratie_of_404(db, registratie_id, current_user)
    if r.status in ("goedgekeurd", "afgekeurd"):
        raise HTTPException(status_code=409,
                            detail="Deze registratie is beoordeeld en kan niet meer wijzigen")

    for naam_veld, waarde in payload.model_dump(exclude_unset=True).items():
        if hasattr(r, naam_veld):
            setattr(r, naam_veld, waarde)

    db.commit()
    db.refresh(r)
    return _registratie_to_dict(db, r, detail=True)


@router.patch("/registraties/{registratie_id}/antwoorden/{antwoord_id}")
def beantwoord(
    registratie_id: str,
    antwoord_id: str,
    payload: AntwoordIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Vul een veld in. Het oordeel volgt meteen uit de norm.

    Valt een meetwaarde buiten de tolerantie, dan komt het antwoord terug met
    `oordeel = niet_akkoord` en staat de registratie op afwijkend. De uitvoerder
    kan gewoon doorgaan -- het systeem beoordeelt de meting, niet de situatie.
    Wat er werkelijk aan de hand is, bepaalt de mens.
    """
    _eis_registreren(current_user)
    r = _registratie_of_404(db, registratie_id, current_user)
    if r.status in ("goedgekeurd", "afgekeurd"):
        raise HTTPException(status_code=409,
                            detail="Deze registratie is beoordeeld en kan niet meer wijzigen")

    a = (db.query(QualityAnswer)
           .filter(QualityAnswer.id == antwoord_id,
                   QualityAnswer.registratie_id == r.id)
           .first())
    if not a:
        raise HTTPException(status_code=404, detail="Antwoord niet gevonden")

    velden = payload.model_dump(exclude_unset=True)
    voor = {"waarde": _antwoord_waarde(a), "oordeel": a.oordeel}

    if "waarde" in velden:
        _zet_antwoord(a, a.veldtype_snapshot, velden["waarde"])
        a.oordeel = _oordeel_voor(a.veldtype_snapshot, velden["waarde"], {
            "norm_min": a.norm_min_snapshot,
            "norm_max": a.norm_max_snapshot,
            "tolerantie": a.tolerantie_snapshot,
        })
        a.beantwoord_op = datetime.now(timezone.utc)
        a.beantwoord_door = current_user.id

    if "toelichting" in velden:
        a.toelichting = velden["toelichting"]

    if "photo_url" in velden and velden["photo_url"]:
        from photo_storage import maybe_offload
        a.photo_url = maybe_offload(velden["photo_url"],
                                    organization_id=current_user.organization_id,
                                    kind="kwaliteit")
        if not a.beantwoord_op:
            a.beantwoord_op = datetime.now(timezone.utc)
            a.beantwoord_door = current_user.id

    db.flush()
    _herbereken(db, r)
    db.commit()
    db.refresh(a)

    log_action(db, request, current_user, action=ACTION.KWALITEIT_REG_ANSWER,
               entity_type="quality_answer", entity_id=a.id,
               before=voor,
               after={"registratie_id": r.id, "veld": a.veld_code,
                      "waarde": _antwoord_waarde(a), "oordeel": a.oordeel})
    return {
        "antwoord": _antwoord_to_dict(a),
        "registratie": {"id": r.id, "status": r.status,
                        "resultaat": r.resultaat, "afwijkend": r.afwijkend},
    }


# ── Bewijsmateriaal ──────────────────────────────────────────────────

@router.post("/registraties/{registratie_id}/bewijs")
def voeg_bewijs_toe(
    registratie_id: str,
    payload: BewijsIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Hang een foto, bon, certificaat of meetrapport aan de registratie.

    De koppeling naar eis en veld is optioneel maar wel het punt: een foto
    zonder verband is over een jaar een foto in een map. Met een eis erbij weet
    je waarom hij er is.
    """
    _eis_registreren(current_user)
    r = _registratie_of_404(db, registratie_id, current_user)
    if r.status in ("goedgekeurd", "afgekeurd"):
        raise HTTPException(status_code=409,
                            detail="Deze registratie is beoordeeld en kan niet meer wijzigen")

    soort = payload.soort or "foto"
    if soort not in BEWIJS_SOORTEN:
        raise HTTPException(
            status_code=400,
            detail=f"Onbekende soort bewijs. Kies uit: {', '.join(sorted(BEWIJS_SOORTEN))}")

    if payload.eis_id:
        eis = (db.query(QualityRequirement)
                 .filter(QualityRequirement.id == payload.eis_id,
                         QualityRequirement.keuring_id == r.keuring_id)
                 .first())
        if not eis:
            raise HTTPException(status_code=404, detail="Eis niet gevonden bij deze keuring")
    if payload.veld_id:
        veld = (db.query(QualityField)
                  .filter(QualityField.id == payload.veld_id,
                          QualityField.keuring_id == r.keuring_id)
                  .first())
        if not veld:
            raise HTTPException(status_code=404, detail="Veld niet gevonden bij deze keuring")

    from photo_storage import maybe_offload
    url = maybe_offload(payload.url, organization_id=current_user.organization_id,
                        kind="kwaliteit") or payload.url

    b = QualityEvidence(
        registratie_id=r.id,
        organization_id=current_user.organization_id,
        eis_id=payload.eis_id,
        veld_id=payload.veld_id,
        soort=soort,
        titel=(payload.titel or "").strip() or None,
        omschrijving=payload.omschrijving,
        url=url,
        mime=payload.mime,
        bestandsnaam=payload.bestandsnaam,
        bytes_grootte=len(payload.url) if payload.url else None,
        lat=payload.lat if payload.lat is not None else r.lat,
        lng=payload.lng if payload.lng is not None else r.lng,
        vastgelegd_op=payload.vastgelegd_op or datetime.now(timezone.utc),
        created_by=current_user.id,
    )
    db.add(b)
    if r.status == "concept":
        r.status = "in_uitvoering"
    db.commit()
    db.refresh(b)

    log_action(db, request, current_user, action=ACTION.KWALITEIT_BEWIJS_ADD,
               entity_type="quality_evidence", entity_id=b.id,
               after={"registratie_id": r.id, "soort": b.soort, "eis_id": b.eis_id,
                      "veld_id": b.veld_id, "bestandsnaam": b.bestandsnaam})
    return _bewijs_to_dict(b)


@router.get("/registraties/{registratie_id}/bewijs/{bewijs_id}")
def bewijs_detail(
    registratie_id: str,
    bewijs_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Een bewijsstuk inclusief de inhoud. Bewust per stuk op te vragen."""
    r = _registratie_of_404(db, registratie_id, current_user)
    b = (db.query(QualityEvidence)
           .filter(QualityEvidence.id == bewijs_id,
                   QualityEvidence.registratie_id == r.id)
           .first())
    if not b:
        raise HTTPException(status_code=404, detail="Bewijsstuk niet gevonden")
    return _bewijs_to_dict(b, met_inhoud=True)


@router.delete("/registraties/{registratie_id}/bewijs/{bewijs_id}")
def verwijder_bewijs(
    registratie_id: str,
    bewijs_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_registreren(current_user)
    r = _registratie_of_404(db, registratie_id, current_user)
    if r.status in ("goedgekeurd", "afgekeurd"):
        raise HTTPException(status_code=409,
                            detail="Deze registratie is beoordeeld; bewijs blijft staan")

    b = (db.query(QualityEvidence)
           .filter(QualityEvidence.id == bewijs_id,
                   QualityEvidence.registratie_id == r.id)
           .first())
    if not b:
        raise HTTPException(status_code=404, detail="Bewijsstuk niet gevonden")

    soort, naam = b.soort, b.bestandsnaam
    db.delete(b)
    db.commit()
    log_action(db, request, current_user, action=ACTION.KWALITEIT_BEWIJS_DELETE,
               entity_type="quality_evidence", entity_id=bewijs_id,
               before={"registratie_id": r.id, "soort": soort, "bestandsnaam": naam})
    return {"verwijderd": True}


# ── Indienen en beoordelen ───────────────────────────────────────────

@router.post("/registraties/{registratie_id}/indienen")
def indienen(
    registratie_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dien de registratie in ter beoordeling.

    Twee dingen worden hier hard gecontroleerd: verplichte velden zijn ingevuld,
    en eisen met bewijsplicht hebben bewijs. Dat is het verschil tussen een
    afvinklijst en een dossier -- als je hier soepel bent, is de goedkeuring
    later niets waard.

    Een registratie die niet akkoord is mag wel worden ingediend. Juist die moet
    langs de controleur.
    """
    _eis_registreren(current_user)
    r = _registratie_of_404(db, registratie_id, current_user)
    if r.status in ("ingediend", "in_beoordeling"):
        raise HTTPException(status_code=409, detail="Deze registratie is al ingediend")
    if r.status in ("goedgekeurd", "afgekeurd"):
        raise HTTPException(status_code=409, detail="Deze registratie is al beoordeeld")

    ontbreekt: list[str] = []
    velden_op_id = {v.id: v for v in (r.keuring.velden or [])}
    for a in (r.antwoorden or []):
        veld = velden_op_id.get(a.veld_id)
        if veld and veld.verplicht and a.beantwoord_op is None and not a.photo_url:
            ontbreekt.append(a.label_snapshot or a.veld_code)
    if ontbreekt:
        raise HTTPException(
            status_code=400,
            detail="Nog niet compleet: " + ", ".join(ontbreekt[:8])
                   + (f" en {len(ontbreekt) - 8} meer" if len(ontbreekt) > 8 else ""))

    bewijs_bij_eis = {b.eis_id for b in (r.bewijs or []) if b.eis_id}
    zonder_bewijs = [e.eisnummer + " " + e.titel
                     for e in (r.keuring.eisen or [])
                     if e.bewijs_vereist and e.id not in bewijs_bij_eis]
    if zonder_bewijs:
        raise HTTPException(
            status_code=400,
            detail="Bewijs ontbreekt bij: " + "; ".join(zonder_bewijs[:5])
                   + (f" en {len(zonder_bewijs) - 5} meer" if len(zonder_bewijs) > 5 else ""))

    _herbereken(db, r)
    r.status = "ingediend"
    r.ingediend_op = datetime.now(timezone.utc)
    db.commit()
    db.refresh(r)

    log_action(db, request, current_user, action=ACTION.KWALITEIT_REG_SUBMIT,
               entity_type="quality_registration", entity_id=r.id,
               after={"keuring_id": r.keuring_id, "resultaat": r.resultaat,
                      "afwijkend": r.afwijkend, "bewijs": len(r.bewijs or [])})
    return _registratie_to_dict(db, r, detail=True)


@router.post("/registraties/{registratie_id}/beoordelen")
def beoordelen(
    registratie_id: str,
    payload: BeoordelingIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Goedkeuren, afkeuren of terugsturen. Afkeuren vraagt om een reden.

    Zonder reden is een afkeur voor de uitvoerder een muur: hij weet niet wat
    hij moet herstellen en de volgende registratie is dezelfde.
    """
    _eis_beoordelen(current_user)
    r = _registratie_of_404(db, registratie_id, current_user)
    if r.status not in ("ingediend", "in_beoordeling"):
        raise HTTPException(
            status_code=409,
            detail="Alleen een ingediende registratie kan worden beoordeeld")

    besluit = payload.besluit
    if besluit not in ("goedkeuren", "afkeuren", "terugsturen"):
        raise HTTPException(status_code=400,
                            detail="Kies goedkeuren, afkeuren of terugsturen")
    reden = (payload.reden or "").strip()
    if besluit in ("afkeuren", "terugsturen") and not reden:
        raise HTTPException(
            status_code=400,
            detail="Geef een reden op, anders weet de uitvoerder niet wat er moet gebeuren")

    voor = {"status": r.status}
    r.status = {"goedkeuren": "goedgekeurd",
                "afkeuren": "afgekeurd",
                "terugsturen": "herziening_vereist"}[besluit]
    r.beoordeeld_op = datetime.now(timezone.utc)
    r.beoordeeld_door = current_user.id
    r.beoordeling_reden = reden or None
    db.commit()
    db.refresh(r)

    log_action(db, request, current_user, action=ACTION.KWALITEIT_REG_REVIEW,
               entity_type="quality_registration", entity_id=r.id,
               before=voor,
               after={"status": r.status, "besluit": besluit, "reden": reden or None})
    return _registratie_to_dict(db, r, detail=True)


@router.delete("/registraties/{registratie_id}")
def verwijder_registratie(
    registratie_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alleen een eigen concept mag weg.

    Zodra er is ingediend hoort de registratie bij het dossier, ook als hij is
    afgekeurd. Juist een afgekeurde registratie is bewijs dat er is gecontroleerd.
    """
    r = _registratie_of_404(db, registratie_id, current_user)
    if r.status not in ("concept", "in_uitvoering"):
        raise HTTPException(
            status_code=409,
            detail="Een ingediende registratie hoort bij het dossier en kan niet weg")
    if r.created_by != current_user.id and not can_manage_toolbox(current_user):
        raise HTTPException(status_code=403,
                            detail="Alleen je eigen concept, of als beheerder")

    keuring_id = r.keuring_id
    db.delete(r)
    db.commit()
    log_action(db, request, current_user, action=ACTION.KWALITEIT_REG_DELETE,
               entity_type="quality_registration", entity_id=registratie_id,
               before={"keuring_id": keuring_id})
    return {"verwijderd": True}
