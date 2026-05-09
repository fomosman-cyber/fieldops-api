"""AI-vision-analyse voor infra-inspecties.

Geeft één foto (multipart of URL) door aan Claude vision, vraagt om gestructureerde
schade-analyse per asset-type, en geeft een gevalideerde dict terug. Het record
wordt opgeslagen in `AIAnalysis` voor compliance + reproduceerbaarheid.

Mens-in-de-loop: het resultaat is een SUGGESTIE die de inspecteur in de
melding-UI accepteert/aanpast/afwijst.

Prompt caching is actief op de system-prompt zodat herhaalde calls per asset-type
ongeveer 90% goedkoper zijn (lange instructie wordt 5 minuten ge-cached).

Stub-modus: zonder ANTHROPIC_API_KEY in env retourneert deze module
deterministische dummy-data, zodat de rest van het systeem (router/audit/UI)
lokaal te testen is.
"""

from __future__ import annotations
import base64
import hashlib
import json
import os
import re
from typing import Any, Optional

import httpx

PROMPT_VERSION = "v2.0-crow"
DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

# Geldige enum-waarden — server valideert AI-output hierop
ERNST_LEVELS = {"geen", "licht", "matig", "ernstig", "kritiek"}
KOSTEN_KLASSE = {"<€500", "€500-2.500", "€2.500-10.000", ">€10.000", None}

# CROW 146 — geldige enum waarden
CROW_ERNST = {"L", "M", "E", None}
CROW_OMVANG = {"1", "2", "3", None}
CROW_SCHADEGROEPEN = {
    "samenhang", "textuur", "vlakheid", "voegen", "watergevoeligheid",
    "stenen", None,
}
CROW_KLASSEN = {
    "L1", "L2", "L3",
    "M1", "M2", "M3",
    "E1", "E2", "E3",
    None,
}


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

_BASE_PROMPT = """Je bent een gespecialiseerde infrastructuur-inspectie-AI voor de Nederlandse wegen- en waterbouw. Je analyseert foto's van objecten in het veld en geeft een gestructureerde schade-rapportage volgens CROW 146 (visuele inspectie wegen) + NEN 2767 (conditiemeting infra).

Werkwijze:
1. Bekijk de foto zorgvuldig
2. Identificeer schade volgens CROW-schadebeelden (scheurvorming, rafeling, oneffenheden, etc.)
3. Classificeer volgens CROW-klasse: ernst (L/M/E) × omvang (1/2/3) → bv L1, M2, E3
4. Vertaal naar NEN 2767-conditie (1-5 schaal)
5. Bepaal onderhouds-categorie (observatie / KO / GO / acuut)
6. Formuleer een aanbevolen vervolgactie

Reageer ALLEEN met geldige JSON in dit exacte format (geen backticks, geen uitleg eromheen):
{
  "schade_aanwezig": true/false,
  "schade_type": "scheur" | "verzakking" | "gat" | "spoorvorming" | "rafeling" | "betonschade" | "corrosie" | "graffiti" | "verlies_onderdelen" | "begroeiing" | "overig" | null,
  "ernst": "geen" | "licht" | "matig" | "ernstig" | "kritiek",
  "kosten_klasse": "<€500" | "€500-2.500" | "€2.500-10.000" | ">€10.000" | null,
  "bevindingen": ["korte observatie", "korte observatie"],
  "aanbevolen_actie": "één zin met concrete vervolgactie",
  "confidence": 0.0 tot 1.0,
  "asset_zichtbaar": true/false,

  "crow_schadegroep": "samenhang" | "textuur" | "vlakheid" | "voegen" | "watergevoeligheid" | "stenen" | null,
  "crow_schadebeeld": "scheurvorming-langs" | "scheurvorming-dwars" | "scheurvorming-kruis" | "scheurvorming-rand" | "scheurvorming-groep" | "rafeling" | "spoorvorming" | "oneffenheden" | "kuilen" | "deformatie" | "verzakking" | "voegwijdte" | "gebroken-stenen" | "ontbrekende-stenen" | null,
  "crow_ernst": "L" | "M" | "E" | null,
  "crow_omvang": "1" | "2" | "3" | null,
  "crow_klasse": "L1" | "L2" | "L3" | "M1" | "M2" | "M3" | "E1" | "E2" | "E3" | null,
  "nen_2767_conditie": 1 | 2 | 3 | 4 | 5 | null
}

Belangrijke regels:
- "asset_zichtbaar": false → de foto is onbruikbaar (vaag, verkeerd object, te donker). Andere velden mogen dan null zijn.
- Wees voorzichtig met "kritiek" / E3 — alleen bij directe veiligheidsdreiging.
- CROW-classificatie alleen invullen als de foto een verharding (asfalt, klinker, beton) toont. Bij andere asset-types (verkeer, groen, riolering) mag het null zijn.
- "crow_klasse" moet consistent zijn met "crow_ernst" + "crow_omvang" (bv ernst=M + omvang=2 → klasse=M2).
- CROW-schadebeeld notatie:
  * Asfalt samenhang: scheurvorming-langs/dwars/kruis/rand/groep
  * Asfalt textuur: rafeling
  * Asfalt vlakheid: spoorvorming, oneffenheden, kuilen, deformatie
  * Elementen vlakheid: verzakking
  * Elementen voegen: voegwijdte
  * Elementen stenen: gebroken-stenen, ontbrekende-stenen
- "bevindingen" bevat 1-5 korte observaties (max 100 tekens elk), feitelijk.
- "confidence" reflecteert hoe zeker je bent over de hele beoordeling.
- Speculeer NIET over wat niet zichtbaar is. Geen aannames.

Mens-in-de-loop: deze output is een SUGGESTIE voor de inspecteur. De inspecteur kan elke veld aanpassen vóór accepteren.
"""

