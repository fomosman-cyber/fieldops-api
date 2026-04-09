from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
from routers import auth_router, demo_router, users_router, org_router, shopify_router

# Maak alle tabellen aan
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="FieldOps API",
    description="Backend API voor FieldOps - Veldregistratie platform",
    version="1.0.0",
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


@app.get("/")
def root():
    return {
        "app": "FieldOps API",
        "version": "1.0.0",
        "docs": "/docs",
        "status": "online",
    }


@app.get("/api/health")
def health_check():
    return {"status": "ok"}



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
