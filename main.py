from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path
from pydantic import BaseModel
import asyncio
import httpx
import os
from database import engine, Base, SessionLocal
from models import Organization, User, AccountStatus, SubscriptionPlan, UserRole
from auth import hash_password
from routers import auth_router, demo_router, users_router, org_router, shopify_router, admin_router, projects_router, meldingen_router, audit_router, assets_router, inspecties_router, webhooks_router, predictive_router, incoming_router, realtime_router, push_router, config_router, google_router, orchestration_router, microsoft_router, nwb_router, integrations_router, seo_router, opleveringen_router, kunstwerken_inspecties_router, inspection_cycle_router, mjop_router, risico_router, bag_router, iso55000_router, digigo_router, iot_router, proborm_router, damo_router, ai_photo_router, compliance_router, daybook_router, notifications_router
from audit import assign_request_id

# Maak alle tabellen aan
Base.metadata.create_all(bind=engine)


def _run_migrations():
    """Eenvoudige idempotente migraties voor nieuwe kolommen.

    SQLAlchemy create_all() maakt geen nieuwe kolommen aan op bestaande tabellen,
    dus we checken hier kolommen die later zijn toegevoegd.
    """
    from sqlalchemy import inspect, text
    try:
        insp = inspect(engine)
        # demo_requests.status (toegevoegd na initiele release)
        if "demo_requests" in insp.get_table_names():
            cols = [c["name"] for c in insp.get_columns("demo_requests")]
            if "status" not in cols:
                print("[migration] demo_requests.status kolom toevoegen...")
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE demo_requests ADD COLUMN status VARCHAR(20) DEFAULT 'pending'"))
                    # Mark bestaande verwerkte rijen als 'approved'
                    conn.execute(text("UPDATE demo_requests SET status = 'approved' WHERE processed = true"))
                print("[migration] demo_requests.status toegevoegd.")

        # users.must_change_password (toegevoegd voor force-reset bij eerste login)
        if "users" in insp.get_table_names():
            user_cols = [c["name"] for c in insp.get_columns("users")]
            if "must_change_password" not in user_cols:
                print("[migration] users.must_change_password kolom toevoegen...")
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN DEFAULT FALSE NOT NULL"))
                print("[migration] users.must_change_password toegevoegd.")

            # users.tokens_invalidated_at — JWT-revocation bij anonymisatie /
            # password-change / admin-deactivatie (sessie-hardening v3.4)
            if "tokens_invalidated_at" not in user_cols:
                print("[migration] users.tokens_invalidated_at kolom toevoegen...")
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE users ADD COLUMN tokens_invalidated_at TIMESTAMP"))
                print("[migration] users.tokens_invalidated_at toegevoegd.")

        # meldingen.asset_id (toegevoegd voor asset-management koppeling)
        if "meldingen" in insp.get_table_names():
            mcols = [c["name"] for c in insp.get_columns("meldingen")]
            if "asset_id" not in mcols:
                print("[migration] meldingen.asset_id kolom toevoegen...")
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE meldingen ADD COLUMN asset_id VARCHAR"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_meldingen_asset_id ON meldingen(asset_id)"))
                print("[migration] meldingen.asset_id toegevoegd.")

            # CROW 146 classificatie + GWWkosten-koppeling (v2.0-crow, mei 2026)
            crow_cols = {
                "crow_schadegroep":     "VARCHAR(40)",
                "crow_schadebeeld":     "VARCHAR(60)",
                "crow_ernst":           "VARCHAR(2)",
                "crow_omvang":          "VARCHAR(2)",
                "crow_klasse":          "VARCHAR(4)",
                "nen_2767_conditie":    "INTEGER",
                "onderhoud_categorie":  "VARCHAR(20)",
                "gw_maatregel":         "VARCHAR(120)",
                "gw_term":              "VARCHAR(160)",
                "gw_kosten_orde":       "VARCHAR(40)",
            }
            mcols = [c["name"] for c in insp.get_columns("meldingen")]  # refresh
            missing = [c for c in crow_cols if c not in mcols]
            if missing:
                print(f"[migration] meldingen CROW-kolommen toevoegen: {missing}")
                with engine.begin() as conn:
                    for col in missing:
                        conn.execute(text(f"ALTER TABLE meldingen ADD COLUMN {col} {crow_cols[col]}"))
                    # Indexen voor predictive + filtering
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_meldingen_crow_klasse ON meldingen(crow_klasse)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_meldingen_onderhoud_cat ON meldingen(onderhoud_categorie)"))
                print("[migration] meldingen CROW-kolommen toegevoegd.")

        # NWB-Wegvakken architectuur — assets uitgebreid met geometry + WVK_ID (v3.4)
        if "assets" in insp.get_table_names():
            acols = [c["name"] for c in insp.get_columns("assets")]
            nwb_cols = {
                "geometry_geojson":   "TEXT",
                "length_m":           "FLOAT",
                "is_segment":         "BOOLEAN DEFAULT 0 NOT NULL",
                "nwb_wvk_id":         "VARCHAR(32)",
                "nwb_wvk_begdat":     "TIMESTAMP",
                "nwb_jte_id_beg":     "VARCHAR(32)",
                "nwb_jte_id_end":     "VARCHAR(32)",
            }
            nwb_missing = [c for c in nwb_cols if c not in acols]
            if nwb_missing:
                print(f"[migration] assets NWB-kolommen toevoegen: {nwb_missing}")
                with engine.begin() as conn:
                    for col in nwb_missing:
                        # PostgreSQL: BOOLEAN DEFAULT FALSE NOT NULL syntax verschilt — handmatig
                        if col == "is_segment":
                            try:
                                conn.execute(text("ALTER TABLE assets ADD COLUMN is_segment BOOLEAN DEFAULT FALSE NOT NULL"))
                            except Exception:
                                # SQLite fallback
                                conn.execute(text("ALTER TABLE assets ADD COLUMN is_segment BOOLEAN DEFAULT 0 NOT NULL"))
                        else:
                            conn.execute(text(f"ALTER TABLE assets ADD COLUMN {col} {nwb_cols[col]}"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_assets_nwb_wvk_id ON assets(nwb_wvk_id)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_assets_is_segment ON assets(is_segment)"))
                print("[migration] assets NWB-kolommen toegevoegd.")

            # Inspectie-cyclus + levensduur kolommen (toegevoegd v3.5+)
            # Deze ontbraken op bestaande databases waardoor SELECT op assets
            # crasht met "column does not exist" — fix die mismatch.
            inspection_cols = {
                "installed_at":             "TIMESTAMP",
                "expected_lifespan_years":  "INTEGER",
                "condition_score":          "INTEGER",
                "last_inspection_at":       "TIMESTAMP",
                "last_inspection_id":       "VARCHAR",
                "next_inspection_due":      "TIMESTAMP",
                "inspection_cycle_months":  "INTEGER",
                "properties_json":          "TEXT",
            }
            insp_missing = [c for c in inspection_cols if c not in acols]
            if insp_missing:
                print(f"[migration] assets inspectie-kolommen toevoegen: {insp_missing}")
                with engine.begin() as conn:
                    for col in insp_missing:
                        conn.execute(text(f"ALTER TABLE assets ADD COLUMN {col} {inspection_cols[col]}"))
                    if "next_inspection_due" in insp_missing:
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_assets_next_inspection_due ON assets(next_inspection_due)"))
                print("[migration] assets inspectie-kolommen toegevoegd.")

        # Organisations — self-service contact-velden voor org-admin (v3.7)
        if "organizations" in insp.get_table_names():
            ocols = [c["name"] for c in insp.get_columns("organizations")]
            org_extra = {
                "contact_email":   "VARCHAR(255)",
                "contact_phone":   "VARCHAR(50)",
                "billing_address": "TEXT",
                "kvk_number":      "VARCHAR(20)",
                "btw_number":      "VARCHAR(30)",
                "logo_data_url":   "TEXT",
                "brand_color":     "VARCHAR(20)",
            }
            org_missing = [c for c in org_extra if c not in ocols]
            if org_missing:
                print(f"[migration] organizations contact-kolommen toevoegen: {org_missing}")
                from sqlalchemy import text as _sql_text
                with engine.begin() as conn:
                    for col in org_missing:
                        conn.execute(_sql_text(f"ALTER TABLE organizations ADD COLUMN {col} {org_extra[col]}"))

        # Photo-kolommen vergroten van VARCHAR(500) naar TEXT zodat
        # inline base64-data-URLs (foto's) zonder truncation worden opgeslagen.
        # Treft 3 tabellen: opleveringspunten, meldingen, inspection_defects.
        photo_col_targets = [
            ("opleveringspunten", "photo_url"),
            ("opleveringspunten", "photo_url_after"),
            ("meldingen",         "photo_url"),
            ("meldingen",         "photo_after_url"),
            ("inspection_defects","photo_url"),
            ("inspection_defects","photo_url_2"),
        ]
        try:
            from sqlalchemy import text as _sql_text
            with engine.begin() as conn:
                for tbl, col in photo_col_targets:
                    if tbl in insp.get_table_names():
                        # Postgres-only ALTER; sqlite negeert dit type-strict via try/except
                        try:
                            conn.execute(_sql_text(
                                f"ALTER TABLE {tbl} ALTER COLUMN {col} TYPE TEXT"
                            ))
                        except Exception as e:
                            # SQLite ondersteunt geen ALTER COLUMN TYPE — silent skip
                            # (SQLite kolommen zijn dynamisch typed, dus geen issue)
                            print(f"[migration] photo-col {tbl}.{col} skipped: {e}")
            print("[migration] photo-kolommen naar TEXT geupgraded (waar nodig).")
        except Exception as e:
            print(f"[migration] photo-cols upgrade error: {e}")

        # NEN-EN 1176 — speeltoestel-classificatie velden (v3.6)
        if "inspections" in insp.get_table_names():
            icols = [c["name"] for c in insp.get_columns("inspections")]
            if "nen1176_inspectie_kind" not in icols:
                print("[migration] inspections.nen1176_inspectie_kind toevoegen...")
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE inspections ADD COLUMN nen1176_inspectie_kind VARCHAR(16)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_inspections_nen1176_kind ON inspections(nen1176_inspectie_kind)"))

        if "inspection_defects" in insp.get_table_names():
            dcols = [c["name"] for c in insp.get_columns("inspection_defects")]
            # NEN-EN 1176 (speeltoestel)
            en1176_missing = []
            if "en1176_categorie" not in dcols:
                en1176_missing.append("en1176_categorie")
            if "en1176_acute_afsluiting" not in dcols:
                en1176_missing.append("en1176_acute_afsluiting")
            if en1176_missing:
                print(f"[migration] inspection_defects NEN-EN 1176 kolommen toevoegen: {en1176_missing}")
                with engine.begin() as conn:
                    if "en1176_categorie" in en1176_missing:
                        conn.execute(text("ALTER TABLE inspection_defects ADD COLUMN en1176_categorie VARCHAR(1)"))
                    if "en1176_acute_afsluiting" in en1176_missing:
                        try:
                            conn.execute(text("ALTER TABLE inspection_defects ADD COLUMN en1176_acute_afsluiting BOOLEAN DEFAULT FALSE NOT NULL"))
                        except Exception:
                            # SQLite fallback
                            conn.execute(text("ALTER TABLE inspection_defects ADD COLUMN en1176_acute_afsluiting BOOLEAN DEFAULT 0 NOT NULL"))

            # VTA (boom) + NEN 3140 (verlichting) + CROW 145 (markering) + NEN 3399 (riolering)
            type_specific_cols = {
                "vta_risicoklasse":              "INTEGER",
                "vta_holte_pct":                 "FLOAT",
                "vta_t_r_ratio":                 "FLOAT",
                "nen3140_isolatie_megaohm":      "FLOAT",
                "nen3140_aardingsweerstand_ohm": "FLOAT",
                "nen3140_aardlek_ms":            "INTEGER",
                "nen3140_aardlek_ma":            "FLOAT",
                "crow145_rl_droog_mcd":          "INTEGER",
                "crow145_rl_nat_mcd":            "INTEGER",
                "nen3399_code":                  "VARCHAR(4)",
                "nen3399_klasse":                "INTEGER",
                "nen3399_streng_id":             "VARCHAR(64)",
            }
            type_missing = [c for c in type_specific_cols if c not in dcols]
            if type_missing:
                print(f"[migration] inspection_defects type-specifieke kolommen toevoegen: {type_missing}")
                with engine.begin() as conn:
                    for col in type_missing:
                        conn.execute(text(f"ALTER TABLE inspection_defects ADD COLUMN {col} {type_specific_cols[col]}"))
                    if "nen3399_streng_id" in type_missing:
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_defects_nen3399_streng ON inspection_defects(nen3399_streng_id)"))

        # OpleveringPunt — photo_url_after (foto na uitvoering, toegevoegd 2026-05-11
        # voor voor/na-vergelijking bij opleverpunten)
        if "opleveringspunten" in insp.get_table_names():
            opcols = [c["name"] for c in insp.get_columns("opleveringspunten")]
            if "photo_url_after" not in opcols:
                print("[migration] opleveringspunten.photo_url_after kolom toevoegen...")
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE opleveringspunten ADD COLUMN photo_url_after VARCHAR(500)"))
                print("[migration] opleveringspunten.photo_url_after toegevoegd.")

        # Job Orchestration Engine — assigned_to + job_cluster_id op meldingen (v3.0)
        if "meldingen" in insp.get_table_names():
            mcols = [c["name"] for c in insp.get_columns("meldingen")]
            orch_cols = {
                "assigned_to":      "VARCHAR",
                "job_cluster_id":   "VARCHAR",
            }
            orch_missing = [c for c in orch_cols if c not in mcols]
            if orch_missing:
                print(f"[migration] meldingen orchestration-kolommen toevoegen: {orch_missing}")
                with engine.begin() as conn:
                    for col in orch_missing:
                        conn.execute(text(f"ALTER TABLE meldingen ADD COLUMN {col} {orch_cols[col]}"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_meldingen_assigned_to ON meldingen(assigned_to)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_meldingen_job_cluster_id ON meldingen(job_cluster_id)"))
                print("[migration] meldingen orchestration-kolommen toegevoegd.")

        # ai_analyses — zelfde CROW-kolommen + termijn (v2.0-crow)
        if "ai_analyses" in insp.get_table_names():
            ai_crow_cols = {
                "crow_schadegroep":     "VARCHAR(40)",
                "crow_schadebeeld":     "VARCHAR(60)",
                "crow_ernst":           "VARCHAR(2)",
                "crow_omvang":          "VARCHAR(2)",
                "crow_klasse":          "VARCHAR(4)",
                "nen_2767_conditie":    "INTEGER",
                "onderhoud_categorie":  "VARCHAR(20)",
                "gw_maatregel":         "VARCHAR(120)",
                "gw_term":              "VARCHAR(160)",
                "gw_kosten_orde":       "VARCHAR(40)",
                "termijn_weken":        "INTEGER",
            }
            acols = [c["name"] for c in insp.get_columns("ai_analyses")]
            ai_missing = [c for c in ai_crow_cols if c not in acols]
            if ai_missing:
                print(f"[migration] ai_analyses CROW-kolommen toevoegen: {ai_missing}")
                with engine.begin() as conn:
                    for col in ai_missing:
                        conn.execute(text(f"ALTER TABLE ai_analyses ADD COLUMN {col} {ai_crow_cols[col]}"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ai_analyses_crow_klasse ON ai_analyses(crow_klasse)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ai_analyses_onderhoud ON ai_analyses(onderhoud_categorie)"))
                print("[migration] ai_analyses CROW-kolommen toegevoegd.")
    except Exception as e:
        print(f"[migration] Waarschuwing: {e}")


_run_migrations()


async def keep_alive_ping():
    """Ping zichzelf elke 10 minuten om Render wake te houden."""
    url = os.environ.get("RENDER_EXTERNAL_URL", "")
    if not url:
        return  # Alleen actief op Render
    await asyncio.sleep(60)  # Wacht 1 min na startup
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await client.get(f"{url}/api/health", timeout=10)
                print("[keep-alive] ping OK")
            except Exception:
                pass
            await asyncio.sleep(600)  # Elke 10 min


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: maak owner account alleen aan als BOOTSTRAP_OWNER=true en password via env
    bootstrap = os.environ.get("BOOTSTRAP_OWNER", "").lower() == "true"
    owner_email = os.environ.get("OWNER_EMAIL", "fomosman@gmail.com")
    owner_password = os.environ.get("OWNER_PASSWORD", "")

    if bootstrap and owner_password:
        db = SessionLocal()
        try:
            existing = db.query(User).filter(User.email == owner_email).first()
            if not existing:
                org = Organization(
                    name="FieldOps",
                    plan=SubscriptionPlan.PROFESSIONAL,
                    status=AccountStatus.ACTIVE,
                    max_users=999,
                    trial_ends_at=None,
                )
                db.add(org)
                db.flush()
                user = User(
                    email=owner_email,
                    hashed_password=hash_password(owner_password),
                    first_name="Faris",
                    last_name="Osman",
                    role=UserRole.ADMIN,
                    is_active=True,
                    is_org_admin=True,
                    organization_id=org.id,
                )
                db.add(user)
                db.commit()
                print(f"Owner account aangemaakt: {owner_email}")
            else:
                print("Owner account bestaat al")
        finally:
            db.close()

    # Start keep-alive taak
    ping_task = asyncio.create_task(keep_alive_ping())
    yield
    ping_task.cancel()


_OPENAPI_DESCRIPTION = """\
**FieldOps API — Compliance-Native Infrastructure OS**

API voor Nederlandse gemeenten, aannemers en waterschappen om infra-veldwerk
te orchestreren met audit-bound AI, CROW/NEN-conformiteit en open integraties.

---

## 🔑 Authenticatie

Alle endpoints (behalve `/api/auth/login` en publieke OAuth-callbacks) vereisen
een Bearer token in de `Authorization` header:

```
Authorization: Bearer <jwt-token>
```

JWT verkrijgen via `POST /api/auth/login` met email + password.
Token TTL standaard 24u — refresh via re-login.

## 🏗️ Drie-lagen architectuur

| Layer | Endpoints | Doel |
|---|---|---|
| **Field Layer** | `/api/meldingen` · `/api/assets` · `/api/inspecties` | Veldwerk capture |
| **Compliance Layer** | `/api/audit` · `/api/predictive` · `/api/clusters` | AI-governance + orchestration |
| **Integration Fabric** | `/api/webhooks` · `/api/google` · `/api/microsoft` · `/api/incoming` | OAuth + events + IoT |

## 🇳🇱 Norm-conformiteit (live)

- **CROW 146a/b** — schadebeeld-classificatie + maatregeltabel
- **NEN 2767-2** — algemene conditiemeting (1-5 schaal)
- **GWWkosten-RAW** — formele maatregel-namen voor bestek

Roadmap (Q3-Q4 2026): NEN 3399 (riool) · NEN 2767-4 (kunstwerken) · VTA · EN 1176

## 🤖 Audit-Bound AI™

Elke AI-output wordt gelogd met `prompt_version`, `model_id`, `confidence` en
mens-acceptatie-state. Onveranderlijke audit-trail per organisatie.

## 🔒 Compliance & data-soevereiniteit

- EU-region hosting (Render Frankfurt)
- AVG/GDPR-compliant audit-log per organisatie + per persoon
- Append-only audit-events met IP + actor + before/after
- Encryption at rest + in transit (TLS 1.3)

## 📚 Meer

- **Whitepaper:** [De toekomst van infra-IT](https://portaal.fieldopsapp.nl/whitepaper)
- **Setup-gidsen:** RENDER-SETUP.md · MICROSOFT-SETUP.md
- **Status & support:** info@fieldopsapp.nl

---

**Categorie:** Compliance-Native Infrastructure OS
**Versie:** v3.3 (Microsoft 365 + Job Orchestration)
**Productie:** https://portaal.fieldopsapp.nl
"""

# OpenAPI tag-metadata — exact aligned op router-tag-namen.
# Volgorde bepaalt UI-display in /docs (FastAPI sorteert binnen tag op alpha).
_OPENAPI_TAGS = [
    {"name": "Authenticatie",          "description": "Login · password-reset · JWT-issuance · session-management."},
    {"name": "Gebruikers",             "description": "User-CRUD · rollen (8 archetypes) · must-change-password flow · skills."},
    {"name": "Organisatie",            "description": "Multi-tenant organisatie-beheer + sub-resources."},
    {"name": "Projecten",              "description": "Projecten — soft archive (default) + hard delete (?hard=true) met cascade-orphan."},
    {"name": "Assets",                 "description": "Asset-register · CSV-import met MOR+ alias-mapping · cascade-orphan delete · NEN 2767 conditie-veld."},
    {"name": "Meldingen",              "description": "Field-events met CROW 146 + NEN 2767 + GWWkosten-koppeling per record. Cluster-koppeling voor orchestration."},
    {"name": "AI-inspecties",          "description": "Audit-Bound AI™ vision-analyses · Claude vision · norm-bound prompts (CROW 146a v1.2) · mens-in-de-loop accept-flow."},
    {"name": "Kunstwerken-inspecties", "description": "Formele inspectierapportage volgens NEN 2767-2 + CROW 134 voor bruggen, viaducten, tunnels, sluizen, duikers, kademuren, gemalen. Element-decompositie · defect-classificatie (ernst × intensiteit × omvang) · conditiescore 1-6 · ondertekende PDF."},
    {"name": "Predictive Maintenance", "description": "Risk-Based Operations Model (4-factor): leeftijd × CROW-klasse × NEN-conditie × meldingen-historie."},
    {"name": "Job Orchestration",      "description": "Clustering van homogene maatregelen op gw_term + geo-proximity + skill-based assignment + productiviteit-savings dashboard."},
    {"name": "Webhooks",               "description": "HMAC-SHA256-signed webhook-out (Slack/Teams/eigen ERPs) + delivery-history + retry-mechanisme."},
    {"name": "IoT-incoming",           "description": "Inkomende events — IoT-sensoren · externe meldsystemen · MOR+ updates · drempelregel-builder (roadmap Q3)."},
    {"name": "Audit-log",              "description": "Onveranderlijk audit-log met norm-versie + IP + actor + before/after-snapshots. Procurement-grade onderbouwing voor Rekenkamer."},
    {"name": "Realtime",               "description": "WebSocket-events per organisatie voor live dashboard-updates · 3-fold fanout (DB + webhook + WebSocket + push)."},
    {"name": "Push notifications",     "description": "VAPID Web Push voor mobile + skill-based notification-routing — alleen specialisten + admins/managers krijgen relevante meldingen."},
    {"name": "NWB-Wegvakken",          "description": "Officiële NL wegvak-data via PDOK (Rijkswaterstaat). Zoek + bulk-import wegvakken met stabiele WVK_ID + LineString-geometry + lengte. Audit-grade voor aanbestedingen."},
    {"name": "Google",                 "description": "OAuth 2.0 (Workspace) + Calendar v3 + Drive API + Maps Places + Street View deeplinks."},
    {"name": "Microsoft",              "description": "OAuth 2.0 (Entra ID) + Microsoft Graph API · Outlook Calendar · OneDrive/SharePoint upload."},
    {"name": "Demo Aanvragen",         "description": "Public demo-aanvraag flow voor sales-leads."},
    {"name": "Admin",                  "description": "Platform-owner endpoints (cross-organisatie management)."},
    {"name": "Shopify Integratie",     "description": "Shopify cross-subdomain login-handoff voor www.fieldopsapp.nl ↔ portaal.fieldopsapp.nl."},
    {"name": "Config",                 "description": "Publieke configuratie (Google Maps key, feature flags) voor frontend."},
]


app = FastAPI(
    title="FieldOps API",
    description=_OPENAPI_DESCRIPTION,
    version="3.3.0",
    lifespan=lifespan,
    openapi_tags=_OPENAPI_TAGS,
    contact={
        "name": "Faris Osman — FieldOps",
        "email": "info@fieldopsapp.nl",
        "url": "https://fieldopsapp.nl",
    },
    license_info={
        "name": "Proprietary — © 2026 FieldOps",
        "url": "https://fieldopsapp.nl/terms",
    },
    servers=[
        {"url": "https://portaal.fieldopsapp.nl", "description": "Production (EU-region)"},
        {"url": "http://localhost:8001", "description": "Local development"},
    ],
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    swagger_ui_parameters={
        "syntaxHighlight.theme": "monokai",
        "docExpansion": "none",       # collapsed by default — sneller scrollen
        "filter": True,                # zoekbalk voor endpoints
        "displayRequestDuration": True,
        "tryItOutEnabled": True,
        "persistAuthorization": True,  # JWT blijft bewaard tijdens sessie
        "tagsSorter": "alpha",
        "operationsSorter": "alpha",
    },
)

# CORS — alleen vertrouwde origins. CORS_ORIGINS env voegt extras toe aan de default-lijst.
_default_origins = [
    "https://fieldopsapp.nl",
    "https://www.fieldopsapp.nl",
    "https://app.fieldopsapp.nl",
    "https://portaal.fieldopsapp.nl",
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:8001",
]
_extra = os.environ.get("CORS_ORIGINS", "")
allowed_origins = _default_origins + [o.strip() for o in _extra.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Global exception handler — vervangt anonieme "Internal Server Error"
# plain-text response met JSON zodat de frontend geen JSON-parse crash krijgt.
# Volledige traceback gaat naar stderr (Render logs); aan de client alleen
# een korte exception-class + bericht (geen file-paths, geen stack-frames).
# ─────────────────────────────────────────────────────────────────────────────
import traceback as _traceback
from fastapi.responses import JSONResponse as _JSONResponse


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    # Naar Render logs (volledige traceback voor diagnose)
    print(f"[UNCAUGHT] {request.method} {request.url.path}", flush=True)
    print(_traceback.format_exc(), flush=True)

    # Aan de client: minimaal — geen paden, geen stack-info
    return _JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {str(exc)[:200]}"},
    )


# Routers
app.include_router(auth_router.router)
app.include_router(demo_router.router)
app.include_router(users_router.router)
app.include_router(org_router.router)
app.include_router(shopify_router.router)
app.include_router(admin_router.router)
app.include_router(projects_router.router)
app.include_router(meldingen_router.router)
app.include_router(audit_router.router)
app.include_router(assets_router.router)
app.include_router(inspecties_router.router)
app.include_router(webhooks_router.router)
app.include_router(predictive_router.router)
app.include_router(incoming_router.router)
app.include_router(realtime_router.router)
app.include_router(push_router.router)
app.include_router(config_router.router)
app.include_router(google_router.router)
app.include_router(microsoft_router.router)
app.include_router(orchestration_router.router)
app.include_router(nwb_router.router)
app.include_router(integrations_router.router)
app.include_router(seo_router.router)
app.include_router(opleveringen_router.router)
app.include_router(kunstwerken_inspecties_router.router)
app.include_router(inspection_cycle_router.router)
app.include_router(mjop_router.router)
app.include_router(risico_router.router)
app.include_router(bag_router.router)
app.include_router(iso55000_router.router)
app.include_router(digigo_router.router)
app.include_router(iot_router.router)
app.include_router(proborm_router.router)
app.include_router(damo_router.router)
app.include_router(ai_photo_router.router)
app.include_router(compliance_router.router)
app.include_router(daybook_router.router)
app.include_router(notifications_router.router)


# Request-ID middleware — koppelt elke request aan een correlatie-ID dat
# in de audit-log terechtkomt. Klanten kunnen 'X-Request-Id' meesturen.
@app.middleware("http")
async def request_id_middleware(request, call_next):
    rid = assign_request_id(request)
    response = await call_next(request)
    response.headers["X-Request-Id"] = rid
    return response


# Security response headers — Mozilla Observatory / securityheaders.com baseline.
# CSP bewust niet meegenomen: portaal.html heeft 200+ inline handlers + 5 CDN's.
# Een strict CSP vereist aparte refactor en zou de portal breken.
_PERMISSIONS_POLICY = (
    "camera=(), microphone=(), geolocation=(self), payment=(), "
    "usb=(), magnetometer=(), gyroscope=(), accelerometer=(), "
    "fullscreen=(self)"
)
_IS_PRODUCTION = bool(os.environ.get("RENDER")) or os.environ.get("ENV") == "production"


@app.middleware("http")
async def security_headers_middleware(request, call_next):
    response = await call_next(request)
    # HSTS — alleen op productie (Render serveert TLS); zet niet op localhost
    # want dat zou je dev-cert in browser cachen voor een jaar.
    if _IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = _PERMISSIONS_POLICY
    # Cross-Origin-Opener-Policy: voorkom dat externe popups window.opener krijgen
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    return response

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# Mount static files (icons, manifest, etc.)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/manifest.webmanifest")
def manifest():
    """PWA manifest — vanuit root voor maximum scope."""
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/{doc}.md")
def serve_setup_doc(doc: str):
    """Serveer whitelisted setup-docs (GOOGLE-SETUP, MICROSOFT-SETUP, ...) als
    plain markdown. Toegankelijk vanuit de portaal-UI als CTA voor admins."""
    allowed = {"GOOGLE-SETUP", "MICROSOFT-SETUP", "RENDER-SETUP", "DEPLOYMENT", "IOS_BUILD_GUIDE"}
    if doc not in allowed:
        from fastapi import HTTPException as _HE
        raise _HE(status_code=404, detail="Document niet gevonden")
    file_path = Path(__file__).parent / f"{doc}.md"
    if not file_path.exists():
        from fastapi import HTTPException as _HE
        raise _HE(status_code=404, detail="Document niet gevonden")
    return FileResponse(file_path, media_type="text/markdown; charset=utf-8")


@app.get("/service-worker.js")
def service_worker():
    """Service worker — moet vanuit root komen voor scope '/'."""
    return FileResponse(
        STATIC_DIR / "service-worker.js",
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Service-Worker-Allowed": "/",
        },
    )


@app.get("/apple-touch-icon.png")
def apple_touch_icon():
    return FileResponse(STATIC_DIR / "icons" / "apple-touch-icon.png")


@app.get("/apple-touch-icon-precomposed.png")
def apple_touch_icon_precomposed():
    return FileResponse(STATIC_DIR / "icons" / "apple-touch-icon.png")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC_DIR / "icons" / "favicon-32.png")



@app.get("/")
def root(request: Request):
    """API root — browsers worden naar /portaal gestuurd; API-clients
    krijgen een JSON met platform-info + documentatie-links."""
    # Browser detection via Accept-header: 'text/html' = een browser-tab,
    # niet een API-call. Stuur ze door naar de portaal-UI.
    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/portaal", status_code=307)
    return {
        "app": "FieldOps API",
        "category": "Compliance-Native Infrastructure OS",
        "version": "3.3.0",
        "status": "online",
        "region": "EU-Frankfurt",
        "documentation": {
            "swagger_ui": "/docs",
            "redoc": "/redoc",
            "openapi_json": "/openapi.json",
            "developer_portal": "/developers",
            "whitepaper": "/whitepaper",
        },
        "compliance": {
            "norms_live": ["CROW 146a", "CROW 146b", "NEN 2767-2"],
            "norms_roadmap": ["NEN 3399", "NEN 2767-4", "VTA", "EN 1176", "ROVL"],
            "data_residency": "EU only",
            "audit_log": "append-only with IP + actor + before/after",
            "ai_governance": "Audit-Bound AI™ — prompt-version + model-id per record",
        },
        "integrations": {
            "live": ["Slack", "Teams", "Google Workspace", "Microsoft 365",
                     "Web Push (VAPID)", "WebSocket", "Webhook (HMAC-SHA256)",
                     "IoT inbound", "Anthropic Claude vision"],
        },
        "contact": "info@fieldopsapp.nl",
    }


@app.get("/developers", response_class=HTMLResponse)
def developer_portal():
    """Public developer portal — enterprise-grade landing voor API-docs.

    SEO-tags: structured data (Organization + SoftwareApplication +
    BreadcrumbList), Open Graph, Twitter Card, canonical. NL-locale want
    de doelgroep is Nederlandse infra-organisaties; zoekwoorden in title
    + description gericht op "CROW", "NEN 2767", "infra API NL".
    """
    return """<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FieldOps API — CROW & NEN 2767 conforme infra-API voor Nederland</title>
<meta name="description" content="REST-API voor Nederlandse infra-organisaties: CROW 146a/b, NEN 2767-2, Audit-Bound AI. Swagger UI, OpenAPI 3.1, EU-region hosting, OAuth 2.0.">
<meta name="keywords" content="CROW API, NEN 2767, infra-software Nederland, asset-management API, MOR-melding API, gemeente meldingen, predictive maintenance NL">
<meta name="robots" content="index, follow, max-image-preview:large">
<meta name="theme-color" content="#0a0f1e">
<link rel="canonical" href="https://portaal.fieldopsapp.nl/developers">

<meta property="og:type" content="website">
<meta property="og:url" content="https://portaal.fieldopsapp.nl/developers">
<meta property="og:site_name" content="FieldOps">
<meta property="og:title" content="FieldOps API — CROW & NEN 2767 conforme infra-API voor Nederland">
<meta property="og:description" content="REST-API voor Nederlandse infra-organisaties: CROW 146a/b, NEN 2767-2, Audit-Bound AI. Swagger UI · OpenAPI 3.1 · EU-region.">
<meta property="og:locale" content="nl_NL">
<meta property="og:image" content="https://portaal.fieldopsapp.nl/static/icons/icon-512.png">
<meta property="og:image:width" content="512">
<meta property="og:image:height" content="512">

<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="FieldOps API — Developers">
<meta name="twitter:description" content="REST-API voor Nederlandse infra-organisaties. CROW-conform · audit-bound · open standards.">
<meta name="twitter:image" content="https://portaal.fieldopsapp.nl/static/icons/icon-512.png">

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": "https://fieldopsapp.nl/#organization",
      "name": "FieldOps",
      "url": "https://fieldopsapp.nl",
      "logo": "https://portaal.fieldopsapp.nl/static/icons/icon-512.png",
      "sameAs": ["https://portaal.fieldopsapp.nl"],
      "areaServed": {"@type": "Country", "name": "Netherlands"},
      "contactPoint": {
        "@type": "ContactPoint",
        "email": "info@fieldopsapp.nl",
        "contactType": "sales"
      }
    },
    {
      "@type": "SoftwareApplication",
      "@id": "https://portaal.fieldopsapp.nl/developers#api",
      "name": "FieldOps API",
      "applicationCategory": "BusinessApplication",
      "applicationSubCategory": "Infrastructure Asset Management",
      "operatingSystem": "Web",
      "description": "REST-API voor Nederlands infra-veldwerk: meldingen, assets, AI-foto-inspecties met CROW 146a/b classificatie, predictive maintenance op NEN 2767-2, en append-only audit-log.",
      "url": "https://portaal.fieldopsapp.nl/developers",
      "offers": {"@type": "Offer", "price": "0", "priceCurrency": "EUR", "availability": "https://schema.org/InStock"},
      "publisher": {"@id": "https://fieldopsapp.nl/#organization"},
      "softwareVersion": "3.3.0",
      "featureList": ["CROW 146a/b classificatie", "NEN 2767-2 conditiescores", "Audit-Bound AI", "OAuth 2.0 (Google + Microsoft)", "HMAC-SHA256 webhooks", "WebSocket realtime", "EU-region hosting"]
    },
    {
      "@type": "BreadcrumbList",
      "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "FieldOps", "item": "https://fieldopsapp.nl"},
        {"@type": "ListItem", "position": 2, "name": "Developer Portal", "item": "https://portaal.fieldopsapp.nl/developers"}
      ]
    }
  ]
}
</script>

<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0a0f1e; --card:#0f172a; --border:#1e293b;
  --text:#f1f5f9; --text-muted:#94a3b8; --text-dim:#64748b;
  --blue:#0284c7; --blue-light:#38bdf8; --blue-bg:rgba(2,132,199,0.1);
  --green:#16a34a;
  --gradient:linear-gradient(135deg,#0284c7 0%,#16a34a 100%);
}
body{font-family:'DM Sans',system-ui,sans-serif;background:var(--bg);color:var(--text);line-height:1.6;-webkit-font-smoothing:antialiased;min-height:100vh}
a{color:var(--blue-light);text-decoration:none}
a:hover{color:#7dd3fc;text-decoration:underline}
code,pre{font-family:'JetBrains Mono',ui-monospace,monospace}

.container{max-width:1100px;margin:0 auto;padding:60px 32px}
.hero{padding:60px 0 40px;border-bottom:1px solid var(--border);margin-bottom:60px;position:relative;overflow:hidden}
.hero::before{content:'';position:absolute;top:-100px;right:-100px;width:600px;height:600px;border-radius:50%;background:radial-gradient(circle,rgba(2,132,199,0.15),transparent 65%);pointer-events:none}
.hero > *{position:relative;z-index:1}
.eyebrow{font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--blue-light);text-transform:uppercase;letter-spacing:0.18em;font-weight:600;margin-bottom:18px;display:inline-flex;align-items:center;gap:10px;background:var(--blue-bg);padding:8px 18px;border-radius:100px;border:1px solid rgba(2,132,199,0.3)}
.eyebrow .dot{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
h1{font-size:clamp(40px,5vw,64px);font-weight:800;line-height:1.05;letter-spacing:-0.025em;margin-bottom:20px;max-width:18ch}
h1 .grad{background:var(--gradient);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.subtitle{font-size:20px;color:var(--text-muted);max-width:600px;line-height:1.5;margin-bottom:32px}

.cta-row{display:flex;gap:12px;flex-wrap:wrap;margin-top:8px}
.btn{display:inline-flex;align-items:center;gap:10px;padding:14px 26px;border-radius:12px;font-size:15px;font-weight:600;transition:all 0.15s;text-decoration:none}
.btn-primary{background:var(--gradient);color:#fff;box-shadow:0 4px 18px rgba(2,132,199,0.30)}
.btn-primary:hover{transform:translateY(-1px);box-shadow:0 6px 24px rgba(2,132,199,0.45);text-decoration:none}
.btn-secondary{background:rgba(255,255,255,0.06);color:var(--text);border:1px solid var(--border)}
.btn-secondary:hover{border-color:var(--blue-light);background:rgba(2,132,199,0.08);text-decoration:none}

h2{font-size:32px;font-weight:700;margin:60px 0 20px;letter-spacing:-0.02em}
.section-eyebrow{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--blue-light);text-transform:uppercase;letter-spacing:0.18em;font-weight:600;margin-bottom:8px}

.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px;margin-bottom:32px}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:24px;transition:border-color 0.15s}
.card:hover{border-color:var(--blue)}
.card h3{font-size:18px;font-weight:700;margin-bottom:8px;display:flex;align-items:center;gap:10px}
.card h3 .icon{font-size:22px}
.card p{font-size:14px;color:var(--text-muted);line-height:1.6}
.card .endpoint{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--blue-light);background:rgba(2,132,199,0.08);padding:3px 10px;border-radius:6px;display:inline-block;margin-top:10px}

.code-block{background:#020617;border:1px solid var(--border);border-radius:10px;padding:18px 22px;font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--text);margin:18px 0;overflow-x:auto}
.code-block .key{color:#7dd3fc}
.code-block .str{color:#86efac}
.code-block .comment{color:var(--text-dim)}

.badges{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}
.badge{font-family:'JetBrains Mono',monospace;font-size:11px;padding:5px 12px;border-radius:100px;background:rgba(255,255,255,0.05);border:1px solid var(--border);color:var(--text-muted);font-weight:500}
.badge.green{background:rgba(22,163,74,0.10);color:#86efac;border-color:rgba(22,163,74,0.30)}
.badge.blue{background:rgba(2,132,199,0.10);color:var(--blue-light);border-color:rgba(2,132,199,0.30)}

footer{margin-top:80px;padding-top:30px;border-top:1px solid var(--border);color:var(--text-dim);font-size:13px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:16px}
footer a{color:var(--text-muted)}
</style>
</head>
<body>
<div class="container">

<div class="hero">
  <div class="eyebrow"><span class="dot"></span> API · v3.3 · production · EU-region</div>
  <h1>De <span class="grad">FieldOps API</span> — voor wie integreert</h1>
  <p class="subtitle">REST-API voor Nederlandse infra-organisaties. CROW-conform · audit-bound · open standards.</p>
  <div class="cta-row">
    <a href="/docs" class="btn btn-primary">📖 Swagger UI</a>
    <a href="/redoc" class="btn btn-secondary">ReDoc</a>
    <a href="/openapi.json" class="btn btn-secondary">OpenAPI 3.1 JSON</a>
    <a href="/whitepaper" class="btn btn-secondary">📄 Whitepaper</a>
  </div>
  <div class="badges">
    <span class="badge green">95+ endpoints</span>
    <span class="badge green">120+ tests passing</span>
    <span class="badge blue">OAuth 2.0 (Google + Microsoft)</span>
    <span class="badge blue">HMAC-SHA256 webhooks</span>
    <span class="badge blue">WebSocket realtime</span>
    <span class="badge">JWT auth (24h TTL)</span>
  </div>
</div>

<div class="section-eyebrow">Quick Start</div>
<h2>Authenticatie in 3 stappen</h2>
<div class="code-block">
<span class="comment"># 1. Login → JWT-token</span>
curl -X POST https://portaal.fieldopsapp.nl/api/auth/login \\
  -H "Content-Type: application/json" \\
  -d '{"email":"<span class="str">jij@bedrijf.nl</span>","password":"<span class="str">...</span>"}'

<span class="comment"># Response: { "access_token": "eyJ...", "token_type": "bearer" }</span>

<span class="comment"># 2. Authorize alle vervolg-calls</span>
curl https://portaal.fieldopsapp.nl/api/meldingen/ \\
  -H "Authorization: <span class="str">Bearer eyJ...</span>"

<span class="comment"># 3. Of: probeer 't direct in Swagger UI</span>
<span class="key">→</span> <a href="/docs">https://portaal.fieldopsapp.nl/docs</a>
</div>

<div class="section-eyebrow">Drie-lagen Architectuur</div>
<h2>API ingedeeld per laag</h2>
<div class="grid">
  <div class="card">
    <h3><span class="icon">📱</span> Field Layer</h3>
    <p>Veldwerk capture vanaf mobiel: meldingen, assets, AI-inspecties met foto-upload.</p>
    <span class="endpoint">/api/meldingen · /api/assets · /api/inspecties</span>
  </div>
  <div class="card">
    <h3><span class="icon">🏛️</span> Compliance Layer</h3>
    <p>Audit-Bound AI™, predictive maintenance, job orchestration, onveranderlijk audit-log.</p>
    <span class="endpoint">/api/audit · /api/predictive · /api/clusters</span>
  </div>
  <div class="card">
    <h3><span class="icon">🔗</span> Integration Fabric</h3>
    <p>OAuth 2.0 (Google + Microsoft), HMAC-webhooks, WebSocket events, IoT-bridges.</p>
    <span class="endpoint">/api/google · /api/microsoft · /api/webhooks</span>
  </div>
</div>

<div class="section-eyebrow">Compliance & Governance</div>
<h2>Wat onder elke API-call ligt</h2>
<div class="grid">
  <div class="card">
    <h3>🇳🇱 Norm-conformiteit</h3>
    <p><strong>Live:</strong> CROW 146a/b · NEN 2767-2 · GWWkosten-RAW maatregel-namen.<br><strong>Roadmap Q3-Q4:</strong> NEN 3399 · NEN 2767-4 · VTA · EN 1176 · ROVL.</p>
  </div>
  <div class="card">
    <h3>🔐 Audit-Bound AI™</h3>
    <p>Elke AI-output: <code>prompt_version</code>, <code>model_id</code>, <code>confidence</code>, mens-acceptatie. Onveranderlijk in audit-log.</p>
  </div>
  <div class="card">
    <h3>🏛️ Append-only audit-log</h3>
    <p>Per-event <code>request_id</code>, IP, actor, before/after-snapshot. Procurement-grade onderbouwing voor Rekenkamer.</p>
  </div>
  <div class="card">
    <h3>🇪🇺 EU data-soevereiniteit</h3>
    <p>Alle data EU-Frankfurt hosting. Sub-processors gepubliceerd. AVG/GDPR-DPA template beschikbaar.</p>
  </div>
</div>

<div class="section-eyebrow">Integraties (live)</div>
<h2>Open standards, geen ommuurde tuin</h2>
<div class="badges">
  <span class="badge green">Slack (HMAC-webhook)</span>
  <span class="badge green">Microsoft Teams (HMAC-webhook)</span>
  <span class="badge green">Google Calendar v3</span>
  <span class="badge green">Google Drive v3</span>
  <span class="badge green">Microsoft Outlook (Graph)</span>
  <span class="badge green">OneDrive (Graph)</span>
  <span class="badge green">Web Push (VAPID)</span>
  <span class="badge green">WebSocket realtime</span>
  <span class="badge green">IoT webhook in</span>
  <span class="badge green">Anthropic Claude vision</span>
  <span class="badge green">CSV/MOR+ import</span>
</div>

<div class="section-eyebrow">Voorbeeld</div>
<h2>AI-melding aanmaken via API</h2>
<div class="code-block">
<span class="comment"># Upload foto + krijg CROW-classificatie</span>
curl -X POST https://portaal.fieldopsapp.nl/api/inspecties/analyse-foto \\
  -H "Authorization: Bearer eyJ..." \\
  -F "file=@<span class="str">scheur.jpg</span>" \\
  -F "asset_type=wegdek"

<span class="comment"># Response (uitgekort):</span>
{
  <span class="key">"crow_klasse"</span>: <span class="str">"M2"</span>,
  <span class="key">"crow_schadebeeld"</span>: <span class="str">"scheurvorming-langs"</span>,
  <span class="key">"onderhoud_categorie"</span>: <span class="str">"KO"</span>,
  <span class="key">"gw_maatregel"</span>: <span class="str">"Vullen polymeer"</span>,
  <span class="key">"gw_kosten_orde"</span>: <span class="str">"€8–15 / m¹"</span>,
  <span class="key">"termijn_weken"</span>: 24,
  <span class="key">"prompt_version"</span>: <span class="str">"v2.0-crow"</span>,
  <span class="key">"model_id"</span>: <span class="str">"claude-sonnet-4-6"</span>,
  <span class="key">"confidence"</span>: 0.91
}
</div>

<footer>
  <div>© 2026 FieldOps · Compliance-Native Infrastructure OS</div>
  <div>
    <a href="https://fieldopsapp.nl">Website</a> ·
    <a href="/whitepaper">Whitepaper</a> ·
    <a href="mailto:info@fieldopsapp.nl">Contact</a>
  </div>
</footer>

</div>
</body>
</html>"""


@app.get("/whitepaper")
@app.get("/whitepaper.pdf")
def whitepaper_download(request: Request):
    """
    Lead-magnet: serveert de FieldOps whitepaper-PDF met een nette filename
    en optionele tracking. Gebruik vanaf de landingpage:
      <a href="https://portaal.fieldopsapp.nl/whitepaper">Download whitepaper</a>
    """
    pdf_path = STATIC_DIR / "downloads" / "fieldops-whitepaper-2026.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Whitepaper not found")

    # Lichte tracking — log de download voor analytics later
    try:
        ua = request.headers.get("user-agent", "")[:200]
        ref = request.headers.get("referer", "")[:200]
        ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
        print(f"[whitepaper-download] ua={ua!r} ref={ref!r} ip={ip!r}")
    except Exception:
        pass

    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename="FieldOps-Whitepaper-2026.pdf",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Content-Disposition": 'attachment; filename="FieldOps-Whitepaper-2026.pdf"',
        },
    )


class ContactRequest(BaseModel):
    name: str
    email: str
    message: str


@app.post("/api/contact")
def contact_form(req: ContactRequest, request: Request):
    """Ontvang contactformulier en stuur notificatie email."""
    from auth import check_public_post_rate_limit
    from audit import log_action, ACTION
    from models import AuditLog
    from datetime import datetime, timezone

    # Anti-spam: 3 per email of 10 per IP per uur. Voorkomt mailbom + quota-uit.
    db = SessionLocal()
    try:
        check_public_post_rate_limit(
            db, action=ACTION.CONTACT_SUBMIT, request=request,
            email=req.email, per_email=3, per_ip=10, window_min=60,
        )
        # Log eerst — anders telt deze submit niet voor de volgende rate-check
        rec = AuditLog(
            user_email=req.email,
            action=ACTION.CONTACT_SUBMIT,
            ip_address=request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                       or (request.client.host if request.client else None),
            user_agent=(request.headers.get("user-agent") or "")[:512],
            details=None,
        )
        db.add(rec); db.commit()
    finally:
        db.close()

    from email_service import send_email, _base_template
    content = f"""
<h2 style="color:#1e293b;font-size:22px;margin:0 0 8px;">Nieuw contactbericht</h2>
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border-radius:12px;margin-bottom:24px;">
<tr><td style="padding:20px 24px;">
<p style="margin:0 0 12px;"><strong>Naam:</strong> {req.name}</p>
<p style="margin:0 0 12px;"><strong>E-mail:</strong> {req.email}</p>
<p style="margin:0;"><strong>Bericht:</strong><br>{req.message}</p>
</td></tr></table>
<p style="color:#94a3b8;font-size:13px;">Reageer rechtstreeks naar {req.email}</p>"""
    send_email("info@fieldopsapp.nl", f"Contactformulier: {req.name}", _base_template(content, "Nieuw bericht"))
    return {"message": "Bericht ontvangen"}


@app.get("/portaal", response_class=HTMLResponse)
def portaal():
    """Serve de FieldOps portaal SPA."""
    html = (TEMPLATES_DIR / "portaal.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/reset-wachtwoord", response_class=HTMLResponse)
def reset_wachtwoord():
    """Serve de wachtwoord-reset pagina."""
    html = (TEMPLATES_DIR / "reset-password.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/handleiding", response_class=HTMLResponse)
def handleiding():
    """Publieke handleiding — bereikbaar zonder login. Linkt vanuit portaal-
    header (❓-knop) en marketing-site footer."""
    html = (TEMPLATES_DIR / "handleiding.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/api/health")
def health_check():
    return {"status": "ok"}



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
