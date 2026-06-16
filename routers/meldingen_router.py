import base64
import csv
import io
import json
import math
import os
import zipfile
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse, Response, RedirectResponse
from sqlalchemy.orm import Session, joinedload
from typing import Optional
from pydantic import BaseModel, Field
from database import get_db
from models import Melding, Project, Asset, User, InspectionDefect
from schemas import MeldingCreate, MeldingResponse, MeldingUpdate
from melding_norm_forms import norm_form_voor, alle_norm_velden_keys
from auth import get_current_user
from permissions import (
    can_create_meldingen, can_change_status, can_edit_melding_full,
    is_inspector, require_org_admin,
)
from audit import log_action, ACTION


# ─────────────────────────────────────────────────────────────────────────────
# Auto-classificatie: category + priority → CROW-klasse + gw_term
# Dit zorgt dat clusters/voorspeller/dashboard meldingen kunnen oppakken.
# ─────────────────────────────────────────────────────────────────────────────

# Mapping van vrije categorie-tekst → werk-term (gebruikt voor clustering).
# Cluster groepeert op gw_term, dus meldingen met dezelfde term kunnen samen.
_CATEGORY_TO_GW_TERM = {
    # Verharding / wegdek
    "wegdek": "Wegdek-reparatie", "verharding": "Wegdek-reparatie",
    "asfalt": "Wegdek-reparatie", "trottoir": "Trottoir-reparatie",
    "fietspad": "Fietspad-reparatie",
    # Verkeer
    "verkeerstekens": "Verkeersborden", "verkeersborden": "Verkeersborden",
    "wegmarkering": "Markering herstellen", "markering": "Markering herstellen",
    "belijning": "Markering herstellen",
    # Verlichting
    "verlichting": "Lichtmast onderhoud", "lichtmast": "Lichtmast onderhoud",
    "lantaarn": "Lichtmast onderhoud", "lantaarnpaal": "Lichtmast onderhoud",
    # Groen
    "groen": "Groen onderhoud", "boom": "Boomsnoei",
    "beplanting": "Groen onderhoud", "gras": "Groen onderhoud",
    "berm": "Berm-onderhoud",
    # Riolering
    "riolering": "Riool-reiniging", "kolk": "Kolk-reiniging",
    "putdeksel": "Putdeksel vervangen",
    # Meubilair
    "straatmeubilair": "Meubilair-reparatie", "bank": "Meubilair-reparatie",
    "afvalbak": "Meubilair-reparatie", "fietsenrek": "Meubilair-reparatie",
    # Speeltoestellen
    "speeltoestel": "Speeltoestel-reparatie", "speelplaats": "Speeltoestel-reparatie",
    # Kabel/leiding
    "kabel": "Kabel-/leiding-werk", "leiding": "Kabel-/leiding-werk",
    # Kunstwerken
    "kademuur": "Kunstwerk-inspectie", "duiker": "Kunstwerk-inspectie",
    "brug": "Kunstwerk-inspectie",
}

# Prioriteit → CROW 146 ernst-omvang klasse (vereenvoudigd)
_PRIORITY_TO_CROW_KLASSE = {
    "laag":     "L1",
    "normaal":  "M1",
    "hoog":     "M3",
    "kritiek":  "E2",
}

