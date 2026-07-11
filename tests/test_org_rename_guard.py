"""Tests voor de org-rename-guard (privilege-escalatie-fix).

is_platform_owner() checkt op org-naam "FieldOps". Zonder guard kon een
gewone org-admin via PATCH /api/organization/ zijn org hernoemen naar
"FieldOps" en zo super-admin worden. Deze tests bewijzen dat dat niet
meer kan, en dat legitieme renames gewoon blijven werken.
"""

from tests.conftest import auth
from database import SessionLocal
from models import Organization


def _org_name(org_id):
    db = SessionLocal()
    try:
        return db.query(Organization).filter(Organization.id == org_id).first().name
    finally:
        db.close()


def test_org_admin_kan_niet_hernoemen_naar_fieldops(client, admin_user):
    """KRITIEK: rename naar 'FieldOps' moet geblokkeerd worden met 400."""
    r = client.patch("/api/organization/", json={"name": "FieldOps"},
                     headers=auth(admin_user))
    assert r.status_code == 400
    assert "gereserveerd" in r.json()["detail"].lower()
    # Naam is NIET gewijzigd in de DB
    assert _org_name(admin_user.organization_id) != "FieldOps"


def test_org_admin_kan_niet_hernoemen_naar_fieldops_varianten(client, admin_user):
    """Ook case-varianten en whitespace-trucs moeten geblokkeerd worden."""
    for poging in ("fieldops", "FIELDOPS", "  FieldOps  ", "Field Ops", "FieldOps\t"):
        r = client.patch("/api/organization/", json={"name": poging},
                         headers=auth(admin_user))
        assert r.status_code == 400, f"variant {poging!r} werd niet geblokkeerd"
    assert _org_name(admin_user.organization_id) != "FieldOps"


def test_org_admin_kan_wel_gewoon_hernoemen(client, admin_user):
    """Legitieme rename blijft werken."""
    r = client.patch("/api/organization/", json={"name": "Gemeente Teststad"},
                     headers=auth(admin_user))
    assert r.status_code == 200
    assert _org_name(admin_user.organization_id) == "Gemeente Teststad"


def test_platform_owner_mag_fieldops_naam_zetten(client, platform_owner):
    """De echte FieldOps-org-admin mag de naam wél (opnieuw) op FieldOps zetten."""
    r = client.patch("/api/organization/", json={"name": "FieldOps"},
                     headers=auth(platform_owner))
    assert r.status_code == 200
    assert _org_name(platform_owner.organization_id) == "FieldOps"


def test_rename_geeft_geen_platform_owner_rechten(client, admin_user, platform_owner):
    """End-to-end bewijs: na een geblokkeerde rename heeft de org-admin
    nog steeds geen toegang tot platform-owner-endpoints."""
    client.patch("/api/organization/", json={"name": "FieldOps"},
                 headers=auth(admin_user))
    # Admin-router (super-admin) moet 403 blijven geven
    r = client.get("/api/admin/overview", headers=auth(admin_user))
    assert r.status_code == 403
