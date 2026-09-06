"""Wat het security-overzicht beweert moet de code waarmaken.

Dit overzicht gaat mee als bijlage bij een aanbesteding. Het stond vol
hardgecodeerde waarden die niet klopten: een sessieduur van 30 minuten terwijl
een token 24 uur geldig is, een wachtwoordminimum van twaalf tekens terwijl het
er acht zijn, en wekelijkse scans met Dependabot en Snyk terwijl er geen enkele
scanconfiguratie in de repository staat.

Een inkoper leest dat naast de eigen /compliance-pagina, die eerlijk zegt dat er
geen deelbaar pentestrapport is. Twee documenten die elkaar tegenspreken kosten
je het dossier -- en terecht, want dan weet niemand welke van de twee waar is.

Deze tests dwingen af dat wat te controleren valt ook gecontroleerd wordt, en
dat de rest eerlijk op "niet aangetoond" staat tot iemand het aantoont.
"""


from auth import (ACCESS_TOKEN_EXPIRE_MINUTES, LOGIN_RATE_LIMIT_PER_EMAIL,
                  MIN_PASSWORD_LENGTH)

from .conftest import auth as _auth


def _posture(client, user):
    r = client.get("/api/compliance/security-posture", headers=_auth(user))
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Wat uit de code komt, komt uit de code
# ---------------------------------------------------------------------------

def test_sessieduur_komt_uit_de_code(client, admin_user):
    """Stond op 30 minuten terwijl een token 24 uur geldig is."""
    d = _posture(client, admin_user)
    assert d["access_control"]["session_timeout_min"] == ACCESS_TOKEN_EXPIRE_MINUTES


def test_wachtwoordbeleid_komt_uit_de_code(client, admin_user):
    """Stond op 12 tekens terwijl het minimum 8 is."""
    d = _posture(client, admin_user)
    assert str(MIN_PASSWORD_LENGTH) in d["access_control"]["password_policy"]


def test_lockout_komt_uit_de_code(client, admin_user):
    d = _posture(client, admin_user)
    assert str(LOGIN_RATE_LIMIT_PER_EMAIL) in d["access_control"]["failed_login_lockout"]


def test_error_tracking_volgt_de_configuratie(client, admin_user, monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert _posture(client, admin_user)["monitoring"]["error_tracking"] \
        == "niet geconfigureerd"


# ---------------------------------------------------------------------------
# Niet aangetoond is niet aangetoond
# ---------------------------------------------------------------------------

ORGANISATORISCH = [
    ("monitoring", "anomaly_detection"),
    ("monitoring", "vulnerability_scanning"),
    ("monitoring", "penetration_testing"),
    ("incident_response", "documented_procedure"),
    ("incident_response", "bereikbaarheid"),
    ("business_continuity", "restore_getest"),
    ("compliance_certifications", "iso_27001"),
    ("compliance_certifications", "soc_2"),
]


def test_onaangetoonde_claims_staan_op_niet_aangetoond(client, admin_user, monkeypatch):
    """Geen enkele hardgecodeerde True meer op iets dat niemand kan verifieren."""
    for blok, sleutel in ORGANISATORISCH:
        monkeypatch.delenv(
            {"anomaly_detection": "COMPLIANCE_ANOMALY_DETECTION",
             "vulnerability_scanning": "COMPLIANCE_KWETSBAARHEIDSSCAN",
             "penetration_testing": "COMPLIANCE_PENTEST",
             "documented_procedure": "COMPLIANCE_INCIDENTPROCEDURE",
             "bereikbaarheid": "COMPLIANCE_PIKET",
             "restore_getest": "COMPLIANCE_RESTORE_GETEST",
             "iso_27001": "COMPLIANCE_ISO27001",
             "soc_2": "COMPLIANCE_SOC2"}[sleutel], raising=False)

    d = _posture(client, admin_user)
    for blok, sleutel in ORGANISATORISCH:
        veld = d[blok][sleutel]
        assert veld["aangetoond"] is False, f"{blok}.{sleutel}"
        assert veld["status"] == "niet aangetoond", f"{blok}.{sleutel}"
        assert veld["instelbaar_via"].startswith("COMPLIANCE_")


def test_ingevulde_claim_wordt_getoond(client, admin_user, monkeypatch):
    monkeypatch.setenv("COMPLIANCE_PENTEST", "Uitgevoerd door Bureau X, maart 2027")
    d = _posture(client, admin_user)
    veld = d["monitoring"]["penetration_testing"]
    assert veld["aangetoond"] is True
    assert "Bureau X" in veld["status"]


def test_geen_dependabot_of_snyk_claim_meer(client, admin_user, monkeypatch):
    """Er staat geen scanconfiguratie in .github/, dus die claim mag niet terug.

    Deze test faalt zodra iemand de oude tekst terugzet zonder de scan ook echt
    in te richten.
    """
    monkeypatch.delenv("COMPLIANCE_KWETSBAARHEIDSSCAN", raising=False)
    tekst = str(_posture(client, admin_user))
    assert "Dependabot" not in tekst
    assert "Snyk" not in tekst


def test_rto_en_rpo_worden_niet_beweerd(client, admin_user):
    """Met dagelijkse back-ups kan een RPO van een uur niet kloppen.

    De oude tekst beweerde rpo_hours 1 naast backup_frequency daily. Dat is
    intern tegenstrijdig: je kunt niet minder dan een dag verliezen als je een
    keer per dag een back-up maakt.
    """
    bc = _posture(client, admin_user)["business_continuity"]
    assert "rto_hours" not in bc and "rpo_hours" not in bc
    assert "aanname" in bc["opmerking"]


def test_certificeringen_van_subverwerkers_worden_niet_geclaimd(client, admin_user):
    d = _posture(client, admin_user)["compliance_certifications"]
    assert "niet overdraagbaar" in d["opmerking"]
