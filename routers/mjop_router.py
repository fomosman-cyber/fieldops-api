"""Meerjaren Onderhoudsplan (MJOP) — export voor directie + Rekenkamer.

Endpoints:
  GET /api/mjop/preview                  Preview MJOP-data (JSON)
  GET /api/mjop/export.csv               Excel-import vriendelijk CSV
  GET /api/mjop/export.pdf               CROW + NEN conform PDF-rapport
  GET /api/mjop/export.xlsx              Zelfde tabellen als Excel-werkboek
  GET /api/mjop/summary                  Aggregaten per jaar + asset-type

Query-params:
  years        Aantal jaren in horizon (default 10, max 25)
  project_id   Filter op project (optioneel)
  asset_type   Filter op asset-type (optioneel)
  include_score_2  Ook score 2 (preventief) meenemen — default False

Bron: NEN 2767-2 + CROW 134 + CROW 145 + RAW-indexen + GWW-kostengids.
Multi-tenant: alle queries gefilterd op organization_id.
RBAC: alle authenticated users mogen lezen (read-only export).

LET OP: Indicatieve kostenranges. Voor exacte ramingen heb je RAW-besteks-
documenten per project nodig. MJOP is een meerjaren-overzicht voor
begrotings-onderbouwing.
"""
from __future__ import annotations
import csv
import io
import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from models import User, Asset, Project, Organization, Melding
from auth import get_current_user
from permissions import require_module

import mjop_kosten as mjop
import inspection_cycle as cycle
import mjop_rapport as rapport
from collections import Counter

router = APIRouter(prefix="/api/mjop", tags=["MJOP"],
                   dependencies=[Depends(require_module("kunstwerken"))])


# ─────────────────────────────────────────────────────────────────────────────
# Core builder
# ─────────────────────────────────────────────────────────────────────────────

def _index_pct(db: Session, organization_id: str) -> Optional[float]:
    """Het indexpercentage van deze organisatie, of None als het niet is gezet.

    Nul telt als "bewust niet indexeren" en niet als "niet ingevuld": wie
    expliciet 0% invult zegt dat hij op prijspeil wil rekenen, en dat is een
    andere uitspraak dan een leeg veld.
    """
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    return org.mjop_index_pct if org else None


def _build_mjop_rows(db: Session, *, organization_id: str,
                     years: int = 10,
                     project_id: Optional[str] = None,
                     asset_type: Optional[str] = None,
                     include_score_2: bool = False,
                     index_pct: Optional[float] = None) -> list[dict]:
    """Maak MJOP-regels voor alle relevante assets.

    Logica:
      1. Pak alle assets met condition_score in deze org
      2. Filter op actionable score (3+, of 2+ als include_score_2)
      3. Bepaal jaar van uitvoering op basis van next_inspection_due of
         expected_lifespan_years
      4. Bereken kosten via mjop_kosten
      5. Sorteer op jaar → asset_type → asset.code
    """
    q = db.query(Asset).filter(
        Asset.organization_id == organization_id,
        Asset.archived_at.is_(None),
        Asset.condition_score.isnot(None),
    )
    if project_id:
        q = q.filter(Asset.project_id == project_id)
    if asset_type:
        q = q.filter(Asset.asset_type == asset_type)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    horizon_end = now + timedelta(days=365 * years)

    rows = []
    threshold = 2 if include_score_2 else 3
    for a in q.all():
        if a.condition_score is None or a.condition_score < threshold:
            continue

        maatregel = mjop.get_maatregel(a.asset_type, a.condition_score)
        if not maatregel:
            continue

        # Bepaal uitvoering-jaar — eerst next_inspection_due, anders projectie
        # uit expected_lifespan_years, anders direct
        if a.next_inspection_due:
            due = a.next_inspection_due
            if due.tzinfo:
                due = due.replace(tzinfo=None)
        else:
            # Fallback: direct uitvoeren in lopende jaar voor score 4+
            due = now if a.condition_score >= 4 else now + timedelta(days=365)

        if due > horizon_end:
            continue  # buiten horizon

        # Bepaal multiplier voor unit-based kosten (per m, per m2)
        multiplier = 1.0
        unit = maatregel.get("unit", "per object")
        if unit == "per m" and a.length_m:
            multiplier = float(a.length_m)
        elif unit == "per m2":
            # Een gemeten oppervlakte gaat voor. Zonder die maat viel de MJOP
            # terug op length_m × 5 m — een aangenomen rijbaanbreedte, en nul
            # zodra een asset ook geen lengte heeft. Assets die uit een schouw
            # komen dragen hun oppervlakte in properties_json; die is gemeten en
            # dus altijd te verkiezen boven de vuistregel.
            multiplier = float(_oppervlakte_uit_properties(a) or 0)
            if not multiplier:
                multiplier = float(a.length_m or 0) * 5

        total = mjop.estimate_total(maatregel, multiplier=multiplier if multiplier else 1.0)

        rows.append({
            "year": due.year,
            "month": due.month,
            "asset_id": a.id,
            "asset_code": a.code,
            "asset_name": a.name,
            "asset_type": a.asset_type,
            "project_id": a.project_id,
            "condition_score": a.condition_score,
            "norm_reference": cycle.norm_reference(a.asset_type),
            "maatregel": maatregel["maatregel"],
            "unit": unit,
            "multiplier": multiplier,
            "min_eur": maatregel["min_eur"],
            "max_eur": maatregel["max_eur"],
            "min_total": total["min_total"],
            "max_total": total["max_total"],
            # Wat het naar verwachting kost in het jaar dat het gebeurt. None
            # zolang er geen indexpercentage is ingesteld -- dan staan alleen
            # de prijspeil-bedragen erboven, en zeggen de exports dat erbij.
            "min_geindexeerd": mjop.indexeer(total["min_total"], due.year, index_pct),
            "max_geindexeerd": mjop.indexeer(total["max_total"], due.year, index_pct),
            "due_date": due.date().isoformat(),
        })

    # Sorteer op (jaar, asset_type, code)
    rows.sort(key=lambda r: (r["year"], r["month"], r["asset_type"], r["asset_code"]))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

