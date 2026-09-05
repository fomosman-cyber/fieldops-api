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

Rolverdeling: opstellen, wijzigen en afsluiten is voor admin/manager — de
uitvoerder leidt de bespreking. Tekenen mag iedereen die is ingelogd, en
externen (onderaannemer, ZZP'er) staan als deelnemer op de lijst zonder dat ze
een account nodig hebben.
"""
import base64
import io
import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import toolbox_ai
from audit import log_action
from auth import get_current_user
from database import get_db
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


# ── PDF ──────────────────────────────────────────────────────────────

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
    try:
        from fpdf import FPDF
    except ImportError:
        return StreamingResponse(
            iter([b"PDF-generator niet geinstalleerd: pip install fpdf2"]),
            status_code=500, media_type="text/plain",
        )

    t = _get_toolbox_or_404(db, toolbox_id, current_user)
    org = db.query(Organization).filter(
        Organization.id == current_user.organization_id).first()
    org_naam = org.name if org else "-"

    # fpdf2 core-font Helvetica is latin-1. Claude schrijft en-dashes en
    # typografische aanhalingstekens, dus saneren is hier geen luxe.
    def safe(v) -> str:
        if v is None:
            return ""
        s = str(v)
        for k, r in (("€", "EUR "), ("–", "-"), ("—", "-"), ("•", "-"),
                     ("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                     ("…", "..."), ("→", "->"), ("×", "x"), ("·", "-"),
                     ("™", "(TM)")):
            s = s.replace(k, r)
        return s.encode("latin-1", "replace").decode("latin-1")

    def _hex_rgb(hexstr: str, default=(2, 132, 199)):
        try:
            h = (hexstr or "").lstrip("#")
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except Exception:
            return default

    BRAND = _hex_rgb(getattr(org, "brand_color", None) or "")

    class _ToolboxPDF(FPDF):
        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(120, 120, 120)
            self.set_x(15)
            self.cell(120, 5, safe(f"{org_naam} - toolbox"))
            self.cell(0, 5, f"Pagina {self.page_no()}/{{nb}}", align="R")
            self.set_text_color(0, 0, 0)

    pdf = _ToolboxPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.alias_nb_pages()
    pdf.add_page()

    # Kop
    pdf.set_fill_color(*BRAND)
    pdf.rect(0, 0, 210, 34, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(15, 10)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 9, "Toolbox")
    pdf.set_xy(15, 20)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, safe(t.onderwerp))
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(42)

    def regel(label, waarde):
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_x(15)
        pdf.cell(34, 6, safe(label))
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, safe(waarde), new_x="LMARGIN", new_y="NEXT")

    regel("Project", t.project.name if t.project else "-")
    regel("Datum", t.datum.strftime("%d-%m-%Y") if t.datum else "-")
    regel("Gehouden door", t.houder_naam or "-")
    regel("Organisatie", org_naam)
    regel("Status", t.status)
    if t.ai_gegenereerd:
        regel("Opgesteld met", f"AI-voorstel ({t.ai_model or 'onbekend model'}), nagelopen door de opsteller")
    pdf.ln(3)

    def kop(tekst):
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_x(15)
        pdf.set_text_color(*BRAND)
        pdf.cell(0, 7, safe(tekst), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    def alinea(tekst):
        pdf.set_font("Helvetica", "", 9)
        pdf.set_x(15)
        pdf.multi_cell(180, 5, safe(tekst))
        pdf.ln(1)

    def opsomming(items):
        pdf.set_font("Helvetica", "", 9)
        for i, item in enumerate(items, 1):
            pdf.set_x(15)
            pdf.multi_cell(180, 5, safe(f"{i}.  {item}"))
        pdf.ln(1)

    if t.inleiding:
        kop("Waar gaat het over")
        alinea(t.inleiding)

    risicos = _lijst(t.risicos)
    if risicos:
        kop("Risico's")
        opsomming(risicos)

    maatregelen = _lijst(t.maatregelen)
    if maatregelen:
        kop("Maatregelen")
        opsomming(maatregelen)

    punten = _lijst(t.bespreekpunten)
    if punten:
        kop("Besproken")
        opsomming(punten)

    if t.afspraken:
        kop("Afspraken")
        alinea(t.afspraken)

    # Presentielijst
    pdf.ln(2)
    kop("Presentielijst")

    deelnemers = list(t.deelnemers or [])
    if not deelnemers:
        alinea("Er zijn geen deelnemers geregistreerd.")
    else:
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(240, 243, 247)
        pdf.set_x(15)
        pdf.cell(62, 7, "Naam", border=1, fill=True)
        pdf.cell(48, 7, "Bedrijf", border=1, fill=True)
        pdf.cell(24, 7, "Aanwezig", border=1, fill=True)
        pdf.cell(46, 7, "Handtekening", border=1, fill=True, new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "", 8)
        for d in deelnemers:
            hoogte = 14
            if pdf.get_y() + hoogte > 265:
                pdf.add_page()
            y = pdf.get_y()
            pdf.set_x(15)
            pdf.cell(62, hoogte, safe(d.naam), border=1)
            pdf.cell(48, hoogte, safe(d.bedrijf or ("-" if d.user_id else "extern")), border=1)
            pdf.cell(24, hoogte, "ja" if d.aanwezig else "nee", border=1, align="C")
            pdf.cell(46, hoogte, "", border=1, new_x="LMARGIN", new_y="NEXT")

            if d.signature_data_url:
                try:
                    raw = base64.b64decode(d.signature_data_url.split(",", 1)[1])
                    pdf.image(io.BytesIO(raw), x=136, y=y + 1, h=hoogte - 2)
                except Exception:
                    # Een onleesbare handtekening mag het document niet slopen.
                    pdf.set_xy(136, y + 4)
                    pdf.set_font("Helvetica", "I", 7)
                    pdf.cell(44, 6, "getekend", align="C")
                    pdf.set_font("Helvetica", "", 8)
            pdf.set_y(y + hoogte)

    t.pdf_generated_at = datetime.now(timezone.utc)
    db.commit()
    log_action(db, request, current_user, action="toolbox.export_pdf",
               entity_type="toolbox", entity_id=t.id,
               after={"deelnemers": len(deelnemers)})

    out = bytes(pdf.output())
    datum = (t.datum or datetime.now(timezone.utc)).date().isoformat()
    fname = f"toolbox-{t.onderwerp}-{datum}.pdf".replace(" ", "-")
    fname = fname.encode("ascii", "ignore").decode("ascii") or "toolbox.pdf"
    return StreamingResponse(
        iter([out]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
