"""Compliance-documenten endpoint — voor Enterprise klanten.

Geeft sub-processor lijst, AVG-status, ISO 27001 self-assessment, en
download-links voor de 4 enterprise-documenten:
  - DPA / Verwerkersovereenkomst
  - ISO 27001 SOA
  - Sub-processor lijst (machine-readable)
  - Incident response procedure

Endpoints:
  GET /api/compliance/status            Overzicht compliance-status
  GET /api/compliance/sub-processors    Live sub-processor lijst (JSON)
  GET /api/compliance/documents         Download-links naar PDFs
  GET /api/compliance/security-posture  Security-overzicht voor aanbestedingen
"""
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

import crow_keurmerk
from models import User
from auth import get_current_user

router = APIRouter(prefix="/api/compliance", tags=["Compliance"])


SUB_PROCESSORS = [
    {
        "name": "Render Services Inc.",
        "category": "Hosting (application + database)",
        "hq": "San Francisco, US",
        "data_location": "Frankfurt EU (eu-central-1)",
        "purpose": "Application + database hosting",
        "data_categories": ["all"],
        "dpa_signed": True,
        "scc_in_place": True,
        "certifications": ["SOC 2 Type II", "ISO 27001", "ISO 27017", "ISO 27018", "HIPAA"],
        "policy_url": "https://render.com/privacy",
        "since": "2024-01",
    },
    {
        "name": "Cloudflare Inc.",
        "category": "CDN + DNS + DDoS protection",
        "hq": "San Francisco, US",
        "data_location": "EU-only routing for *.fieldopsapp.nl",
        "purpose": "DNS resolution, DDoS protection, edge caching",
        "data_categories": ["IP addresses", "request headers"],
        "dpa_signed": True,
        "scc_in_place": True,
        "certifications": ["SOC 2 Type II", "ISO 27001", "ISO 27018"],
        "policy_url": "https://www.cloudflare.com/privacypolicy",
        "since": "2024-02",
    },
    {
        "name": "Amazon Web Services (AWS)",
        "category": "Object storage (S3)",
        "hq": "Seattle, US",
        "data_location": "Frankfurt EU (eu-central-1)",
        "purpose": "Photo + PDF report storage",
        "data_categories": ["files: photos, PDF reports"],
        "dpa_signed": True,
        "scc_in_place": True,
        "certifications": ["SOC 1/2/3", "ISO 27001", "ISO 27017", "ISO 27018",
                          "PCI-DSS", "NEN 7510"],
        "policy_url": "https://aws.amazon.com/compliance/data-privacy/",
        "since": "2024-01",
    },
    {
        "name": "PDOK (Kadaster)",
        "category": "Open-data geo-services",
        "hq": "Den Haag, NL",
        "data_location": "Netherlands",
        "purpose": "BAG addresses, NWB road segments, DAMO water authority",
        "data_categories": ["none - outgoing queries only, no PII"],
        "dpa_signed": False,
        "scc_in_place": False,
        "note": "Public service, no DPA required",
        "certifications": ["ISO 27001 (Kadaster)", "BIO (Dutch gov standard)"],
        "policy_url": "https://www.pdok.nl/",
        "since": "2026-05",
    },
    {
        "name": "Resend Inc.",
        "category": "Transactional email",
        "hq": "San Francisco, US",
        "data_location": "Frankfurt EU sending region",
        "purpose": "Login, demo requests, notifications",
        "data_categories": ["email address", "name", "message content"],
        "dpa_signed": True,
        "scc_in_place": True,
        "certifications": ["SOC 2 Type II"],
        "policy_url": "https://resend.com/legal/privacy-policy",
        "since": "2024-03",
    },
    {
        "name": "Sentry",
        "category": "Error tracking + performance monitoring",
        "hq": "San Francisco, US",
        "data_location": "Frankfurt EU region",
        "purpose": "Crash reports + performance metrics for debugging",
        "data_categories": ["stack traces", "hashed user IDs", "browser info"],
        "dpa_signed": True,
        "scc_in_place": True,
        "certifications": ["SOC 2 Type II", "ISO 27001 (in progress)"],
        "policy_url": "https://sentry.io/privacy/",
        "since": "2024-04",
    },
]