_ASSET_SPECIFIC = {
    "wegdek": """Specifiek voor wegdek — let op:
- Scheuren (langs/dwars/alligator), gaten, spoorvorming, rafeling, oneffenheden
- Voegen tussen platen, randen
- NEN 2767 Wegen: ernst gebaseerd op breedte/diepte/oppervlak""",
    "kunstwerken": """Specifiek voor kunstwerken (bruggen/viaducten/sluizen) — let op:
- Betonschade, scheurvorming, voegen
- Wapeningscorrosie zichtbaar?
- Aansluitingen, leuningen, brugdek
- Roest, opbol-vorming, afgebroken stukken""",
    "watergangen": """Specifiek voor watergangen — let op:
- Oeverbeschoeiing, beschadigingen
- Verzakkingen, ondermijning
- Begroeiing/dichtgegroeid
- Vervuiling, drijvend afval""",
    "keringen": """Specifiek voor keringen (dijken/damwanden) — let op:
- Scheurvorming, verzakkingen
- Erosie, kwel, gravers
- Begroeiing/wortels in talud
- Damwand: roest, ontzetting""",
    "riolering": """Specifiek voor riolering — let op:
- Putdeksel-conditie, scheuren rond rand
- Water in put, verstopping zichtbaar?
- Beschadiging stortrand
- Leiding-aansluitingen""",
    "lantaarnpaal": """Specifiek voor lantaarnpaal — let op:
- Scheefstand, deuk, breuk
- Roest aan voet
- Armatuur/lamp ontbreekt of stuk
- Loshangende kabels (gevaar!)""",
    "verkeer": """Specifiek voor verkeersinfra — let op:
- Markering: zichtbaarheid, slijtage, ontbrekende delen
- Borden: leesbaarheid, scheefstand, vervuiling
- Geleiderail: deuken, ontbrekende segmenten""",
    "groen": """Specifiek voor groen — let op:
- Bermbreuk, talud-erosie
- Dode/afgebroken takken (veiligheid)
- Invasieve exoten (Japanse duizendknoop, reuzenberenklauw)
- Onderhoudstoestand""",
}


def _system_prompt(asset_type: Optional[str]) -> str:
    extra = _ASSET_SPECIFIC.get((asset_type or "").lower(), "")
    return _BASE_PROMPT + ("\n\n" + extra if extra else "")


# ─────────────────────────────────────────────────────────────────────────────
# Image-helpers
# ─────────────────────────────────────────────────────────────────────────────

_ALLOWED_MEDIA = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def detect_media_type(filename: Optional[str], content_type: Optional[str]) -> str:
    if content_type and content_type in _ALLOWED_MEDIA:
        return content_type
    if filename:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")
    return "image/jpeg"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Resultaat-validatie
# ─────────────────────────────────────────────────────────────────────────────

def _coerce_result(raw_text: str) -> dict:
    """Probeer het JSON-object uit de model-output te halen. Claude antwoord
    is meestal pure JSON; als er rommel omheen staat, zoek het eerste { ... }.
    """
    text = raw_text.strip()
    # Strip eventuele code-fences die ondanks instructies erin sluipen
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            raise ValueError("Modeloutput bevat geen JSON-object")
        return json.loads(m.group(0))


