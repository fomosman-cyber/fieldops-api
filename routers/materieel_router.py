"""Materieel: het register, de inzet per dag, en de CO2 die eruit volgt.

Endpoints:
  GET    /api/materieel/config                 soorten, brandstoffen, klassen, factoren
  GET    /api/materieel/leveranciers           namen die al eerder zijn gebruikt
  GET    /api/materieel/inzet                  regels van een dag of periode
  POST   /api/materieel/inzet                  machine op een dag zetten
  PATCH  /api/materieel/inzet/{id}
  DELETE /api/materieel/inzet/{id}
  GET    /api/materieel/co2                    optelling per periode
  GET    /api/materieel/co2.xlsx|.pdf          hetzelfde in de huisstijl
  PUT    /api/materieel/factoren               eigen emissiefactoren (org-admin)
  POST   /api/materieel/import                 materieellijst uit Excel/CSV
  GET    /api/materieel                        het register
  POST   /api/materieel
  PATCH  /api/materieel/{id}
  DELETE /api/materieel/{id}                   archiveert, verwijdert niet

**Het register is niet de inzet.** Zie de toelichting bij de modellen. De
volgorde van de routes hierboven is niet willekeurig: `/{id}` staat onderaan,
anders vangt hij `/config` en `/inzet` af.

**Wie mag wat.** Het register is stamdata en valt onder dezelfde groep als
assets (uitvoerder, projectleider, beheerder). Een dagregel mag iedereen in de
organisatie voor zichzelf invullen -- dat is het punt van een dagboek -- en
alleen zijn eigen regels wijzigen. De org-beheerder kijkt mee.

**De uitkomst wordt bevroren.** Kilogrammen, factor en bron worden bij het
opslaan weggeschreven en niet opnieuw berekend bij het lezen. Als de landelijke
lijst volgend jaar wijzigt, blijft het rapport over dit jaar hetzelfde. Dat is
wat een auditor verwacht en wat een herberekening-bij-het-lezen niet levert.

**Draaiuren zijn geen werkuren.** Een kraan die zes uur draait komt niet als
zes uur in de urenregistratie van het werkdagboek: die uren gaan naar de
facturatie en een machine stuurt geen factuur voor zichzelf. De dagboekregel
die we meeschrijven heeft daarom bewust geen `duration_minutes`.
"""

from datetime import date as date_type, datetime, time, timezone
from typing import Optional

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

import materieel as mt
from audit import ACTION, log_action
from auth import get_current_user
from database import get_db
from daybook_logger import log_daybook
from models import DaybookEntry, Materieel, MaterieelInzet, Project, User
from permissions import can_manage_assets, is_org_admin, require_module, require_org_admin

router = APIRouter(prefix="/api/materieel", tags=["Materieel & CO2"],
                   dependencies=[Depends(require_module("dagboek"))])

MAX_PERIODE_DAGEN = 400   # een jaarrapportage moet kunnen, een decennium niet


# ── Schemas ──────────────────────────────────────────────────────────

class MaterieelIn(BaseModel):
    naam: str = Field(..., min_length=1, max_length=160)
    soort: str = "overig"
    omschrijving: Optional[str] = None
    intern_nummer: Optional[str] = Field(default=None, max_length=60)
    kenteken: Optional[str] = Field(default=None, max_length=20)
    eigendom: str = "eigen"
    leverancier: Optional[str] = Field(default=None, max_length=160)
    energiedrager: str = "diesel"
    verbruik_per_uur: Optional[float] = Field(default=None, ge=0, le=1000)
    emissieklasse: str = "onbekend"
    bouwjaar: Optional[int] = Field(default=None, ge=1900, le=2100)
    vermogen_kw: Optional[float] = Field(default=None, ge=0, le=5000)
    opmerking: Optional[str] = None
    actief: bool = True


class MaterieelPatch(BaseModel):
    naam: Optional[str] = Field(default=None, min_length=1, max_length=160)
    soort: Optional[str] = None
    omschrijving: Optional[str] = None
    intern_nummer: Optional[str] = Field(default=None, max_length=60)
    kenteken: Optional[str] = Field(default=None, max_length=20)
    eigendom: Optional[str] = None
    leverancier: Optional[str] = Field(default=None, max_length=160)
    energiedrager: Optional[str] = None
    verbruik_per_uur: Optional[float] = Field(default=None, ge=0, le=1000)
    emissieklasse: Optional[str] = None
    bouwjaar: Optional[int] = Field(default=None, ge=1900, le=2100)
    vermogen_kw: Optional[float] = Field(default=None, ge=0, le=5000)
    opmerking: Optional[str] = None
    actief: Optional[bool] = None


class InzetIn(BaseModel):
    datum: Optional[date_type] = None          # default = vandaag
    materieel_id: Optional[str] = None
    # Een regel uit de standaardlijst (data/materieel_catalogus.json). Levert
    # naam, soort, vermogen en een geschat verbruik per uur als die niet uit
    # het register of het formulier komen.
    catalogus_code: Optional[str] = Field(default=None, max_length=40)
    materieel_naam: Optional[str] = Field(default=None, max_length=160)
    leverancier: Optional[str] = Field(default=None, max_length=160)
    energiedrager: Optional[str] = None
    project_id: Optional[str] = None
    draaiuren: Optional[float] = Field(default=None, ge=0, le=24)
    brandstof_hoeveelheid: Optional[float] = Field(default=None, ge=0, le=100000)
    # Eigen kengetal voor een losse regel; wint van register en standaardlijst.
    verbruik_per_uur: Optional[float] = Field(default=None, ge=0, le=1000)
    vermogen_kw: Optional[float] = Field(default=None, ge=0, le=5000)
    emissieklasse: Optional[str] = None
    opmerking: Optional[str] = None


