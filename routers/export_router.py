"""Een tabel uit het portaal als PDF of Excel, in de huisstijl.

Sommige overzichten bestaan alleen in de browser: de gefilterde lijst in
Meldingen-rapportage, een beeldkwaliteitsschouw die nog niet is opgeslagen.
Die stuurt het portaal hierheen als kolommen en rijen; de server tekent er
hetzelfde briefhoofd, dezelfde kopregel en dezelfde voet omheen als bij de
rapporten die hij zelf maakt. Zo ziet elke export er hetzelfde uit, waar de
gegevens ook vandaan komen.

Het logo en de gegevens komen altijd van de organisatie van de gebruiker,
nooit uit het verzoek: niemand kan een document op andermans briefpapier
laten zetten.
"""
from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from audit import log_action
from auth import get_current_user
from database import get_db
from export_huisstijl import (
    SOORTEN,
    Blad,
    Kolom,
    bestandsnaam,
    excel_antwoord,
    excel_van,
    klant_van,
    pdf_antwoord,
    pdf_van,
)
from models import User

router = APIRouter(prefix="/api/export", tags=["Export"])

MAX_BLADEN = 6
MAX_KOLOMMEN = 40
MAX_RIJEN = 20_000
MAX_RIJEN_PDF = 3_000          # meer is geen leesbaar document meer
MAX_TEKST = 2_000


class KolomIn(BaseModel):
    naam: str = Field(..., min_length=1, max_length=80)
    soort: str = "tekst"
    decimalen: int = Field(2, ge=0, le=6)


class BladIn(BaseModel):
    naam: str = Field(..., min_length=1, max_length=80)
    kolommen: List[KolomIn] = Field(..., min_length=1, max_length=MAX_KOLOMMEN)
    rijen: List[List[Any]] = Field(default_factory=list)
    totaal: Optional[List[Any]] = None
    toelichting: List[str] = Field(default_factory=list, max_length=20)
    liggend: Optional[bool] = None


class TabelExportIn(BaseModel):
    titel: str = Field(..., min_length=1, max_length=120)
    ondertitel: str = Field("", max_length=200)
    bladen: List[BladIn] = Field(..., min_length=1, max_length=MAX_BLADEN)


def _waarde(v: Any) -> Any:
    """Alleen platte waarden; lange tekst wordt ingekort."""
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, (list, dict)):
        v = str(v)
    s = str(v)
    return s if len(s) <= MAX_TEKST else s[:MAX_TEKST - 1] + "…"


def _bladen(payload: TabelExportIn, *, voor_pdf: bool) -> list[Blad]:
    totaal_rijen = sum(len(b.rijen) for b in payload.bladen)
    grens = MAX_RIJEN_PDF if voor_pdf else MAX_RIJEN
    if totaal_rijen > grens:
        raise HTTPException(status_code=400, detail=(
            f"Te veel regels voor één {'PDF' if voor_pdf else 'Excel'} ({totaal_rijen}, "
            f"maximaal {grens}). Verklein de selectie."))
    bladen = []
    for b in payload.bladen:
        for k in b.kolommen:
            if k.soort not in SOORTEN:
                raise HTTPException(status_code=400, detail=f"Onbekende kolomsoort: {k.soort}")
        n = len(b.kolommen)
        for r in b.rijen + ([b.totaal] if b.totaal is not None else []):
            if len(r) != n:
                raise HTTPException(status_code=400, detail=(
                    f"Blad '{b.naam}': elke regel moet {n} waarden hebben"))
        bladen.append(Blad(
            naam=b.naam,
            kolommen=[Kolom(k.naam, k.soort, k.decimalen) for k in b.kolommen],
            rijen=[[_waarde(v) for v in r] for r in b.rijen],
            totaal=[_waarde(v) for v in b.totaal] if b.totaal is not None else None,
            toelichting=[_waarde(t) for t in b.toelichting],
            liggend=b.liggend,
        ))
    return bladen


def _log(db, request: Request, user: User, payload: TabelExportIn, formaat: str) -> None:
    log_action(db, request, user, action="export.tabel", entity_type="export", entity_id=None,
               extra={"titel": payload.titel, "formaat": formaat,
                      "regels": sum(len(b.rijen) for b in payload.bladen)})


@router.post("/tabel.xlsx")
def tabel_als_excel(payload: TabelExportIn, request: Request,
                    current_user: User = Depends(get_current_user), db=Depends(get_db)):
    """Kolommen en rijen uit het portaal als Excel in de huisstijl."""
    bladen = _bladen(payload, voor_pdf=False)
    inhoud = excel_van(klant_van(current_user.organization), payload.titel, bladen,
                       ondertitel=payload.ondertitel)
    _log(db, request, current_user, payload, "xlsx")
    return excel_antwoord(inhoud, bestandsnaam(payload.titel, ext="xlsx"))


@router.post("/tabel.pdf")
def tabel_als_pdf(payload: TabelExportIn, request: Request,
                  current_user: User = Depends(get_current_user), db=Depends(get_db)):
    """Dezelfde tabel als PDF: zelfde kolommen, zelfde opmaak."""
    bladen = _bladen(payload, voor_pdf=True)
    inhoud = pdf_van(klant_van(current_user.organization), payload.titel, bladen,
                     ondertitel=payload.ondertitel)
    _log(db, request, current_user, payload, "pdf")
    return pdf_antwoord(inhoud, bestandsnaam(payload.titel, ext="pdf"))