def _validate(result: dict) -> dict:
    """Valideer en normaliseer de schade-rapportage. Onbekende velden worden
    weggegooid; ontbrekende velden krijgen None.

    Sinds v2.0-crow voegt deze ook CROW 146-classificatie toe (schadegroep,
    schadebeeld, ernst, omvang, klasse) + NEN 2767 conditie + maatregel-advies
    via crow_kosten lookup.
    """
    out = {
        "schade_aanwezig": bool(result.get("schade_aanwezig")) if result.get("schade_aanwezig") is not None else None,
        "schade_type": result.get("schade_type") or None,
        "ernst": result.get("ernst") if result.get("ernst") in ERNST_LEVELS else None,
        "kosten_klasse": result.get("kosten_klasse") if result.get("kosten_klasse") in KOSTEN_KLASSE else None,
        "bevindingen": [],
        "aanbevolen_actie": (result.get("aanbevolen_actie") or "")[:500] or None,
        "confidence": None,
        "asset_zichtbaar": bool(result.get("asset_zichtbaar")) if result.get("asset_zichtbaar") is not None else True,
    }
    # Bevindingen: lijst van strings, max 5, max 100 tekens elk
    raw_bev = result.get("bevindingen") or []
    if isinstance(raw_bev, list):
        out["bevindingen"] = [str(b)[:100] for b in raw_bev if b][:5]
    # Confidence in [0, 1]
    try:
        c = float(result.get("confidence"))
        if 0.0 <= c <= 1.0:
            out["confidence"] = c
    except (TypeError, ValueError):
        pass

    # ─── CROW 146 classificatie (nieuw in v2.0) ───
    crow_ernst = result.get("crow_ernst")
    crow_omvang = result.get("crow_omvang")
    crow_klasse = result.get("crow_klasse")

    # Normaliseer ernst/omvang
    if crow_ernst and isinstance(crow_ernst, str):
        crow_ernst = crow_ernst.strip().upper()[:1]
        if crow_ernst not in ("L", "M", "E"):
            crow_ernst = None
    else:
        crow_ernst = None

    if crow_omvang is not None:
        s = str(crow_omvang).strip()[:1]
        if s in ("1", "2", "3"):
            crow_omvang = s
        else:
            crow_omvang = None

    # Reconstrueer klasse uit ernst+omvang als nodig
    if crow_ernst and crow_omvang and not crow_klasse:
        crow_klasse = f"{crow_ernst}{crow_omvang}"
    elif crow_klasse and isinstance(crow_klasse, str):
        crow_klasse = crow_klasse.strip().upper()
        if crow_klasse not in CROW_KLASSEN or not crow_klasse:
            crow_klasse = None

    out["crow_schadegroep"] = result.get("crow_schadegroep") if result.get("crow_schadegroep") in CROW_SCHADEGROEPEN else None
    out["crow_schadebeeld"] = (result.get("crow_schadebeeld") or "").strip().lower() or None
    out["crow_ernst"] = crow_ernst
    out["crow_omvang"] = crow_omvang
    out["crow_klasse"] = crow_klasse

    # NEN 2767 conditie 1-5
    nen = result.get("nen_2767_conditie")
    try:
        nen_i = int(nen) if nen is not None else None
        out["nen_2767_conditie"] = nen_i if nen_i and 1 <= nen_i <= 5 else None
    except (TypeError, ValueError):
        out["nen_2767_conditie"] = None

    # Verrijk met onderhouds-categorie + GWWkosten-koppeling
    if crow_klasse:
        try:
            from crow_kosten import lookup_maatregel
            advies = lookup_maatregel(
                schadebeeld=out["crow_schadebeeld"] or "",
                klasse=crow_klasse,
                schadegroep=out["crow_schadegroep"],
            )
            out["onderhoud_categorie"] = advies["categorie"]  # observatie/KO/GO/acuut
            out["gw_maatregel"] = advies["maatregel"]
            out["gw_term"] = advies["gw_term"]
            out["gw_kosten_orde"] = advies["kosten_orde"]
            out["gw_eenheid"] = advies["eenheid"]
            out["termijn_weken"] = advies["termijn_weken"]
        except Exception:
            # Lookup-fout mag de validatie nooit breken
            out["onderhoud_categorie"] = None
            out["gw_maatregel"] = None
    else:
        out["onderhoud_categorie"] = None
        out["gw_maatregel"] = None

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Claude API call — prompt caching aan, vision input via base64 of URL
# ─────────────────────────────────────────────────────────────────────────────

class InspectionResult(dict):
    """Dict-subklasse zodat de router 'result["..."]' kan doen én attributes."""
    @property
    def model_id(self): return self.get("_model_id")
    @property
    def prompt_version(self): return self.get("_prompt_version")
    @property
    def raw(self): return self.get("_raw_response")