class InzetPatch(BaseModel):
    datum: Optional[date_type] = None
    # Alleen voor een regel zonder registerstuk: daar is de naam het enige
    # aanknopingspunt en moet een typefout te herstellen zijn.
    materieel_naam: Optional[str] = Field(default=None, min_length=1, max_length=160)
    leverancier: Optional[str] = Field(default=None, max_length=160)
    energiedrager: Optional[str] = None
    project_id: Optional[str] = None
    draaiuren: Optional[float] = Field(default=None, ge=0, le=24)
    brandstof_hoeveelheid: Optional[float] = Field(default=None, ge=0, le=100000)
    verbruik_per_uur: Optional[float] = Field(default=None, ge=0, le=1000)
    vermogen_kw: Optional[float] = Field(default=None, ge=0, le=5000)
    emissieklasse: Optional[str] = None
    opmerking: Optional[str] = None


class FactorenIn(BaseModel):
    """Eigen emissiefactoren: energiedrager → kg CO2 per eenheid. Leeg = landelijk."""
    factoren: dict[str, float] = Field(default_factory=dict)


# ── Helpers ──────────────────────────────────────────────────────────

def _vandaag() -> date_type:
    """Vandaag in Nederlandse tijd, niet in UTC.

    Een werkdagboek vul je 's avonds in. Met de UTC-datum belandt alles wat na
    middernacht Nederlandse tijd wordt ingevuld op de dag ervoor -- in de zomer
    zelfs alles na 22:00 zou het omgekeerd zijn geweest. Dezelfde valkuil als
    bug #11 in het dagboek zelf, en dezelfde omrekening (`naar_nl`).
    """
    from export_huisstijl import naar_nl
    return naar_nl(datetime.now(timezone.utc)).date()


def _eigen_factoren(org) -> dict:
    ruw = getattr(org, "co2_factoren", None)
    if not ruw:
        return {}
    try:
        waarde = json.loads(ruw)
    except (ValueError, TypeError):
        return {}
    return waarde if isinstance(waarde, dict) else {}


def _eis_beheer(user: User) -> None:
    if not can_manage_assets(user):
        raise HTTPException(403, "Alleen een beheerder of projectleider beheert het materieelregister")


def _controleer_keuzes(soort=None, eigendom=None, energiedrager=None, emissieklasse=None) -> None:
    """Onbekende keuzes weigeren, niet stilletjes op de standaard zetten.

    Zelfde lijn als bij de schouw-instellingen: wie iets anders invult dan er
    wordt opgeslagen, denkt dat hij iets heeft vastgelegd wat er niet staat.
    """
    if soort is not None and soort not in mt.SOORTEN:
        raise HTTPException(400, f"Onbekende soort: {soort}")
    if eigendom is not None and eigendom not in mt.EIGENDOM:
        raise HTTPException(400, f"Onbekend eigendom: {eigendom}")
    if energiedrager is not None and energiedrager not in mt.ENERGIEDRAGERS:
        raise HTTPException(400, f"Onbekende energiedrager: {energiedrager}")
    if emissieklasse is not None and emissieklasse not in mt.EMISSIEKLASSEN:
        raise HTTPException(400, f"Onbekende emissieklasse: {emissieklasse}")


def _periode(van: Optional[str], tot: Optional[str]) -> tuple[date_type, date_type]:
    vandaag = _vandaag()
    try:
        d_van = date_type.fromisoformat(van) if van else vandaag.replace(day=1)
        d_tot = date_type.fromisoformat(tot) if tot else vandaag
    except ValueError:
        raise HTTPException(400, "Datums moeten als JJJJ-MM-DD")
    if d_tot < d_van:
        raise HTTPException(400, "De einddatum ligt voor de begindatum")
    if (d_tot - d_van).days > MAX_PERIODE_DAGEN:
        raise HTTPException(400, f"Kies een periode van hoogstens {MAX_PERIODE_DAGEN} dagen")
    return d_van, d_tot


def _zichtbare_inzet(db: Session, user: User, d_van: date_type, d_tot: date_type,
                     *, user_id: Optional[str] = None, hele_organisatie: bool = False,
                     project_id: Optional[str] = None):
    """De regels die deze gebruiker mag zien, in de gevraagde periode.

    **Standaard je eigen regels.** Het werkdagboek is een persoonlijk scherm:
    de tijdlijn toont wat jij die dag deed. Een org-beheerder kreeg hier eerst
    de regels van de hele organisatie terug, waardoor in zijn eigen dagboek de
    machines van zijn ploeg stonden en de CO2-teller het bedrijfstotaal gaf
    naast een tijdlijn met alleen zijn eigen regels. Wie over een ander wil
    kijken vraagt daar nu om (`user_id`), en wie de hele organisatie wil zien
    ook (`hele_organisatie`) -- dat laatste is wat de rapportage doet.
    """
    q = (db.query(MaterieelInzet)
           .filter(MaterieelInzet.organization_id == user.organization_id,
                   MaterieelInzet.datum >= d_van,
                   MaterieelInzet.datum <= d_tot,
                   MaterieelInzet.deleted_at.is_(None)))
    if hele_organisatie:
        if not is_org_admin(user):
            q = q.filter(MaterieelInzet.user_id == user.id)
    elif user_id and user_id != user.id:
        if not is_org_admin(user):
            raise HTTPException(403, "Geen toegang tot de regels van een andere gebruiker")
        q = q.filter(MaterieelInzet.user_id == user_id)
    else:
        q = q.filter(MaterieelInzet.user_id == user.id)
    if project_id:
        q = q.filter(MaterieelInzet.project_id == project_id)
    return q.order_by(MaterieelInzet.datum.desc(), MaterieelInzet.created_at.desc())


