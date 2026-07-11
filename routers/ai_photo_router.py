"""AI-foto-classificatie — smart stub met heuristieken.

Voor inspecteurs in opleiding die hulp willen bij defect-herkenning:
upload een foto + context (asset_type + element_code), krijg suggesties
voor mogelijke defect-types met waarschijnlijkheid.

**Belangrijk:** dit is een **heuristieken-stub**, geen echte ML-classificatie.
Een productie-versie heeft nodig:
  - Getrainde model op 10.000+ gelabelde defect-foto's per asset-type
  - GPU-inference of cloud-API (Azure Custom Vision / GCP AutoML)
  - Confidence-callibration + uitleg

In MVP genereren we suggesties op basis van:
  1. Asset-type + element-code → top-N waarschijnlijke defecten uit taxonomy
  2. Foto-metadata (helderheid, kleur, EXIF) → simpele heuristieken
  3. Eerder geconstateerde defecten op vergelijkbare assets → frequentie-prior

Output: gerangschikte lijst defect-codes met confidence-percentage.
Endpoint:
  POST /api/ai/classify   Foto + context → defect-suggesties
  GET  /api/ai/status     Versie + capabilities
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import User, InspectionDefect, InspectionElement
from auth import get_current_user

import kunstwerken_taxonomy as kt
import crow_146
import nen_en_1176
import nen_3140

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["AI-foto-classificatie"])

AI_VERSION = "ai-stub.v0.3-en1176-nen3140-2026-05"


class ClassifyRequest(BaseModel):
    asset_type: str = Field(..., description="brug, boom, riolering, ...")
    element_code: Optional[str] = Field(None, description="bv. BRUG.PIJLERS, BOOM.STAM")
    photo_data_url: Optional[str] = Field(None, description="data:image/...;base64,..")
    photo_mean_brightness: Optional[float] = Field(None, ge=0, le=255,
        description="Optionele helderheid 0-255 (als frontend al heeft uitgerekend)")
    photo_dominant_color: Optional[str] = Field(None,
        description="Hex kleur '#rrggbb' voor heuristieken")


class DefectSuggestion(BaseModel):
    gebrek_code: str
    gebrek_naam: str
    confidence_pct: int  # 0-100
    reason: str          # waarom voorgesteld
    method: str = "heuristic"


@router.get("/status")
def ai_status(current_user: User = Depends(get_current_user)):
    """Versie + capabilities."""
    return {
        "version": AI_VERSION,
        "method": "heuristic-stub",
        "supported_inputs": ["asset_type", "element_code", "photo_data_url",
                              "photo_mean_brightness", "photo_dominant_color"],
        "supported_asset_types": list(kt.KUNSTWERK_TYPES.keys()),
        "limitations": [
            "Geen echte vision-model — heuristieken op metadata + taxonomy-priors",
            "Productie-versie heeft GPU-inference op getraind model nodig",
            "Confidence-cijfers zijn indicatief, geen statistisch valide kansen",
        ],
        "next_milestones": [
            "Verzamel 10k+ gelabelde foto's per asset-type",
            "Train op Azure Custom Vision of GCP AutoML",
            "A/B-test heuristiek vs. model op test-set",
        ],
    }


def _color_to_rgb(hex_color: str) -> Optional[tuple]:
    if not hex_color or not hex_color.startswith("#"):
        return None
    try:
        h = hex_color.lstrip("#")
        if len(h) == 6:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        pass
    return None


def _frequency_prior(db: Session, organization_id: str,
                     element_code: str) -> dict:
    """Tel hoe vaak elke defect-code voorkwam op vergelijkbare elementen.

    Returns dict: {defect_code: count}. Gebruikt als prior voor confidence.
    """
    rows = db.query(
        InspectionDefect.gebrek_code,
    ).join(InspectionElement, InspectionDefect.element_id == InspectionElement.id).filter(
        InspectionElement.element_code == element_code,
        InspectionDefect.organization_id == organization_id,
        InspectionDefect.gebrek_code.isnot(None),
    ).limit(500).all()
    freq = {}
    for r in rows:
        if r.gebrek_code:
            freq[r.gebrek_code] = freq.get(r.gebrek_code, 0) + 1
    return freq


@router.post("/classify")
def classify(
    req: ClassifyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genereer defect-suggesties op basis van context + heuristieken.

    Strategie:
      1. Taxonomy-prior: alle bekende defecten voor (asset_type, element_code)
         krijgen base-confidence 25.
      2. Frequentie-boost: defecten die vaker voorkomen op vergelijkbare
         elementen in deze organisatie krijgen +5-15.
      3. Heuristiek-boost:
         - Lage helderheid + asset_type='boom' → "holte" / "donkere plek" +10
         - Dominante kleur in oranje/rood range + asset met staal/beton →
           "wapeningscorrosie" / "rotting" +10
         - Lage helderheid + 'brug.pijlers' → "scheurvorming" +5
    """
    asset_type = kt.normalize_type(req.asset_type)
    if not asset_type:
        raise HTTPException(status_code=400,
                            detail=f"Onbekend asset_type: {req.asset_type}")

    # Verzamel kandidaten uit taxonomy
    if req.element_code:
        candidates = kt.gebreken_voor(asset_type, req.element_code) or []
    else:
        # Alle elementen samen
        candidates = []
        for e in kt.elementen_voor(asset_type) or []:
            for g in e.get("gebreken") or []:
                if not any(c["code"] == g["code"] for c in candidates):
                    candidates.append(g)

    if not candidates:
        return {
            "suggestions": [],
            "version": AI_VERSION,
            "note": "Geen defect-kandidaten gevonden in taxonomy",
        }

    # Frequentie-prior uit eerdere inspecties
    freq = (_frequency_prior(db, current_user.organization_id, req.element_code)
             if req.element_code else {})

    suggestions = []
    rgb = _color_to_rgb(req.photo_dominant_color or "")
    brightness = req.photo_mean_brightness

    for g in candidates:
        code = g["code"]
        confidence = 25  # base
        reasons = ["taxonomy"]

        # Frequentie-boost
        if code in freq:
            f = freq[code]
            boost = min(15, 3 + f)  # max +15
            confidence += boost
            reasons.append(f"vaak gezien ({f}x)")

        # Heuristiek: lage helderheid + boom + stam-holte
        if asset_type == "boom" and brightness is not None and brightness < 60:
            if code in ("holte", "rotting_wortel", "zwam"):
                confidence += 15
                reasons.append("donkere foto (mogelijk holte/rotting)")

        # Heuristiek: orange/rood + beton-element → corrosie / rotting
        if rgb and rgb[0] > 130 and rgb[1] < 100 and rgb[2] < 80:
            if code in ("wapeningscorrosie", "afspatting", "kanker", "rotting_wortel"):
                confidence += 10
                reasons.append("oranje/rood-tint (mogelijk corrosie)")

        # Heuristiek: lage helderheid + brug.pijlers
        if asset_type == "brug" and req.element_code in (
                "BRUG.PIJLERS", "BRUG.ONDERBOUW") and brightness is not None and brightness < 80:
            if code in ("scheurvorming", "afspatting"):
                confidence += 8
                reasons.append("schaduw-rijke foto (mogelijk scheur)")

        # Heuristiek: groen overheersend + boom → vitaliteit-issue minder waarschijnlijk
        if asset_type == "boom" and rgb and rgb[1] > rgb[0] and rgb[1] > rgb[2]:
            if code in ("dood_hout", "vitaliteitsverlies", "uitval_top"):
                confidence -= 5
                reasons.append("groen-overheersend (kroon mogelijk OK)")

        confidence = max(5, min(95, confidence))
        suggestions.append(DefectSuggestion(
            gebrek_code=code,
            gebrek_naam=g["naam"],
            confidence_pct=confidence,
            reason=" + ".join(reasons),
            method="heuristic",
        ))

    suggestions.sort(key=lambda s: -s.confidence_pct)
    return {
        "asset_type": asset_type,
        "element_code": req.element_code,
        "suggestions": [s.dict() for s in suggestions[:8]],
        "version": AI_VERSION,
        "method": "heuristic-stub",
        "note": "Confidence-cijfers zijn indicatief. Geen vervanging voor ervaren inspecteur.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CROW 146a/b — specialized verharding-inspectie endpoint
# ─────────────────────────────────────────────────────────────────────────────

class Crow146Request(BaseModel):
    verhardingstype: Optional[str] = Field(None, description="'a' = asfalt, 'b' = elementen (klinkers/tegels). Leeg = auto-detect.")
    asset_type: Optional[str] = Field(None, description="wegvak, trottoir, fietspad — hint voor auto-detect")
    properties_hint: Optional[str] = Field(None, description="vrije tekst hint (bv. 'ZOAB-2-deklaag' of 'betonstraatsteen')")
    element_context: Optional[str] = Field(None, description="bv. 'BRUG.BRUGDEK' — relevant element binnen kunstwerk")
    photo_data_url: Optional[str] = Field(None, description="data:image/...;base64,...")
    photo_mean_brightness: Optional[float] = Field(None, ge=0, le=255)
    photo_dominant_color: Optional[str] = Field(None, description="hex of 'r,g,b'")


_CROW_KLASSEN = ["L1", "L2", "L3", "M1", "M2", "M3", "E1", "E2", "E3"]


def _vision_crow146(photo_data_url: str, *, asset_type, properties_hint,
                    element_context, vt, type_label) -> Optional[dict]:
    """Echte foto-analyse via Claude vision (``inspections.analyze_image``).

    Dit kijkt naar de werkelijke pixels (i.t.t. de heuristiek hieronder, die
    alleen op metadata + taxonomy draait). Returnt de classify-crow146-respons,
    of ``None`` als vision niet beschikbaar is of faalt → de caller valt dan
    terug op de heuristiek (geen harde fout voor de inspecteur).
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import inspections
        raw, media = inspections.decode_data_url(photo_data_url)
        ctx = " · ".join(p for p in (properties_hint, element_context) if p)
        vres = inspections.analyze_image(
            image_bytes=raw,
            image_media_type=media,
            asset_type="wegdek",  # CROW 146 = verharding → verharding-prompt
            extra_context=ctx or None,
        )
    except Exception as e:  # noqa: BLE001 — alle vision-fouten → heuristiek-fallback
        logger.warning("CROW146 vision-analyse mislukt, terugval op heuristiek: %s", e)
        return None

    schadebeeld = vres.get("crow_schadebeeld")
    klasse = vres.get("crow_klasse")
    conf = vres.get("confidence")
    conf_pct = int(round(conf * 100)) if isinstance(conf, (int, float)) else None

    suggestions = []
    if vres.get("schade_aanwezig") and (schadebeeld or vres.get("schade_type")):
        code = schadebeeld or vres.get("schade_type")
        naam = (schadebeeld or code).replace("-", " ").replace("_", " ").capitalize()
        suggestions.append({
            "code": code,
            "naam": naam,
            "confidence_pct": conf_pct if conf_pct is not None else 80,
            "klasse": klasse,
            "schadegroep": vres.get("crow_schadegroep"),
            "ernst": vres.get("crow_ernst"),
            "omvang": vres.get("crow_omvang"),
            "maatregel": vres.get("gw_maatregel") or vres.get("aanbevolen_actie"),
            "gw_term": vres.get("gw_term"),
            "nen_2767_conditie": vres.get("nen_2767_conditie"),
            "onderhoud_categorie": vres.get("onderhoud_categorie"),
            "method": "claude-vision",
        })

    return {
        "verhardingstype": vt,
        "verhardingstype_label": type_label,
        "detection_method": "vision",
        "suggestions": suggestions,
        "klasse_advies": {k: crow_146.klasse_advies(k) for k in _CROW_KLASSEN},
        "bevindingen": vres.get("bevindingen") or [],
        "aanbevolen_actie": vres.get("aanbevolen_actie"),
        "nen_2767_conditie": vres.get("nen_2767_conditie"),
        "asset_zichtbaar": vres.get("asset_zichtbaar", True),
        "version": AI_VERSION,
        "method": "claude-vision-crow146",
        "model_id": vres.get("_model_id"),
        "note": "Analyse op basis van de werkelijke foto-pixels via Claude vision "
                "(CROW 146 + NEN 2767). Suggestie voor de inspecteur — controleer vóór accepteren.",
        "norm_reference": "CROW 146 (2010) + Standaard 2015 + NEN 2767",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/classify-crow146")
def classify_crow146_endpoint(
    req: Crow146Request,
    current_user: User = Depends(get_current_user),
):
    """CROW 146 verharding-inspectie classificatie.

    Met foto + ``ANTHROPIC_API_KEY`` analyseert dit de werkelijke pixels via
    Claude vision (CROW 146 + NEN 2767). Zonder foto/key valt het terug op een
    deterministische heuristiek (verhardingstype + CROW 146-taxonomy).

    Identificeert verhardingstype (asfalt 146a vs elementen 146b) en geeft
    waarschijnlijke schadebeelden met advies-maatregel + GW-term.

    Geschikt voor:
      - Inspecteurs bij schouwronde wegen/trottoirs
      - Snelle classificatie bij bulk-meldingen openbare ruimte
      - Suggestie-input bij Kunstwerken-inspectie BRUG.BRUGDEK element

    Niet bedoeld als vervanging voor formele CROW 146-inspectie.
    """
    # Auto-detect verhardingstype als niet opgegeven
    rgb = _color_to_rgb(req.photo_dominant_color or "")
    rgb_tuple = (rgb[0], rgb[1], rgb[2]) if rgb else None

    vt = req.verhardingstype
    if vt not in ("a", "b"):
        vt = crow_146.detect_verhardingstype(
            asset_type=req.asset_type,
            properties_hint=req.properties_hint,
            dominant_color_rgb=rgb_tuple,
        )

    type_label = "Asfaltverharding (CROW 146a)" if vt == "a" else "Elementenverharding (CROW 146b)"

    # Echte foto-analyse wanneer er een foto + API-key is; anders heuristiek.
    if req.photo_data_url:
        vision = _vision_crow146(
            req.photo_data_url,
            asset_type=req.asset_type,
            properties_hint=req.properties_hint,
            element_context=req.element_context,
            vt=vt, type_label=type_label,
        )
        if vision is not None:
            return vision

    suggestions = crow_146.classify_crow146(
        verhardingstype=vt,
        brightness=req.photo_mean_brightness,
        dominant_color_rgb=rgb_tuple,
        element_context=req.element_context,
    )

    return {
        "verhardingstype": vt,
        "verhardingstype_label": type_label,
        "detection_method": "explicit" if req.verhardingstype else "auto",
        "suggestions": suggestions,
        "klasse_advies": {k: crow_146.klasse_advies(k) for k in _CROW_KLASSEN},
        "version": AI_VERSION,
        "method": "heuristic-crow146",
        "note": "Heuristische top-5 (geen foto-analyse beschikbaar) — vervang door gecertificeerde "
                "inspectie voor formeel rapport. Confidence is op basis van CROW 146-taxonomy, geen ML-model.",
        "norm_reference": "CROW 146 (2010) + Standaard 2015",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# NEN-EN 1176 — speeltoestellen-classificatie
# ─────────────────────────────────────────────────────────────────────────────

class En1176Request(BaseModel):
    asset_type: Optional[str] = Field(None, description="speeltoestel, schommel, klimrek, glijbaan, ...")
    properties_hint: Optional[str] = Field(None, description="vrije tekst hint (bv. 'houten kinderwip')")
    categorie: Optional[str] = Field(None, description="A=visueel B=operationeel C=hoofd D=acuut (auto-detect mogelijk)")
    photo_data_url: Optional[str] = Field(None, description="data:image/...;base64,...")
    photo_mean_brightness: Optional[float] = Field(None, ge=0, le=255)
    photo_dominant_color: Optional[str] = Field(None, description="hex of 'r,g,b'")


_EN1176_CAT_LABEL = {
    "A": "A - Visuele inspectie (wekelijks)",
    "B": "B - Operationele inspectie (1-3 maand)",
    "C": "C - Hoofdinspectie (jaarlijks)",
    "D": "D - Acuut afsluiten (direct)",
}

_EN1176_PROMPT = """Je bent een NEN-EN 1176-inspecteur van speeltoestellen. Bekijk de foto en beoordeel de veiligheid.

Reageer ALLEEN met geldige JSON (geen uitleg eromheen):
{
  "asset_zichtbaar": true/false,
  "categorie": "A" | "B" | "C" | "D",
  "faaltypes": [
    {"naam": "<kort>", "ernst": "acuut"|"hoog"|"middel"|"laag", "maatregel": "<kort advies>", "confidence_pct": 0-100}
  ],
  "bevindingen": ["<korte feitelijke observatie>"]
}
Categorie: A=visueel/wekelijks, B=operationeel, C=hoofdinspectie, D=acuut afsluiten.
Regels: 'acuut' alleen bij direct gevaar (val/beknelling/scherpe delen/instabiliteit). Speculeer niet over wat niet zichtbaar is. Max 5 faaltypes."""


def _vision_en1176(photo_data_url, *, asset_type, properties_hint) -> Optional[dict]:
    """Echte foto-analyse via Claude vision voor NEN-EN 1176. None → heuristiek-fallback."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import inspections
        raw, media = inspections.decode_data_url(photo_data_url)
        ctx = " - ".join(p for p in (asset_type, properties_hint) if p)
        vres = inspections.analyze_photo_json(
            image_bytes=raw, image_media_type=media,
            system_prompt=_EN1176_PROMPT, extra_context=ctx or None)
    except Exception as e:  # noqa: BLE001
        logger.warning("EN1176 vision-analyse mislukt, terugval op heuristiek: %s", e)
        return None
    if not isinstance(vres, dict):
        return None

    cat = vres.get("categorie") if vres.get("categorie") in ("A", "B", "C", "D") else "B"
    suggestions = []
    for f in (vres.get("faaltypes") or [])[:5]:
        if not isinstance(f, dict) or not f.get("naam"):
            continue
        conf = f.get("confidence_pct")
        suggestions.append({
            "naam": f.get("naam"),
            "categorie": cat,
            "klasse_advies": f.get("ernst"),
            "maatregel": f.get("maatregel"),
            "confidence_pct": int(conf) if isinstance(conf, (int, float)) else 75,
            "method": "claude-vision",
        })
    return {
        "categorie": cat,
        "categorie_label": _EN1176_CAT_LABEL.get(cat, _EN1176_CAT_LABEL["B"]),
        "detection_method": "vision",
        "suggestions": suggestions,
        "klasse_advies": {k: nen_en_1176.klasse_advies(k) for k in ["acuut", "hoog", "middel", "laag"]},
        "bevindingen": vres.get("bevindingen") or [],
        "asset_zichtbaar": vres.get("asset_zichtbaar", True),
        "version": AI_VERSION,
        "method": "claude-vision-nen-en-1176",
        "model_id": vres.get("_model_id"),
        "note": "Analyse op basis van de foto-pixels via Claude vision. Pre-screening - geen "
                "vervanging voor een gecertificeerde NEN-EN 1176 hoofdinspectie.",
        "norm_reference": "NEN-EN 1176-1:2017 (nationale aanvulling 2025)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/classify-en1176")
def classify_en1176_endpoint(
    req: En1176Request,
    current_user: User = Depends(get_current_user),
):
    """NEN-EN 1176 speeltoestellen-veiligheid classificatie.

    Identificeert inspectie-categorie (A/B/C/D) en top-5 faaltypes met
    advies-maatregel. Geschikt voor:
      - Gemeentelijke groen-inspecteurs bij wekelijkse schouw
      - Externe NEN-EN 1176 jaarcheck (hoofdinspectie type C)
      - Direct-afsluit beoordeling bij melding (type D)

    Niet bedoeld als vervanging voor gecertificeerde NEN-EN 1176 hoofdinspectie.
    """
    # Echte foto-analyse wanneer er een foto + API-key is; anders heuristiek.
    if req.photo_data_url:
        vision = _vision_en1176(req.photo_data_url, asset_type=req.asset_type,
                                properties_hint=req.properties_hint)
        if vision is not None:
            return vision

    rgb = _color_to_rgb(req.photo_dominant_color or "")
    rgb_tuple = (rgb[0], rgb[1], rgb[2]) if rgb else None

    suggestions = nen_en_1176.classify_en1176(
        asset_type=req.asset_type,
        properties_hint=req.properties_hint,
        categorie=req.categorie if req.categorie in ("A", "B", "C", "D") else None,
        brightness=req.photo_mean_brightness,
        dominant_color_rgb=rgb_tuple,
    )

    cat = req.categorie if req.categorie in ("A", "B", "C", "D") else nen_en_1176.detect_categorie(req.asset_type, req.properties_hint)
    cat_label = {
        "A": "A — Visuele inspectie (wekelijks)",
        "B": "B — Operationele inspectie (1-3 maand)",
        "C": "C — Hoofdinspectie (jaarlijks)",
        "D": "D — Acuut afsluiten (direct)",
    }.get(cat, "B — Operationele inspectie")

    return {
        "categorie": cat,
        "categorie_label": cat_label,
        "detection_method": "explicit" if req.categorie else "auto",
        "suggestions": suggestions,
        "klasse_advies": {k: nen_en_1176.klasse_advies(k) for k in ["acuut", "hoog", "middel", "laag"]},
        "version": AI_VERSION,
        "method": "heuristic-nen-en-1176",
        "note": "Heuristische classificatie — gebruik voor pre-screening, niet voor formele NEN-EN 1176 hoofdcheck.",
        "norm_reference": "NEN-EN 1176-1:2017 (nationale aanvulling 2025)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# NEN 3140 — elektrische installaties LS (openbare verlichting, verkeerslichten)
# ─────────────────────────────────────────────────────────────────────────────

class Nen3140Request(BaseModel):
    asset_type: Optional[str] = Field(None, description="lichtmast, verkeerslicht, armatuur, kast, ...")
    properties_hint: Optional[str] = Field(None, description="vrije tekst hint")
    # Meetwaarden (optioneel; harde grens-check als ingevuld)
    isolatie_megaohm: Optional[float] = Field(None, ge=0, description="Isolatieweerstand in MΩ")
    aarding_ohm: Optional[float] = Field(None, ge=0, description="Aardingsweerstand in Ω")
    aardlek_ms: Optional[float] = Field(None, ge=0, description="Aardlek-uitschakeltijd in ms")
    aardlek_ma: Optional[float] = Field(None, ge=0, description="Aardlek-stroom in mA")
    # Foto-features (visuele defecten)
    photo_data_url: Optional[str] = Field(None, description="data:image/...;base64,...")
    photo_mean_brightness: Optional[float] = Field(None, ge=0, le=255)
    photo_dominant_color: Optional[str] = Field(None, description="hex of 'r,g,b'")


_NEN3140_PROMPT = """Je bent een NEN 3140-inspecteur van elektrische laagspannings-installaties in de openbare ruimte (lichtmast, verkeerslicht, schakelkast, armatuur). Beoordeel ZICHTBARE defecten op de foto.

Reageer ALLEEN met geldige JSON:
{
  "asset_zichtbaar": true/false,
  "defects": [
    {"naam": "<kort>", "ernst": "kritiek"|"hoog"|"middel"|"laag", "maatregel": "<kort advies>", "confidence_pct": 0-100}
  ],
  "bevindingen": ["<korte observatie>"]
}
Let op: corrosie aan voet/mast, loshangende of blootliggende kabels, beschadigde/open behuizing, ontbrekende afdekking, waterintreding, scheefstand.
Regels: 'kritiek' alleen bij direct gevaar (aanraakbare spanning, blootliggende geleiders). Meetwaarden (isolatie/aarding/aardlek) kun je NIET uit een foto bepalen - laat die buiten beschouwing. Max 5 defects."""


def _vision_nen3140_suggestions(photo_data_url, *, asset_type, properties_hint):
    """Echte foto-defect-detectie via Claude vision. Returnt (suggestions, meta)
    of (None, None) → caller valt terug op de heuristiek. De meetwaarde-
    grenswaarden blijven sowieso los hiervan (die komen niet uit een foto)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, None
    try:
        import inspections
        raw, media = inspections.decode_data_url(photo_data_url)
        ctx = " - ".join(p for p in (asset_type, properties_hint) if p)
        vres = inspections.analyze_photo_json(
            image_bytes=raw, image_media_type=media,
            system_prompt=_NEN3140_PROMPT, extra_context=ctx or None)
    except Exception as e:  # noqa: BLE001
        logger.warning("NEN3140 vision-analyse mislukt, terugval op heuristiek: %s", e)
        return None, None
    if not isinstance(vres, dict):
        return None, None

    suggestions = []
    for d in (vres.get("defects") or [])[:5]:
        if not isinstance(d, dict) or not d.get("naam"):
            continue
        conf = d.get("confidence_pct")
        suggestions.append({
            "naam": d.get("naam"),
            "klasse_advies": d.get("ernst"),
            "maatregel": d.get("maatregel"),
            "confidence_pct": int(conf) if isinstance(conf, (int, float)) else 75,
            "method": "claude-vision",
        })
    meta = {
        "bevindingen": vres.get("bevindingen") or [],
        "asset_zichtbaar": vres.get("asset_zichtbaar", True),
        "model_id": vres.get("_model_id"),
    }
    return suggestions, meta


@router.post("/classify-nen3140")
def classify_nen3140_endpoint(
    req: Nen3140Request,
    current_user: User = Depends(get_current_user),
):
    """NEN 3140 elektrische LS-installaties classificatie.

    Combineert harde grens-checks (gemeten waarden vs norm) met heuristische
    visuele defect-detectie. Bij meetwaarden boven/onder norm krijgen die
    defecten automatisch hoge confidence.

    Geschikt voor:
      - Jaarlijkse NEN 3140 visuele inspectie openbare verlichting
      - 5-jaarlijkse meet-ronde met multimeter/aardingsmeter
      - Acute melding 'lamp brandt niet' → quick triage
    """
    # Grens-checks op gemeten waarden — altijd, los van de foto.
    grens_breaches = nen_3140.check_grenswaarden(
        isolatie_megaohm=req.isolatie_megaohm,
        aarding_ohm=req.aarding_ohm,
        aardlek_ms=req.aardlek_ms,
        aardlek_ma=req.aardlek_ma,
    )

    # Visuele defecten: echte foto-analyse wanneer foto + key, anders heuristiek.
    vision_sugg, vision_meta = (None, None)
    if req.photo_data_url:
        vision_sugg, vision_meta = _vision_nen3140_suggestions(
            req.photo_data_url, asset_type=req.asset_type,
            properties_hint=req.properties_hint)

    if vision_sugg is not None:
        suggestions = vision_sugg
        method = "grens-check + claude-vision-nen-3140"
        detection_method = "vision"
    else:
        rgb = _color_to_rgb(req.photo_dominant_color or "")
        rgb_tuple = (rgb[0], rgb[1], rgb[2]) if rgb else None
        suggestions = nen_3140.classify_nen3140(
            asset_type=req.asset_type,
            properties_hint=req.properties_hint,
            isolatie_megaohm=req.isolatie_megaohm,
            aarding_ohm=req.aarding_ohm,
            aardlek_ms=req.aardlek_ms,
            aardlek_ma=req.aardlek_ma,
            brightness=req.photo_mean_brightness,
            dominant_color_rgb=rgb_tuple,
        )
        method = "grens-check + heuristic-nen-3140"
        detection_method = "heuristic"

    result = {
        "suggestions": suggestions,
        "grens_breaches": grens_breaches,
        "grens_breach_count": len(grens_breaches),
        "detection_method": detection_method,
        "klasse_advies": {k: nen_3140.klasse_advies(k) for k in ["kritiek", "hoog", "middel", "laag"]},
        "version": AI_VERSION,
        "method": method,
        "note": "Grens-checks volgen NEN 3140 strikt. Foto-defectdetectie is pre-screening - "
                "formele meting blijft vereist.",
        "norm_reference": "NEN 3140:2011 + supplement A2:2019",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if vision_meta:
        result["bevindingen"] = vision_meta["bevindingen"]
        result["asset_zichtbaar"] = vision_meta["asset_zichtbaar"]
        result["model_id"] = vision_meta["model_id"]
    return result