def _stub_result(asset_type: Optional[str]) -> InspectionResult:
    """Deterministische fake-output als ANTHROPIC_API_KEY ontbreekt."""
    out = _validate({
        "schade_aanwezig": True,
        "schade_type": "scheur",
        "ernst": "matig",
        "kosten_klasse": "€500-2.500",
        "bevindingen": [
            "Langsscheur over circa 2 meter in rechter rijspoor",
            "Lichte verzakking aan oost-zijde",
        ],
        "aanbevolen_actie": "Inplannen voor reparatie binnen 4-6 weken; monitor uitbreiding",
        "confidence": 0.78,
        "asset_zichtbaar": True,
        # CROW 146 classificatie (stub)
        "crow_schadegroep": "samenhang",
        "crow_schadebeeld": "scheurvorming-langs",
        "crow_ernst": "M",
        "crow_omvang": "2",
        "crow_klasse": "M2",
        "nen_2767_conditie": 3,
    })
    out["_model_id"] = "stub-no-api-key"
    out["_prompt_version"] = PROMPT_VERSION
    out["_raw_response"] = "(stub-mode — set ANTHROPIC_API_KEY voor echte analyse)"
    return InspectionResult(out)


def analyze_image(
    *,
    image_bytes: Optional[bytes] = None,
    image_media_type: Optional[str] = None,
    image_url: Optional[str] = None,
    asset_type: Optional[str] = None,
    extra_context: Optional[str] = None,
) -> InspectionResult:
    """Analyseer één foto. Geef ofwel `image_bytes`+`image_media_type`,
    ofwel `image_url`. Returnt een gevalideerde dict met `_model_id`,
    `_prompt_version`, `_raw_response` als technische metadata.
    """
    if not image_bytes and not image_url:
        raise ValueError("image_bytes of image_url is verplicht")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _stub_result(asset_type)

    # Lazy import — anthropic is alleen nodig als API key gezet is
    try:
        import anthropic  # noqa: F401
    except ImportError:
        # Fallback naar HTTP-direct als SDK ontbreekt
        return _http_fallback(api_key, image_bytes, image_media_type, image_url,
                              asset_type, extra_context)

    return _via_sdk(api_key, image_bytes, image_media_type, image_url,
                    asset_type, extra_context)


def _user_content(image_bytes, image_media_type, image_url, extra_context):
    image_block: dict
    if image_bytes:
        image_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image_media_type or "image/jpeg",
                "data": base64.standard_b64encode(image_bytes).decode("ascii"),
            },
        }
    else:
        image_block = {"type": "image", "source": {"type": "url", "url": image_url}}

    instr = "Analyseer deze foto. Reageer met geldige JSON volgens het opgegeven schema."
    if extra_context:
        instr += f"\n\nExtra context van de inspecteur: {extra_context[:500]}"

    return [image_block, {"type": "text", "text": instr}]


def _via_sdk(api_key, image_bytes, image_media_type, image_url,
             asset_type, extra_context) -> InspectionResult:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    sys_prompt = _system_prompt(asset_type)

    msg = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=2048,
        system=[{"type": "text", "text": sys_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{
            "role": "user",
            "content": _user_content(image_bytes, image_media_type, image_url, extra_context),
        }],
    )
    raw_text = "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")
    parsed = _coerce_result(raw_text)
    out = InspectionResult(_validate(parsed))
    out["_model_id"] = msg.model
    out["_prompt_version"] = PROMPT_VERSION
    out["_raw_response"] = raw_text
    return out


def _http_fallback(api_key, image_bytes, image_media_type, image_url,
                   asset_type, extra_context) -> InspectionResult:
    """Direct HTTP-call als anthropic SDK niet geïnstalleerd is."""
    sys_prompt = _system_prompt(asset_type)
    body = {
        "model": DEFAULT_MODEL,
        "max_tokens": 2048,
        "system": [{"type": "text", "text": sys_prompt, "cache_control": {"type": "ephemeral"}}],
        "messages": [{
            "role": "user",
            "content": _user_content(image_bytes, image_media_type, image_url, extra_context),
        }],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    with httpx.Client(timeout=60.0) as cli:
        r = cli.post("https://api.anthropic.com/v1/messages", json=body, headers=headers)
        r.raise_for_status()
        data = r.json()
    raw_text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    parsed = _coerce_result(raw_text)
    out = InspectionResult(_validate(parsed))
    out["_model_id"] = data.get("model", DEFAULT_MODEL)
    out["_prompt_version"] = PROMPT_VERSION
    out["_raw_response"] = raw_text
    return out