def _namen(db: Session, inzet: list[MaterieelInzet]) -> tuple[dict, dict]:
    user_ids = {i.user_id for i in inzet if i.user_id}
    project_ids = {i.project_id for i in inzet if i.project_id}
    users = db.query(User.id, User.first_name, User.last_name, User.email).filter(
        User.id.in_(user_ids)).all() if user_ids else []
    projecten = db.query(Project.id, Project.name).filter(
        Project.id.in_(project_ids)).all() if project_ids else []
    return ({u.id: ((u.first_name or "") + " " + (u.last_name or "")).strip() or u.email
             for u in users},
            {p.id: p.name for p in projecten})


def _toon(i: MaterieelInzet, users: dict, projecten: dict,
          eigen_factoren: Optional[dict] = None) -> dict:
    drager = mt.ENERGIEDRAGERS.get(i.energiedrager or "", {})
    klasse = i.emissieklasse or (i.materieel.emissieklasse if i.materieel_id and i.materieel else None)
    vermogen = i.vermogen_kw or (i.materieel.vermogen_kw if i.materieel_id and i.materieel else None)
    return {
        "id": i.id,
        "datum": i.datum.isoformat() if i.datum else None,
        "user_id": i.user_id,
        "user_naam": users.get(i.user_id),
        "materieel_id": i.materieel_id,
        "materieel_naam": i.materieel_naam,
        "soort": i.soort,
        "soort_naam": mt.SOORTEN.get(i.soort or "", i.soort or ""),
        "leverancier": i.leverancier,
        "energiedrager": i.energiedrager,
        "energiedrager_naam": drager.get("naam", i.energiedrager),
        "eenheid": drager.get("eenheid", ""),
        "draaiuren": i.draaiuren,
        "brandstof_hoeveelheid": i.brandstof_hoeveelheid,
        # Het kengetal zoals het gold toen de regel werd gemaakt. Het scherm
        # rekent zijn voorbeeld hiermee door, anders laat het bij het bewerken
        # een ander getal zien dan de server opslaat.
        "verbruik_per_uur": i.verbruik_per_uur,
        "project_id": i.project_id,
        "project_naam": projecten.get(i.project_id) if i.project_id else None,
        "co2_kg": i.co2_kg,
        "co2_methode": i.co2_methode,
        "co2_factor": i.co2_factor,
        "co2_bron": i.co2_bron,
        # Wat hetzelfde aantal liters op diesel B7 had uitgestoten, min wat er
        # uitkwam (zie mt.reductie_kg). None: niet te vergelijken.
        "reductie_kg": mt.reductie_kg(energiedrager=i.energiedrager, co2_kg=i.co2_kg,
                                      co2_factor=i.co2_factor, eigen_factoren=eigen_factoren),
        "catalogus_code": i.catalogus_code,
        "vermogen_kw": vermogen,
        "emissieklasse": klasse,
        "emissieklasse_naam": mt.EMISSIEKLASSEN.get(klasse or "", klasse or ""),
        "opmerking": i.opmerking,
    }


def _dagboektekst(i: MaterieelInzet) -> tuple[str, str]:
    """Titel en toelichting voor de spiegelregel in het werkdagboek."""
    titel = f"Materieel: {i.materieel_naam}"
    if i.draaiuren:
        titel += f" — {i.draaiuren:g} uur"
    regels = []
    if i.leverancier:
        regels.append(f"Leverancier: {i.leverancier}")
    eenheid = mt.ENERGIEDRAGERS.get(i.energiedrager or "", {}).get("eenheid", "")
    if i.brandstof_hoeveelheid is not None:
        regels.append(f"Getankt: {i.brandstof_hoeveelheid:g} {eenheid}".strip())
    if i.co2_kg is not None:
        hoe = "geschat" if i.co2_methode == "geschat" else "gemeten"
        regels.append(f"CO2: {i.co2_kg:g} kg ({hoe})")
    else:
        regels.append("CO2: niet berekend — geen verbruik ingevuld")
    if i.opmerking:
        regels.append(i.opmerking)
    return titel[:255], "\n".join(regels)


def _totaal_met_reductie(regels: list[MaterieelInzet], eigen_factoren: dict) -> dict:
    """mt.totaal, plus de reductie ten opzichte van diesel over dezelfde regels."""
    uit = mt.totaal([mt.Berekening(kg=r.co2_kg, methode=r.co2_methode) for r in regels])
    reducties = [mt.reductie_kg(energiedrager=r.energiedrager, co2_kg=r.co2_kg,
                                co2_factor=r.co2_factor, eigen_factoren=eigen_factoren)
                 for r in regels]
    uit["kg_reductie"] = round(sum(x for x in reducties if x), 2)
    return uit


def _schrijf_berekening(i: MaterieelInzet, eigen_factoren: dict) -> None:
    uitkomst = mt.bereken(
        energiedrager=i.energiedrager,
        brandstof_hoeveelheid=i.brandstof_hoeveelheid,
        draaiuren=i.draaiuren,
        verbruik_per_uur=i.verbruik_per_uur,
        eigen_factoren=eigen_factoren,
    )
    i.co2_kg = uitkomst.kg
    i.co2_methode = uitkomst.methode
    i.co2_factor = uitkomst.factor.kg_co2_per_eenheid if uitkomst.factor else None
    i.co2_bron = (uitkomst.factor.herkomst if uitkomst.factor else None)


# ── Config ───────────────────────────────────────────────────────────

