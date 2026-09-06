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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import wpi_checklist as wc
from audit import log_action
from auth import get_current_user
from database import get_db
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


@router.get("/{wpi_id}/export.pdf")
def export_wpi_pdf(
    wpi_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rapport van de rondgang: score, de punten die niet in orde waren, en
    wie wat oplost."""
    try:
        from fpdf import FPDF
    except ImportError:
        return StreamingResponse(
            iter([b"PDF-generator niet geinstalleerd: pip install fpdf2"]),
            status_code=500, media_type="text/plain",
        )

    w = _get_wpi_or_404(db, wpi_id, current_user)
    org = db.query(Organization).filter(
        Organization.id == current_user.organization_id).first()
    org_naam = org.name if org else "-"

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

    class _WpiPDF(FPDF):
        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(120, 120, 120)
            self.set_x(15)
            self.cell(120, 5, safe(f"{org_naam} - werkplekinspectie"))
            self.cell(0, 5, f"Pagina {self.page_no()}/{{nb}}", align="R")
            self.set_text_color(0, 0, 0)

    pdf = _WpiPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.set_fill_color(*BRAND)
    pdf.rect(0, 0, 210, 34, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(15, 10)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 9, "Werkplekinspectie")
    pdf.set_xy(15, 20)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, safe(w.project.name if w.project else "-"))
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(42)

    def regel(label, waarde):
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_x(15)
        pdf.cell(38, 6, safe(label))
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, safe(waarde), new_x="LMARGIN", new_y="NEXT")

    antwoorden = list(w.antwoorden or [])
    telling = wc.bereken_score([{"antwoord": a.antwoord} for a in antwoorden])

    regel("Datum", w.datum.strftime("%d-%m-%Y") if w.datum else "-")
    regel("Locatie", w.locatie or "-")
    regel("Uitgevoerd door", w.inspecteur_naam or "-")
    regel("Status", w.status)
    regel("Vragenlijst", w.checklist_versie or "-")
    regel("Score", f"{w.score_pct}% in orde" if w.score_pct is not None else "nog niet afgerond")
    regel("Beoordeeld", f"{telling['beoordeeld']} van {telling['totaal']} "
                        f"({telling['nvt']} n.v.t.)")
    pdf.ln(3)

    if w.algemene_indruk:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_x(15)
        pdf.set_text_color(*BRAND)
        pdf.cell(0, 7, "Algemene indruk", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_x(15)
        pdf.multi_cell(180, 5, safe(w.algemene_indruk))
        pdf.ln(2)

    # Eerst wat niet in orde was -- dat is waar het rapport over gaat.
    niet_ok = [a for a in antwoorden if a.antwoord == "nee"]
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_x(15)
    pdf.set_text_color(*BRAND)
    pdf.cell(0, 7, safe(f"Niet in orde ({len(niet_ok)})"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    if not niet_ok:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_x(15)
        pdf.multi_cell(180, 5, "Geen bijzonderheden aangetroffen.")
    else:
        for a in niet_ok:
            if pdf.get_y() > 250:
                pdf.add_page()
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_x(15)
            pdf.multi_cell(180, 5, safe(f"{a.question_code} - {a.question_text_snapshot}"))
            pdf.set_font("Helvetica", "", 9)
            if a.toelichting:
                pdf.set_x(19)
                pdf.multi_cell(176, 5, safe(a.toelichting))
            if a.actie:
                pdf.set_x(19)
                pdf.multi_cell(176, 5, safe(
                    "Actie: " + a.actie
                    + (f" ({a.actiehouder_naam})" if a.actiehouder_naam else "")
                    + (" - gereed" if a.actie_gereed else " - open")))
            if a.photo_url:
                try:
                    if a.photo_url.startswith("data:image"):
                        raw = base64.b64decode(a.photo_url.split(",", 1)[1])
                        pdf.image(io.BytesIO(raw), x=19, w=50)
                except Exception:
                    pass  # een onleesbare foto mag het rapport niet slopen
            pdf.ln(2)

    # Daarna de volledige lijst, zodat zichtbaar is wat er gecontroleerd is.
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_x(15)
    pdf.set_text_color(*BRAND)
    pdf.cell(0, 7, "Volledige checklist", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    huidige_cat = None
    for a in antwoorden:
        if pdf.get_y() > 262:
            pdf.add_page()
        if a.categorie != huidige_cat:
            huidige_cat = a.categorie
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_x(15)
            pdf.cell(0, 6, safe(wc.CATEGORIEEN.get(huidige_cat, huidige_cat or "")),
                     new_x="LMARGIN", new_y="NEXT")
        label = {"ja": "in orde", "nee": "NIET IN ORDE", "nvt": "n.v.t."}.get(
            a.antwoord, "niet beantwoord")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_x(19)
        pdf.cell(24, 5, safe(label), border=0)
        pdf.multi_cell(152, 5, safe(a.question_text_snapshot))

    w.pdf_generated_at = datetime.now(timezone.utc)
    db.commit()
    log_action(db, request, current_user, action="wpi.export_pdf",
               entity_type="wpi", entity_id=w.id)

    out = bytes(pdf.output())
    datum = (w.datum or datetime.now(timezone.utc)).date().isoformat()
    fname = f"werkplekinspectie-{datum}.pdf"
    return StreamingResponse(
        iter([out]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
