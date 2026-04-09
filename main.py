from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path
from database import engine, Base, SessionLocal
from models import Organization, User, AccountStatus, SubscriptionPlan, UserRole
from auth import hash_password
from routers import auth_router, demo_router, users_router, org_router, shopify_router, admin_router, projects_router, meldingen_router

# Maak alle tabellen aan
Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: maak owner account aan als die nog niet bestaat
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == "fomosman@gmail.com").first()
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
                email="fomosman@gmail.com",
                hashed_password=hash_password("Nieuwjaar2026@"),
                first_name="Faris",
                last_name="Osman",
                role=UserRole.ADMIN,
                is_active=True,
                is_org_admin=True,
                organization_id=org.id,
            )
            db.add(user)
            db.commit()
            print("Owner account aangemaakt: fomosman@gmail.com")
        else:
            print("Owner account bestaat al")
    finally:
        db.close()
    yield


app = FastAPI(
    title="FieldOps API",
    description="Backend API voor FieldOps - Veldregistratie platform",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS - sta frontend toe
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

TEMPLATES_DIR = Path(__file__).parent / "templates"


@app.get("/")
def root():
    return {
        "app": "FieldOps API",
        "version": "1.0.0",
        "docs": "/docs",
        "status": "online",
    }


@app.get("/portaal", response_class=HTMLResponse)
def portaal():
    """Serve de FieldOps portaal SPA."""
    html = (TEMPLATES_DIR / "portaal.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/api/health")
def health_check():
    return {"status": "ok"}



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