@router.get("/config")
def config(current_user: User = Depends(get_current_user)):
    """Alles wat het scherm nodig heeft om de keuzelijsten te vullen.

    De factoren gaan mee inclusief bron en versiedatum. Dat is geen sieraad:
    wie een CO2-getal aan zijn opdrachtgever laat zien moet kunnen zeggen
    waarmee het gerekend is.
    """
    lijst = mt.factorenlijst()
    eigen = _eigen_factoren(current_user.organization)
    return {
        "soorten": [{"code": k, "naam": v, "zonder_energie": k in mt.SOORTEN_ZONDER_ENERGIE}
                    for k, v in mt.SOORTEN.items()],
        "energiedragers": [{"code": k, **v} for k, v in mt.ENERGIEDRAGERS.items()],
        "emissieklassen": [{"code": k, "naam": v} for k, v in mt.EMISSIEKLASSEN.items()],
        "eigendom": [{"code": k, "naam": v} for k, v in mt.EIGENDOM.items()],
        "factoren": {
            "lijst": lijst.get("lijst", ""),
            "versie": lijst.get("versie", ""),
            "bron": lijst.get("bron", ""),
            "toelichting": lijst.get("toelichting", ""),
            "rijen": lijst.get("factoren", []),
        },
        "eigen_factoren": eigen,
        "catalogus": mt.catalogus(),
    }


