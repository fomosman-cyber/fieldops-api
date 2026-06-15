"""Tests voor de uitgebreide skills-catalogus (bug #15).

'Mijn skills' dekte alleen wegverharding (CROW 146). De maatregelen-lijst is
uitgebreid naar het volledige CROW + NEN pakket (kunstwerken, riolering, groen,
verlichting/elektra, speeltoestellen, straatmeubilair, water, reiniging), elk
met een domein-label zodat de lijst per domein groepeert.
"""
from crow_kosten import SKILL_CODES, SKILL_DOMAIN
from tests.conftest import auth


def test_every_skill_has_a_domain():
    """Geen enkele skill mag zonder domein vallen (anders → 'Overig')."""
    missing = [c for c in SKILL_CODES if c not in SKILL_DOMAIN]
    assert not missing, f"skills zonder domein: {missing}"


def test_catalog_covers_multiple_norm_domains():
    """De catalogus dekt nu kunstwerken/riool/verlichting/speeltoestellen, niet alleen wegverharding."""
    domains = set(SKILL_DOMAIN.values())
    for d in ("Wegverharding", "Kunstwerken", "Riolering", "Groen",
              "Verlichting & elektra", "Speeltoestellen"):
        assert d in domains, f"domein ontbreekt: {d}"
    # Substantiële uitbreiding t.o.v. de oude 15 verharding-skills
    assert len(SKILL_CODES) >= 40


def test_catalog_endpoint_exposes_domein(client, admin_user):
    r = client.get("/api/skills/catalog", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    data = r.json()
    assert all("domein" in s for s in data), "catalog-item mist 'domein'"
    # Een kunstwerk-skill is aanwezig met juist domein
    beton = next((s for s in data if s["code"] == "BETONREPARATIE"), None)
    assert beton is not None
    assert beton["domein"] == "Kunstwerken"


def test_new_skill_code_can_be_saved(client, inspector_user):
    """Een nieuwe (niet-verharding) skill kan opgeslagen worden via PUT /me/skills."""
    r = client.put("/api/users/me/skills",
                   json={"skills": [
                       {"skill_code": "BETONREPARATIE", "proficiency": 5},
                       {"skill_code": "LICHTMAST_VERVANGEN", "proficiency": 3},
                   ]},
                   headers=auth(inspector_user))
    assert r.status_code == 200, r.text
    r = client.get("/api/users/me/skills", headers=auth(inspector_user))
    codes = {s["skill_code"] for s in r.json()}
    assert {"BETONREPARATIE", "LICHTMAST_VERVANGEN"} <= codes