# CROW-categorie afgeleid van klasse
_KLASSE_TO_CATEGORIE = {
    "L1": "observatie", "L2": "observatie", "L3": "klein onderhoud",
    "M1": "klein onderhoud", "M2": "klein onderhoud", "M3": "regulier onderhoud",
    "E1": "regulier onderhoud", "E2": "groot onderhoud", "E3": "vervanging",
}


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Afstand in meters tussen 2 WGS84-coordinaten (Haversine-formule)."""
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _point_seg_dist_m(plat, plng, alat, alng, blat, blng) -> float:
    """Afstand (m) van punt P tot lijnsegment A-B via lokale equirect-projectie.

    Voor de korte afstanden hier (<2 km) is equirectangular nauwkeurig genoeg en
    veel goedkoper dan een echte geodetische segment-afstand. Cruciaal voor
    wegvakken: een melding kan midden op een segment liggen, ver van elk vertex —
    punt-tot-segment vindt die wél (punt-tot-vertex zou 'm missen).
    """
    lat0 = math.radians(plat)
    m_lat = 111_320.0
    m_lng = 111_320.0 * math.cos(lat0)
    ax = (alng - plng) * m_lng; ay = (alat - plat) * m_lat
    bx = (blng - plng) * m_lng; by = (blat - plat) * m_lat
    dx = bx - ax; dy = by - ay
    seg2 = dx * dx + dy * dy
    if seg2 == 0.0:
        return math.hypot(ax, ay)
    t = -(ax * dx + ay * dy) / seg2
    t = max(0.0, min(1.0, t))
    cx = ax + t * dx; cy = ay + t * dy
    return math.hypot(cx, cy)


def _geometry_pieces(geojson_str, max_points: int = 200) -> list:
    """Parse geometry_geojson → lijst van 'pieces' (elk een lijst (lat,lng)-vertices).

    Point → één piece met één punt; LineString → één piece; MultiLineString →
    meerdere pieces. Lange pieces worden licht gesampled (begin- + eindvertex
    blijven behouden). Lege lijst bij ongeldige/onbekende geometrie.
    """
    if not geojson_str:
        return []
    try:
        geo = json.loads(geojson_str) if isinstance(geojson_str, str) else geojson_str
    except (ValueError, TypeError):
        return []
    if not isinstance(geo, dict):
        return []
    gtype = geo.get("type")
    coords = geo.get("coordinates") or []
    if gtype == "Point":
        return [[(coords[1], coords[0])]] if len(coords) >= 2 else []
    if gtype == "LineString":
        raw_pieces = [coords]
    elif gtype == "MultiLineString":
        raw_pieces = coords
    else:
        return []
    pieces = []
    for raw in raw_pieces:
        if not raw:
            continue
        step = max(1, len(raw) // max_points)
        pts = []
        for i in range(0, len(raw), step):
            pt = raw[i]
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                pts.append((pt[1], pt[0]))   # geojson = [lng, lat] → (lat, lng)
        last = raw[-1]
        if isinstance(last, (list, tuple)) and len(last) >= 2:
            tail = (last[1], last[0])
            if not pts or pts[-1] != tail:
                pts.append(tail)
        if pts:
            pieces.append(pts)
    return pieces


def _build_asset_geo_index(assets: list) -> list:
    """Pre-parse assets → linkbare index met geometrie-pieces + bbox.

    Punt-assets gebruiken (lat,lng); lijn-assets (wegvakken) hun geometry-vertices.
    Eén keer bouwen vóór een bulk-loop — vermijdt herhaald JSON-parsen per melding.
    """
    index = []
    for a in assets:
        if a.lat is not None and a.lng is not None:
            pieces = [[(a.lat, a.lng)]]
        else:
            pieces = _geometry_pieces(a.geometry_geojson)
        if not pieces:
            continue
        lats = [p[0] for piece in pieces for p in piece]
        lngs = [p[1] for piece in pieces for p in piece]
        index.append({
            "id": a.id,
            "is_point": a.lat is not None and a.lng is not None,
            "pieces": pieces,
            "min_lat": min(lats), "max_lat": max(lats),
            "min_lng": min(lngs), "max_lng": max(lngs),
        })
    return index


def _nearest_asset_in_index(lat: float, lng: float, index: list, max_m: float = 200.0):
    """(asset_id, afstand_m) van het dichtstbijzijnde asset binnen max_m, of (None, None).

    Werkt voor punt-assets (haversine — identiek aan het oude gedrag, geen regressie)
    én wegvakken (punt-tot-segment over de lijn-geometrie).
    """
    # Bbox-marge = de drempel omgerekend naar graden (lat/lng apart — lng
    # comprimeert met de breedtegraad), +20% buffer. De marge MOET met max_m
    # meeschalen: het dichtstbijzijnde punt op een geometrie ligt altijd binnen
    # de vertex-bbox, dus een melding binnen max_m valt binnen bbox±(max_m in graden).
    margin_lat = max_m / 110_540.0 * 1.2
    margin_lng = max_m / (111_320.0 * max(0.1, math.cos(math.radians(lat)))) * 1.2
    best_id = None
    best_d = max_m
    for e in index:
        if (lat < e["min_lat"] - margin_lat or lat > e["max_lat"] + margin_lat or
                lng < e["min_lng"] - margin_lng or lng > e["max_lng"] + margin_lng):
            continue
        for piece in e["pieces"]:
            if len(piece) == 1:
                d = _haversine_m(lat, lng, piece[0][0], piece[0][1])
                if d < best_d:
                    best_d, best_id = d, e["id"]
            else:
                for i in range(len(piece) - 1):
                    d = _point_seg_dist_m(lat, lng,
                                          piece[i][0], piece[i][1],
                                          piece[i + 1][0], piece[i + 1][1])
                    if d < best_d:
                        best_d, best_id = d, e["id"]
    if best_id is None:
        return None, None
    return best_id, best_d


def _auto_link_nearest_asset(melding: Melding, index: list, max_m: float = 200.0) -> bool:
    """Koppel melding aan dichtstbijzijnde asset (punt OF wegvak-lijn) binnen max_m.

    `index` = output van _build_asset_geo_index (pre-geparset). Idempotent — als
    asset_id al gezet is, doe niks. Voorwaarde: melding heeft lat/lng.
    """
    if melding.asset_id or melding.lat is None or melding.lng is None:
        return False
    best_id, _d = _nearest_asset_in_index(melding.lat, melding.lng, index, max_m)
    if best_id:
        melding.asset_id = best_id
        return True
    return False


def _enrich_classification(melding: Melding) -> bool:
    """Vul ontbrekende CROW/GW velden in op basis van category + priority.

    Returns True als er iets is aangevuld. Bestaande waarden blijven staan
    (idempotent — kan veilig vaker worden aangeroepen).
    """
    changed = False
    if melding.category and not melding.gw_term:
        cat_key = melding.category.strip().lower()
        term = _CATEGORY_TO_GW_TERM.get(cat_key)
        if term:
            melding.gw_term = term
            changed = True
    if melding.priority and not melding.crow_klasse:
        klasse = _PRIORITY_TO_CROW_KLASSE.get(melding.priority.strip().lower())
        if klasse:
            melding.crow_klasse = klasse
            if not melding.onderhoud_categorie:
                melding.onderhoud_categorie = _KLASSE_TO_CATEGORIE.get(klasse)
            changed = True
    return changed

router = APIRouter(prefix="/api/meldingen", tags=["Meldingen"])


def _clean_norm_data(norm_data) -> Optional[str]:
    """Sanitize norm_data (#16) → JSON-string, of None.

    Behoudt alleen bekende norm-veld-keys en niet-lege waarden, zodat de
    opslag niet vervuild raakt met willekeurige client-velden.
    """
    if not norm_data or not isinstance(norm_data, dict):
        return None
    allowed = alle_norm_velden_keys()
    cleaned = {k: v for k, v in norm_data.items()
               if k in allowed and v not in (None, "", [])}
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else None


def _melding_to_response(melding: Melding, light: bool = False, from_inspection: bool = False) -> dict:
    """Converteer Melding naar response dict met creator_name + asset-koppeling.

    light=True laat de (vaak grote base64-)foto's WEG en geeft alleen
    has_photo/has_photo_after-vlaggen terug. Gebruikt door lijst-/kaart-
    endpoints om de payload klein te houden (performance — foto's worden lazy
    per melding geladen via GET /{id}). De detail-/create-/update-responses
    gebruiken light=False en bevatten de volledige foto's.
    """
    creator = melding.creator
    creator_name = f"{creator.first_name} {creator.last_name}" if creator else None
    # Asset-koppeling meegeven — nodig voor melding-document (#12) en de
    # asset-koppeling in de melding-modal.
    asset = melding.asset if melding.asset_id else None
    # Norm-specifieke velden (#16) — opgeslagen als JSON-string
    norm_data = None
    if melding.norm_data_json:
        try:
            norm_data = json.loads(melding.norm_data_json)
        except (ValueError, TypeError):
            norm_data = None
    return {
        "id": melding.id,
        "title": melding.title,
        "description": melding.description,
        "category": melding.category,
        "priority": melding.priority,
        "status": melding.status,
        "lat": melding.lat,
        "lng": melding.lng,
        "photo_url": None if light else melding.photo_url,
        "photo_after_url": None if light else melding.photo_after_url,
        "has_photo": bool(melding.photo_url),
        "has_photo_after": bool(melding.photo_after_url),
        "project_id": melding.project_id,
        "asset_id": melding.asset_id,
        "asset_code": asset.code if asset else None,
        "asset_type": asset.asset_type if asset else None,
        "from_inspection": from_inspection,
        "crow_schadegroep": melding.crow_schadegroep,
        "crow_schadebeeld": melding.crow_schadebeeld,
        "crow_ernst": melding.crow_ernst,
        "crow_omvang": melding.crow_omvang,
        "crow_klasse": melding.crow_klasse,
        "nen_2767_conditie": melding.nen_2767_conditie,
        "onderhoud_categorie": melding.onderhoud_categorie,
        "gw_maatregel": melding.gw_maatregel,
        "norm_data": norm_data,
        "created_by": melding.created_by,
        "created_at": melding.created_at,
        "creator_name": creator_name,
    }


@router.get("/", response_model=list[MeldingResponse])
def list_meldingen(
    project_id: Optional[str] = Query(None),
    asset_id: Optional[str] = Query(None, description="Filter op asset — gebruikt o.a. door Voorspeller-drilldown (#13)"),
    with_photos: bool = Query(False, description="Inline foto's (base64) meesturen. Default uit voor performance; alleen aanzetten voor kleine, gefilterde lijsten (bv. asset-drilldown)."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alle meldingen van de organisatie ophalen, optioneel gefilterd op project en/of asset.

    Standaard worden de (zware base64-)foto's NIET meegestuurd (alleen
    has_photo-vlaggen) — dat hield de lijst traag bij meerdere projecten.
    Zet with_photos=true alleen aan voor kleine, gefilterde lijsten.
    """
    query = (db.query(Melding)
               .options(joinedload(Melding.asset), joinedload(Melding.creator))
               .filter(Melding.organization_id == current_user.organization_id))
    if project_id:
        query = query.filter(Melding.project_id == project_id)
    if asset_id:
        query = query.filter(Melding.asset_id == asset_id)
    meldingen = query.order_by(Melding.created_at.desc()).all()
    light = not with_photos
    # Welke meldingen zijn gekoppeld aan een (formele CROW/NEN-)inspectie? Eén
    # bulk-query op InspectionDefect.melding_id (geen N+1). De Meldingen-tab
    # gebruikt deze vlag om te schakelen tussen Klein onderhoud en CROW grote ronde.
    inspectie_ids = {
        mid for (mid,) in db.query(InspectionDefect.melding_id).filter(
            InspectionDefect.organization_id == current_user.organization_id,
            InspectionDefect.melding_id.isnot(None),
        ).all()
    }
    return [_melding_to_response(m, light=light, from_inspection=(m.id in inspectie_ids))
            for m in meldingen]


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
    # Photo-offload naar S3 (als geconfigureerd) — backwards compat: base64
    # blijft werken als env-vars niet gezet zijn
    from photo_storage import maybe_offload
    org_id = current_user.organization_id
    photo_url_final = maybe_offload(data.photo_url, organization_id=org_id, kind="melding")
    photo_after_url_final = maybe_offload(data.photo_after_url, organization_id=org_id, kind="melding-after")

    melding = Melding(
        title=data.title,
        description=data.description,
        category=data.category,
        priority=data.priority or "normaal",
        lat=data.lat,
        lng=data.lng,
        photo_url=photo_url_final,
        photo_after_url=photo_after_url_final,
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
        norm_data_json=_clean_norm_data(getattr(data, "norm_data", None)),
    )
    db.add(melding)
    # Auto-koppel aan dichtstbijzijnde asset als er geen handmatig gekozen is.
    # (Bug: nieuwe meldingen werden server-side nooit aan een asset gekoppeld —
    # _auto_link_nearest_asset draaide alleen bij CSV-import/enrich.) Begrensde
    # bounding-box query i.p.v. alle org-assets, voor performance bij grote orgs.
    if melding.asset_id is None and melding.lat is not None and melding.lng is not None:
        box = 0.004  # ~280m lng / ~440m lat marge rond de 200m-drempel (haversine filtert exact)
        nearby = (db.query(Asset)
                    .filter(Asset.organization_id == current_user.organization_id,
                            Asset.archived_at.is_(None),
                            Asset.lat.isnot(None), Asset.lng.isnot(None),
                            Asset.lat.between(melding.lat - box, melding.lat + box),
                            Asset.lng.between(melding.lng - box, melding.lng + box))
                    .all())
        # Hot-path bewust punt-only (snelle bbox-query). Wegvak-koppeling
        # (lijn-geometrie) gebeurt via de bulk-backfill/enrich-paden.
        _auto_link_nearest_asset(melding, _build_asset_geo_index(nearby), max_m=200.0)
    db.commit()
    db.refresh(melding)
    log_action(db, request, current_user,
               action=ACTION.MELDING_CREATE,
               entity_type="melding", entity_id=melding.id,
               after={"title": melding.title, "category": melding.category,
                      "priority": melding.priority, "project_id": melding.project_id,
                      "crow_klasse": melding.crow_klasse,
                      "onderhoud_categorie": melding.onderhoud_categorie})

    # Werkdagboek: auto-entry voor de inspecteur (best-effort)
    from daybook_logger import log_daybook
    log_daybook(
        db,
        user_id=current_user.id,
        organization_id=current_user.organization_id,
        entry_type="melding_created",
        title="Melding aangemaakt: " + (melding.title or "(zonder titel)"),
        description=(("Prioriteit: " + (melding.priority or "normaal"))
                     + (("\nCategorie: " + melding.category) if melding.category else "")),
        source_type="melding",
        source_id=melding.id,
        lat=melding.lat,
        lng=melding.lng,
        project_id=melding.project_id,
    )

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
    "photo":       ["photo", "foto", "photo_url", "foto_url", "fotourl", "afbeelding",
                    "image", "image_url", "bestand", "filename", "bestandsnaam"],
}

# Toegestane priority-waarden (case-insensitive). Onbekend → "normaal".
_VALID_PRIORITIES = {"laag", "normaal", "hoog", "kritiek"}

# Foto-import (F5): max grootte per foto (ruw, vóór base64). Grotere worden overgeslagen.
_MAX_PHOTO_BYTES = 8 * 1024 * 1024
_PHOTO_EXT_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                   "webp": "image/webp", "gif": "image/gif"}


