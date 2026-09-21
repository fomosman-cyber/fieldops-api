"""Toolbox-router — de veiligheidsbespreking op de bouwplaats.

Endpoints:
  GET    /api/toolbox/                          Lijst (mijn org)
  POST   /api/toolbox/genereer                  AI-voorstel, slaat nog niets op
  POST   /api/toolbox/                          Nieuwe toolbox
  GET    /api/toolbox/{id}                      Detail + presentielijst
  PATCH  /api/toolbox/{id}                      Bijwerken
  DELETE /api/toolbox/{id}                      Verwijderen
  POST   /api/toolbox/{id}/deelnemers           Deelnemer toevoegen (ook externen)
  DELETE /api/toolbox/{id}/deelnemers/{did}     Deelnemer verwijderen
  POST   /api/toolbox/{id}/deelnemers/{did}/sign   Tekenen
  POST   /api/toolbox/{id}/afsluiten            Presentielijst definitief maken
  GET    /api/toolbox/{id}/export.pdf           Toolbox + ondertekende presentielijst
  GET    /api/toolbox/{id}/export.xlsx          Zelfde inhoud en presentielijst als werkboek

Rolverdeling: opstellen, wijzigen en afsluiten is voor admin/manager — de
uitvoerder leidt de bespreking. Tekenen mag iedereen die is ingelogd, en
externen (onderaannemer, ZZP'er) staan als deelnemer op de lijst zonder dat ze
een account nodig hebben.
"""
import base64
import io
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fpdf.fonts import FontFace
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import toolbox_ai
from audit import log_action
from auth import get_current_user
from database import get_db
from export_huisstijl import (BLAUW, GRIJS, INKT, LETTER_PDF, LIJN, STREEP, WIT, Blad,
                              HuisstijlPDF, Kolom, als_tekst, excel_antwoord, excel_van,
                              klant_van, naar_nl, pdf_antwoord, rgb)
from models import Asset, Melding, Organization, Project, Toolbox, ToolboxDeelnemer, User
from permissions import can_manage_toolbox, require_module

router = APIRouter(prefix="/api/toolbox", tags=["Veiligheid"],
                   dependencies=[Depends(require_module("veiligheid"))])


# ── Pydantic-schemas ─────────────────────────────────────────────────

class ToolboxGenereerIn(BaseModel):
    project_id: str = Field(..., min_length=1)
    onderwerp: str = Field(..., min_length=1, max_length=255)


class ToolboxIn(BaseModel):
    project_id: str = Field(..., min_length=1)
    onderwerp: str = Field(..., min_length=1, max_length=255)
    datum: Optional[datetime] = None
    inleiding: Optional[str] = None
    risicos: Optional[List[str]] = None
    maatregelen: Optional[List[str]] = None
    bespreekpunten: Optional[List[str]] = None
    afspraken: Optional[str] = None
    ai_gegenereerd: bool = False
    ai_model: Optional[str] = None
    ai_prompt_versie: Optional[str] = None


class ToolboxUpdate(BaseModel):
    onderwerp: Optional[str] = None
    datum: Optional[datetime] = None
    inleiding: Optional[str] = None
    risicos: Optional[List[str]] = None
    maatregelen: Optional[List[str]] = None
    bespreekpunten: Optional[List[str]] = None
    afspraken: Optional[str] = None
    status: Optional[str] = Field(default=None, pattern="^(concept|gehouden|afgesloten)$")


class DeelnemerIn(BaseModel):
    naam: Optional[str] = None            # verplicht voor externen; anders uit het account
    bedrijf: Optional[str] = None
    user_id: Optional[str] = None         # eigen medewerker
    aanwezig: bool = True
    order_index: int = 0


class SignIn(BaseModel):
    signature_data_url: str = Field(..., min_length=1)


# ── Helpers ──────────────────────────────────────────────────────────

