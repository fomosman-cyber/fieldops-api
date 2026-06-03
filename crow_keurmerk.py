"""CROW-keurmerk conformiteit — normdekking wegbeheersysteem.

Structureel, machine-leesbaar overzicht van welke NEN/CROW-normen FieldOps
implementeert, met bewijs (module + endpoint-groep + test) per criterium.

Dient als single source of truth voor:
  - GET /api/compliance/norm-conformance   (live, in-app + aanbestedingen)
  - het conformiteits-dossier voor de CROW-keurmerk-aanvraag (wegbeheersysteem)

Data-only (geen DB / FastAPI) zodat het los testbaar is. Houd dit eerlijk: elk
criterium verwijst naar code + tests die daadwerkelijk in de repo staan, zodat
een auditor het kan verifiëren.
"""
from __future__ import annotations

# Statuswaarden — bewust conservatief.
#   volledig   = norm-conform geïmplementeerd + getest
#   gedeeltelijk = kernfunctionaliteit aanwezig, uitbreiding gepland
STATUS_VOLLEDIG = "volledig"
STATUS_GEDEELTELIJK = "gedeeltelijk"

# Conformiteits-catalogus. Per criterium:
#   domein, norm, criterium (wat de norm vraagt), status,
#   modules (broncode), endpoints (API-groep), tests (bewijs).
CONFORMANCE: list[dict] = [
    {
        "domein": "Conditiemeting kunstwerken",
        "norm": "NEN 2767-2 + CROW 134",
        "criterium": "Element-decompositie per kunstwerk, gebrek-classificatie "
                     "(ernst × intensiteit × omvang) en conditiescore 1-6 via de "
                     "worst-element-regel.",
        "status": STATUS_VOLLEDIG,
        "modules": ["nen2767_scoring.py", "kunstwerken_taxonomy.py"],
        "endpoints": ["/api/kunstwerken-inspecties"],
        "tests": ["tests/test_kunstwerken_inspecties.py",
                  "tests/test_norm_specifieke_formulieren.py"],
    },
    {
        "domein": "Verharding-inspectie (wegen)",
        "norm": "CROW 146a/b",
        "criterium": "Visuele inspectie asfalt- en elementenverharding met "
                     "schadebeelden, ernst/omvang-klassen en maatregel-advies.",
        "status": STATUS_VOLLEDIG,
        "modules": ["crow_146.py", "kunstwerken_taxonomy.py"],
        "endpoints": ["/api/kunstwerken-inspecties", "/api/imports"],
        "tests": ["tests/test_kunstwerken_inspecties.py"],
    },
    {
        "domein": "Beeldkwaliteit openbare ruimte",
        "norm": "CROW-kwaliteitscatalogus (CAR) — beeldkwaliteit A+…D",
        "criterium": "Schouw van wegmeubilair op beeldkwaliteit met worst-aspect-"
                     "eindoordeel en toetsing aan een gemeentelijk ambitieniveau.",
        "status": STATUS_VOLLEDIG,
        "modules": ["car2023.py"],
        "endpoints": ["/api/car2023"],
        "tests": ["tests/test_car2023.py"],
    },
    {
        "domein": "Wegmarkering",
        "norm": "CROW 145",
        "criterium": "Retroreflectie-beoordeling (RL droog/nat) met vervang-drempels "
                     "voor wegmarkering.",
        "status": STATUS_VOLLEDIG,
        "modules": ["kunstwerken_taxonomy.py"],
        "endpoints": ["/api/kunstwerken-inspecties"],
        "tests": ["tests/test_norm_specifieke_formulieren.py"],
    },
    {
        "domein": "Openbare verlichting",
        "norm": "NEN 3140 (+ NEN 1010)",
        "criterium": "Elektrische keuring laagspanning: isolatieweerstand, "
                     "aardingsweerstand en aardlek-uitschakeltijden met norm-grenzen.",
        "status": STATUS_VOLLEDIG,
        "modules": ["nen_3140.py"],
        "endpoints": ["/api/kunstwerken-inspecties"],
        "tests": ["tests/test_speeltoestel_verlichting_mjop.py"],
    },
    {
        "domein": "Speeltoestellen",
        "norm": "NEN-EN 1176",
        "criterium": "Veiligheidsclassificatie (categorie A-D) met routine-, "
                     "operationele en jaarlijkse hoofdinspectie.",
        "status": STATUS_VOLLEDIG,
        "modules": ["nen_en_1176.py"],
        "endpoints": ["/api/kunstwerken-inspecties"],
        "tests": ["tests/test_speeltoestel_verlichting_mjop.py"],
    },
    {
        "domein": "Riolering",
        "norm": "NEN 3399 + NEN-EN 13508-2",
        "criterium": "Visuele riolering-inspectie met 3-letter schadecodes en "
                     "eindklasse 1-5 per streng.",
        "status": STATUS_VOLLEDIG,
        "modules": ["kunstwerken_taxonomy.py"],
        "endpoints": ["/api/kunstwerken-inspecties"],
        "tests": ["tests/test_norm_specifieke_formulieren.py"],
    },
    {
        "domein": "Inspectie-cyclus & planning",
        "norm": "NEN 2767-2 / CROW 134 / NEN 3399 / NEN-EN 1176 / NEN 3140",
        "criterium": "Norm-conforme inspectiefrequentie per asset-type en conditie, "
                     "met automatische agendering van de volgende inspectie.",
        "status": STATUS_VOLLEDIG,
        "modules": ["inspection_cycle.py"],
        "endpoints": ["/api/inspection-cycle"],
        "tests": ["tests/test_inspection_cycle.py"],
    },
    {
        "domein": "Meerjarenonderhoudsplan (MJOP)",
        "norm": "CROW 145 + GWW-kostengids 2024 + RAW",
        "criterium": "Maatregel + indicatieve kostenrange per (asset-type, conditie) "
                     "met meerjaren-horizon en export.",
        "status": STATUS_VOLLEDIG,
        "modules": ["mjop_kosten.py", "crow_kosten.py"],
        "endpoints": ["/api/mjop"],
        "tests": ["tests/test_speeltoestel_verlichting_mjop.py"],
    },
    {
        "domein": "Risicogestuurd beheer",
        "norm": "RAMS / ISO 31000",
        "criterium": "Risicoscore per asset uit faalkans × schadepotentieel × "
                     "blootstelling, voor prioritering van onderhoud.",
        "status": STATUS_VOLLEDIG,
        "modules": ["risico_matrix.py"],
        "endpoints": ["/api/risico"],
        "tests": ["tests/test_predictive.py"],
    },
    {
        "domein": "Predictief onderhoud",
        "norm": "NEN 2767-2 degradatieprognose",
        "criterium": "Voorspelling van conditie-degradatie (NEN 3→4) op basis van "
                     "leeftijd, conditie en meldingenhistorie, met trend en betrouwbaarheid.",
        "status": STATUS_VOLLEDIG,
        "modules": ["predictive.py"],
        "endpoints": ["/api/predictive"],
        "tests": ["tests/test_predictive.py", "tests/test_predictive_extras.py"],
    },
    {
        "domein": "Verkeersmaatregelen bij werkzaamheden",
        "norm": "CROW 96b",
        "criterium": "Standaard verkeersmaatregelen/afzettingen bij wegwerkzaamheden.",
        "status": STATUS_VOLLEDIG,
        "modules": ["crow96b_afzetting.py"],
        "endpoints": ["/api/kunstwerken-inspecties"],
        "tests": ["tests/test_norm_specifieke_formulieren.py"],
    },
    {
        "domein": "Data-uitwisseling & interoperabiliteit",
        "norm": "IMBOR + BGT + NWB",
        "criterium": "Asset-taxonomie volgens IMBOR, koppeling met BGT/NWB en import "
                     "van gemeente-export (GeoJSON/shapefile, RD→WGS84).",
        "status": STATUS_VOLLEDIG,
        "modules": ["imbor_taxonomy.py", "routers/imports_router.py", "nwb_integration.py"],
        "endpoints": ["/api/imbor", "/api/imports", "/api/nwb"],
        "tests": ["tests/test_imbor.py", "tests/test_imports.py", "tests/test_wegvakken.py"],
    },
    {
        "domein": "Traceerbaarheid & audit-trail",
        "norm": "Zorgplicht (art. 6:174 BW) — wie/wat/wanneer",
        "criterium": "Onveranderlijke audit-log van mutaties en een werkdagboek per "
                     "gebruiker, met organisatie-scope.",
        "status": STATUS_VOLLEDIG,
        "modules": ["audit.py", "daybook_logger.py"],
        "endpoints": ["/api/audit", "/api/daybook"],
        "tests": ["tests/test_audit.py"],
    },
    {
        "domein": "Informatiebeveiliging & AVG",
        "norm": "ISO 27001 + AVG/GDPR",
        "criterium": "Verwerkersovereenkomst, sub-processor-register, ISO 27001 "
                     "self-assessment en security-posture voor aanbestedingen.",
        "status": STATUS_VOLLEDIG,
        "modules": ["routers/compliance_router.py", "permissions.py"],
        "endpoints": ["/api/compliance"],
        "tests": ["tests/test_security.py", "tests/test_permissions.py"],
    },
]


def domeinen() -> list[str]:
    return [c["domein"] for c in CONFORMANCE]


def normen() -> list[str]:
    """Unieke norm-referenties (volgorde behouden)."""
    seen: list[str] = []
    for c in CONFORMANCE:
        if c["norm"] not in seen:
            seen.append(c["norm"])
    return seen


def summary() -> dict:
    """Telling per status + dekkingsgraad."""
    total = len(CONFORMANCE)
    volledig = sum(1 for c in CONFORMANCE if c["status"] == STATUS_VOLLEDIG)
    gedeeltelijk = sum(1 for c in CONFORMANCE if c["status"] == STATUS_GEDEELTELIJK)
    return {
        "criteria_totaal": total,
        "volledig": volledig,
        "gedeeltelijk": gedeeltelijk,
        "dekkingsgraad_pct": round(volledig / total * 100) if total else 0,
    }


def dossier() -> dict:
    """Volledig conformiteits-overzicht voor API + dossier-generatie."""
    return {
        "titel": "FieldOps — norm-conformiteit wegbeheersysteem",
        "samenvatting": summary(),
        "normen": normen(),
        "criteria": CONFORMANCE,
    }
