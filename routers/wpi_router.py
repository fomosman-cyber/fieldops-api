"""Werkplekinspectie-router (WPI) — de rondgang langs de controlevragen.

Endpoints:
  GET    /api/wpi/checklist              De vragenlijst zelf
  GET    /api/wpi/                       Lijst van rondgangen
  POST   /api/wpi/                       Start een rondgang (vult alle vragen voor)
  GET    /api/wpi/{id}                   Detail met alle antwoorden
  PATCH  /api/wpi/{id}                   Kop bijwerken (locatie, indruk)
  PATCH  /api/wpi/{id}/antwoorden/{aid}  Een vraag beantwoorden
  POST   /api/wpi/{id}/afronden          Vastzetten en score berekenen
  DELETE /api/wpi/{id}                   Verwijderen
  GET    /api/wpi/{id}/export.pdf        Rapport voor de opdrachtgever
  GET    /api/wpi/{id}/export.xlsx       Hetzelfde rapport als werkboek
  GET    /api/wpi/acties/open            Alle openstaande acties uit alle rondgangen

Rollen volgen de rest van Veiligheid: opstellen, invullen en afronden is voor
admin en manager -- een WPI is het werk van de uitvoerder of KAM-functionaris.
Lezen mag iedereen binnen de organisatie; anders dan bij incidenten staan hier
geen gezondheidsgegevens in, en een ploeg die de openstaande punten kan zien
lost ze eerder op.
"""
import base64
import io
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import wpi_checklist as wc
from audit import log_action
from auth import get_current_user
from database import get_db
from export_huisstijl import (GRIJS, INKT, LETTER_PDF, Blad, HuisstijlPDF, Kolom,
                              excel_antwoord, excel_van, klant_van, pdf_antwoord, rgb)
from models import Organization, Project, User, Werkplekinspectie, WerkplekinspectieAntwoord
from permissions import can_manage_toolbox, require_module

router = APIRouter(prefix="/api/wpi", tags=["Veiligheid"],
                   dependencies=[Depends(require_module("veiligheid"))])


# ── Pydantic-schemas ─────────────────────────────────────────────────

class WpiIn(BaseModel):
    project_id: str = Field(..., min_length=1)
    datum: Optional[datetime] = None
    locatie: Optional[str] = None


class WpiUpdate(BaseModel):
    locatie: Optional[str] = None
    datum: Optional[datetime] = None
    algemene_indruk: Optional[str] = None


class AntwoordIn(BaseModel):
    antwoord: Optional[str] = Field(default=None, pattern="^(ja|nee|nvt)$")
    toelichting: Optional[str] = None
    photo_url: Optional[str] = None
    actie: Optional[str] = None
    actiehouder_id: Optional[str] = None
    actie_gereed: Optional[bool] = None


# ── Helpers ──────────────────────────────────────────────────────────

def _eis_beheer(current_user: User) -> None:
    if not can_manage_toolbox(current_user):
        raise HTTPException(
            status_code=403,
            detail="Alleen een beheerder of manager kan een werkplekinspectie invullen")


def _antwoord_to_dict(a: WerkplekinspectieAntwoord) -> dict:
    return {
        "id": a.id,
        "question_code": a.question_code,
        "vraag": a.question_text_snapshot,
        "categorie": a.categorie,
        "antwoord": a.antwoord,
        "toelichting": a.toelichting,
        "photo_url": a.photo_url,
        "actie": a.actie,
        "actiehouder_id": a.actiehouder_id,
        "actiehouder_naam": a.actiehouder_naam,
        "actie_gereed": bool(a.actie_gereed),
        "order_index": a.order_index,
    }