def _load_photos_zip(raw: bytes) -> dict:
    """basename (lowercase) → data-URL (base64). Niet-afbeeldingen/oversized overgeslagen."""
    out: dict = {}
    if not raw or not zipfile.is_zipfile(io.BytesIO(raw)):
        return out
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for n in zf.namelist():
            if n.endswith("/"):
                continue
            ext = n.rsplit(".", 1)[-1].lower() if "." in n else ""
            mime = _PHOTO_EXT_MIME.get(ext)
            if not mime:
                continue
            data = zf.read(n)
            if not data or len(data) > _MAX_PHOTO_BYTES:
                continue
            b64 = base64.b64encode(data).decode("ascii")
            out[os.path.basename(n).lower()] = f"data:{mime};base64,{b64}"
    return out


def _resolve_photo(value: Optional[str], photo_map: dict) -> Optional[str]:
    """CSV-fotowaarde → photo_url: directe (data-)URL, of bestandsnaam uit de zip."""
    if not value:
        return None
    v = value.strip()
    if v.lower().startswith(("http://", "https://", "data:")):
        return v
    return photo_map.get(os.path.basename(v).lower())


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


@router.get("/export.csv")
def export_meldingen_csv(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter op status (open/in_behandeling/opgelost/afgerond)"),
    project_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, alias="from", description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, alias="to", description="YYYY-MM-DD"),
    format: str = Query("imbor", description="'imbor' (GBI-conform), 'standard' (FieldOps-native), of 'mor-plus' (gemeentelijk)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Exporteer meldingen naar CSV — geschikt voor handmatige import in
    GBI World / iAsset / Antea Beheer.

    Formats:
      - **imbor**: kolomnamen die GBI/Antea verwachten bij CSV-import
        (objectnummer, objecttype, melding_type, lat/lng, RD x/y berekend,
        prioriteit, status, datum_constatering, omschrijving)
      - **standard**: FieldOps-native (alle velden zoals in API)
      - **mor-plus**: MOR+ standaard voor gemeenten (Melding Openbare Ruimte)

    Gebruik:
      1. Filter op periode + project + status
      2. Download CSV
      3. Open in Excel of upload direct in GBI/iAsset CSV-import

    Max 10.000 meldingen per export.
    """
    q = db.query(Melding).filter(Melding.organization_id == current_user.organization_id)
    if status_filter:
        q = q.filter(Melding.status == status_filter)
    if project_id:
        q = q.filter(Melding.project_id == project_id)
    if date_from:
        try:
            from datetime import date as _dt
            d = _dt.fromisoformat(date_from)
            q = q.filter(Melding.created_at >= datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc))
        except Exception:
            raise HTTPException(400, "from moet YYYY-MM-DD zijn")
    if date_to:
        try:
            from datetime import date as _dt, timedelta as _td
            d = _dt.fromisoformat(date_to)
            q = q.filter(Melding.created_at < datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc) + _td(days=1))
        except Exception:
            raise HTTPException(400, "to moet YYYY-MM-DD zijn")

    meldingen = q.order_by(Melding.created_at.desc()).limit(10000).all()

    # Project + asset lookups (1 query each, geen N+1)
    proj_ids = list({m.project_id for m in meldingen if m.project_id})
    asset_ids = list({m.asset_id for m in meldingen if m.asset_id})
    proj_map = {}
    if proj_ids:
        for p in db.query(Project.id, Project.name).filter(Project.id.in_(proj_ids)).all():
            proj_map[p.id] = p.name
    asset_map = {}
    if asset_ids:
        for a in db.query(Asset.id, Asset.code, Asset.asset_type).filter(Asset.id.in_(asset_ids)).all():
            asset_map[a.id] = a

    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM voor Excel
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)

    if format == "imbor":
        # IMBOR/GBI-conforme kolomnamen — gemeenten kunnen dit direct in GBI importeren.
        # NB: alleen WGS84-coords. RD New conversie via pyproj is roadmap (MVP gebruikt
        # WGS84 die elke moderne GIS accepteert).
        writer.writerow([
            "objectnummer", "objecttype", "melding_type", "prioriteit", "status",
            "datum_constatering", "lat", "lng",
            "omschrijving", "categorie", "project", "fieldops_melding_id",
        ])
        for m in meldingen:
            asset = asset_map.get(m.asset_id) if m.asset_id else None
            obj_code = (asset.code if asset else "") or ""
            obj_type = (asset.asset_type if asset else "") or (m.category or "")
            writer.writerow([
                obj_code,
                obj_type,
                "melding",
                m.priority or "normaal",
                m.status or "open",
                m.created_at.strftime("%Y-%m-%d") if m.created_at else "",
                f"{m.lat:.7f}" if m.lat is not None else "",
                f"{m.lng:.7f}" if m.lng is not None else "",
                ((m.title or "") + " — " + (m.description or "")).replace("\n", " ").replace("\r", "")[:500],
                m.category or "",
                proj_map.get(m.project_id, "") if m.project_id else "",
                m.id,
            ])
    elif format == "mor-plus":
        # MOR+ standaard voor gemeenten
        writer.writerow([
            "MOR_ID", "TITEL", "OMSCHRIJVING", "CATEGORIE", "PRIORITEIT", "STATUS",
            "AANGEMAAKT", "BIJGEWERKT", "LAT", "LNG", "OBJECT_CODE", "PROJECT",
        ])
        for m in meldingen:
            asset = asset_map.get(m.asset_id) if m.asset_id else None
            writer.writerow([
                m.id, m.title or "", (m.description or "").replace("\n", " ")[:500],
                m.category or "", m.priority or "normaal", m.status or "open",
                m.created_at.isoformat() if m.created_at else "",
                m.updated_at.isoformat() if m.updated_at else "",
                f"{m.lat:.7f}" if m.lat is not None else "",
                f"{m.lng:.7f}" if m.lng is not None else "",
                (asset.code if asset else "") or "",
                proj_map.get(m.project_id, "") if m.project_id else "",
            ])
    else:
        # Standard FieldOps-native
        writer.writerow([
            "id", "title", "description", "category", "priority", "status",
            "lat", "lng", "project", "asset_code",
            "crow_klasse", "nen_2767_conditie",
            "created_at", "updated_at",
        ])
        for m in meldingen:
            asset = asset_map.get(m.asset_id) if m.asset_id else None
            writer.writerow([
                m.id, m.title or "", (m.description or "").replace("\n", " ")[:500],
                m.category or "", m.priority or "normaal", m.status or "open",
                f"{m.lat:.7f}" if m.lat is not None else "",
                f"{m.lng:.7f}" if m.lng is not None else "",
                proj_map.get(m.project_id, "") if m.project_id else "",
                (asset.code if asset else "") or "",
                m.crow_klasse or "",
                m.nen_2767_conditie or "",
                m.created_at.isoformat() if m.created_at else "",
                m.updated_at.isoformat() if m.updated_at else "",
            ])

    csv_bytes = buf.getvalue().encode("utf-8")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fname = f"meldingen-export-{format}-{ts}.csv"

    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Export-Format": format,
            "X-Export-Count": str(len(meldingen)),
        },
    )


# RD New (EPSG:28992) conversie — roadmap. Voor nu alleen WGS84 in exports
# omdat onze in-house approximatie te grote afwijking gaf. Productie-grade:
# `from pyproj import Transformer; Transformer.from_crs(4326, 28992)`.


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
    file: UploadFile = File(..., description="CSV met meldingen. Verplichte kolom: title (of titel). Optioneel: description, category, priority, lat, lng, project (naam) of project_id, asset_code, foto."),
    photos: Optional[UploadFile] = File(None, description="Optionele .zip met foto's; de CSV-fotokolom verwijst dan naar bestandsnamen."),
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
    # Geo-assets voor auto-linking: punt-assets (lat/lng) én wegvakken (lijn-geometrie).
    geo_assets = [a for a in assets
                  if (a.lat is not None and a.lng is not None) or a.geometry_geojson]
    geo_index = _build_asset_geo_index(geo_assets)

    # F5: optionele foto-zip inladen (bestandsnaam → base64 data-URL)
    photo_map = _load_photos_zip(await photos.read()) if photos is not None else {}
    from photo_storage import maybe_offload

    created = 0
    photos_matched = 0
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
        # Auto-classificatie zodat clusters/voorspeller deze meldingen oppakken
        _enrich_classification(melding)
        # Auto-link aan dichtstbijzijnde asset binnen 200m als geen expliciete asset_code
        if not melding.asset_id:
            _auto_link_nearest_asset(melding, geo_index, max_m=200.0)
        # F5: foto koppelen (directe URL in CSV, of bestandsnaam uit de zip)
        photo_val = _get_csv(row, mapping, "photo")
        resolved_photo = _resolve_photo(photo_val, photo_map)
        if resolved_photo:
            melding.photo_url = maybe_offload(
                resolved_photo, organization_id=current_user.organization_id, kind="melding")
            photos_matched += 1
        elif photo_val:
            warnings.append({"row": i, "warning": f"foto '{photo_val}' niet gevonden (geen URL en niet in zip)"})
        db.add(melding)
        created += 1

    db.commit()

    log_action(db, request, current_user,
               action=ACTION.MELDING_CREATE, entity_type="melding-bulk-csv",
               entity_id=None,
               after={"created": created, "errors": len(errors), "warnings": len(warnings)})

    return {
        "created": created,
        "photos_matched": photos_matched,
        "total_rows": len(rows),
        "errors": errors,
        "warnings": warnings,
        "columns_matched": {k: mapping.get(k) for k in _MELDING_CSV_ALIASES if mapping.get(k)},
    }


@router.post("/enrich-classifications")
def enrich_all_classifications(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verrijk alle bestaande meldingen — CROW-classificatie + asset-koppeling.

    Doet 2 dingen idempotent (kan veilig vaker draaien):
      1. Vul ontbrekende gw_term/crow_klasse op basis van category+priority
      2. Koppel meldingen-zonder-asset aan dichtstbijzijnd asset binnen 200m —
         zowel punt-assets (lat/lng) ALS wegvakken (lijn-geometrie), zodat
         wegdek-/scheur-meldingen ook aan hun wegvak koppelen. Werkt voor elke
         melding met coordinaten.

    Onmisbaar nadat je een CSV-import hebt gedaan zonder classificatie of
    asset-codes in de CSV: zonder dit zien clusters, voorspeller en
    dashboard die meldingen niet. Voor alleen koppelen + impact-rapport (dry-run)
    is er ook POST /backfill-asset-links.
    """
    if not can_edit_melding_full(current_user):
        raise HTTPException(status_code=403, detail="Geen rechten")

    items = db.query(Melding).filter(
        Melding.organization_id == current_user.organization_id,
    ).all()

    # Pre-load geo-assets voor auto-linking: punt-assets én wegvakken (lijn-geometrie).
    all_assets = db.query(Asset).filter(
        Asset.organization_id == current_user.organization_id,
        Asset.archived_at.is_(None),
    ).all()
    geo_assets = [a for a in all_assets
                  if (a.lat is not None and a.lng is not None) or a.geometry_geojson]
    geo_index = _build_asset_geo_index(geo_assets)

    enriched = 0
    linked = 0
    for m in items:
        if _enrich_classification(m):
            enriched += 1
        if _auto_link_nearest_asset(m, geo_index, max_m=200.0):
            linked += 1
    db.commit()

    log_action(db, request, current_user,
               action=ACTION.MELDING_UPDATE, entity_type="melding-enrich",
               entity_id=None,
               after={"enriched": enriched, "linked_to_asset": linked,
                      "total": len(items), "geo_assets_available": len(geo_assets)})
    return {
        "enriched": enriched,
        "linked_to_asset": linked,
        "total": len(items),
        "geo_assets_available": len(geo_assets),
    }


@router.post("/backfill-asset-links")
def backfill_asset_links(
    request: Request,
    dry_run: bool = Query(True, description="True = alleen rapporteren, niets opslaan (veilig). Zet false om écht te koppelen."),
    max_m: float = Query(200.0, ge=10, le=2000, description="Max. koppel-afstand in meters"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Koppel bestaande meldingen-zonder-asset aan het dichtstbijzijnde asset —
    nu óók aan **wegvakken** (lijn-geometrie), niet alleen punt-assets.

    Dit was de rem op de Voorspeller-conditie-schatting: wegdek-/scheur-meldingen
    konden nooit aan hun wegvak koppelen (de oude linker keek alleen naar punt-
    assets met lat/lng), dus bleven die assets zonder gekoppelde melding en zonder
    conditie-schatting. Punt-tot-segment-afstand vindt nu de juiste wegvak.

    Idempotent + **standaard dry-run**: draai eerst zonder opslaan om de impact +
    de resterende gaten te zien, daarna met `dry_run=false` om te committen. Het
    rapport splitst koppeling via punt-asset vs. wegvak-lijn en telt waarom
    meldingen ongekoppeld blijven (geen GPS / geen asset binnen drempel).
    """
    if not can_edit_melding_full(current_user):
        raise HTTPException(status_code=403, detail="Geen rechten")

    all_assets = db.query(Asset).filter(
        Asset.organization_id == current_user.organization_id,
        Asset.archived_at.is_(None),
    ).all()
    geo_assets = [a for a in all_assets
                  if (a.lat is not None and a.lng is not None) or a.geometry_geojson]
    index = _build_asset_geo_index(geo_assets)
    is_point_by_id = {e["id"]: e["is_point"] for e in index}
    point_assets = sum(1 for e in index if e["is_point"])
    line_assets = sum(1 for e in index if not e["is_point"])

    meldingen = db.query(Melding).filter(
        Melding.organization_id == current_user.organization_id,
    ).all()

    already = no_gps = newly = via_point = via_line = no_match = 0
    touched_assets: set = set()
    for m in meldingen:
        if m.asset_id:
            already += 1
            continue
        if m.lat is None or m.lng is None:
            no_gps += 1
            continue
        best_id, _d = _nearest_asset_in_index(m.lat, m.lng, index, max_m)
        if not best_id:
            no_match += 1
            continue
        newly += 1
        touched_assets.add(best_id)
        if is_point_by_id.get(best_id):
            via_point += 1
        else:
            via_line += 1
        if not dry_run:
            m.asset_id = best_id

    if not dry_run and newly:
        db.commit()
        log_action(db, request, current_user,
                   action=ACTION.MELDING_UPDATE, entity_type="melding-backfill-link",
                   entity_id=None,
                   after={"newly_linked": newly, "via_point": via_point,
                          "via_line": via_line, "distinct_assets": len(touched_assets),
                          "max_m": max_m})
    else:
        db.rollback()

    return {
        "dry_run": dry_run,
        "max_m": max_m,
        "total_meldingen": len(meldingen),
        "already_linked": already,
        "newly_linked": newly,
        "linked_via_point_asset": via_point,
        "linked_via_wegvak_line": via_line,
        "distinct_assets_touched": len(touched_assets),
        "skipped_no_gps": no_gps,
        "skipped_no_asset_in_range": no_match,
        "point_assets_available": point_assets,
        "line_assets_available": line_assets,
    }


@router.get("/form-schema")
def melding_form_schema(
    asset_type: Optional[str] = Query(None, description="Asset-type (vrij/alias); leeg = geen norm-velden"),
    current_user: User = Depends(get_current_user),
):
    """Norm-specifieke invulvelden voor een asset-type (#16).

    Gebruikt door de melding-modal om dynamisch de juiste NEN/CROW-velden te
    tonen per asset/categorie. Verharding (CROW 146) heeft een eigen sectie en
    geeft hier een lege veldenlijst terug.

    NB: deze route staat bewust vóór /{melding_id} zodat 'form-schema' niet als
    melding-id wordt opgevat.
    """
    return norm_form_voor(asset_type)


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


@router.get("/{melding_id}/thumbnail")
def melding_thumbnail(
    melding_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Kleine thumbnail van de melding-foto — voor de lijst-weergave.

    De lijst stuurt geen base64 meer mee (perf); deze endpoint decodeert de
    base64-foto en schaalt 'm naar ~96px JPEG zodat de lijst snel kleine
    thumbnails kan tonen. Bij een externe URL (S3) wordt doorverwezen. De
    frontend laadt 'm lazy (alleen zichtbare rijen) via fetch + objectURL.
    """
    melding = db.query(Melding.photo_url).filter(
        Melding.id == melding_id,
        Melding.organization_id == current_user.organization_id,
    ).first()
    if not melding or not melding.photo_url:
        raise HTTPException(status_code=404, detail="Geen foto")
    url = melding.photo_url
    if not url.startswith("data:"):
        # Externe URL (S3) — doorverwijzen; browser haalt 'm direct op
        return RedirectResponse(url)
    try:
        from PIL import Image
        b64 = url.split(",", 1)[1]
        img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
        img.thumbnail((96, 96))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=70)
        return Response(content=out.getvalue(), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})
    except Exception:
        raise HTTPException(status_code=404, detail="Foto niet leesbaar")


@router.api_route("/{melding_id}", methods=["PUT", "PATCH"], response_model=MeldingResponse)
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

    # Norm-data (#16) apart afhandelen: het model-veld heet norm_data_json en
    # wordt als JSON-string opgeslagen. Uit update_data halen vóór de generieke
    # before-snapshot + setattr-loop (het model heeft geen 'norm_data'-attribuut).
    norm_data_present = "norm_data" in update_data
    norm_data_value = update_data.pop("norm_data", None)

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

    # Project-update: valideer dat het project bestaat binnen dezelfde
    # organisatie (anders FK-fout/cross-tenant leak). null = ontkoppel.
    if "project_id" in update_data:
        pid = update_data["project_id"]
        if pid is not None and pid != "":
            project_exists = db.query(Project).filter(
                Project.id == pid,
                Project.organization_id == current_user.organization_id,
            ).first()
            if not project_exists:
                raise HTTPException(
                    status_code=400,
                    detail=f"Project '{pid}' niet gevonden in jouw organisatie",
                )
        else:
            # Leeg-string normaliseren naar None zodat FK NULL wordt
            update_data["project_id"] = None

    # Photo-offload bij updates van photo_url / photo_after_url
    from photo_storage import maybe_offload, is_data_url
    org_id = current_user.organization_id
    if "photo_url" in update_data and is_data_url(update_data["photo_url"]):
        offloaded = maybe_offload(update_data["photo_url"], organization_id=org_id, kind="melding")
        if offloaded:
            update_data["photo_url"] = offloaded
    if "photo_after_url" in update_data and is_data_url(update_data["photo_after_url"]):
        offloaded = maybe_offload(update_data["photo_after_url"], organization_id=org_id, kind="melding-after")
        if offloaded:
            update_data["photo_after_url"] = offloaded

    for field, value in update_data.items():
        setattr(melding, field, value)
    if norm_data_present:
        melding.norm_data_json = _clean_norm_data(norm_data_value)
    db.commit()
    db.refresh(melding)

    log_action(db, request, current_user,
               action=ACTION.MELDING_STATUS if status_change else ACTION.MELDING_UPDATE,
               entity_type="melding", entity_id=melding.id,
               before=before, after=update_data)

    # Werkdagboek: auto-entry bij status-wijziging (alleen status, geen field-edit
    # — anders krijg je een entry per losse veld-update)
    if status_change:
        from daybook_logger import log_daybook
        new_status = update_data.get("status")
        log_daybook(
            db,
            user_id=current_user.id,
            organization_id=current_user.organization_id,
            entry_type="melding_status_change",
            title="Status gewijzigd: " + (melding.title or "(zonder titel)") + " → " + (new_status or ""),
            description=("Van '" + (before.get("status") or "open") + "' naar '" + (new_status or "") + "'"),
            source_type="melding",
            source_id=melding.id,
            lat=melding.lat,
            lng=melding.lng,
            project_id=melding.project_id,
        )

    # Notificatie bij toewijzings-wijziging (assigned_to changed)
    if "assigned_to" in update_data and update_data["assigned_to"] != before.get("assigned_to"):
        from notifier import notify
        new_assignee = update_data["assigned_to"]
        if new_assignee and new_assignee != current_user.id:
            assigner_name = ((current_user.first_name or "") + " " + (current_user.last_name or "")).strip()
            # Notify de nieuwe uitvoerder (niet jezelf als je het aan jezelf toewijst)
            notify(
                db,
                user_id=new_assignee,
                organization_id=current_user.organization_id,
                notif_type="assigned",
                title="🎯 Toegewezen: " + (melding.title or "(zonder titel)"),
                body=("Categorie: " + (melding.category or "—")
                      + " · Prioriteit: " + (melding.priority or "normaal")
                      + " · Door: " + assigner_name),
                icon="🎯",
                link_type="melding",
                link_id=melding.id,
                email_context={
                    "assigner_name": assigner_name,
                    "melding_title": melding.title or "(zonder titel)",
                    "melding_category": melding.category or "",
                    "melding_priority": melding.priority or "normaal",
                },
            )

    return _melding_to_response(melding)


# ─────────────────────────────────────────────────────────────────────────────
# Bulk-acties
# ─────────────────────────────────────────────────────────────────────────────

class BulkMeldingUpdate(BaseModel):
    """Bulk-update payload voor meldingen.

    Alleen meegegeven velden worden gewijzigd. Identieke validatie als PATCH
    op individuele melding. Max 500 meldingen per call (anti-DOS).
    """
    melding_ids: list[str] = Field(..., min_length=1, max_length=500)
    status: Optional[str] = None
    priority: Optional[str] = None
    category: Optional[str] = None
    assigned_to: Optional[str] = None     # user_id of "" voor ontkoppelen
    project_id: Optional[str] = None      # of "" voor ontkoppelen
    onderhoud_categorie: Optional[str] = None


@router.post("/bulk-update")
def bulk_update(
    payload: BulkMeldingUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bulk-update meerdere meldingen tegelijk.

    Use-cases:
      - Selecteer 10 open meldingen → status="afgerond"
      - Verplaats 30 wegmarkering-meldingen naar project-A
      - Wijs 5 inspecties toe aan team-lid X

    RBAC volgt dezelfde regels als individuele PATCH (zie update_melding):
      - admin/manager/technician = alle velden
      - inspector = alleen eigen meldingen, geen status
      - contractor = alleen status

    Validatie:
      - Alle meldingen binnen eigen org
      - Asset-FK + project-FK gecheckt
      - Status='opgelost'/'afgerond' vereist photo_after (check per melding)

    Output: { updated: int, skipped: [{id, reason}], audit_logged: int }
    """
    if not (can_edit_melding_full(current_user) or is_inspector(current_user) or can_change_status(current_user)):
        raise HTTPException(status_code=403, detail="Geen rechten voor bulk-update")

    # Welke velden zijn meegegeven (exclude None én exclude_unset)
    raw = payload.model_dump(exclude={"melding_ids"}, exclude_unset=True)
    update_fields = {k: v for k, v in raw.items() if v is not None or k in raw}
    if not update_fields:
        raise HTTPException(status_code=400, detail="Geen velden om te wijzigen")

    # Normaliseer "" naar None voor nullable FKs
    for fk in ("assigned_to", "project_id"):
        if fk in update_fields and update_fields[fk] == "":
            update_fields[fk] = None

    # Validate project_id (als opgegeven en niet None)
    if "project_id" in update_fields and update_fields["project_id"]:
        proj = db.query(Project).filter(
            Project.id == update_fields["project_id"],
            Project.organization_id == current_user.organization_id,
        ).first()
        if not proj:
            raise HTTPException(status_code=400, detail="Project niet in jouw organisatie")

    # Validate assigned_to (user moet in eigen org zitten)
    if "assigned_to" in update_fields and update_fields["assigned_to"]:
        u = db.query(User).filter(
            User.id == update_fields["assigned_to"],
            User.organization_id == current_user.organization_id,
            User.is_active == True,
        ).first()
        if not u:
            raise HTTPException(status_code=400, detail="Toegewezen gebruiker niet in jouw organisatie")

    # Ophalen meldingen — alleen binnen org
    meldingen = db.query(Melding).filter(
        Melding.id.in_(payload.melding_ids),
        Melding.organization_id == current_user.organization_id,
    ).all()
    found_ids = {m.id for m in meldingen}
    missing_ids = [mid for mid in payload.melding_ids if mid not in found_ids]

    updated = 0
    skipped = [{"id": mid, "reason": "niet gevonden"} for mid in missing_ids]
    is_status_change = "status" in update_fields

    for m in meldingen:
        # RBAC per melding
        if can_edit_melding_full(current_user):
            pass
        elif is_inspector(current_user):
            if m.created_by != current_user.id:
                skipped.append({"id": m.id, "reason": "geen eigenaarschap"})
                continue
            if is_status_change:
                skipped.append({"id": m.id, "reason": "inspector mag geen status wijzigen"})
                continue
        elif can_change_status(current_user):
            # Contractor: alleen pure status-update
            if list(update_fields.keys()) != ["status"]:
                skipped.append({"id": m.id, "reason": "alleen status-only is toegestaan"})
                continue

        # Foto-na-verplichting bij afsluiten
        if is_status_change and update_fields["status"] in ("opgelost", "afgerond"):
            if not m.photo_after_url:
                skipped.append({"id": m.id, "reason": "foto-na ontbreekt voor afsluiten"})
                continue

        before = {k: getattr(m, k) for k in update_fields.keys()}
        new_status = update_fields.get("status") if is_status_change else None
        old_status = m.status if is_status_change else None

        for field, value in update_fields.items():
            setattr(m, field, value)
        updated += 1

        log_action(db, request, current_user,
                   action=ACTION.MELDING_STATUS if is_status_change else ACTION.MELDING_UPDATE,
                   entity_type="melding", entity_id=m.id,
                   before=before, after=update_fields)

        # Dagboek-entry per status-wijziging
        if is_status_change and new_status != old_status:
            from daybook_logger import log_daybook
            log_daybook(
                db,
                user_id=current_user.id,
                organization_id=current_user.organization_id,
                entry_type="melding_status_change",
                title="Bulk status-wijziging: " + (m.title or "(zonder titel)") + " → " + (new_status or ""),
                description="Bulk-actie van '" + (old_status or "open") + "' naar '" + (new_status or "") + "'",
                source_type="melding",
                source_id=m.id,
                lat=m.lat,
                lng=m.lng,
                project_id=m.project_id,
                commit=False,  # 1x commit aan einde
            )

        # Notificatie bij bulk-assignment
        if "assigned_to" in update_fields:
            new_assignee = update_fields["assigned_to"]
            old_assignee = before.get("assigned_to")
            if new_assignee and new_assignee != old_assignee and new_assignee != current_user.id:
                from notifier import notify
                assigner_name = ((current_user.first_name or "") + " " + (current_user.last_name or "")).strip()
                notify(
                    db,
                    user_id=new_assignee,
                    organization_id=current_user.organization_id,
                    notif_type="assigned",
                    title="🎯 Toegewezen: " + (m.title or "(zonder titel)"),
                    body="Bulk-toewijzing · Door: " + assigner_name,
                    icon="🎯",
                    link_type="melding",
                    link_id=m.id,
                    commit=False,
                    email_context={
                        "assigner_name": assigner_name,
                        "melding_title": m.title or "(zonder titel)",
                        "melding_category": m.category or "",
                        "melding_priority": m.priority or "normaal",
                    },
                )

    db.commit()

    return {
        "updated": updated,
        "skipped": skipped,
        "skipped_count": len(skipped),
        "total_requested": len(payload.melding_ids),
        "fields_changed": list(update_fields.keys()),
    }


class BulkMeldingDelete(BaseModel):
    melding_ids: list[str] = Field(..., min_length=1, max_length=500)


@router.post("/bulk-delete")
def bulk_delete(
    payload: BulkMeldingDelete,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Bulk-delete meldingen (alleen org-admin)."""
    meldingen = db.query(Melding).filter(
        Melding.id.in_(payload.melding_ids),
        Melding.organization_id == current_user.organization_id,
    ).all()
    deleted = 0
    for m in meldingen:
        snapshot = {"title": m.title, "category": m.category, "status": m.status,
                    "project_id": m.project_id}
        log_action(db, request, current_user,
                   action=ACTION.MELDING_DELETE,
                   entity_type="melding", entity_id=m.id, before=snapshot)
        db.delete(m)
        deleted += 1
    db.commit()
    return {
        "deleted": deleted,
        "total_requested": len(payload.melding_ids),
    }


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
