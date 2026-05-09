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
from routers import auth_router, demo_router, users_router, org_router, shopify_router, admin_router, projects_router, meldingen_router, audit_router, assets_router, inspecties_router, webhooks_router, predictive_router, incoming_router, realtime_router, push_router, config_router, google_router, orchestration_router
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


app = FastAPI(
    title="FieldOps API",
    description="Backend API voor FieldOps - Veldregistratie platform",
    version="1.0.0",
    lifespan=lifespan,
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
app.include_router(orchestration_router.router)


# Request-ID middleware — koppelt elke request aan een correlatie-ID dat
# in de audit-log terechtkomt. Klanten kunnen 'X-Request-Id' meesturen.
@app.middleware("http")
async def request_id_middleware(request, call_next):
    rid = assign_request_id(request)
    response = await call_next(request)
    response.headers["X-Request-Id"] = rid
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
def root():
    return {
        "app": "FieldOps API",
        "version": "1.0.0",
        "docs": "/docs",
        "status": "online",
    }


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
def contact_form(req: ContactRequest):
    """Ontvang contactformulier en stuur notificatie email."""
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


@app.get("/api/health")
def health_check():
    return {"status": "ok"}



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