def _wpi_to_dict(w: Werkplekinspectie, *, include_antwoorden: bool = False) -> dict:
    antwoorden = list(w.antwoorden or [])
    uit = {
        "id": w.id,
        "project_id": w.project_id,
        "project_name": w.project.name if w.project else None,
        "datum": w.datum.isoformat() if w.datum else None,
        "locatie": w.locatie,
        "inspecteur_id": w.inspecteur_id,
        "inspecteur_naam": w.inspecteur_naam,
        "status": w.status,
        "checklist_versie": w.checklist_versie,
        "algemene_indruk": w.algemene_indruk,
        "score_pct": w.score_pct,
        "aantal_niet_in_orde": w.aantal_niet_in_orde,
        "open_acties": sum(1 for a in antwoorden
                           if a.antwoord == "nee" and not a.actie_gereed),
        "afgerond_op": w.afgerond_op.isoformat() if w.afgerond_op else None,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }
    if not w.score_pct and w.status == "concept":
        # Tussenstand tonen zolang de rondgang loopt, zodat je ziet hoe ver je bent.
        uit["voortgang"] = wc.bereken_score(
            [{"antwoord": a.antwoord} for a in antwoorden])
    if include_antwoorden:
        uit["antwoorden"] = [_antwoord_to_dict(a) for a in antwoorden]
        uit["categorieen"] = wc.CATEGORIEEN
    return uit


def _get_wpi_or_404(db: Session, wpi_id: str, current_user: User) -> Werkplekinspectie:
    w = (db.query(Werkplekinspectie)
           .filter(Werkplekinspectie.id == wpi_id,
                   Werkplekinspectie.organization_id == current_user.organization_id)
           .first())
    if not w:
        raise HTTPException(status_code=404, detail="Werkplekinspectie niet gevonden")
    return w


def _eis_niet_afgerond(w: Werkplekinspectie) -> None:
    if w.status == "afgerond":
        raise HTTPException(
            status_code=409,
            detail="Deze werkplekinspectie is afgerond en kan niet meer worden gewijzigd")


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/checklist")
def get_checklist(current_user: User = Depends(get_current_user)):
    """De vragenlijst zelf, zodat het portaal hem kan tonen zonder hem te kopiëren."""
    return {
        "versie": wc.WPI_VERSION,
        "categorieen": wc.CATEGORIEEN,
        "vragen": wc.VRAGEN,
    }


