"""Tests voor per-org module-toggles (PORTAL_MODULES).

Dekking:
  - Default (enabled_modules NULL) = alle modules bereikbaar
  - Super-admin zet modules per org via PUT /api/admin/organizations/{id}
  - Uitgeschakelde module → 403 op de bijbehorende routers (kunstwerken,
    mjop, predictive, clusters, opleveren, dagboek)
  - Lege string = alle optionele modules uit; basis-endpoints blijven werken
  - Onbekende module-key → 400
  - Platform-org (FieldOps) heeft altijd alles, ook met modules "uit"
  - GET /api/organization/ + /api/admin/overview exposen enabled_modules
"""

import json

from database import SessionLocal
from models import Organization, PORTAL_MODULES
from tests.conftest import auth


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Per module één representatief GET-endpoint om de gate op te testen
MODULE_ENDPOINTS = {
    "kunstwerken": "/api/kunstwerken-inspecties/",
    "predictive":  "/api/predictive/summary",
    "clusters":    "/api/clusters",
    "opleveren":   "/api/opleveringen/",
    "dagboek":     "/api/daybook/summary",
    "mijn-dag":    "/api/users/me/clusters",
    "veiligheid":  "/api/toolbox/",
    "bouw":        "/api/bouw/",
    "schouw":      "/api/schouw/ritten",
}


def _set_modules_direct(org_id, keys):
    """Modules rechtstreeks in de DB zetten (sneller dan via de admin-API)."""
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        org.enabled_modules = json.dumps(keys) if keys is not None else None
        db.commit()
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Default-gedrag
# ─────────────────────────────────────────────────────────────────────────────

def test_default_null_alle_modules_bereikbaar(client, admin_user):
    """Bestaande orgs (enabled_modules NULL) merken niets: geen 403."""
    for key, endpoint in MODULE_ENDPOINTS.items():
        r = client.get(endpoint, headers=auth(admin_user))
        assert r.status_code != 403, f"{key}: onverwachte 403 bij default"


def test_uitgeschakelde_module_geeft_403(client, admin_user):
    _set_modules_direct(admin_user.organization_id, ["predictive"])
    # Alles behalve predictive geblokkeerd
    for key, endpoint in MODULE_ENDPOINTS.items():
        r = client.get(endpoint, headers=auth(admin_user))
        if key == "predictive":
            assert r.status_code != 403, "predictive staat aan maar gaf 403"
        else:
            assert r.status_code == 403, f"{key}: verwachtte 403, kreeg {r.status_code}"
            assert "niet actief" in r.json()["detail"]


def test_mjop_valt_onder_kunstwerken_module(client, admin_user):
    _set_modules_direct(admin_user.organization_id, [])
    r = client.get("/api/mjop/summary", headers=auth(admin_user))
    assert r.status_code == 403


def test_alles_uit_basis_blijft_werken(client, admin_user):
    """Lege module-lijst raakt de basis-onderdelen niet."""
    _set_modules_direct(admin_user.organization_id, [])
    for endpoint in ("/api/projects/", "/api/meldingen/", "/api/assets/"):
        r = client.get(endpoint, headers=auth(admin_user))
        assert r.status_code == 200, f"{endpoint}: {r.status_code}"


def test_platform_org_altijd_alles(client, platform_owner):
    """FieldOps-org negeert module-toggles — eigenaar ziet altijd alles."""
    _set_modules_direct(platform_owner.organization_id, [])
    for key, endpoint in MODULE_ENDPOINTS.items():
        r = client.get(endpoint, headers=auth(platform_owner))
        assert r.status_code != 403, f"{key}: platform-org kreeg 403"


# ─────────────────────────────────────────────────────────────────────────────
# Admin-API (super-admin beheert modules per org)
# ─────────────────────────────────────────────────────────────────────────────