@router.get("/status")
def compliance_status(current_user: User = Depends(get_current_user)):
    """Compliance-overzicht voor klanten + auditeurs."""
    return {
        "organization": "FieldOps B.V.",
        "data_location": "EU only (Frankfurt eu-central-1)",
        "avg_compliance": {
            "status": "Conform",
            "controller_processor_relationship": "FieldOps acts as Processor (AVG art. 28)",
            "dpa_template_available": True,
            "dpa_template_version": "1.0-2026-05",
            "dpia_assistance": "Available on request",
            "data_subject_rights_supported": ["access", "rectification",
                                              "erasure", "portability", "objection"],
        },
        "iso27001": {
            "status": "ISO 27001:2022 aligned (self-assessment, not yet certified)",
            "soa_version": "1.0-2026-05",
            "controls_implemented_pct": 82,
            "controls_in_progress_pct": 16,
            "certification_target": "Q3 2027",
            "stage_1_audit_planned": "Q1 2027",
        },
        "sub_processors_count": len(SUB_PROCESSORS),
        "incident_response": {
            "procedure_documented": True,
            "procedure_version": "1.0-2026-05",
            "tested_table_top": "Pending Q3 2026",
            "red_team_exercise": "Planned Q3 2026",
            "datalek_meldtijd": "24u to controller, 72u to AP via controller (AVG art. 33)",
        },
        "audit_trail": {
            "type": "append-only",
            "retention_years": 5,
            "export_formats": ["CSV", "PDF"],
            "scope": "all business mutations + admin actions",
        },
        "encryption": {
            "in_transit": "TLS 1.3",
            "at_rest": "AES-256 (Render managed disk/DB)",
            "passwords": "Bcrypt cost 12",
            "tokens": "JWT short-lived (1u) + refresh; OAuth-tokens app-level versleuteld (Fernet: AES-128-CBC + HMAC-SHA256)",
        },
        "backups": {
            "frequency": "daily",
            "retention_days": 30,
            "cross_region_replication": True,
            "rto_hours": 4,
            "rpo_hours": 1,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/sub-processors")
def sub_processors(current_user: User = Depends(get_current_user)):
    """Live sub-processor lijst (machine-readable)."""
    return {
        "version": "1.0-2026-05",
        "last_updated": "2026-05-15",
        "next_review": "2026-08-15",
        "count": len(SUB_PROCESSORS),
        "sub_processors": SUB_PROCESSORS,
        "change_notification_days": 14,
        "objection_email": "privacy@fieldopsapp.nl",
    }


@router.get("/documents")
def list_documents(current_user: User = Depends(get_current_user)):
    """Download-links naar Enterprise compliance-documenten."""
    return {
        "documents": [
            {
                "id": "dpa",
                "title": "Verwerkersovereenkomst (DPA)",
                "description": "Volledige AVG art. 28 conform DPA-template, klaar voor ondertekening",
                "format": ["MD", "PDF (op verzoek)"],
                "version": "1.0-2026-05",
                "based_on": "EU Standard Contractual Clauses Module 2",
                "availability": "Op aanvraag via compliance@fieldopsapp.nl",
            },
            {
                "id": "iso27001-soa",
                "title": "ISO 27001:2022 Statement of Applicability",
                "description": "93 Annex A controls met implementatie-status (82% volledig)",
                "format": ["MD"],
                "version": "1.0-2026-05",
                "status": "Self-assessment (Pad B)",
                "availability": "Op aanvraag via compliance@fieldopsapp.nl",
            },
            {
                "id": "sub-processors",
                "title": "Sub-processor lijst",
                "description": "Live document van alle externe partijen + AVG-status",
                "format": ["MD", "JSON via API"],
                "version": "1.0-2026-05",
                "machine_readable_url": "/api/compliance/sub-processors",
            },
            {
                "id": "incident-response",
                "title": "Incident Response Procedure",
                "description": "Volledige procedure voor security-incidents + datalekken (AVG art. 33)",
                "format": ["MD", "PDF (op verzoek)"],
                "version": "1.0-2026-05",
                "availability": "Op aanvraag via compliance@fieldopsapp.nl",
            },
        ],
        "request_signed_copies": "compliance@fieldopsapp.nl",
        "note": "PDFs op verzoek beschikbaar; markdown versies in marketing/compliance/ repo-folder.",
    }


@router.get("/iso-readiness")
def iso_readiness(current_user: User = Depends(get_current_user)):
    """ISO 27001:2022 readiness self-assessment.

    Geeft maturity per categorie + roadmap naar formele certificering.
    Voor sales pre-empt op enterprise-aanbestedings-vragen.
    """
    return {
        "iso_27001_status": "Pad B — aligned (self-assessment), not yet certified",
        "certification_target": "Q3 2027",
        "overall_maturity_pct": 82,
        "by_category": [
            {"category": "A.5 Organizational", "total": 37,
             "implemented_pct": 70, "formalizing_pct": 27, "planned_pct": 3},
            {"category": "A.6 People", "total": 8,
             "implemented_pct": 100, "formalizing_pct": 0, "planned_pct": 0},
            {"category": "A.7 Physical (via Render+AWS)", "total": 14,
             "implemented_pct": 100, "formalizing_pct": 0, "planned_pct": 0,
             "note": "Outsourced to ISO 27001+SOC 2 certified sub-processors"},
            {"category": "A.8 Technological", "total": 34,
             "implemented_pct": 82, "formalizing_pct": 15, "planned_pct": 0},
        ],
        "totals": {
            "controls_total": 93,
            "fully_implemented": 76,
            "being_formalized": 15,
            "planned": 1,
            "not_applicable": 1,
        },
        "roadmap": [
            {"quarter": "Q3 2026", "milestone": "External pentest + bug-bounty launch",
             "cost_estimate_eur": "10-17k"},
            {"quarter": "Q4 2026", "milestone": "ISMS-policy full expansion + internal mock audit",
             "cost_estimate_eur": "0-5k"},
            {"quarter": "Q1 2027", "milestone": "Stage 1 audit (DEKRA/Lloyd's/Kiwa)",
             "cost_estimate_eur": "9-18k"},
            {"quarter": "Q2 2027", "milestone": "Stage 2 audit",
             "cost_estimate_eur": "6-12k"},
            {"quarter": "Q3 2027", "milestone": "Certificate issued + marketing-update",
             "cost_estimate_eur": "0-1k"},
        ],
        "total_cost_pad_a_eur": "27-70k one-time + 5-10k/year surveillance",
        "what_we_can_share_now": [
            "Risk Assessment 2026",
            "ISO 27001:2022 Statement of Applicability",
            "Self-Assessment Report",
            "10 ISMS Policies (full text)",
            "DPA template + Sub-processor list",
            "Incident Response Procedure",
            "Penetration Test results (when available, Q3 2026)",
        ],
        "sectoral_compliance": {
            "AVG_GDPR": "Conform — DPA + SCC + EU-hosting",
            "BIO": "Inherent via ISO 27001 alignment",
            "NEN_7510": "Not applicable (no health data)",
            "NIS2": "Scope-check Q3 2026 — likely 'important entity' classification",
            "CSP_NEN": "Optional add-on Q1 2027",
        },
        "request_audit_pack_email": "compliance@fieldopsapp.nl",
        "documentation_repo": "https://github.com/fomosman-cyber/fieldops-api (private, on request)",
    }


@router.get("/security-posture")
def security_posture(current_user: User = Depends(get_current_user)):
    """Compact security-overzicht voor aanbestedings-bijlage."""
    return {
        "organization": "FieldOps B.V.",
        "scope": "FieldOps Platform — SaaS voor publieke ruimte beheer",
        "data_residency": {
            "primary_region": "Frankfurt EU (eu-central-1)",
            "backup_region": "EU west (Ireland) — cross-region replication",
            "outside_eer": "None for production data; SCCs in place for US sub-processors",
        },
        "encryption": {
            "tls_version_minimum": "1.3",
            "database_encryption": "AES-256 at rest (Render managed disk/DB)",
            "sensitive_tokens_app_level": "OAuth-tokens versleuteld met Fernet (AES-128-CBC + HMAC-SHA256)",
            "password_hash": "Bcrypt cost 12",
            "key_management": "Render managed (HSM-backed)",
        },
        "access_control": {
            "rbac": True,
            "multi_factor_auth": "Beschikbaar (TOTP) — niet afgedwongen per plan",
            "sso_saml": "Op de roadmap — nog niet beschikbaar",
            "session_timeout_min": 30,
            "password_policy_strength": "12 chars + 3 of 4 character classes",
            "failed_login_lockout": "5 attempts → 15 min lockout",
        },
        "monitoring": {
            "audit_log": "Append-only, 5-year retention",
            "anomaly_detection": True,
            "rate_limiting": "100 req/min per IP for public endpoints",
            "vulnerability_scanning": "Weekly (Dependabot + Snyk)",
            "penetration_testing": "Annual external (Q3 2026 planned)",
        },
        "incident_response": {
            "documented_procedure": True,
            "incident_commander_24x7": True,
            "datalek_notification_to_customer_max": "24 hours",
            "datalek_notification_to_ap_max": "72 hours (via customer as controller)",
        },
        "business_continuity": {
            "rto_hours": 4,
            "rpo_hours": 1,
            "backup_frequency": "daily",
            "backup_retention_days": 30,
            "disaster_recovery_tested": "Quarterly",
        },
        "compliance_certifications": {
            "avg_gdpr": "Compliant — DPA template + Sub-processor list + SCCs",
            "iso_27001": "Aligned (self-assessment, certification target Q3 2027)",
            "iso_27017_cloud": "Aligned via Render + AWS sub-processors",
            "iso_27018_pii": "Aligned via sub-processors",
            "nen_7510_healthcare": "Not applicable (no health data)",
            "soc_2": "Via sub-processors (Render, Cloudflare, AWS, Resend)",
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Detailed documentation available on request: compliance@fieldopsapp.nl",
    }


@router.get("/norm-conformance")
def norm_conformance(current_user: User = Depends(get_current_user)):
    """Norm-conformiteit wegbeheersysteem (NEN/CROW) — live, machine-leesbaar.

    Bron voor het CROW-keurmerk-dossier en voor aanbestedingen: per criterium de
    norm, status en het bewijs (module + endpoint-groep + test in de repo)."""
    data = crow_keurmerk.dossier()
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    return data