def _oppervlakte_uit_properties(asset) -> float | None:
    """Gemeten oppervlakte van een asset, als die er is.

    `properties_json` is een vrije JSON-string per asset-type. Assets die uit
    een schouw of import komen zetten daar `oppervlakte_m2` in; zonder die maat
    moet de MJOP terugvallen op een aangenomen breedte.
    """
    if not asset.properties_json:
        return None
    try:
        props = json.loads(asset.properties_json)
    except (ValueError, TypeError):
        return None
    if not isinstance(props, dict):
        return None
    waarde = props.get("oppervlakte_m2")
    try:
        return float(waarde) if waarde is not None else None
    except (TypeError, ValueError):
        return None


@router.get("/preview")
def preview_mjop(
    years: int = Query(10, ge=1, le=25),
    project_id: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    include_score_2: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Preview MJOP-data als JSON (max 200 regels)."""
    index_pct = _index_pct(db, current_user.organization_id)
    rows = _build_mjop_rows(
        db,
        organization_id=current_user.organization_id,
        years=years, project_id=project_id, asset_type=asset_type,
        include_score_2=include_score_2, index_pct=index_pct,
    )
    return {
        "count": len(rows),
        "items": rows[:500],   # cap voor preview
        "horizon_years": years,
        "kosten_version": mjop.KOSTEN_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Indicatieve kostenranges — geen RAW-bestek. Voor onderbouwing aanbesteding gebruik je een eigen kosten-raming per project.",
    }


@router.get("/summary")
def mjop_summary(
    years: int = Query(10, ge=1, le=25),
    project_id: Optional[str] = Query(None),
    include_score_2: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aggregaten per jaar + per asset-type — voor directie-grafiek.

    Returns:
      summary_by_year[year]            = {min_total, max_total, count}
      summary_by_type[asset_type]      = {min_total, max_total, count}
      grand_total                      = {min, max}
    """
    index_pct = _index_pct(db, current_user.organization_id)
    rows = _build_mjop_rows(
        db,
        organization_id=current_user.organization_id,
        years=years, project_id=project_id,
        include_score_2=include_score_2, index_pct=index_pct,
    )

    by_year: dict = {}
    by_type: dict = {}
    grand_min = 0
    grand_max = 0
    grand_min_idx = 0
    grand_max_idx = 0

    for r in rows:
        y = r["year"]
        t = r["asset_type"]
        by_year.setdefault(y, {"year": y, "min_total": 0, "max_total": 0,
                                "min_geindexeerd": 0, "max_geindexeerd": 0,
                                "count": 0, "by_type": {}})
        by_type.setdefault(t, {"asset_type": t, "min_total": 0, "max_total": 0,
                                "count": 0, "norm_reference": r["norm_reference"]})

        by_year[y]["min_total"] += r["min_total"]
        by_year[y]["max_total"] += r["max_total"]
        by_year[y]["min_geindexeerd"] += (r["min_geindexeerd"] or 0)
        by_year[y]["max_geindexeerd"] += (r["max_geindexeerd"] or 0)
        by_year[y]["count"] += 1
        by_year[y]["by_type"].setdefault(t, 0)
        by_year[y]["by_type"][t] += r["min_total"]

        by_type[t]["min_total"] += r["min_total"]
        by_type[t]["max_total"] += r["max_total"]
        by_type[t]["count"] += 1

        grand_min += r["min_total"]
        grand_max += r["max_total"]
        grand_min_idx += (r["min_geindexeerd"] or 0)
        grand_max_idx += (r["max_geindexeerd"] or 0)

    return {
        "horizon_years": years,
        "by_year": sorted(by_year.values(), key=lambda x: x["year"]),
        "by_type": sorted(by_type.values(), key=lambda x: -x["max_total"]),
        "grand_total": {"min": grand_min, "max": grand_max},
        # Wat het kost in de jaren dat het gebeurt. Zonder ingesteld
        # indexpercentage None -- dan is er niets doorgerekend, en dat hoort
        # zichtbaar te zijn in plaats van gelijk aan het prijspeil-bedrag.
        "grand_total_geindexeerd": (
            {"min": grand_min_idx, "max": grand_max_idx}
            if index_pct is not None else None),
        "index_pct": index_pct,
        "prijspeil_jaar": mjop.PRIJSPEIL_JAAR,
        "index_toelichting": mjop.index_toelichting(index_pct),
        "total_assets": len(rows),
        "kosten_version": mjop.KOSTEN_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/export.csv")
def export_mjop_csv(
    years: int = Query(10, ge=1, le=25),
    project_id: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    include_score_2: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Excel-import vriendelijk CSV met alle MJOP-regels.

    Gebruikt `;` als delimiter (NL Excel-default) en BOM voor UTF-8.
    """
    index_pct = _index_pct(db, current_user.organization_id)
    rows = _build_mjop_rows(
        db,
        organization_id=current_user.organization_id,
        years=years, project_id=project_id, asset_type=asset_type,
        include_score_2=include_score_2, index_pct=index_pct,
    )

    buf = io.StringIO()
    buf.write("﻿")  # BOM voor Excel UTF-8 herkenning
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "Jaar", "Maand", "Asset code", "Asset naam", "Type", "Norm",
        "Conditie", "Maatregel", "Eenheid", "Hoeveelheid",
        "Min € per eenheid", "Max € per eenheid",
        f"Min € totaal (prijspeil {mjop.PRIJSPEIL_JAAR})",
        f"Max € totaal (prijspeil {mjop.PRIJSPEIL_JAAR})",
        "Min € geïndexeerd", "Max € geïndexeerd", "Geplande datum",
    ])
    for r in rows:
        writer.writerow([
            r["year"], r["month"], r["asset_code"], r["asset_name"] or "",
            r["asset_type"], r["norm_reference"],
            r["condition_score"], r["maatregel"],
            r["unit"], r["multiplier"],
            r["min_eur"], r["max_eur"],
            r["min_total"], r["max_total"],
            r["min_geindexeerd"] if r["min_geindexeerd"] is not None else "",
            r["max_geindexeerd"] if r["max_geindexeerd"] is not None else "",
            r["due_date"],
        ])
    # Voettekst — meta
    writer.writerow([])
    writer.writerow([f"MJOP gegenereerd op {datetime.now(timezone.utc).date().isoformat()}"])
    writer.writerow([f"Versie kosten-katalogus: {mjop.KOSTEN_VERSION}"])
    writer.writerow([mjop.index_toelichting(index_pct)])
    writer.writerow(["Bronnen: NEN 2767-2 + CROW 134 + CROW 145 + GWW-kostengids 2024"])
    writer.writerow(["LET OP: indicatieve kostenranges, geen RAW-bestek"])

    buf.seek(0)
    today = datetime.now(timezone.utc).date().isoformat()
    filename = f"mjop-{today}.csv"
    return StreamingResponse(
        iter([buf.read()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# PDF- en Excel-export — in de FieldOps-huisstijl, met logo en gegevens van
# de klant (export_huisstijl.py)
# ─────────────────────────────────────────────────────────────────────────────
#
# Het rapport en het werkboek komen uit dezelfde gegevens (`_rapport_gegevens`)
# en dezelfde kolommen (`_kolommen` -> `_bladen`): een tabel in de PDF is een
# tabblad in Excel, met dezelfde kolomnamen in dezelfde volgorde. Wie de PDF en
# de Excel naast elkaar legt, leest twee keer hetzelfde.

_TITEL = "Meerjaren Onderhoudsplan"

_COND_LABELS = {1: "1 – Uitstekend", 2: "2 – Goed", 3: "3 – Redelijk",
                4: "4 – Matig", 5: "5 – Slecht", 6: "6 – Zeer slecht"}
_MAATREGEL_PER_SCORE = {1: "Geen actie", 2: "Preventief", 3: "Klein onderhoud",
                        4: "Groot onderhoud", 5: "Vervanging", 6: "Acute vervanging"}

_INHOUD = (("", "Managementsamenvatting"), ("1.", "Methodologie"),
           ("2.", "Asset-overzicht"), ("3.", "Meldingen-context"),
           ("4.", "Kosten per jaar"), ("5.", "Volledige MJOP-tabel"),
           ("6.", "Voorbeeld-berekening"), ("7.", "Bronnen en disclaimers"))

_BRON_NORMEN = (
    "NEN 2767-2 (2017): Conditiemeting infrastructuur. Bepaalt de 6-puntsschaal "
    "voor de assets (1 = uitstekend, 6 = zeer slecht).",
    "CROW 134: Inspectie van vaste/beweegbare bruggen + viaducten.",
    "CROW 145: Inspectie wegmarkering (retroreflectie-meting).",
    "NEN 3140: Periodieke inspectie elektrische installaties (verlichting).",
    "NEN-EN 1176: Veiligheid speeltoestellen (Warenwetbesluit).",
    "NEN 3399: Visuele inspectie van vrijvervalriool (camera).",
    "VTA (Mattheck): Visual Tree Assessment voor bomen.",
    "GWW-kostengids 2024: prijsbasis per maatregel.",
)

_BRONNEN = (
    "NEN 2767-2 (2017): Conditiemeting infrastructuur – 6-puntsschaal",
    "NEN 2767-4: Terreinen en buitenruimten (fontein, kunstgrasveld)",
    "CROW 134 (2010): Inspectie vaste/beweegbare bruggen",
    "CROW 145: Inspectie en onderhoud wegmarkering",
    "CROW 146a/b: Visuele inspectie verhardingen (asfalt/elementen)",
    "NEN 3140: Bedrijfsvoering elektrische installaties",
    "NEN-EN 1176: Veiligheid speeltoestellen",
    "NEN 3399: Visuele inspectie vrijvervalriool",
    "VTA (Mattheck): Visual Tree Assessment voor bomen",
    "GWW-kostengids 2024 (CROW): prijsbasis maatregelen",
    "RWS Areaalrapportage Kunstwerken (KW-elementenlijst)",
)

_DISCLAIMER = (
    "Dit MJOP-rapport bevat indicatieve kostenramingen op basis van "
    "norm-conforme cyclus-prognoses en gepubliceerde eenheidsprijzen. "
    "Het is bedoeld voor begrotings- en directie-doeleinden (BBV-conform "
    "voor gemeenten, Rekenkamer-onderbouwing voor publieke organisaties).",
    "Voor onderbouwing van aanbestedingen, RAW-bestek of contractuele "
    "verplichtingen is een aparte project-specifieke kostenraming vereist "
    "die rekening houdt met locatie-specifieke uitvoeringsomstandigheden, "
    "marktprijzen op aanbestedingsdatum en eventuele bijkomende werken.",
)


@dataclass
class _Rapport:
    """Alles wat PDF en Excel van één MJOP-export nodig hebben."""
    org: Optional[Organization]
    org_naam: str
    project_naam: Optional[str]
    years: int
    include_score_2: bool
    index_pct: Optional[float]
    rows: list
    all_assets: list
    asset_type_counts: Counter
    cond_counts: Counter
    assets_zonder_score: int
    open_meldingen: list
    meld_per_type: Counter
    meld_per_asset: Counter

    @property
    def met_index(self) -> bool:
        return self.index_pct is not None

    @property
    def scope_naam(self) -> str:
        return self.project_naam or f"{self.org_naam} (organisatie-breed)"

    @property
    def ondertitel(self) -> str:
        return f"{self.org_naam} – {self.project_naam or 'Organisatie-breed'}"


def _rapport_gegevens(db: Session, current_user: User, *, years: int,
                      project_id: Optional[str], asset_type: Optional[str],
                      include_score_2: bool) -> _Rapport:
    """Verzamel de MJOP-regels plus de context voor het rapport. Alles gefilterd
    op de organisatie van de ingelogde gebruiker; een project-id van een andere
    organisatie levert dus geen naam en geen regels op."""
    org_id = current_user.organization_id
    index_pct = _index_pct(db, org_id)
    rows = _build_mjop_rows(
        db, organization_id=org_id,
        years=years, project_id=project_id, asset_type=asset_type,
        include_score_2=include_score_2, index_pct=index_pct,
    )

    project_naam = None
    if project_id:
        p = db.query(Project).filter(
            Project.id == project_id,
            Project.organization_id == org_id,
        ).first()
        if p:
            project_naam = p.name
    org = db.query(Organization).filter(Organization.id == org_id).first()

    # Asset-overzicht binnen organisatie (en project-filter indien gezet)
    asset_q = db.query(Asset).filter(
        Asset.organization_id == org_id,
        Asset.archived_at.is_(None),
    )
    if project_id:
        asset_q = asset_q.filter(Asset.project_id == project_id)
    if asset_type:
        asset_q = asset_q.filter(Asset.asset_type == asset_type)
    all_assets = asset_q.all()

    # Open meldingen voor context
    meld_q = db.query(Melding).filter(
        Melding.organization_id == org_id,
        Melding.status.in_(("open", "in_behandeling", "in_uitvoering", "nieuw")),
    )
    if project_id:
        meld_q = meld_q.filter(Melding.project_id == project_id)
    open_meldingen = meld_q.all()

    return _Rapport(
        org=org,
        org_naam=(org.name if org else None) or "—",
        project_naam=project_naam,
        years=years,
        include_score_2=include_score_2,
        index_pct=index_pct,
        rows=rows,
        all_assets=all_assets,
        asset_type_counts=Counter(a.asset_type for a in all_assets if a.asset_type),
        cond_counts=Counter(a.condition_score for a in all_assets if a.condition_score is not None),
        assets_zonder_score=sum(1 for a in all_assets if a.condition_score is None),
        open_meldingen=open_meldingen,
        meld_per_type=Counter(m.category for m in open_meldingen if m.category),
        meld_per_asset=Counter(m.asset_id for m in open_meldingen if m.asset_id),
    )


def _kolommen(met_index: bool) -> dict:
    """De kolommen van elke tabel, één keer gedefinieerd voor PDF én Excel."""
    from export_huisstijl import Kolom

    pp = mjop.PRIJSPEIL_JAAR
    def geld(naam):                 # hele euro's, zoals kengetallen en samenvatting
        return Kolom(naam, "geld", decimalen=0)

    geindexeerd = [geld("Min geïndexeerd"), geld("Max geïndexeerd")] if met_index else []
    return {
        "per_jaar": [
            Kolom("Jaar", "jaar"), Kolom("Regels", "heel"),
            geld(f"Min totaal (prijspeil {pp})"),
            geld(f"Max totaal (prijspeil {pp})"),
            *geindexeerd,
        ],
        "tabel": [
            Kolom("Jaar", "jaar"), Kolom("Geplande datum", "datum"),
            Kolom("Asset-code"), Kolom("Asset-naam"), Kolom("Type"), Kolom("Norm"),
            Kolom("Conditie", "heel"), Kolom("Maatregel"), Kolom("Eenheid"),
            Kolom("Hoeveelheid", "getal", 1),
            geld("Min per eenheid"), geld("Max per eenheid"),
            geld(f"Min totaal (prijspeil {pp})"),
            geld(f"Max totaal (prijspeil {pp})"),
            *geindexeerd,
        ],
        "types": [Kolom("Type"), Kolom("Aantal", "heel"), Kolom("% van totaal", "getal", 1)],
        "conditie": [Kolom("Conditie-score"), Kolom("Aantal", "heel"), Kolom("Maatregel")],
        "categorieen": [Kolom("Categorie"), Kolom("Aantal", "heel")],
        "meldingen_assets": [Kolom("Asset-code"), Kolom("Type"),
                             Kolom("Aantal meldingen", "heel"), Kolom("Conditie", "heel")],
    }


def _bladen(r: _Rapport) -> dict:
    """De tabellen van het rapport als `Blad`: in de PDF een tabel, in Excel
    een tabblad. Volgorde = volgorde van de tabbladen."""
    from export_huisstijl import Blad

    k = _kolommen(r.met_index)

    def idx(*waarden):
        return list(waarden) if r.met_index else []

    # ── Kosten per jaar (hoofdstuk 4)
    per_jaar: dict[int, dict] = {}
    for row in r.rows:
        d = per_jaar.setdefault(row["year"], {"n": 0, "min": 0, "max": 0,
                                              "min_idx": 0, "max_idx": 0})
        d["n"] += 1
        d["min"] += row["min_total"]
        d["max"] += row["max_total"]
        d["min_idx"] += row["min_geindexeerd"] or 0
        d["max_idx"] += row["max_geindexeerd"] or 0
    tot_min = sum(row["min_total"] for row in r.rows)
    tot_max = sum(row["max_total"] for row in r.rows)
    tot_min_idx = sum((row["min_geindexeerd"] or 0) for row in r.rows)
    tot_max_idx = sum((row["max_geindexeerd"] or 0) for row in r.rows)

    voetnoot = [
        mjop.index_toelichting(r.index_pct),
        f"Versie kosten-katalogus: {mjop.KOSTEN_VERSION}",
        "Bronnen: NEN 2767-2 + CROW 134 + CROW 145 + GWW-kostengids 2024",
        "LET OP: indicatieve kostenranges, geen RAW-bestek. " + _DISCLAIMER[1],
    ]

    kosten_per_jaar = Blad(
        "Kosten per jaar", k["per_jaar"],
        [[jaar, d["n"], d["min"], d["max"], *idx(d["min_idx"], d["max_idx"])]
         for jaar, d in sorted(per_jaar.items())],
        totaal=(["Totaal", len(r.rows), tot_min, tot_max, *idx(tot_min_idx, tot_max_idx)]
                if r.rows else None),
        toelichting=[_horizon_regel(r)] + voetnoot,
    )

    # ── Volledige MJOP-tabel (hoofdstuk 5)
    mjop_tabel = Blad(
        "MJOP-tabel", k["tabel"],
        [[row["year"], row["due_date"], row["asset_code"] or "", row["asset_name"] or "",
          row["asset_type"] or "", row["norm_reference"] or "", row["condition_score"],
          row["maatregel"], row["unit"], row["multiplier"],
          row["min_eur"], row["max_eur"], row["min_total"], row["max_total"],
          *idx(row["min_geindexeerd"], row["max_geindexeerd"])]
         for row in r.rows],
        totaal=(["Totaal", "", "", "", "", "", None, "", "", None, None, None,
                 tot_min, tot_max, *idx(tot_min_idx, tot_max_idx)]
                if r.rows else None),
        toelichting=["Hoeveelheid = lengte (m) of oppervlakte (m²) van het asset; "
                     "1 bij prijzen per object."] + voetnoot,
    )

    # ── Asset-overzicht (hoofdstuk 2)
    totaal_getypeerd = sum(r.asset_type_counts.values()) or 1
    types = Blad(
        "Assets per type", k["types"],
        [[atype, cnt, cnt / totaal_getypeerd * 100]
         for atype, cnt in r.asset_type_counts.most_common()],
        toelichting=[f"Assets in scope: {len(r.all_assets)} · met conditie-score: "
                     f"{len(r.all_assets) - r.assets_zonder_score} · zonder score "
                     f"(geen MJOP-regel): {r.assets_zonder_score}"],
    )
    conditie_rijen = [[_COND_LABELS.get(s, f"Score {s}"), r.cond_counts[s],
                       _MAATREGEL_PER_SCORE.get(s, "-")] for s in sorted(r.cond_counts)]
    if r.assets_zonder_score:
        conditie_rijen.append(["Geen conditie-score", r.assets_zonder_score, "Vraagt inspectie"])
    conditie = Blad("Conditie-verdeling", k["conditie"], conditie_rijen,
                    toelichting=["Conditie volgens NEN 2767-2: 1 = uitstekend, 6 = zeer slecht."])

    # ── Meldingen-context (hoofdstuk 3)
    categorieen = Blad(
        "Meldingen per categorie", k["categorieen"],
        [[cat, cnt] for cat, cnt in r.meld_per_type.most_common(10)],
        toelichting=[f"Totaal aantal open/actieve meldingen: {len(r.open_meldingen)} "
                     f"(top 10 categorieën)."],
    )
    asset_lookup = {a.id: a for a in r.all_assets}
    meldingen_assets = Blad(
        "Assets met meldingen", k["meldingen_assets"],
        [[a.code or "-", a.asset_type or "-", cnt, a.condition_score]
         for aid, cnt in r.meld_per_asset.most_common(15)
         if (a := asset_lookup.get(aid)) is not None],
        toelichting=["Top 15 assets met de meeste open meldingen."],
    )
    return {"per_jaar": kosten_per_jaar, "tabel": mjop_tabel, "types": types,
            "conditie": conditie, "categorieen": categorieen,
            "meldingen_assets": meldingen_assets}


def _horizon_regel(r: _Rapport) -> str:
    return (f"Horizon: {r.years} jaar – alleen actionable scores (3+)"
            + (" incl. preventief (score 2)" if r.include_score_2 else ""))


def _eur(bedrag) -> str:
    from export_huisstijl import nl_getal
    return "€ " + nl_getal(float(bedrag or 0), 0)


def _aantal(waarde) -> str:
    from export_huisstijl import nl_getal
    try:
        v = float(waarde)
        heel = v == int(v)
    except (TypeError, ValueError, OverflowError):
        return str(waarde or "-")
    return nl_getal(v, 0 if heel else 2)


def _euroteken(tekst: str) -> str:
    """mjop_rapport schrijft 'EUR 12.345' (uit de tijd dat de PDF-letter geen
    euroteken had); in de huisstijl staat overal '€ 12.345'."""
    return tekst.replace("EUR ", "€ ")


def _bestandsnaam(project_naam: Optional[str], ext: str) -> str:
    """mjop-<project>-<datum>.<ext>, zoals de export altijd heette. Alleen
    ASCII, want een header kan geen willekeurige tekens dragen."""
    today = datetime.now(timezone.utc).date().isoformat()
    slug = unicodedata.normalize("NFKD", project_naam or "all")
    slug = slug.encode("ascii", "ignore").decode("ascii").replace(" ", "-").lower()
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in slug)[:30].strip("-") or "all"
    return f"mjop-{slug}-{today}.{ext}"


# ── bouwstenen voor het rapport, bovenop HuisstijlPDF ───────────────────────

def _hoofdstuk(pdf, titel: str, *, ruimte: float) -> None:
    """Hoofdstukkop die meteen door een subkop of opsomming gevolgd wordt.
    sectie() houdt 36 mm vrij, subsectie() daarna nog eens 30: zonder deze
    extra ruimte bleef de hoofdstukkop soms alleen onderaan staan."""
    pdf.ruimte_nodig(ruimte)
    pdf.sectie(titel)


def _alinea(pdf, tekst: str, **kw) -> None:
    pdf.tekst(tekst, **kw)
    pdf.ln(2)


def _opsomming(pdf, punten, *, niveau: int = 0) -> None:
    """Opsomming met hangende inspringing. Een punt is tekst, of (tekst,
    [subpunten])."""
    from export_huisstijl import INKT, LETTER_PDF, rgb

    for punt in punten:
        tekst, sub = punt if isinstance(punt, tuple) else (punt, ())
        pdf.ruimte_nodig(6)
        pdf.set_font(LETTER_PDF, "", 10)
        pdf.set_text_color(*rgb(INKT))
        pdf.set_x(pdf.MARGE + 1.5 + niveau * 6)
        pdf.cell(4.5, 5, "•" if niveau == 0 else "–")
        pdf.multi_cell(0, 5, tekst, new_x="LMARGIN", new_y="NEXT")
        if sub:
            _opsomming(pdf, sub, niveau=niveau + 1)
    if niveau == 0:
        pdf.ln(2)


def _formule(pdf, regels) -> None:
    """Rekenregels in een grijs vlak, in een letter met vaste breedte."""
    from export_huisstijl import INKT, LIJN, VLAK, rgb

    h = 4.8 * len(regels) + 4
    pdf.ruimte_nodig(h + 4)
    y = pdf.get_y() + 0.5
    pdf.set_fill_color(*rgb(VLAK))
    pdf.set_draw_color(*rgb(LIJN))
    pdf.set_line_width(0.25)
    pdf.rect(pdf.MARGE, y, pdf.w - 2 * pdf.MARGE, h, "DF")
    pdf.set_font("Courier", "", 9)
    pdf.set_text_color(*rgb(INKT))
    for i, regel in enumerate(regels):
        pdf.set_xy(pdf.MARGE + 4, y + 2 + i * 4.8)
        pdf.cell(pdf.w - 2 * pdf.MARGE - 8, 4.8, regel)
    pdf.set_xy(pdf.MARGE, y + h + 3)


def _mjop_pdf(r: _Rapport, bladen: dict) -> bytes:
    from export_huisstijl import (GRIJS, HuisstijlPDF, INKT, LETTER_PDF, Kolom, als_tekst,
                                  klant_van, rgb)

    rows = r.rows
    pdf = HuisstijlPDF(klant_van(r.org), _TITEL, ondertitel=r.ondertitel)

    # ═════════════════════════════════════════════════════════════════
    # PAGINA 1 — TITEL, KERNCIJFERS, INHOUD
    # ═════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titelblok()
    pdf.kv([
        ("Organisatie", r.org_naam),
        ("Project", r.project_naam or "Alle projecten (organisatie-breed)"),
        ("Horizon", f"{r.years} jaar"),
        ("Inclusief score 2", "ja (preventief)" if r.include_score_2 else "nee (alleen actionable)"),
        ("Kosten-versie", mjop.KOSTEN_VERSION),
        ("Prijspeil", str(mjop.PRIJSPEIL_JAAR)),
        ("Indexatie", f"{r.index_pct:.1f}% per jaar" if r.met_index else "niet ingesteld"),
    ])

    # De belangrijkste regel van dit rapport. Iemand neemt deze bedragen over in
    # een begroting; dan moet er onder staan of ze in euro's van nu zijn of van
    # het jaar van uitvoering. Zonder die zin is een bedrag van 2034 niet te
    # onderscheiden van een bedrag van vandaag.
    _alinea(pdf, mjop.index_toelichting(r.index_pct), grootte=9, kleur=GRIJS, stijl="I", regel=4.6)
    pdf.ln(2)

    # Het derde vakje is het getal dat in een begroting belandt. Staat er een
    # index, dan hoort daar het bedrag van het jaar van uitvoering -- anders
    # leest een directeur een som van 2034-werk in euro's van 2025.
    total_min = sum(x["min_total"] for x in rows)
    total_max = sum(x["max_total"] for x in rows)
    total_max_idx = sum((x["max_geindexeerd"] or 0) for x in rows)
    kpi_bedrag = total_max_idx if r.met_index else total_max
    kpi_label = ("max kosten (geïndexeerd)" if r.met_index
                 else f"max kosten (prijspeil {mjop.PRIJSPEIL_JAAR})")
    pdf.subsectie("Kerncijfers")
    pdf.kengetallen([
        (str(len(r.all_assets)), "assets in scope"),
        (str(len(rows)), "MJOP-regels"),
        (_eur(kpi_bedrag), kpi_label),
    ])

    pdf.subsectie("Inhoudsopgave")
    for nr, titel in _INHOUD:
        pdf.set_x(pdf.MARGE)
        pdf.set_font(LETTER_PDF, "", 10)
        pdf.set_text_color(*rgb(GRIJS))
        pdf.cell(8, 6, nr)
        pdf.set_text_color(*rgb(INKT))
        pdf.cell(0, 6, titel, new_x="LMARGIN", new_y="NEXT")

    # ═════════════════════════════════════════════════════════════════
    # MANAGEMENTSAMENVATTING (inleiding + de 'so what' in proza)
    # ═════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.sectie("Managementsamenvatting")
    prioriteit_count = sum(c for s, c in r.cond_counts.items() if s is not None and s >= 4)
    by_year_max: dict[int, float] = {}
    for x in rows:
        by_year_max[x["year"]] = by_year_max.get(x["year"], 0.0) + x["max_total"]
    piekjaar = max(by_year_max, key=by_year_max.get) if by_year_max else None
    piekjaar_max = by_year_max.get(piekjaar, 0.0) if piekjaar is not None else 0.0
    _alinea(pdf, _euroteken(rapport.inleiding(
        scope_naam=r.scope_naam, years=r.years, assets_in_scope=len(r.all_assets),
        include_score_2=r.include_score_2)))
    _alinea(pdf, _euroteken(rapport.managementsamenvatting(
        total_min=total_min, total_max=total_max, years=r.years, mjop_regels=len(rows),
        assets_count=len({x["asset_id"] for x in rows}),
        assets_zonder_score=r.assets_zonder_score,
        prioriteit_count=prioriteit_count, piekjaar=piekjaar, piekjaar_max=piekjaar_max)))

    # ═════════════════════════════════════════════════════════════════
    # 1 — METHODOLOGIE (FORMULE + NORMEN)
    # ═════════════════════════════════════════════════════════════════
    _hoofdstuk(pdf, "1. Methodologie – hoe is de begroting opgebouwd?", ruimte=70)
    pdf.subsectie("Bron-normen")
    _opsomming(pdf, _BRON_NORMEN)

    pdf.subsectie("Berekening per regel")
    _alinea(pdf, "Voor elk asset met een conditie-score wordt een MJOP-regel berekend:")
    _formule(pdf, ["eindbedrag = eenheidsprijs × hoeveelheid × cyclus-factor"])
    pdf.ruimte_nodig(45)                 # "Waarbij:" niet los van de opsomming
    pdf.tekst("Waarbij:")
    _opsomming(pdf, [
        "eenheidsprijs: GWW-kostengids basis per maatregel-categorie "
        "(per m, per m² of per stuk – afhankelijk van asset-type)",
        "hoeveelheid: length_m van asset, of standaard breedte voor wegvakken",
        ("cyclus-factor: hoe vaak in de horizon (years) terugkomt op basis van de "
         "norm-conforme inspectie-cyclus per asset-type:", [
             "Brug/viaduct: 6 jaar (CROW 134)",
             "Speeltoestel: 1 jaar (NEN-EN 1176, wettelijk)",
             "Verlichting: 5 jaar (NEN 3140)",
             "Wegmarkering: 2 jaar (CROW 145)",
             "Boom: 1-3 jaar afhankelijk van VTA-risicoklasse",
         ]),
    ])
    pdf.ruimte_nodig(32)
    pdf.tekst("De maatregel zelf wordt afgeleid uit de conditie-score:")
    _opsomming(pdf, [
        "score 3: regulier onderhoud (kleinere ingreep)",
        "score 4: groot onderhoud",
        "score 5: vervanging (capex)",
        "score 6: vervanging (acuut)",
    ] + (["score 2 (preventief): meegenomen vanwege include_score_2=true"]
         if r.include_score_2 else []))

    pdf.subsectie("Min/max-bandbreedte")
    _alinea(pdf, "Per maatregel publiceert de GWW-kostengids 2024 een ondergrens en "
                 "bovengrens (bijvoorbeeld € 5-10/m voor pleksgewijze reparatie). "
                 "Wij rapporteren beide:")
    _formule(pdf, ["min_total = ondergrens × hoeveelheid × cyclus",
                   "max_total = bovengrens × hoeveelheid × cyclus"])
    _alinea(pdf, "Het verschil tussen min en max weerspiegelt onzekerheid in marktprijs, "
                 "uitvoeringsomstandigheden en bijkomende posten. Voor aanbesteding-"
                 "onderbouwing is een eigen RAW-besteksraming per project alsnog vereist.")

    # ═════════════════════════════════════════════════════════════════
    # 2 — ASSET-OVERZICHT
    # ═════════════════════════════════════════════════════════════════
    _hoofdstuk(pdf, "2. Asset-overzicht", ruimte=80)
    pdf.kv([
        ("Totaal aantal assets in scope", str(len(r.all_assets))),
        ("Met conditie-score", str(len(r.all_assets) - r.assets_zonder_score)),
        ("Zonder score (geen MJOP-regel)", str(r.assets_zonder_score)),
    ], labelbreedte=62)
    for kop, blad in (("Verdeling per asset-type", bladen["types"]),
                      ("Conditie-verdeling (NEN 2767-2)", bladen["conditie"])):
        pdf.subsectie(kop)
        if blad.rijen:
            pdf.tabel(blad.kolommen, blad.rijen)
        else:
            _alinea(pdf, "Geen assets in deze selectie.", grootte=9.5, kleur=GRIJS)

    # ═════════════════════════════════════════════════════════════════
    # 3 — MELDINGEN-CONTEXT
    # ═════════════════════════════════════════════════════════════════
    pdf.sectie("3. Meldingen-context")
    _alinea(pdf, "Open meldingen geven aanvullend signaal: assets met veel meldingen "
                 "degraderen vaak sneller dan de cyclus-prognose. Bij voortdurende meldingen "
                 "op een asset is het verstandig om de eerstvolgende inspectie te vervroegen.")
    _alinea(pdf, f"Totaal aantal open/actieve meldingen: {len(r.open_meldingen)}", stijl="B")
    if r.meld_per_type:
        pdf.subsectie("Top categorieën met openstaande meldingen")
        pdf.tabel(bladen["categorieen"].kolommen, bladen["categorieen"].rijen)
    if bladen["meldingen_assets"].rijen:
        pdf.subsectie("Top assets met meeste open meldingen")
        pdf.tabel(bladen["meldingen_assets"].kolommen, bladen["meldingen_assets"].rijen)

    # ═════════════════════════════════════════════════════════════════
    # 4 — KOSTEN PER JAAR
    # ═════════════════════════════════════════════════════════════════
    pdf.sectie("4. Kosten per jaar")
    _alinea(pdf, _horizon_regel(r))
    per_jaar = bladen["per_jaar"]
    if per_jaar.rijen:
        pdf.tabel(per_jaar.kolommen, per_jaar.rijen, totaal=per_jaar.totaal)
    else:
        _alinea(pdf, "Geen MJOP-regels gevonden voor deze filter-combinatie.",
                grootte=9.5, kleur=GRIJS)
    pdf.tekst(mjop.index_toelichting(r.index_pct), grootte=8, kleur=GRIJS, regel=4.2)

    # ═════════════════════════════════════════════════════════════════
    # 5 — VOLLEDIGE MJOP-TABEL (liggend: alle kolommen van de Excel)
    # ═════════════════════════════════════════════════════════════════
    if rows:
        pdf.add_page(orientation="L")
        pdf.sectie("5. Volledige MJOP-tabel (per asset, per jaar)")
        tabel = bladen["tabel"]
        pdf.tabel(tabel.kolommen, tabel.rijen, totaal=tabel.totaal, lettergrootte=7)
        for regel in tabel.toelichting[:2]:
            pdf.tekst(regel, grootte=8, kleur=GRIJS, regel=4.2)
    else:
        pdf.sectie("5. Volledige MJOP-tabel (per asset, per jaar)")
        _alinea(pdf, "Geen MJOP-regels gevonden voor deze filter-combinatie.",
                grootte=9.5, kleur=GRIJS)

    # ═════════════════════════════════════════════════════════════════
    # 6 — VOORBEELD-BEREKENING (concreet uitgewerkt)
    # ═════════════════════════════════════════════════════════════════
    if rows:
        pdf.add_page()
        pdf.sectie("6. Voorbeeld-berekening")
        # De duurste regel: daar is de bandbreedte het best te zien.
        sample = max(rows, key=lambda x: x["max_total"])
        _alinea(pdf, "Hieronder de uitgewerkte berekening voor de duurste MJOP-regel in "
                     "dit rapport, zodat je kunt zien hoe min/max tot stand komt.")
        pdf.kv([
            ("Asset-code", str(sample["asset_code"] or "-")),
            ("Asset-type", str(sample["asset_type"] or "-")),
            ("Conditie-score", f"{sample['condition_score']} (NEN 2767-2)"),
            ("Norm-bron", str(sample.get("norm_reference") or "-")),
            ("Maatregel", str(sample["maatregel"])),
            ("Eenheid", str(sample["unit"])),
            ("Hoeveelheid", _aantal(sample["multiplier"])),
            ("Eenheidsprijs min", _eur(sample["min_eur"])),
            ("Eenheidsprijs max", _eur(sample["max_eur"])),
            ("Geplande datum", als_tekst(Kolom("", "datum"), sample.get("due_date")) or "-"),
        ])
        pdf.subsectie("Berekening")
        verschil = (sample["max_total"] / max(sample["min_total"], 1) - 1) * 100
        _formule(pdf, [
            f"min_total   = {_eur(sample['min_eur'])} × {_aantal(sample['multiplier'])}"
            f" = {_eur(sample['min_total'])}",
            f"max_total   = {_eur(sample['max_eur'])} × {_aantal(sample['multiplier'])}"
            f" = {_eur(sample['max_total'])}",
            f"bandbreedte = {_eur(sample['max_total'] - sample['min_total'])}"
            f" ({verschil:.0f}% verschil)",
        ])
    else:
        pdf.sectie("6. Voorbeeld-berekening")
        _alinea(pdf, "Geen MJOP-regels gevonden voor deze filter-combinatie.",
                grootte=9.5, kleur=GRIJS)

    # ═════════════════════════════════════════════════════════════════
    # 7 — BRONNEN + DISCLAIMER
    # ═════════════════════════════════════════════════════════════════
    _hoofdstuk(pdf, "7. Bronnen en disclaimers", ruimte=90)
    pdf.subsectie("Geraadpleegde normen en richtlijnen")
    _opsomming(pdf, _BRONNEN)
    pdf.ruimte_nodig(40)
    pdf.subsectie("Disclaimer")
    for alinea in _DISCLAIMER:
        _alinea(pdf, alinea, grootte=9, stijl="I", regel=4.8)
    pdf.tekst(f"Maatregel-bibliotheek versie: {mjop.KOSTEN_VERSION}", grootte=9, kleur=GRIJS)

    return pdf.uitvoer()


@router.get("/export.pdf")
def export_mjop_pdf(
    years: int = Query(10, ge=1, le=25),
    project_id: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    include_score_2: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """PDF-rapport (A4) — CROW 134 + 145 + NEN 2767-2 conform.

    Bevat titelpagina met kerncijfers, inhoud, managementsamenvatting,
    methodologie, asset-overzicht, meldingen, kosten per jaar, de volledige
    MJOP-tabel, een voorbeeld-berekening en bronnen + disclaimer. Briefhoofd
    met logo en gegevens van de organisatie, FieldOps-huisstijl.
    """
    from export_huisstijl import pdf_antwoord

    r = _rapport_gegevens(db, current_user, years=years, project_id=project_id,
                          asset_type=asset_type, include_score_2=include_score_2)
    return pdf_antwoord(_mjop_pdf(r, _bladen(r)), _bestandsnaam(r.project_naam, "pdf"))


@router.get("/export.xlsx")
def export_mjop_xlsx(
    years: int = Query(10, ge=1, le=25),
    project_id: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    include_score_2: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Excel-werkboek met dezelfde tabellen als het PDF-rapport, één per
    tabblad: kosten per jaar, de volledige MJOP-tabel, assets per type,
    conditie-verdeling en de meldingen-context. Zelfde briefhoofd en opmaak
    als de PDF; bedragen zijn echte getallen met een euro-opmaak."""
    from export_huisstijl import excel_antwoord, excel_van, klant_van, nu_lokaal

    r = _rapport_gegevens(db, current_user, years=years, project_id=project_id,
                          asset_type=asset_type, include_score_2=include_score_2)
    nu = nu_lokaal()
    meta = (f"Horizon {years} jaar · prijspeil {mjop.PRIJSPEIL_JAAR} · "
            f"geëxporteerd op {nu.strftime('%d-%m-%Y')} om {nu.strftime('%H:%M')}")
    inhoud = excel_van(klant_van(r.org), _TITEL, list(_bladen(r).values()),
                       ondertitel=r.ondertitel, meta=meta)
    return excel_antwoord(inhoud, _bestandsnaam(r.project_naam, "xlsx"))