def test_admin_zet_modules_via_put(client, platform_owner, admin_user):
    org_id = admin_user.organization_id
    r = client.put(
        f"/api/admin/organizations/{org_id}",
        params={"enabled_modules": "kunstwerken,predictive"},
        headers=auth(platform_owner),
    )
    assert r.status_code == 200
    assert sorted(r.json()["enabled_modules"]) == ["kunstwerken", "predictive"]

    # Effect direct zichtbaar voor de org-gebruiker
    assert client.get("/api/kunstwerken-inspecties/", headers=auth(admin_user)).status_code != 403
    assert client.get("/api/opleveringen/", headers=auth(admin_user)).status_code == 403


def test_admin_lege_string_alles_uit(client, platform_owner, admin_user):
    org_id = admin_user.organization_id
    r = client.put(
        f"/api/admin/organizations/{org_id}",
        params={"enabled_modules": ""},
        headers=auth(platform_owner),
    )
    assert r.status_code == 200
    assert r.json()["enabled_modules"] == []
    assert client.get("/api/predictive/summary", headers=auth(admin_user)).status_code == 403


def test_admin_onbekende_module_400(client, platform_owner, admin_user):
    r = client.put(
        f"/api/admin/organizations/{admin_user.organization_id}",
        params={"enabled_modules": "kunstwerken,tijdmachine"},
        headers=auth(platform_owner),
    )
    assert r.status_code == 400
    assert "tijdmachine" in r.json()["detail"]


def test_admin_niet_meegeven_laat_modules_ongemoeid(client, platform_owner, admin_user):
    org_id = admin_user.organization_id
    _set_modules_direct(org_id, ["kunstwerken"])
    r = client.put(
        f"/api/admin/organizations/{org_id}",
        params={"max_users": 25},
        headers=auth(platform_owner),
    )
    assert r.status_code == 200
    assert r.json()["enabled_modules"] == ["kunstwerken"]


def test_gewone_org_admin_mag_geen_modules_zetten(client, admin_user):
    """Module-beheer is super-admin-only — org-admin krijgt 403 op admin-API."""
    r = client.put(
        f"/api/admin/organizations/{admin_user.organization_id}",
        params={"enabled_modules": "kunstwerken"},
        headers=auth(admin_user),
    )
    assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Zichtbaarheid in API-responses (front-end leest dit)
# ─────────────────────────────────────────────────────────────────────────────

def test_organization_response_bevat_enabled_modules(client, admin_user):
    _set_modules_direct(admin_user.organization_id, ["kunstwerken", "dagboek"])
    r = client.get("/api/organization/", headers=auth(admin_user))
    assert r.status_code == 200
    assert sorted(r.json()["enabled_modules"]) == ["dagboek", "kunstwerken"]


def test_organization_response_null_default(client, admin_user):
    r = client.get("/api/organization/", headers=auth(admin_user))
    assert r.status_code == 200
    assert r.json()["enabled_modules"] is None


def test_admin_overview_bevat_enabled_modules(client, platform_owner, admin_user):
    _set_modules_direct(admin_user.organization_id, ["opleveren"])
    r = client.get("/api/admin/overview", headers=auth(platform_owner))
    assert r.status_code == 200
    per_org = {o["id"]: o for o in r.json()["organizations"]}
    assert per_org[admin_user.organization_id]["enabled_modules"] == ["opleveren"]
    # Platform-org zelf: NULL (= alles)
    assert per_org[platform_owner.organization_id]["enabled_modules"] is None


def test_module_registry_consistent(client):
    """De keys die de tests gebruiken bestaan echt in PORTAL_MODULES."""
    for key in MODULE_ENDPOINTS:
        assert key in PORTAL_MODULES
    # Elke module in het register hoort een endpoint te hebben dat hem
    # server-side afdwingt. Staat een key hier niet in, dan is de toggle
    # alleen een front-end-gordijn en blijft de data opvraagbaar.
    assert set(MODULE_ENDPOINTS) == set(PORTAL_MODULES), (
        f"zonder server-side gate: {set(PORTAL_MODULES) - set(MODULE_ENDPOINTS)}")
