"""Shared pytest fixtures.

Strategie: één tijdelijke SQLite-DB per test-sessie. Tabellen worden door
SQLAlchemy create_all() aangemaakt zodra `main` geïmporteerd wordt. Tussen
tests resetten we relevante tabellen via `clean_db` zodat tests onafhankelijk
zijn.

Dit overschrijft DATABASE_URL VOORDAT de app importeert. Anthropic-key wordt
expliciet leeg gezet zodat AI-vision in stub-mode draait.
"""

import os
import tempfile
import pytest

# Cruciaal: env vars zetten BEFORE main wordt geïmporteerd
_TEMP_DB = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
_TEMP_DB.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEMP_DB.name}"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["SECRET_KEY"] = "test-secret-do-not-use-in-prod"
os.environ["ALGORITHM"] = "HS256"

# Nu pas importeren — Base.metadata.create_all() is dan al gedraaid in main.py
import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import (  # noqa: E402
    Organization, User, Project, Asset, Melding, AIAnalysis, AuditLog,
    WebhookEndpoint, WebhookDelivery, IncomingWebhook, PasswordResetToken,
    Invitation, DemoRequest, AccountStatus, SubscriptionPlan, UserRole,
    Inspection, InspectionElement, InspectionDefect, InspectionAnswer,
    Toolbox, ToolboxDeelnemer, Incident,
    Werkplekinspectie, WerkplekinspectieAntwoord,
)
from auth import hash_password, create_access_token  # noqa: E402


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def clean_db():
    """Tussen tests alle business-tabellen leegmaken.

    Schema blijft staan — SQLAlchemy create_all() draait alleen bij import.
    """
    db = SessionLocal()
    try:
        # Volgorde: kinderen vóór ouders om FK-constraints te respecteren
        for model in (
            WebhookDelivery, WebhookEndpoint, IncomingWebhook,
            InspectionAnswer, InspectionDefect, InspectionElement, Inspection,
            ToolboxDeelnemer, Toolbox, Incident,
            WerkplekinspectieAntwoord, Werkplekinspectie,
            AIAnalysis, AuditLog, PasswordResetToken,
            Melding, Asset, Project, Invitation, DemoRequest,
            User, Organization,
        ):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()
    yield


def _make_user(db, email, *, org, role=UserRole.ADMIN, is_org_admin=True,
               password="test1234", first_name="Test", last_name="User"):
    user = User(
        email=email,
        hashed_password=hash_password(password),
        first_name=first_name, last_name=last_name,
        role=role, is_org_admin=is_org_admin,
        organization_id=org.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def org(request):
    """Een verse test-organisatie. Naam afhankelijk van test-naam zodat
    cross-test-leak zichtbaar is als een test toch overschietende data heeft."""
    db = SessionLocal()
    try:
        o = Organization(
            name=f"TestOrg-{request.node.name[:20]}",
            plan=SubscriptionPlan.PROFESSIONAL,
            status=AccountStatus.ACTIVE,
            max_users=50,
        )
        db.add(o); db.commit(); db.refresh(o)
        return o
    finally:
        db.close()


@pytest.fixture
def admin_user(org):
    db = SessionLocal()
    try:
        u = _make_user(db, f"admin-{org.id[:8]}@test.nl", org=org,
                       role=UserRole.ADMIN, is_org_admin=True)
        return u
    finally:
        db.close()


@pytest.fixture
def viewer_user(org):
    db = SessionLocal()
    try:
        return _make_user(db, f"viewer-{org.id[:8]}@test.nl", org=org,
                          role=UserRole.VIEWER, is_org_admin=False)
    finally:
        db.close()


@pytest.fixture
def inspector_user(org):
    db = SessionLocal()
    try:
        return _make_user(db, f"inspector-{org.id[:8]}@test.nl", org=org,
                          role=UserRole.INSPECTOR, is_org_admin=False)
    finally:
        db.close()


@pytest.fixture
def contractor_user(org):
    db = SessionLocal()
    try:
        return _make_user(db, f"contractor-{org.id[:8]}@test.nl", org=org,
                          role=UserRole.CONTRACTOR, is_org_admin=False)
    finally:
        db.close()


@pytest.fixture
def technician_user(org):
    db = SessionLocal()
    try:
        return _make_user(db, f"tech-{org.id[:8]}@test.nl", org=org,
                          role=UserRole.TECHNICIAN, is_org_admin=False)
    finally:
        db.close()


@pytest.fixture
def manager_user(org):
    db = SessionLocal()
    try:
        return _make_user(db, f"manager-{org.id[:8]}@test.nl", org=org,
                          role=UserRole.MANAGER, is_org_admin=False)
    finally:
        db.close()


@pytest.fixture
def platform_owner():
    """Een gebruiker die admin is binnen de FieldOps-org (= platform owner)."""
    db = SessionLocal()
    try:
        org = Organization(plan=SubscriptionPlan.PROFESSIONAL,
                           status=AccountStatus.ACTIVE, max_users=999)
        # Test-bootstrap van de platform-org: expliciete opt-in voor de
        # gereserveerde naam (zelfde patroon als main.py lifespan).
        org.allow_reserved_name = True
        org.name = "FieldOps"
        db.add(org); db.flush()
        u = _make_user(db, "owner@fieldopsapp.nl", org=org,
                       role=UserRole.ADMIN, is_org_admin=True)
        return u
    finally:
        db.close()


def make_token(user):
    return create_access_token(data={
        "sub": user.id, "org": user.organization_id,
        "role": user.role.value if user.role else "viewer",
    })


def auth(user):
    return {"Authorization": "Bearer " + make_token(user)}