@router.get("/acties/open")
def open_acties(
    project_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alle punten die niet in orde waren en nog niet zijn opgelost.

    Staat bewust vóór /{wpi_id} in dit bestand: dat pad is ook één segment en
    zou /acties anders opslokken.
    """
    q = (db.query(WerkplekinspectieAntwoord)
           .join(Werkplekinspectie,
                 Werkplekinspectie.id == WerkplekinspectieAntwoord.wpi_id)
           .filter(WerkplekinspectieAntwoord.organization_id == current_user.organization_id,
                   WerkplekinspectieAntwoord.antwoord == "nee",
                   WerkplekinspectieAntwoord.actie_gereed.is_(False)))
    if project_id:
        q = q.filter(Werkplekinspectie.project_id == project_id)

    items = q.order_by(WerkplekinspectieAntwoord.created_at.desc()).limit(300).all()
    return [{
        **_antwoord_to_dict(a),
        "wpi_id": a.wpi_id,
        "project_name": a.inspectie.project.name if a.inspectie and a.inspectie.project else None,
        "datum": a.inspectie.datum.isoformat() if a.inspectie and a.inspectie.datum else None,
    } for a in items]


@router.get("/")
def list_wpi(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = (db.query(Werkplekinspectie)
           .filter(Werkplekinspectie.organization_id == current_user.organization_id))
    if project_id:
        q = q.filter(Werkplekinspectie.project_id == project_id)
    if status:
        q = q.filter(Werkplekinspectie.status == status)
    items = q.order_by(Werkplekinspectie.created_at.desc()).limit(500).all()
    return [_wpi_to_dict(w) for w in items]


@router.post("/")
def create_wpi(
    payload: WpiIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start een rondgang. Alle vragen worden meteen aangemaakt.

    Bewust vooraf en niet gaandeweg: zo staat de complete lijst op je scherm en
    zie je wat je nog niet gehad hebt. Een lijst die zich opbouwt terwijl je
    loopt laat je makkelijk iets overslaan.
    """
    _eis_beheer(current_user)
    project = (db.query(Project)
                 .filter(Project.id == payload.project_id,
                         Project.organization_id == current_user.organization_id)
                 .first())
    if not project:
        raise HTTPException(status_code=404, detail="Project niet gevonden")

    naam = " ".join(x for x in (current_user.first_name, current_user.last_name) if x).strip()
    w = Werkplekinspectie(
        organization_id=current_user.organization_id,
        project_id=project.id,
        datum=payload.datum or datetime.now(timezone.utc),
        locatie=payload.locatie,
        inspecteur_id=current_user.id,
        inspecteur_naam=naam or current_user.email,
        status="concept",
        checklist_versie=wc.WPI_VERSION,
        created_by=current_user.id,
    )
    db.add(w)
    db.flush()

    for i, v in enumerate(wc.VRAGEN):
        db.add(WerkplekinspectieAntwoord(
            wpi_id=w.id,
            organization_id=current_user.organization_id,
            question_code=v["code"],
            question_version=wc.WPI_VERSION,
            question_text_snapshot=v["vraag"][:500],
            categorie=v["categorie"],
            order_index=i,
        ))

    db.commit()
    db.refresh(w)
    log_action(db, request, current_user, action="wpi.create",
               entity_type="wpi", entity_id=w.id,
               after={"project_id": w.project_id, "vragen": len(wc.VRAGEN)})
    return _wpi_to_dict(w, include_antwoorden=True)


@router.get("/{wpi_id}")
def get_wpi(
    wpi_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    w = _get_wpi_or_404(db, wpi_id, current_user)
    return _wpi_to_dict(w, include_antwoorden=True)


@router.patch("/{wpi_id}")
def update_wpi(
    wpi_id: str,
    payload: WpiUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    w = _get_wpi_or_404(db, wpi_id, current_user)
    _eis_niet_afgerond(w)
    for veld, waarde in payload.model_dump(exclude_unset=True).items():
        setattr(w, veld, waarde)
    db.commit()
    db.refresh(w)
    return _wpi_to_dict(w, include_antwoorden=True)


@router.patch("/{wpi_id}/antwoorden/{antwoord_id}")
def beantwoord(
    wpi_id: str,
    antwoord_id: str,
    payload: AntwoordIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Beantwoord één controlevraag.

    Een NEE zonder toelichting wordt geweigerd. "Niet in orde" zonder te zeggen
    wát er niet in orde is, is voor degene die het moet oplossen waardeloos --
    en voor een auditor een leeg vinkje.

    Het afvinken van een actie (`actie_gereed`) mag óók als de inspectie al is
    afgerond: het punt blijft staan zoals het geconstateerd is, maar het werk
    eraan loopt door.
    """
    _eis_beheer(current_user)
    w = _get_wpi_or_404(db, wpi_id, current_user)

    a = (db.query(WerkplekinspectieAntwoord)
           .filter(WerkplekinspectieAntwoord.id == antwoord_id,
                   WerkplekinspectieAntwoord.wpi_id == w.id)
           .first())
    if not a:
        raise HTTPException(status_code=404, detail="Vraag niet gevonden")

    velden = payload.model_dump(exclude_unset=True)
    alleen_actie_afvinken = set(velden) <= {"actie_gereed"}
    if not alleen_actie_afvinken:
        _eis_niet_afgerond(w)

    if velden.get("antwoord") == "nee":
        toelichting = velden.get("toelichting", a.toelichting)
        if not (toelichting or "").strip():
            raise HTTPException(
                status_code=400,
                detail="Vul bij 'niet in orde' een toelichting in — zonder uitleg kan "
                       "niemand er iets mee")

    if "actiehouder_id" in velden and velden["actiehouder_id"]:
        u = (db.query(User)
               .filter(User.id == velden["actiehouder_id"],
                       User.organization_id == current_user.organization_id)
               .first())
        if not u:
            raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")
        a.actiehouder_naam = " ".join(
            x for x in (u.first_name, u.last_name) if x).strip() or u.email

    for veld, waarde in velden.items():
        setattr(a, veld, waarde)
    db.commit()
    db.refresh(a)
    return _antwoord_to_dict(a)


@router.post("/{wpi_id}/afronden")
def afronden(
    wpi_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Zet de rondgang vast en bereken de score.

    Weigert als er nog vragen open staan: een halve rondgang met een mooie
    score is misleidend, en dat is precies wat een auditor eruit haalt.
    """
    _eis_beheer(current_user)
    w = _get_wpi_or_404(db, wpi_id, current_user)
    if w.status == "afgerond":
        raise HTTPException(status_code=409, detail="Deze werkplekinspectie is al afgerond")

    antwoorden = list(w.antwoorden or [])
    onbeantwoord = [a for a in antwoorden if not a.antwoord]
    if onbeantwoord:
        raise HTTPException(
            status_code=400,
            detail=f"Er staan nog {len(onbeantwoord)} vragen open. Beantwoord ze, of zet "
                   f"ze op 'niet van toepassing'.")

    telling = wc.bereken_score([{"antwoord": a.antwoord} for a in antwoorden])
    w.status = "afgerond"
    w.score_pct = telling["score_pct"]
    w.aantal_niet_in_orde = telling["niet_in_orde"]
    w.afgerond_op = datetime.now(timezone.utc)
    db.commit()
    db.refresh(w)

    log_action(db, request, current_user, action="wpi.afronden",
               entity_type="wpi", entity_id=w.id,
               after={"score_pct": w.score_pct, "niet_in_orde": w.aantal_niet_in_orde})
    return _wpi_to_dict(w, include_antwoorden=True)


@router.delete("/{wpi_id}")
def delete_wpi(
    wpi_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    w = _get_wpi_or_404(db, wpi_id, current_user)
    _eis_niet_afgerond(w)
    db.delete(w)
    db.commit()
    log_action(db, request, current_user, action="wpi.delete",
               entity_type="wpi", entity_id=wpi_id)
    return {"ok": True}


# ── Export: PDF en Excel ─────────────────────────────────────────────
#
# In de huisstijl van export_huisstijl: FieldOps-kleuren, met het logo en de
# gegevens van de klant in het briefhoofd. De tabellen hebben in PDF en Excel
# dezelfde kolommen; de foto's staan alleen in de PDF.

KOLOMMEN_GEGEVENS = [Kolom("Onderdeel", breedte=30), Kolom("Waarde", breedte=70)]
KOLOMMEN_ACTIES = [Kolom("Code", breedte=31), Kolom("Vraag", breedte=45),
                   Kolom("Toelichting", breedte=38), Kolom("Actie", breedte=29),
                   Kolom("Actiehouder", breedte=22), Kolom("Status", breedte=13)]
KOLOMMEN_CHECKLIST = [Kolom("Categorie", breedte=34), Kolom("Vraag", breedte=72),
                      Kolom("Status", breedte=26), Kolom("Opmerking", breedte=46)]

_TITEL = "Werkplekinspectie"
_STATUS = {"ja": "in orde", "nee": "NIET IN ORDE", "nvt": "n.v.t."}


def _export_inhoud(w: Werkplekinspectie) -> dict:
    """Wat er in het rapport staat, één keer uitgerekend voor PDF en Excel."""
    antwoorden = list(w.antwoorden or [])
    telling = wc.bereken_score([{"antwoord": a.antwoord} for a in antwoorden])
    score = f"{w.score_pct}% in orde" if w.score_pct is not None else "nog niet afgerond"
    beoordeeld = f"{telling['beoordeeld']} van {telling['totaal']} ({telling['nvt']} n.v.t.)"

    gegevens = [
        ("Datum", w.datum.strftime("%d-%m-%Y") if w.datum else "-"),
        ("Locatie", w.locatie or "-"),
        ("Uitgevoerd door", w.inspecteur_naam or "-"),
        ("Status", w.status),
        ("Vragenlijst", w.checklist_versie or "-"),
        ("Score", score),
        ("Beoordeeld", beoordeeld),
    ]

    # Eerst wat niet in orde was -- dat is waar het rapport over gaat. Open
    # zolang de actie niet gereed is, dezelfde telling als /acties/open.
    niet_ok = [a for a in antwoorden if a.antwoord == "nee"]
    acties = [[a.question_code, a.question_text_snapshot, a.toelichting, a.actie,
               a.actiehouder_naam, "gereed" if a.actie_gereed else "open"]
              for a in niet_ok]

    checklist = [[wc.CATEGORIEEN.get(a.categorie, a.categorie or ""),
                  a.question_text_snapshot,
                  _STATUS.get(a.antwoord, "niet beantwoord"),
                  a.toelichting]
                 for a in antwoorden]

    samenvatting = ([["Project", w.project.name if w.project else "-"]]
                    + [[k, v] for k, v in gegevens])
    if w.algemene_indruk:
        samenvatting.append(["Algemene indruk", w.algemene_indruk])

    return {
        "gegevens": gegevens,
        "niet_ok": niet_ok,
        "samenvatting": Blad("Samenvatting", KOLOMMEN_GEGEVENS, samenvatting),
        # Brede tabbladen liggend afdrukken, anders wordt de letter te klein.
        "acties": Blad("Acties", KOLOMMEN_ACTIES, acties, liggend=True),
        "checklist": Blad("Checklist", KOLOMMEN_CHECKLIST, checklist, liggend=True),
    }


def _foto(data_url: Optional[str]) -> Optional[tuple[bytes, int, int]]:
    """Een foto als JPEG, rechtop gezet, met breedte en hoogte in pixels.

    Alleen foto's die als data-URL zijn opgeslagen; een onleesbare foto geeft
    None, want die mag het rapport niet slopen. Verkleind tot wat op papier
    nog iets toevoegt, zodat een rondgang met tien foto's geen 40 MB wordt.
    """
    if not data_url or not data_url.startswith("data:image") or "," not in data_url:
        return None
    try:
        from PIL import Image, ImageOps
        ruw = base64.b64decode(data_url.split(",", 1)[1])
        with Image.open(io.BytesIO(ruw)) as beeld:
            beeld.load()
            beeld = ImageOps.exif_transpose(beeld).convert("RGB")
            beeld.thumbnail((1400, 1400))
            uit = io.BytesIO()
            beeld.save(uit, format="JPEG", quality=85)
            return uit.getvalue(), beeld.width, beeld.height
    except Exception:  # noqa: BLE001
        return None


def _past(pdf: HuisstijlPDF, tekst: str, breedte: float) -> str:
    """Tekst ingekort tot hij op één regel van `breedte` mm past."""
    tekst = (tekst or "").strip()
    if pdf.get_string_width(tekst) <= breedte:
        return tekst
    while tekst and pdf.get_string_width(tekst + "…") > breedte:
        tekst = tekst[:-1]
    return tekst.rstrip() + "…"


def _fotos(pdf: HuisstijlPDF, antwoorden: list) -> None:
    """De foto's bij de punten die niet in orde waren, drie naast elkaar, met
    de vraagcode eronder zodat je ze bij de tabel terugvindt. De foto's staan
    op één onderlijn, zodat de onderschriften van een rij gelijk lopen."""
    fotos = [(a, f) for a in antwoorden if (f := _foto(a.photo_url))]
    if not fotos:
        return
    per_rij, tussen, max_h, onderschrift = 3, 4.0, 62.0, 8.5
    breed = (pdf.w - 2 * pdf.MARGE - tussen * (per_rij - 1)) / per_rij
    for i in range(0, len(fotos), per_rij):
        rij = fotos[i:i + per_rij]
        maten = []
        for _, (_, px_b, px_h) in rij:
            b, h = breed, breed * px_h / px_b
            if h > max_h:
                b, h = max_h * px_b / px_h, max_h
            maten.append((b, h))
        hoogste = max(h for _, h in maten)
        hoogte = hoogste + onderschrift
        if i == 0:
            pdf.ruimte_nodig(hoogte + 10)
            pdf.subsectie("Foto's")
        else:
            pdf.ruimte_nodig(hoogte + tussen)
        y = pdf.get_y()
        for k, ((a, (data, _, _)), (b, h)) in enumerate(zip(rij, maten)):
            x = pdf.MARGE + k * (breed + tussen)
            try:
                pdf.image(io.BytesIO(data), x=x, y=y + hoogste - h, w=b, h=h)
            except Exception:  # noqa: BLE001 — een onleesbare foto mag het rapport niet slopen
                continue
            pdf.set_xy(x, y + hoogste + 1.2)
            pdf.set_font(LETTER_PDF, "B", 7.5)
            pdf.set_text_color(*rgb(INKT))
            pdf.cell(breed, 3.4, _past(pdf, a.question_code, breed))
            pdf.set_xy(x, y + hoogste + 4.6)
            pdf.set_font(LETTER_PDF, "", 7)
            pdf.set_text_color(*rgb(GRIJS))
            pdf.cell(breed, 3.2, _past(pdf, a.question_text_snapshot, breed))
        pdf.set_text_color(*rgb(INKT))
        pdf.set_xy(pdf.MARGE, y + hoogte + tussen)


def _bestandsnaam(w: Werkplekinspectie, ext: str) -> str:
    datum = (w.datum or datetime.now(timezone.utc)).date().isoformat()
    return f"werkplekinspectie-{datum}.{ext}"


def _klant(db: Session, current_user: User):
    org = db.query(Organization).filter(
        Organization.id == current_user.organization_id).first()
    return klant_van(org)


@router.get("/{wpi_id}/export.pdf")
def export_wpi_pdf(
    wpi_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rapport van de rondgang: score, de punten die niet in orde waren, en
    wie wat oplost."""
    w = _get_wpi_or_404(db, wpi_id, current_user)
    inhoud = _export_inhoud(w)

    pdf = HuisstijlPDF(_klant(db, current_user), _TITEL,
                       ondertitel=w.project.name if w.project else "-")
    pdf.add_page()
    pdf.titelblok()
    pdf.kv(inhoud["gegevens"], labelbreedte=40)

    if w.algemene_indruk:
        pdf.sectie("Algemene indruk")
        pdf.tekst(w.algemene_indruk)
        pdf.ln(2)

    niet_ok, acties = inhoud["niet_ok"], inhoud["acties"]
    pdf.sectie(f"Niet in orde ({len(niet_ok)})")
    if not niet_ok:
        pdf.tekst("Geen bijzonderheden aangetroffen.", grootte=9.5)
    else:
        pdf.tabel(acties.kolommen, acties.rijen)
        _fotos(pdf, niet_ok)

    # Daarna de volledige lijst, zodat zichtbaar is wat er gecontroleerd is.
    # De categorie staat alleen boven de eerste vraag van elke groep.
    checklist = inhoud["checklist"]
    rijen, vorige = [], None
    for rij in checklist.rijen:
        rijen.append(["" if rij[0] == vorige else rij[0], *rij[1:]])
        vorige = rij[0]
    pdf.sectie("Volledige checklist")
    pdf.tabel(checklist.kolommen, rijen)

    w.pdf_generated_at = datetime.now(timezone.utc)
    db.commit()
    log_action(db, request, current_user, action="wpi.export_pdf",
               entity_type="wpi", entity_id=w.id)
    return pdf_antwoord(pdf.uitvoer(), _bestandsnaam(w, "pdf"))


@router.get("/{wpi_id}/export.xlsx")
def export_wpi_xlsx(
    wpi_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """De rondgang als werkboek: samenvatting, de acties bij wat niet in orde
    was, en de volledige checklist -- met dezelfde kolommen als de PDF."""
    w = _get_wpi_or_404(db, wpi_id, current_user)
    inhoud = _export_inhoud(w)
    bestand = excel_van(
        _klant(db, current_user), _TITEL,
        [inhoud["samenvatting"], inhoud["acties"], inhoud["checklist"]],
        ondertitel=w.project.name if w.project else "")
    log_action(db, request, current_user, action="wpi.export_xlsx",
               entity_type="wpi", entity_id=w.id)
    return excel_antwoord(bestand, _bestandsnaam(w, "xlsx"))