@router.get("/leveranciers")
def leveranciers(current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Namen die deze organisatie eerder heeft ingevuld.

    Er is geen leveranciersadministratie en die hoort hier ook niet: je typt
    "Boels" en klaar. Maar twee keer "Boels" en één keer "boels " is straks
    drie leveranciers in het rapport, dus biedt het scherm aan wat er al staat.
    """
    uit_register = db.query(Materieel.leverancier).filter(
        Materieel.organization_id == current_user.organization_id,
        Materieel.leverancier.isnot(None), Materieel.leverancier != "").distinct().all()
    uit_inzet = db.query(MaterieelInzet.leverancier).filter(
        MaterieelInzet.organization_id == current_user.organization_id,
        MaterieelInzet.leverancier.isnot(None), MaterieelInzet.leverancier != "").distinct().all()
    namen = {r[0].strip() for r in list(uit_register) + list(uit_inzet) if r[0] and r[0].strip()}
    return {"leveranciers": sorted(namen, key=str.lower)}


@router.put("/factoren")
def zet_factoren(body: FactorenIn, request: Request,
                 current_user: User = Depends(require_org_admin),
                 db: Session = Depends(get_db)):
    """Eigen emissiefactoren vastleggen. Een lege map zet ze terug op landelijk."""
    schoon: dict[str, float] = {}
    for drager, waarde in body.factoren.items():
        if drager not in mt.ENERGIEDRAGERS:
            raise HTTPException(400, f"Onbekende energiedrager: {drager}")
        if isinstance(waarde, bool) or not isinstance(waarde, (int, float)) or waarde < 0:
            raise HTTPException(400, f"Factor voor {drager} moet een getal van 0 of hoger zijn")
        schoon[drager] = float(waarde)

    org = current_user.organization
    if org is None:
        raise HTTPException(404, "Geen organisatie gevonden")
    org.co2_factoren = json.dumps(schoon) if schoon else None
    db.commit()
    log_action(db, request, current_user, action=ACTION.MATERIEEL_FACTOREN,
               entity_type="organization", entity_id=org.id, after=schoon)
    return {"eigen_factoren": schoon}


# ── Inzet per dag ────────────────────────────────────────────────────

@router.get("/inzet")
def lijst_inzet(
    datum: Optional[str] = Query(None, description="JJJJ-MM-DD; één dag"),
    van: Optional[str] = Query(None, alias="from"),
    tot: Optional[str] = Query(None, alias="to"),
    user_id: Optional[str] = Query(None, description="Andere gebruiker; alleen org-beheerder"),
    iedereen: bool = Query(False, description="Hele organisatie; alleen org-beheerder"),
    project_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """De regels van één dag (`datum`) of een periode (`from`/`to`).

    Standaard je eigen regels -- zie `_zichtbare_inzet`.
    """
    if datum:
        try:
            d_van = d_tot = date_type.fromisoformat(datum)
        except ValueError:
            raise HTTPException(400, "datum moet als JJJJ-MM-DD")
    else:
        d_van, d_tot = _periode(van, tot)

    regels = _zichtbare_inzet(db, current_user, d_van, d_tot, user_id=user_id,
                              hele_organisatie=iedereen, project_id=project_id).all()
    users, projecten = _namen(db, regels)
    eigen = _eigen_factoren(current_user.organization)
    return {
        "van": d_van.isoformat(),
        "tot": d_tot.isoformat(),
        "aantal": len(regels),
        "totaal": _totaal_met_reductie(regels, eigen),
        "regels": [_toon(r, users, projecten, eigen) for r in regels],
    }


@router.post("/inzet", status_code=201)
def nieuwe_inzet(body: InzetIn, request: Request,
                 current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Een machine op een dag zetten.

    Kies een stuk uit het register, of typ een naam voor iets wat je eenmalig
    huurt. Beide mag; niets van beide niet.
    """
    stuk: Optional[Materieel] = None
    if body.materieel_id:
        stuk = db.query(Materieel).filter(
            Materieel.id == body.materieel_id,
            Materieel.organization_id == current_user.organization_id).first()
        if not stuk:
            raise HTTPException(404, "Materieel niet gevonden")

    kat = mt.catalogus_item(body.catalogus_code)
    if body.catalogus_code and kat is None:
        raise HTTPException(400, f"Onbekend materieel in de standaardlijst: {body.catalogus_code}")

    naam = (body.materieel_naam or (stuk.naam if stuk else "") or (kat["naam"] if kat else "")).strip()
    if not naam:
        raise HTTPException(400, "Kies materieel uit de lijst of vul een naam in")

    energiedrager = (body.energiedrager or (stuk.energiedrager if stuk else None)
                     or (kat.get("energiedrager") if kat else None) or "diesel")
    _controleer_keuzes(energiedrager=energiedrager, emissieklasse=body.emissieklasse)

    if body.project_id:
        bestaat = db.query(Project.id).filter(
            Project.id == body.project_id,
            Project.organization_id == current_user.organization_id).first()
        if not bestaat:
            raise HTTPException(404, "Project niet gevonden")

    inzet = MaterieelInzet(
        organization_id=current_user.organization_id,
        user_id=current_user.id,
        datum=body.datum or _vandaag(),
        project_id=body.project_id,
        materieel_id=stuk.id if stuk else None,
        materieel_naam=naam[:160],
        soort=(stuk.soort if stuk else None) or (kat["soort"] if kat else None),
        leverancier=(body.leverancier or (stuk.leverancier if stuk else None) or "").strip()[:160] or None,
        energiedrager=energiedrager,
        draaiuren=body.draaiuren,
        brandstof_hoeveelheid=body.brandstof_hoeveelheid,
        # Eigen getal wint, dan het register, dan de standaardlijst.
        verbruik_per_uur=_eerste(body.verbruik_per_uur,
                                 stuk.verbruik_per_uur if stuk else None,
                                 kat.get("verbruik_per_uur") if kat else None),
        catalogus_code=kat["code"] if kat else None,
        vermogen_kw=_eerste(body.vermogen_kw, stuk.vermogen_kw if stuk else None,
                            kat.get("vermogen_kw") if kat else None),
        emissieklasse=body.emissieklasse or (stuk.emissieklasse if stuk else None),
        opmerking=body.opmerking,
    )
    _schrijf_berekening(inzet, _eigen_factoren(current_user.organization))
    db.add(inzet)
    db.commit()
    db.refresh(inzet)

    titel, tekst = _dagboektekst(inzet)
    entry = log_daybook(
        db, user_id=current_user.id, organization_id=current_user.organization_id,
        entry_type="materieel_inzet", title=titel, description=tekst,
        source_type="materieel", source_id=inzet.id,
        # Middaguur: de regel hoort bij een dag, niet bij een tijdstip, en
        # midden op de dag blijft hij in elke tijdzone op de goede datum staan.
        occurred_at=datetime.combine(inzet.datum, time(12, 0), tzinfo=timezone.utc),
        project_id=inzet.project_id,
        # Bewust geen duration_minutes: draaiuren zijn geen werkuren.
    )
    if entry is not None:
        inzet.daybook_entry_id = entry.id
        db.commit()

    log_action(db, request, current_user, action=ACTION.MATERIEEL_INZET_CREATE,
               entity_type="materieel_inzet", entity_id=inzet.id,
               after={"materieel": inzet.materieel_naam, "datum": inzet.datum.isoformat(),
                      "co2_kg": inzet.co2_kg, "methode": inzet.co2_methode})
    users, projecten = _namen(db, [inzet])
    return _toon(inzet, users, projecten, _eigen_factoren(current_user.organization))


def _eerste(*waarden):
    """De eerste waarde die is ingevuld. 0 telt als ingevuld (een elektrische
    machine verbruikt 0 liter per uur), None niet."""
    for w in waarden:
        if w is not None:
            return w
    return None


def _eigen_regel(db: Session, inzet_id: str, current_user: User) -> MaterieelInzet:
    inzet = db.query(MaterieelInzet).filter(
        MaterieelInzet.id == inzet_id,
        MaterieelInzet.organization_id == current_user.organization_id,
        MaterieelInzet.deleted_at.is_(None)).first()
    if not inzet:
        raise HTTPException(404, "Regel niet gevonden")
    if inzet.user_id != current_user.id and not is_org_admin(current_user):
        raise HTTPException(403, "Dit is niet jouw regel")
    return inzet


def _werk_dagboekregel_bij(db: Session, inzet: MaterieelInzet) -> None:
    """Houd de spiegelregel in de tijdlijn gelijk aan de inzet."""
    if not inzet.daybook_entry_id:
        return
    entry = db.query(DaybookEntry).filter(DaybookEntry.id == inzet.daybook_entry_id).first()
    if not entry:
        return
    titel, tekst = _dagboektekst(inzet)
    entry.title, entry.description = titel, tekst
    entry.project_id = inzet.project_id
    entry.occurred_at = datetime.combine(inzet.datum, time(12, 0), tzinfo=timezone.utc)


@router.patch("/inzet/{inzet_id}")
def wijzig_inzet(inzet_id: str, body: InzetPatch, request: Request,
                 current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    inzet = _eigen_regel(db, inzet_id, current_user)
    velden = body.model_dump(exclude_unset=True)
    if velden.get("materieel_naam") and inzet.materieel_id:
        raise HTTPException(400, "De naam komt uit het register en hoort daar te worden gewijzigd")
    if "energiedrager" in velden:
        _controleer_keuzes(energiedrager=velden["energiedrager"])
    if velden.get("emissieklasse"):
        _controleer_keuzes(emissieklasse=velden["emissieklasse"])
    if velden.get("project_id"):
        bestaat = db.query(Project.id).filter(
            Project.id == velden["project_id"],
            Project.organization_id == current_user.organization_id).first()
        if not bestaat:
            raise HTTPException(404, "Project niet gevonden")

    voor = {"co2_kg": inzet.co2_kg, "draaiuren": inzet.draaiuren,
            "brandstof": inzet.brandstof_hoeveelheid}
    for veld, waarde in velden.items():
        if veld == "leverancier" and waarde is not None:
            waarde = waarde.strip()[:160] or None
        setattr(inzet, veld, waarde)
    _schrijf_berekening(inzet, _eigen_factoren(current_user.organization))
    _werk_dagboekregel_bij(db, inzet)
    db.commit()
    db.refresh(inzet)

    log_action(db, request, current_user, action=ACTION.MATERIEEL_INZET_UPDATE,
               entity_type="materieel_inzet", entity_id=inzet.id,
               before=voor, after={"co2_kg": inzet.co2_kg, "draaiuren": inzet.draaiuren,
                                   "brandstof": inzet.brandstof_hoeveelheid})
    users, projecten = _namen(db, [inzet])
    return _toon(inzet, users, projecten, _eigen_factoren(current_user.organization))


@router.delete("/inzet/{inzet_id}")
def verwijder_inzet(inzet_id: str, request: Request,
                    current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Regel terugdraaien. De spiegelregel in het dagboek gaat mee -- anders
    blijft het werkdagboek een machine tonen die er niet stond."""
    inzet = _eigen_regel(db, inzet_id, current_user)
    # Vastleggen vóór de commit: daarna zijn de attributen verlopen en moet de
    # rij opnieuw worden opgehaald om de auditregel te kunnen schrijven.
    spoor = {"materieel": inzet.materieel_naam, "datum": inzet.datum.isoformat()}
    nu = datetime.now(timezone.utc)
    inzet.deleted_at = nu
    if inzet.daybook_entry_id:
        entry = db.query(DaybookEntry).filter(DaybookEntry.id == inzet.daybook_entry_id).first()
        if entry:
            entry.deleted_at = nu
    db.commit()
    log_action(db, request, current_user, action=ACTION.MATERIEEL_INZET_DELETE,
               entity_type="materieel_inzet", entity_id=inzet_id, before=spoor)
    return {"verwijderd": True}


# ── Het CO2-overzicht ────────────────────────────────────────────────

def _rapport(db: Session, current_user: User, d_van: date_type, d_tot: date_type,
             project_id: Optional[str]) -> dict:
    # Een rapportage gaat over het bedrijf, niet over één persoon: een
    # beheerder telt de hele organisatie op, wie dat niet is ziet zijn eigen
    # regels.
    regels = _zichtbare_inzet(db, current_user, d_van, d_tot,
                              hele_organisatie=True, project_id=project_id).all()
    users, projecten = _namen(db, regels)

    def optellen(sleutel) -> list[dict]:
        emmers: dict[str, dict] = {}
        for r in regels:
            naam = sleutel(r) or "(niet ingevuld)"
            e = emmers.setdefault(naam, {"naam": naam, "regels": 0, "draaiuren": 0.0,
                                         "kg_gemeten": 0.0, "kg_geschat": 0.0,
                                         "zonder_getal": 0})
            e["regels"] += 1
            e["draaiuren"] += r.draaiuren or 0
            if r.co2_methode == "gemeten" and r.co2_kg is not None:
                e["kg_gemeten"] += r.co2_kg
            elif r.co2_methode == "geschat" and r.co2_kg is not None:
                e["kg_geschat"] += r.co2_kg
            else:
                e["zonder_getal"] += 1
        for e in emmers.values():
            e["draaiuren"] = round(e["draaiuren"], 2)
            e["kg_gemeten"] = round(e["kg_gemeten"], 2)
            e["kg_geschat"] = round(e["kg_geschat"], 2)
            e["kg_totaal"] = round(e["kg_gemeten"] + e["kg_geschat"], 2)
        return sorted(emmers.values(), key=lambda e: -e["kg_totaal"])

    eigen = _eigen_factoren(current_user.organization)
    lijst = mt.factorenlijst()
    return {
        "van": d_van.isoformat(),
        "tot": d_tot.isoformat(),
        "totaal": _totaal_met_reductie(regels, eigen),
        "per_materieel": optellen(lambda r: r.materieel_naam),
        "per_project": optellen(lambda r: projecten.get(r.project_id) if r.project_id else None),
        "per_leverancier": optellen(lambda r: r.leverancier),
        "per_energiedrager": optellen(
            lambda r: mt.ENERGIEDRAGERS.get(r.energiedrager or "", {}).get("naam", r.energiedrager)),
        "per_gebruiker": optellen(lambda r: users.get(r.user_id)),
        "factorenlijst": {"lijst": lijst.get("lijst", ""), "versie": lijst.get("versie", ""),
                          "bron": lijst.get("bron", "")},
        "regels": [_toon(r, users, projecten, eigen) for r in regels],
    }


@router.get("/co2")
def co2_overzicht(van: Optional[str] = Query(None, alias="from"),
                  tot: Optional[str] = Query(None, alias="to"),
                  project_id: Optional[str] = Query(None),
                  current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """De optelling over een periode, uitgesplitst.

    Gemeten en geschat staan apart, en het aantal regels zonder getal staat
    erbij. Eén som zou suggereren dat het beeld compleet is.
    """
    d_van, d_tot = _periode(van, tot)
    return _rapport(db, current_user, d_van, d_tot, project_id)


def _rapport_bladen(rapport: dict):
    from export_huisstijl import Blad, Kolom

    detail_kolommen = [
        Kolom("Datum", "datum"), Kolom("Materieel"), Kolom("Soort"), Kolom("Leverancier"),
        Kolom("Project"), Kolom("Energiedrager"), Kolom("Draaiuren", "getal", 2),
        Kolom("Getankt", "getal", 2), Kolom("Eenheid"), Kolom("CO2 (kg)", "getal", 2),
        Kolom("Reductie (kg)", "getal", 2), Kolom("Grondslag"), Kolom("Factor"),
    ]
    methode_naam = {"gemeten": "Gemeten", "geschat": "Geschat", "onbekend": "Niet berekend"}
    detail = [[
        date_type.fromisoformat(r["datum"]) if r["datum"] else None,
        r["materieel_naam"], r["soort_naam"], r["leverancier"] or "",
        r["project_naam"] or "", r["energiedrager_naam"], r["draaiuren"],
        r["brandstof_hoeveelheid"], r["eenheid"], r["co2_kg"], r["reductie_kg"],
        methode_naam.get(r["co2_methode"], r["co2_methode"]),
        r["co2_factor"],
    ] for r in rapport["regels"]]

    t = rapport["totaal"]
    bladen = [Blad("Inzet per dag", detail_kolommen, detail,
                   totaal=["", "Totaal", "", "", "", "", None, None, "", t["kg_totaal"],
                           t.get("kg_reductie"), "", ""])]

    groep_kolommen = [Kolom("Naam"), Kolom("Regels", "heel"), Kolom("Draaiuren", "getal", 2),
                      Kolom("CO2 gemeten (kg)", "getal", 2), Kolom("CO2 geschat (kg)", "getal", 2),
                      Kolom("CO2 totaal (kg)", "getal", 2), Kolom("Regels zonder getal", "heel")]
    for titel, sleutel in (("Per materieel", "per_materieel"), ("Per project", "per_project"),
                           ("Per leverancier", "per_leverancier"),
                           ("Per energiedrager", "per_energiedrager")):
        rijen = [[g["naam"], g["regels"], g["draaiuren"], g["kg_gemeten"], g["kg_geschat"],
                  g["kg_totaal"], g["zonder_getal"]] for g in rapport[sleutel]]
        if rijen:
            bladen.append(Blad(titel, groep_kolommen, rijen,
                               totaal=["Totaal", None, None, t["kg_gemeten"], t["kg_geschat"],
                                       t["kg_totaal"], t["regels_zonder_getal"]]))
    return bladen


def _rapport_ondertitel(rapport: dict) -> str:
    t = rapport["totaal"]
    periode = (f"{date_type.fromisoformat(rapport['van']).strftime('%d-%m-%Y')} t/m "
               f"{date_type.fromisoformat(rapport['tot']).strftime('%d-%m-%Y')}")
    delen = [periode,
             f"{t['kg_totaal']:g} kg CO2 (waarvan {t['kg_geschat']:g} kg geschat)"]
    if t.get("kg_reductie"):
        delen.append(f"{t['kg_reductie']:g} kg minder dan op diesel")
    if t["regels_zonder_getal"]:
        delen.append(f"{t['regels_zonder_getal']} regel(s) zonder verbruik — niet meegeteld")
    lijst = rapport.get("factorenlijst", {})
    if lijst.get("versie"):
        delen.append(f"factoren: {lijst.get('lijst', '')} {lijst['versie']}")
    return " · ".join(d for d in delen if d)


def _rapport_export(formaat: str, van, tot, project_id, current_user, db):
    from export_huisstijl import (bestandsnaam, excel_antwoord, excel_van, klant_van,
                                  pdf_antwoord, pdf_van)
    d_van, d_tot = _periode(van, tot)
    rapport = _rapport(db, current_user, d_van, d_tot, project_id)
    bladen = _rapport_bladen(rapport)
    klant = klant_van(current_user.organization)
    naam = bestandsnaam("CO2", d_van.isoformat(), "tot", d_tot.isoformat(),
                        ext=formaat, met_datum=False)
    ondertitel = _rapport_ondertitel(rapport)
    if formaat == "xlsx":
        return excel_antwoord(excel_van(klant, "CO2-registratie materieel", bladen,
                                        ondertitel=ondertitel), naam)
    return pdf_antwoord(pdf_van(klant, "CO2-registratie materieel", bladen,
                                ondertitel=ondertitel), naam)


@router.get("/co2.xlsx")
def co2_excel(van: Optional[str] = Query(None, alias="from"),
              tot: Optional[str] = Query(None, alias="to"),
              project_id: Optional[str] = Query(None),
              current_user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    return _rapport_export("xlsx", van, tot, project_id, current_user, db)


@router.get("/co2.pdf")
def co2_pdf(van: Optional[str] = Query(None, alias="from"),
            tot: Optional[str] = Query(None, alias="to"),
            project_id: Optional[str] = Query(None),
            current_user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    return _rapport_export("pdf", van, tot, project_id, current_user, db)


# ── Het register ─────────────────────────────────────────────────────

class ImportIn(BaseModel):
    inhoud: str = Field(..., max_length=2_000_000)
    bevestigen: bool = False


@router.post("/import")
def importeer(body: ImportIn, request: Request,
              current_user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """Een materieellijst inlezen uit een geplakte CSV of Excel-export.

    Zonder `bevestigen` doet dit niets: je krijgt terug wat er gelezen is en
    wat er niet klopt. Pas met `bevestigen` wordt het weggeschreven. Een
    import die meteen doorschrijft is een import die je niet meer terug kunt
    draaien.
    """
    _eis_beheer(current_user)
    rijen, waarschuwingen = mt.lees_csv(body.inhoud)
    if not body.bevestigen:
        return {"gelezen": len(rijen), "waarschuwingen": waarschuwingen,
                "voorbeeld": rijen[:20], "opgeslagen": 0}

    bestaand = {
        (m.naam or "").strip().lower()
        for m in db.query(Materieel).filter(
            Materieel.organization_id == current_user.organization_id).all()
    }
    opgeslagen, overgeslagen = 0, 0
    for rij in rijen:
        if rij["naam"].strip().lower() in bestaand:
            overgeslagen += 1
            continue
        db.add(Materieel(
            organization_id=current_user.organization_id,
            naam=rij["naam"][:160],
            soort=rij.get("soort") or "overig",
            intern_nummer=(rij.get("intern_nummer") or None),
            kenteken=(rij.get("kenteken") or None),
            eigendom=rij.get("eigendom") or "eigen",
            leverancier=(rij.get("leverancier") or None),
            energiedrager=rij.get("energiedrager") or "diesel",
            emissieklasse=rij.get("emissieklasse") or "onbekend",
            bouwjaar=rij.get("bouwjaar"),
            verbruik_per_uur=rij.get("verbruik_per_uur"),
            opmerking=(rij.get("opmerking") or None),
            created_by=current_user.id,
        ))
        bestaand.add(rij["naam"].strip().lower())
        opgeslagen += 1
    db.commit()
    log_action(db, request, current_user, action=ACTION.MATERIEEL_IMPORT,
               entity_type="materieel", extra={"opgeslagen": opgeslagen,
                                               "overgeslagen": overgeslagen})
    return {"gelezen": len(rijen), "opgeslagen": opgeslagen,
            "overgeslagen_want_bestond_al": overgeslagen,
            "waarschuwingen": waarschuwingen}


def _toon_stuk(m: Materieel) -> dict:
    drager = mt.ENERGIEDRAGERS.get(m.energiedrager or "", {})
    return {
        "id": m.id, "naam": m.naam, "soort": m.soort,
        "soort_naam": mt.SOORTEN.get(m.soort or "", m.soort or ""),
        "omschrijving": m.omschrijving, "intern_nummer": m.intern_nummer,
        "kenteken": m.kenteken, "eigendom": m.eigendom,
        "eigendom_naam": mt.EIGENDOM.get(m.eigendom or "", m.eigendom or ""),
        "leverancier": m.leverancier, "energiedrager": m.energiedrager,
        "energiedrager_naam": drager.get("naam", m.energiedrager),
        "eenheid": drager.get("eenheid", ""),
        "verbruik_per_uur": m.verbruik_per_uur,
        "emissieklasse": m.emissieklasse,
        "emissieklasse_naam": mt.EMISSIEKLASSEN.get(m.emissieklasse or "", m.emissieklasse or ""),
        "bouwjaar": m.bouwjaar, "vermogen_kw": m.vermogen_kw,
        "actief": m.actief, "opmerking": m.opmerking,
    }


@router.get("")
def lijst_register(zoek: Optional[str] = Query(None),
                   soort: Optional[str] = Query(None),
                   alleen_actief: bool = Query(True),
                   current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    q = db.query(Materieel).filter(Materieel.organization_id == current_user.organization_id)
    if alleen_actief:
        q = q.filter(Materieel.actief.is_(True))
    if soort:
        q = q.filter(Materieel.soort == soort)
    if zoek:
        naald = f"%{zoek.strip()}%"
        q = q.filter(or_(Materieel.naam.ilike(naald), Materieel.intern_nummer.ilike(naald),
                         Materieel.kenteken.ilike(naald), Materieel.leverancier.ilike(naald)))
    stukken = q.order_by(Materieel.naam.asc()).all()
    return {"aantal": len(stukken), "materieel": [_toon_stuk(m) for m in stukken]}


@router.post("", status_code=201)
def nieuw_stuk(body: MaterieelIn, request: Request,
               current_user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    _eis_beheer(current_user)
    _controleer_keuzes(body.soort, body.eigendom, body.energiedrager, body.emissieklasse)
    stuk = Materieel(organization_id=current_user.organization_id,
                     created_by=current_user.id, **body.model_dump())
    db.add(stuk)
    db.commit()
    db.refresh(stuk)
    log_action(db, request, current_user, action=ACTION.MATERIEEL_CREATE,
               entity_type="materieel", entity_id=stuk.id, after={"naam": stuk.naam})
    return _toon_stuk(stuk)


def _stuk_van_org(db: Session, stuk_id: str, current_user: User) -> Materieel:
    stuk = db.query(Materieel).filter(
        Materieel.id == stuk_id,
        Materieel.organization_id == current_user.organization_id).first()
    if not stuk:
        raise HTTPException(404, "Materieel niet gevonden")
    return stuk


@router.patch("/{stuk_id}")
def wijzig_stuk(stuk_id: str, body: MaterieelPatch, request: Request,
                current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    _eis_beheer(current_user)
    stuk = _stuk_van_org(db, stuk_id, current_user)
    velden = body.model_dump(exclude_unset=True)
    _controleer_keuzes(velden.get("soort"), velden.get("eigendom"),
                       velden.get("energiedrager"), velden.get("emissieklasse"))
    voor = {"naam": stuk.naam, "energiedrager": stuk.energiedrager,
            "verbruik_per_uur": stuk.verbruik_per_uur}
    for veld, waarde in velden.items():
        setattr(stuk, veld, waarde)
    db.commit()
    db.refresh(stuk)
    log_action(db, request, current_user, action=ACTION.MATERIEEL_UPDATE,
               entity_type="materieel", entity_id=stuk.id, before=voor,
               after={k: velden[k] for k in velden})
    return _toon_stuk(stuk)


@router.delete("/{stuk_id}")
def archiveer_stuk(stuk_id: str, request: Request,
                   current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """Uit de keuzelijst halen, niet weggooien.

    Aan dit materieelstuk hangen dagen uit het verleden. Wie het echt verwijdert
    haalt de onderbouwing onder een afgegeven CO2-rapportage vandaan, dus
    archiveren is het enige wat hier gebeurt.
    """
    _eis_beheer(current_user)
    stuk = _stuk_van_org(db, stuk_id, current_user)
    stuk.actief = False
    db.commit()
    log_action(db, request, current_user, action=ACTION.MATERIEEL_ARCHIVE,
               entity_type="materieel", entity_id=stuk.id, after={"actief": False})
    return {"gearchiveerd": True}
