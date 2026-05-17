import csv
import io
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
from database import get_db
from models import Melding, Project, Asset, User
from schemas import MeldingCreate, MeldingResponse, MeldingUpdate
from auth import get_current_user
from permissions import (
    can_create_meldingen, can_change_status, can_edit_melding_full,
    is_inspector, require_org_admin,
)
from audit import log_action, ACTION

router = APIRouter(prefix="/api/meldingen", tags=["Meldingen"])


def _melding_to_response(melding: Melding) -> dict:
    """Converteer Melding naar response dict met creator_name."""
    creator = melding.creator
    creator_name = f"{creator.first_name} {creator.last_name}" if creator else None
    return {
        "id": melding.id,
        "title": melding.title,
        "description": melding.description,
        "category": melding.category,
        "priority": melding.priority,
        "status": melding.status,
        "lat": melding.lat,
        "lng": melding.lng,
        "photo_url": melding.photo_url,
        "photo_after_url": melding.photo_after_url,
        "project_id": melding.project_id,
        "created_by": melding.created_by,
        "created_at": melding.created_at,
        "creator_name": creator_name,
    }


@router.get("/", response_model=list[MeldingResponse])
def list_meldingen(
    project_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alle meldingen van de organisatie ophalen, optioneel gefilterd op project."""
    query = db.query(Melding).filter(
        Melding.organization_id == current_user.organization_id,
    )
    if project_id:
        query = query.filter(Melding.project_id == project_id)
    meldingen = query.order_by(Melding.created_at.desc()).all()
    return [_melding_to_response(m) for m in meldingen]


@router.post("/", response_model=MeldingResponse)
def create_melding(
    data: MeldingCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Nieuwe melding aanmaken."""
    if not can_create_meldingen(current_user):
        raise HTTPException(status_code=403, detail="Je rol heeft geen rechten om meldingen aan te maken")
    melding = Melding(
        title=data.title,
        description=data.description,
        category=data.category,
        priority=data.priority or "normaal",
        lat=data.lat,
        lng=data.lng,
        photo_url=data.photo_url,
        photo_after_url=data.photo_after_url,
        project_id=data.project_id,
        asset_id=getattr(data, "asset_id", None),
        organization_id=current_user.organization_id,
        created_by=current_user.id,
        # CROW 146 + GWWkosten classificatie (optioneel)
        crow_schadegroep=getattr(data, "crow_schadegroep", None),
        crow_schadebeeld=getattr(data, "crow_schadebeeld", None),
        crow_ernst=getattr(data, "crow_ernst", None),
        crow_omvang=getattr(data, "crow_omvang", None),
        crow_klasse=getattr(data, "crow_klasse", None),
        nen_2767_conditie=getattr(data, "nen_2767_conditie", None),
        onderhoud_categorie=getattr(data, "onderhoud_categorie", None),
        gw_maatregel=getattr(data, "gw_maatregel", None),
        gw_term=getattr(data, "gw_term", None),
        gw_kosten_orde=getattr(data, "gw_kosten_orde", None),
    )
    db.add(melding)
    db.commit()
    db.refresh(melding)
    log_action(db, request, current_user,
               action=ACTION.MELDING_CREATE,
               entity_type="melding", entity_id=melding.id,
               after={"title": melding.title, "category": melding.category,
                      "priority": melding.priority, "project_id": melding.project_id,
                      "crow_klasse": melding.crow_klasse,
                      "onderhoud_categorie": melding.onderhoud_categorie})
    return _melding_to_response(melding)


# ─────────────────────────────────────────────────────────────────────────────
# CSV bulk-import
# ─────────────────────────────────────────────────────────────────────────────

# Kolom-aliases (case-insensitive). Verplicht is alleen `title`/`titel`.
_MELDING_CSV_ALIASES = {
    "title":       ["title", "titel", "onderwerp", "korte_omschrijving", "naam"],
    "description": ["description", "omschrijving", "beschrijving", "toelichting", "opmerking", "notitie"],
    "category":    ["category", "categorie", "type", "schadetype", "object_type", "soort"],
    "priority":    ["priority", "prioriteit", "urgentie"],
    "lat":         ["lat", "latitude", "breedtegraad", "wgs_lat", "y"],
    "lng":         ["lng", "lon", "longitude", "lengtegraad", "wgs_lng", "x"],
    "project_id":  ["project_id", "projectid"],
    "project":     ["project", "project_naam", "projectnaam", "projectname", "project_name"],
    "asset_code":  ["asset_code", "assetcode", "object_code", "objectcode", "objectnummer"],
}

# Toegestane priority-waarden (case-insensitive). Onbekend → "normaal".
_VALID_PRIORITIES = {"laag", "normaal", "hoog", "kritiek"}


def _norm(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "_").replace("-", "_")


def _build_melding_mapping(fieldnames: list[str]) -> tuple[dict, list[str]]:
    """Map verplicht/optioneel doel-veld → originele CSV-kolomnaam."""
    norm_to_orig = {_norm(c): c for c in (fieldnames or [])}
    mapping: dict[str, str] = {}
    for target, aliases in _MELDING_CSV_ALIASES.items():
        for a in aliases:
            if _norm(a) in norm_to_orig:
                mapping[target] = norm_to_orig[_norm(a)]
                break
    missing = ["title"] if "title" not in mapping else []
    return mapping, missing


def _get_csv(row: dict, mapping: dict, key: str) -> Optional[str]:
    col = mapping.get(key)
    if not col:
        return None
    v = row.get(col)
    return v.strip() if isinstance(v, str) and v.strip() else None


@router.get("/import/template.csv")
def import_template_csv(
    current_user: User = Depends(get_current_user),
):
    """Download een lege CSV-template met de verwachte kolommen + 2 voorbeeldrijen."""
    buf = io.StringIO()
    buf.write("﻿")  # BOM voor Excel UTF-8 herkenning
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "title", "description", "category", "priority",
        "lat", "lng", "project", "asset_code",
    ])
    writer.writerow([
        "Scheur in wegdek",
        "Bij hoek Dijkweg / Galgeweg — graag inspecteren",
        "wegdek", "normaal",
        "51.992", "4.211",
        "Gemeente Westland", "",
    ])
    writer.writerow([
        "Lichtmast defect",
        "Lamp brandt al 3 weken niet, klacht van inwoner",
        "verlichting", "hoog",
        "52.008", "4.183",
        "Gemeente Westland", "LM-Naaldwijk-042",
    ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="meldingen-template.csv"'},
    )


@router.post("/import/csv")
async def import_meldingen_csv(
    request: Request,
    file: UploadFile = File(..., description="CSV met meldingen. Verplichte kolom: title (of titel). Optioneel: description, category, priority, lat, lng, project (naam) of project_id, asset_code."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bulk-CSV-import voor meldingen.

    Verwacht een UTF-8 of Windows-1252 CSV met `;` of `,` als delimiter.
    Verplichte kolom: **title** (of `titel`).

    Optionele kolommen (aliases zie taxonomy onderaan response):
      - description / omschrijving
      - category / categorie / type
      - priority / prioriteit (laag/normaal/hoog/kritiek, default normaal)
      - lat / latitude / breedtegraad
      - lng / longitude / lengtegraad
      - project (naam) of project_id
      - asset_code

    Onbekende project-naam = melding wordt aangemaakt zonder project_id (warning per rij).
    """
    if not can_create_meldingen(current_user):
        raise HTTPException(status_code=403, detail="Je rol heeft geen rechten om meldingen aan te maken")

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp1252")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Bestand moet UTF-8 of Windows-1252 zijn")

    sample = text[:2048]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    mapping, missing = _build_melding_mapping(reader.fieldnames or [])
    if missing:
        raise HTTPException(status_code=400, detail={
            "error": f"Verplichte kolommen ontbreken: {missing}",
            "csv_columns": list(reader.fieldnames or []),
            "tip": "Hernoem je titel-kolom naar 'title' of 'titel'.",
            "aliases": _MELDING_CSV_ALIASES,
        })

    # Lookup-tabellen voor performance: project-naam → id, asset-code → id
    projects = db.query(Project).filter(
        Project.organization_id == current_user.organization_id,
    ).all()
    project_by_exact_name = {p.name.lower().strip(): p.id for p in projects if p.name}
    project_ids = {p.id for p in projects}

    def _find_project_id(needle: str) -> Optional[str]:
        """Match project op naam — eerst exact, dan case-insensitive substring.

        Voorbeeld: 'Gemeente Westland' matcht op 'KO - Gemeente Westland'
        zodat CSV-imports met afgekorte projectnamen werken.
        """
        if not needle:
            return None
        key = needle.lower().strip()
        # 1) Exacte match — snelst
        pid = project_by_exact_name.get(key)
        if pid:
            return pid
        # 2) Case-insensitive substring in beide richtingen
        for p in projects:
            if not p.name:
                continue
            pname = p.name.lower()
            if key in pname or pname in key:
                return p.id
        return None

    assets = db.query(Asset).filter(
        Asset.organization_id == current_user.organization_id,
        Asset.archived_at.is_(None),
    ).all()
    asset_by_code = {a.code: a.id for a in assets if a.code}

    created = 0
    errors: list[dict] = []
    warnings: list[dict] = []
    rows = list(reader)

    for i, row in enumerate(rows, start=2):  # rij 1 = header
        title = _get_csv(row, mapping, "title")
        if not title:
            errors.append({"row": i, "error": "title is leeg"})
            continue

        # Project lookup
        project_id = _get_csv(row, mapping, "project_id")
        project_name = _get_csv(row, mapping, "project")
        if project_id and project_id not in project_ids:
            warnings.append({"row": i, "warning": f"onbekende project_id '{project_id}' — genegeerd"})
            project_id = None
        elif not project_id and project_name:
            pid = _find_project_id(project_name)
            if pid:
                project_id = pid
            else:
                warnings.append({"row": i, "warning": f"project '{project_name}' niet gevonden — melding zonder project"})

        # Asset lookup
        asset_id = None
        asset_code = _get_csv(row, mapping, "asset_code")
        if asset_code:
            asset_id = asset_by_code.get(asset_code)
            if not asset_id:
                warnings.append({"row": i, "warning": f"asset_code '{asset_code}' niet gevonden — melding zonder asset"})

        # Priority validatie
        prio = (_get_csv(row, mapping, "priority") or "normaal").lower()
        if prio not in _VALID_PRIORITIES:
            warnings.append({"row": i, "warning": f"priority '{prio}' onbekend → 'normaal'"})
            prio = "normaal"

        # Lat/lng validatie
        lat = lng = None
        try:
            v = _get_csv(row, mapping, "lat")
            if v:
                lat = float(v.replace(",", "."))
        except ValueError:
            warnings.append({"row": i, "warning": f"lat '{v}' is geen geldig getal"})
        try:
            v = _get_csv(row, mapping, "lng")
            if v:
                lng = float(v.replace(",", "."))
        except ValueError:
            warnings.append({"row": i, "warning": f"lng '{v}' is geen geldig getal"})

        melding = Melding(
            title=title,
            description=_get_csv(row, mapping, "description"),
            category=_get_csv(row, mapping, "category"),
            priority=prio,
            lat=lat,
            lng=lng,
            project_id=project_id,
            asset_id=asset_id,
            organization_id=current_user.organization_id,
            created_by=current_user.id,
        )
        db.add(melding)
        created += 1

    db.commit()

    log_action(db, request, current_user,
               action=ACTION.MELDING_CREATE, entity_type="melding-bulk-csv",
               entity_id=None,
               after={"created": created, "errors": len(errors), "warnings": len(warnings)})

    return {
        "created": created,
        "total_rows": len(rows),
        "errors": errors,
        "warnings": warnings,
        "columns_matched": {k: mapping.get(k) for k in _MELDING_CSV_ALIASES if mapping.get(k)},
    }


@router.get("/{melding_id}", response_model=MeldingResponse)
def get_melding(
    melding_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Enkele melding ophalen."""
    melding = db.query(Melding).filter(
        Melding.id == melding_id,
        Melding.organization_id == current_user.organization_id,
    ).first()
    if not melding:
        raise HTTPException(status_code=404, detail="Melding niet gevonden")
    return _melding_to_response(melding)


@router.put("/{melding_id}", response_model=MeldingResponse)
def update_melding(
    melding_id: str,
    update: MeldingUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Melding bijwerken."""
    melding = db.query(Melding).filter(
        Melding.id == melding_id,
        Melding.organization_id == current_user.organization_id,
    ).first()
    if not melding:
        raise HTTPException(status_code=404, detail="Melding niet gevonden")

    update_data = update.model_dump(exclude_unset=True)
    is_status_only = set(update_data.keys()) == {"status"}
    has_status_field = "status" in update_data

    # Permissielogica gerouteerd via permissions.py:
    # - Volledige edit (alle velden incl. status): admin/manager/technician
    # - Inspector mag eigen meldingen, MAAR geen status
    # - Contractor mag enkel status (en alleen via een status-only payload)
    # - Viewer mag niets
    if can_edit_melding_full(current_user):
        pass  # alle velden ok
    elif is_inspector(current_user):
        if melding.created_by != current_user.id:
            raise HTTPException(status_code=403, detail="Toezichthouders mogen alleen eigen meldingen bewerken")
        if has_status_field:
            raise HTTPException(status_code=403, detail="Toezichthouders mogen geen status wijzigen")
    elif can_change_status(current_user):
        # Aannemers e.d. — alleen pure status-update toegestaan
        if not is_status_only:
            raise HTTPException(status_code=403, detail="Je rol mag alleen de status wijzigen")
    else:
        raise HTTPException(status_code=403, detail="Geen rechten om meldingen te wijzigen")

    before = {k: getattr(melding, k) for k in update_data.keys()}
    status_change = has_status_field and update_data["status"] != melding.status

    # Foto-na verplicht vóór afsluiten — bewijslast voor opdrachtgevers
    # dat het werk daadwerkelijk is uitgevoerd. Geldt voor de twee terminal-
    # statussen 'opgelost' en 'afgerond'. Een melding direct herstellen naar
    # 'open' (heropen) blijft mogelijk zonder foto.
    if status_change and update_data["status"] in ("opgelost", "afgerond"):
        new_photo_after = update_data.get("photo_after_url")
        existing_photo_after = melding.photo_after_url
        if not (new_photo_after or existing_photo_after):
            raise HTTPException(
                status_code=400,
                detail="Foto na uitvoering vereist voordat de melding afgesloten "
                       "(opgelost/afgerond) kan worden. Upload eerst een foto.",
            )

    for field, value in update_data.items():
        setattr(melding, field, value)
    db.commit()
    db.refresh(melding)

    log_action(db, request, current_user,
               action=ACTION.MELDING_STATUS if status_change else ACTION.MELDING_UPDATE,
               entity_type="melding", entity_id=melding.id,
               before=before, after=update_data)
    return _melding_to_response(melding)


@router.delete("/{melding_id}")
def delete_melding(
    melding_id: str,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Melding verwijderen (alleen admin)."""
    melding = db.query(Melding).filter(
        Melding.id == melding_id,
        Melding.organization_id == current_user.organization_id,
    ).first()
    if not melding:
        raise HTTPException(status_code=404, detail="Melding niet gevonden")

    snapshot = {"title": melding.title, "category": melding.category,
                "status": melding.status, "project_id": melding.project_id}
    db.delete(melding)
    db.commit()
    log_action(db, request, current_user,
               action=ACTION.MELDING_DELETE,
               entity_type="melding", entity_id=melding_id, before=snapshot)
    return {"message": "Melding verwijderd"}