def _lijst(waarde: Optional[str]) -> list:
    """JSON-kolom naar lijst. Een kapotte of lege waarde is een lege lijst."""
    if not waarde:
        return []
    try:
        uit = json.loads(waarde)
        return uit if isinstance(uit, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _dump(waarde: Optional[List[str]]) -> Optional[str]:
    if waarde is None:
        return None
    return json.dumps([str(x) for x in waarde], ensure_ascii=False)


def _deelnemer_to_dict(d: ToolboxDeelnemer) -> dict:
    return {
        "id": d.id,
        "user_id": d.user_id,
        "naam": d.naam,
        "bedrijf": d.bedrijf,
        "extern": d.user_id is None,
        "aanwezig": d.aanwezig,
        "getekend": bool(d.signature_data_url),
        "signed_at": d.signed_at.isoformat() if d.signed_at else None,
        "order_index": d.order_index,
    }


def _toolbox_to_dict(t: Toolbox, *, include_deelnemers: bool = False) -> dict:
    out = {
        "id": t.id,
        "project_id": t.project_id,
        "project_name": t.project.name if t.project else None,
        "onderwerp": t.onderwerp,
        "datum": t.datum.isoformat() if t.datum else None,
        "houder_id": t.houder_id,
        "houder_naam": t.houder_naam,
        "status": t.status,
        "inleiding": t.inleiding,
        "risicos": _lijst(t.risicos),
        "maatregelen": _lijst(t.maatregelen),
        "bespreekpunten": _lijst(t.bespreekpunten),
        "afspraken": t.afspraken,
        "ai_gegenereerd": bool(t.ai_gegenereerd),
        "ai_model": t.ai_model,
        "deelnemers_count": len(t.deelnemers or []),
        "getekend_count": sum(1 for d in (t.deelnemers or []) if d.signature_data_url),
        "created_by": t.created_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
    if include_deelnemers:
        out["deelnemers"] = [_deelnemer_to_dict(d) for d in (t.deelnemers or [])]
    return out


def _get_toolbox_or_404(db: Session, toolbox_id: str, current_user: User) -> Toolbox:
    t = (db.query(Toolbox)
           .filter(Toolbox.id == toolbox_id,
                   Toolbox.organization_id == current_user.organization_id)
           .first())
    if not t:
        raise HTTPException(status_code=404, detail="Toolbox niet gevonden")
    return t


def _get_project_or_404(db: Session, project_id: str, current_user: User) -> Project:
    """Project ophalen binnen de eigen organisatie.

    De org-check hoort hier en niet alleen op de toolbox: zonder deze filter
    kan iemand een toolbox aan het project van een andere klant hangen door
    een gegokt project_id mee te sturen.
    """
    p = (db.query(Project)
           .filter(Project.id == project_id,
                   Project.organization_id == current_user.organization_id)
           .first())
    if not p:
        raise HTTPException(status_code=404, detail="Project niet gevonden")
    return p


def _eis_beheer(current_user: User) -> None:
    if not can_manage_toolbox(current_user):
        raise HTTPException(
            status_code=403,
            detail="Alleen een beheerder of manager kan een toolbox opstellen of wijzigen")


def _eis_niet_afgesloten(t: Toolbox) -> None:
    if t.status == "afgesloten":
        raise HTTPException(
            status_code=409,
            detail="Deze toolbox is afgesloten en kan niet meer worden gewijzigd")


def _projectcontext(db: Session, project: Project, current_user: User) -> tuple:
    """Assets en openstaande meldingen van dit project, voor de AI-generatie."""
    assets = (db.query(Asset)
                .filter(Asset.organization_id == current_user.organization_id,
                        Asset.project_id == project.id)
                .limit(200).all())
    meldingen = (db.query(Melding)
                   .filter(Melding.organization_id == current_user.organization_id,
                           Melding.project_id == project.id,
                           Melding.status != "afgerond")
                   .order_by(Melding.created_at.desc())
                   .limit(30).all())
    return (
        [{"asset_type": getattr(a, "asset_type", None)} for a in assets],
        [{"titel": getattr(m, "title", None) or getattr(m, "titel", None),
          "prioriteit": str(getattr(m, "priority", "") or ""),
          "categorie": str(getattr(m, "category", "") or "")} for m in meldingen],
    )


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/")
def list_toolboxen(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Toolbox).filter(Toolbox.organization_id == current_user.organization_id)
    if project_id:
        q = q.filter(Toolbox.project_id == project_id)
    if status:
        q = q.filter(Toolbox.status == status)
    items = q.order_by(Toolbox.created_at.desc()).limit(500).all()
    return [_toolbox_to_dict(t) for t in items]


@router.post("/genereer")
def genereer_toolbox(
    payload: ToolboxGenereerIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stel een voorstel op. Slaat nog niets op — de uitvoerder leest het na,
    past aan en bewaart het pas daarna via POST /api/toolbox/.

    Geeft altijd 200: valt de AI weg, dan komt er een sjabloon terug met
    `bron: "sjabloon"`, zodat het portaal dat eerlijk kan tonen.
    """
    _eis_beheer(current_user)
    project = _get_project_or_404(db, payload.project_id, current_user)
    assets, meldingen = _projectcontext(db, project, current_user)

    voorstel = toolbox_ai.genereer_toolbox(
        onderwerp=payload.onderwerp,
        project_naam=project.name,
        assets=assets,
        meldingen=meldingen,
    )
    voorstel["project_id"] = project.id
    voorstel["onderwerp"] = payload.onderwerp
    voorstel["context_gebruikt"] = {
        "assets": len(assets),
        "open_meldingen": len(meldingen),
    }
    return voorstel


@router.post("/")
def create_toolbox(
    payload: ToolboxIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    project = _get_project_or_404(db, payload.project_id, current_user)

    naam = " ".join(x for x in (current_user.first_name, current_user.last_name) if x).strip()
    t = Toolbox(
        organization_id=current_user.organization_id,
        project_id=project.id,
        onderwerp=payload.onderwerp,
        datum=payload.datum or datetime.now(timezone.utc),
        houder_id=current_user.id,
        houder_naam=naam or current_user.email,
        status="concept",
        inleiding=payload.inleiding,
        risicos=_dump(payload.risicos),
        maatregelen=_dump(payload.maatregelen),
        bespreekpunten=_dump(payload.bespreekpunten),
        afspraken=payload.afspraken,
        ai_gegenereerd=payload.ai_gegenereerd,
        ai_model=payload.ai_model,
        ai_prompt_versie=payload.ai_prompt_versie,
        created_by=current_user.id,
    )
    db.add(t)
    db.commit()
    db.refresh(t)

    log_action(db, request, current_user, action="toolbox.create",
               entity_type="toolbox", entity_id=t.id,
               after={"onderwerp": t.onderwerp, "project_id": t.project_id,
                      "ai_gegenereerd": t.ai_gegenereerd})
    return _toolbox_to_dict(t, include_deelnemers=True)


@router.get("/{toolbox_id}")
def get_toolbox(
    toolbox_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = _get_toolbox_or_404(db, toolbox_id, current_user)
    return _toolbox_to_dict(t, include_deelnemers=True)


@router.patch("/{toolbox_id}")
def update_toolbox(
    toolbox_id: str,
    payload: ToolboxUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    t = _get_toolbox_or_404(db, toolbox_id, current_user)
    _eis_niet_afgesloten(t)

    velden = payload.model_dump(exclude_unset=True)
    for veld in ("risicos", "maatregelen", "bespreekpunten"):
        if veld in velden:
            setattr(t, veld, _dump(velden.pop(veld)))
    for veld, waarde in velden.items():
        setattr(t, veld, waarde)

    db.commit()
    db.refresh(t)
    log_action(db, request, current_user, action="toolbox.update",
               entity_type="toolbox", entity_id=t.id,
               after={"status": t.status})
    return _toolbox_to_dict(t, include_deelnemers=True)


@router.delete("/{toolbox_id}")
def delete_toolbox(
    toolbox_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    t = _get_toolbox_or_404(db, toolbox_id, current_user)
    _eis_niet_afgesloten(t)

    onderwerp = t.onderwerp
    db.delete(t)          # deelnemers gaan mee via cascade
    db.commit()
    log_action(db, request, current_user, action="toolbox.delete",
               entity_type="toolbox", entity_id=toolbox_id,
               before={"onderwerp": onderwerp})
    return {"ok": True}


# ── Presentielijst ───────────────────────────────────────────────────

@router.post("/{toolbox_id}/deelnemers")
def add_deelnemer(
    toolbox_id: str,
    payload: DeelnemerIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Zet iemand op de presentielijst.

    Met user_id voor een eigen medewerker (naam wordt uit het account gehaald),
    of met alleen een naam voor een externe. Die externe hoeft geen account te
    hebben maar moet wel kunnen tekenen.
    """
    _eis_beheer(current_user)
    t = _get_toolbox_or_404(db, toolbox_id, current_user)
    _eis_niet_afgesloten(t)

    naam = (payload.naam or "").strip()
    user_id = None
    if payload.user_id:
        u = (db.query(User)
               .filter(User.id == payload.user_id,
                       User.organization_id == current_user.organization_id)
               .first())
        if not u:
            raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")
        user_id = u.id
        if not naam:
            naam = " ".join(x for x in (u.first_name, u.last_name) if x).strip() or u.email

    if not naam:
        raise HTTPException(status_code=400, detail="Naam is verplicht")

    d = ToolboxDeelnemer(
        toolbox_id=t.id,
        organization_id=current_user.organization_id,
        user_id=user_id,
        naam=naam,
        bedrijf=(payload.bedrijf or "").strip() or None,
        aanwezig=payload.aanwezig,
        order_index=payload.order_index,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    log_action(db, request, current_user, action="toolbox.deelnemer_add",
               entity_type="toolbox", entity_id=t.id,
               after={"naam": d.naam, "extern": d.user_id is None})
    return _deelnemer_to_dict(d)


@router.delete("/{toolbox_id}/deelnemers/{deelnemer_id}")
def delete_deelnemer(
    toolbox_id: str,
    deelnemer_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _eis_beheer(current_user)
    t = _get_toolbox_or_404(db, toolbox_id, current_user)
    _eis_niet_afgesloten(t)

    d = (db.query(ToolboxDeelnemer)
           .filter(ToolboxDeelnemer.id == deelnemer_id,
                   ToolboxDeelnemer.toolbox_id == t.id)
           .first())
    if not d:
        raise HTTPException(status_code=404, detail="Deelnemer niet gevonden")

    naam = d.naam
    db.delete(d)
    db.commit()
    log_action(db, request, current_user, action="toolbox.deelnemer_delete",
               entity_type="toolbox", entity_id=t.id, before={"naam": naam})
    return {"ok": True}


@router.post("/{toolbox_id}/deelnemers/{deelnemer_id}/sign")
def sign_deelnemer(
    toolbox_id: str,
    deelnemer_id: str,
    payload: SignIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Tekenen op het scherm. Bewust GEEN beheerderscheck: de hele ploeg tekent,
    en dat gebeurt op het toestel van de uitvoerder terwijl hij erbij staat.
    """
    t = _get_toolbox_or_404(db, toolbox_id, current_user)
    _eis_niet_afgesloten(t)

    if not payload.signature_data_url.startswith("data:image"):
        raise HTTPException(status_code=400, detail="Handtekening moet een afbeelding-data-URL zijn")

    d = (db.query(ToolboxDeelnemer)
           .filter(ToolboxDeelnemer.id == deelnemer_id,
                   ToolboxDeelnemer.toolbox_id == t.id)
           .first())
    if not d:
        raise HTTPException(status_code=404, detail="Deelnemer niet gevonden")

    d.signature_data_url = payload.signature_data_url
    d.signed_at = datetime.now(timezone.utc)
    d.aanwezig = True
    if t.status == "concept":
        t.status = "gehouden"
    db.commit()
    db.refresh(d)
    log_action(db, request, current_user, action="toolbox.sign",
               entity_type="toolbox", entity_id=t.id,
               after={"deelnemer": d.naam})
    return _deelnemer_to_dict(d)


@router.post("/{toolbox_id}/afsluiten")
def afsluiten(
    toolbox_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Presentielijst definitief maken. Daarna wijzigt er niets meer — dat is
    het punt van een registratie die bij een audit standhoudt.
    """
    _eis_beheer(current_user)
    t = _get_toolbox_or_404(db, toolbox_id, current_user)
    if t.status == "afgesloten":
        raise HTTPException(status_code=409, detail="Deze toolbox is al afgesloten")

    t.status = "afgesloten"
    db.commit()
    db.refresh(t)
    log_action(db, request, current_user, action="toolbox.afsluiten",
               entity_type="toolbox", entity_id=t.id,
               after={"deelnemers": len(t.deelnemers or []),
                      "getekend": sum(1 for d in (t.deelnemers or []) if d.signature_data_url)})
    return _toolbox_to_dict(t, include_deelnemers=True)


# ── Export: PDF en Excel ─────────────────────────────────────────────
#
# In de huisstijl van export_huisstijl: FieldOps-kleuren, met het logo en de
# gegevens van de klant in het briefhoofd. De presentielijst heeft in PDF en
# Excel dezelfde kolommen; alleen de PDF draagt de handtekeningen zelf.

KOLOMMEN_GEGEVENS = [Kolom("Onderdeel", breedte=30), Kolom("Waarde", breedte=70)]
KOLOMMEN_AANWEZIGEN = [Kolom("Naam", breedte=62), Kolom("Bedrijf", breedte=48),
                       Kolom("Aanwezig", breedte=24), Kolom("Handtekening", breedte=46)]

_TITEL = "Toolbox"
_RIJ_MET_HANDTEKENING = 14.0   # mm; genoeg om een handtekening te herkennen


def _gegevens(t: Toolbox, org_naam: str) -> list[tuple[str, str]]:
    gegevens = [
        ("Project", t.project.name if t.project else "-"),
        ("Datum", t.datum.strftime("%d-%m-%Y") if t.datum else "-"),
        ("Gehouden door", t.houder_naam or "-"),
        ("Organisatie", org_naam),
        ("Status", t.status),
    ]
    if t.ai_gegenereerd:
        gegevens.append(("Opgesteld met",
                         f"AI-voorstel ({t.ai_model or 'onbekend model'}), "
                         "nagelopen door de opsteller"))
    return gegevens


def _getekend_op(d: ToolboxDeelnemer) -> str:
    if not d.signed_at:
        return "getekend"
    moment = d.signed_at if d.signed_at.tzinfo else d.signed_at.replace(tzinfo=timezone.utc)
    return f"getekend op {naar_nl(moment).strftime('%d-%m-%Y %H:%M')}"


def _aanwezigen_rij(d: ToolboxDeelnemer) -> list:
    """Eén regel van de presentielijst. In Excel staat bij de handtekening
    wanneer er getekend is; de PDF zet daar de handtekening zelf neer."""
    return [d.naam, d.bedrijf or ("-" if d.user_id else "extern"), bool(d.aanwezig),
            _getekend_op(d) if d.signature_data_url else ""]


def _handtekening(data_url: Optional[str]) -> Optional[bytes]:
    """De handtekening als PNG, of None als hij niet te lezen is.

    Vooraf gecontroleerd: een kapotte afbeelding halverwege de tabel zou de
    hele PDF laten klappen, en een onleesbare handtekening mag het document
    niet slopen.
    """
    if not data_url or "," not in data_url:
        return None
    try:
        from PIL import Image
        ruw = base64.b64decode(data_url.split(",", 1)[1])
        with Image.open(io.BytesIO(ruw)) as beeld:
            beeld.load()
            uit = io.BytesIO()
            beeld.convert("RGBA").save(uit, format="PNG")
            return uit.getvalue()
    except Exception:  # noqa: BLE001
        return None


def _presentielijst(pdf: HuisstijlPDF, deelnemers: list) -> None:
    """Presentielijst met de handtekening in de laatste kolom.

    Zelfde opmaak als `HuisstijlPDF.tabel()` (blauwe kop, gestreepte rijen,
    kop terug op elke nieuwe pagina). Die kan geen afbeelding in een cel
    zetten, dus deze tabel wordt hier met dezelfde instellingen getekend.
    """
    grootte = 8.5
    pdf.ruimte_nodig(16 + _RIJ_MET_HANDTEKENING)
    pdf.set_fill_color(*rgb(WIT))       # zoals tabel(): anders erven de rijen blauw
    pdf.set_x(pdf.MARGE)
    pdf.set_font(LETTER_PDF, "", grootte)
    pdf.set_text_color(*rgb(INKT))
    pdf.set_draw_color(*rgb(LIJN))
    pdf.set_line_width(0.2)
    kop = FontFace(emphasis="BOLD", color=rgb(WIT), fill_color=rgb(BLAUW))
    with pdf.table(width=pdf.w - 2 * pdf.MARGE,
                   col_widths=[k.breedte for k in KOLOMMEN_AANWEZIGEN],
                   headings_style=kop, cell_fill_color=rgb(STREEP), cell_fill_mode="ROWS",
                   borders_layout="HORIZONTAL_LINES", line_height=grootte * 0.55,
                   text_align="LEFT", v_align="MIDDLE", padding=(1.1, 1.5), align="LEFT",
                   first_row_as_headings=True, repeat_headings=1) as tabel:
        kopregel = tabel.row()
        for k in KOLOMMEN_AANWEZIGEN:
            kopregel.cell(k.naam)
        for d in deelnemers:
            waarden = _aanwezigen_rij(d)
            rij = tabel.row(min_height=_RIJ_MET_HANDTEKENING)
            for k, v in zip(KOLOMMEN_AANWEZIGEN[:-1], waarden[:-1]):
                rij.cell(als_tekst(k, v))
            beeld = _handtekening(d.signature_data_url)
            if beeld:
                rij.cell(img=beeld, img_fill_width=False)
            elif d.signature_data_url:
                rij.cell("getekend", style=FontFace(emphasis="ITALICS", color=rgb(GRIJS)))
            else:
                rij.cell("")
    pdf.ln(3)


def _opsomming(pdf: HuisstijlPDF, items: list) -> None:
    """Genummerde lijst; een tweede regel springt in onder de tekst."""
    pdf.set_font(LETTER_PDF, "", 10)
    pdf.set_text_color(*rgb(INKT))
    for i, item in enumerate(items, 1):
        pdf.ruimte_nodig(6)
        y = pdf.get_y()
        pdf.set_xy(pdf.MARGE, y)
        pdf.cell(7, 5.2, f"{i}.")
        pdf.set_xy(pdf.MARGE + 7, y)
        pdf.multi_cell(0, 5.2, str(item), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(0.6)
    pdf.ln(1.5)


def _bestandsnaam(t: Toolbox, ext: str) -> str:
    """toolbox-<onderwerp>-<datum>. Alleen ASCII: een header met een Ł of
    een aanhalingsteken laat de download stuklopen."""
    datum = (t.datum or datetime.now(timezone.utc)).date().isoformat()
    naam = f"toolbox-{t.onderwerp}-{datum}"
    naam = unicodedata.normalize("NFKD", naam).encode("ascii", "ignore").decode("ascii")
    naam = re.sub(r"-{2,}", "-", re.sub(r"[^A-Za-z0-9._-]", "-", naam)).strip("-")
    return f"{naam or 'toolbox'}.{ext}"


def _organisatie(db: Session, current_user: User):
    return db.query(Organization).filter(
        Organization.id == current_user.organization_id).first()


@router.get("/{toolbox_id}/export.pdf")
def export_toolbox_pdf(
    toolbox_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Toolbox plus ondertekende presentielijst als PDF.

    Dit is wat een opdrachtgever of de Arbeidsinspectie opvraagt: waar ging het
    over, wie was erbij, en heeft die persoon getekend.
    """
    t = _get_toolbox_or_404(db, toolbox_id, current_user)
    org = _organisatie(db, current_user)
    org_naam = org.name if org else "-"

    pdf = HuisstijlPDF(klant_van(org), _TITEL, ondertitel=t.onderwerp)
    pdf.add_page()
    pdf.titelblok()
    pdf.kv(_gegevens(t, org_naam), labelbreedte=40)

    if t.inleiding:
        pdf.sectie("Waar gaat het over")
        pdf.tekst(t.inleiding)
        pdf.ln(2)

    for titel, items in (("Risico's", _lijst(t.risicos)),
                         ("Maatregelen", _lijst(t.maatregelen)),
                         ("Besproken", _lijst(t.bespreekpunten))):
        if items:
            pdf.sectie(titel)
            _opsomming(pdf, items)

    if t.afspraken:
        pdf.sectie("Afspraken")
        pdf.tekst(t.afspraken)
        pdf.ln(2)

    deelnemers = list(t.deelnemers or [])
    pdf.sectie("Presentielijst")
    if not deelnemers:
        pdf.tekst("Er zijn geen deelnemers geregistreerd.")
    else:
        _presentielijst(pdf, deelnemers)

    t.pdf_generated_at = datetime.now(timezone.utc)
    db.commit()
    log_action(db, request, current_user, action="toolbox.export_pdf",
               entity_type="toolbox", entity_id=t.id,
               after={"deelnemers": len(deelnemers)})
    return pdf_antwoord(pdf.uitvoer(), _bestandsnaam(t, "pdf"))


@router.get("/{toolbox_id}/export.xlsx")
def export_toolbox_xlsx(
    toolbox_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """De toolbox als werkboek: de inhoud op het eerste tabblad, de
    presentielijst op het tweede. Handtekeningen staan alleen in de PDF;
    Excel zegt wanneer er getekend is."""
    t = _get_toolbox_or_404(db, toolbox_id, current_user)
    org = _organisatie(db, current_user)

    rijen = [[k, v] for k, v in _gegevens(t, org.name if org else "-")]
    if t.inleiding:
        rijen.append(["Waar gaat het over", t.inleiding])
    for label, items in (("Risico", _lijst(t.risicos)),
                         ("Maatregel", _lijst(t.maatregelen)),
                         ("Bespreekpunt", _lijst(t.bespreekpunten))):
        rijen += [[f"{label} {i}", item] for i, item in enumerate(items, 1)]
    if t.afspraken:
        rijen.append(["Afspraken", t.afspraken])

    deelnemers = list(t.deelnemers or [])
    bladen = [
        Blad("Samenvatting", KOLOMMEN_GEGEVENS, rijen),
        Blad("Aanwezigen", KOLOMMEN_AANWEZIGEN, [_aanwezigen_rij(d) for d in deelnemers]),
    ]
    bestand = excel_van(klant_van(org), _TITEL, bladen, ondertitel=t.onderwerp)
    log_action(db, request, current_user, action="toolbox.export_xlsx",
               entity_type="toolbox", entity_id=t.id,
               after={"deelnemers": len(deelnemers)})
    return excel_antwoord(bestand, _bestandsnaam(t, "xlsx"))
