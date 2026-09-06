"""De planner die de nachtelijke controle daadwerkelijk laat draaien.

`billing_reconciliatie` deed het werk al, maar niets riep hem aan. Een controle
die alleen draait als iemand eraan denkt, is geen controle.

Het gevaar zit niet in "hij draait niet" -- dat zie je aan een leeg logboek.
Het gevaar zit in "hij draait twee keer": dan gaat er een tweede
waarschuwingsmail uit naar een klant die al gewaarschuwd is. Deze tests zijn
daarom vooral tests op het claimen: eerst de regel in het logboek, dan pas het
werk, en een tweede aanroep die zichzelf stilhoudt.
"""

from datetime import datetime, timedelta, timezone

import pytest

import billing_planner as bp
from database import SessionLocal
from models import AccountStatus, AuditLog, Organization, SubscriptionPlan


def _sessie_werk(fn):
    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()


def _middag(dagen_geleden: int = 0) -> datetime:
    """Een moment ruim na UUR_UTC, zodat het moment altijd 'daar' is."""
    return (datetime.now(timezone.utc)
            - timedelta(days=dagen_geleden)).replace(hour=12, minute=0)


def _naief(moment: datetime) -> datetime:
    """De kolommen in deze database zijn tijdzone-loos."""
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _wanbetaler(naam="Wanbetaler"):
    """Een organisatie waarvan de betaalde termijn ruim voorbij is.

    Bewust zonder `billing_seats`, want dan stelt `beoordeel` ook geen
    seats-wijziging voor en gaat er in deze test niets richting Mollie.
    """
    db = SessionLocal()
    try:
        o = Organization(
            name=naam,
            plan=SubscriptionPlan.PROFESSIONAL,
            status=AccountStatus.ACTIVE,
            max_users=10,
            mollie_subscription_id="sub_test",
            paid_until=_naief(datetime.now(timezone.utc)) - timedelta(days=60),
        )
        db.add(o)
        db.commit()
        db.refresh(o)
        return o.id
    finally:
        db.close()


def _status(org_id):
    db = SessionLocal()
    try:
        o = db.query(Organization).filter(Organization.id == org_id).first()
        return getattr(o.status, "value", o.status)
    finally:
        db.close()


def _acties():
    db = SessionLocal()
    try:
        return [r.action for r in db.query(AuditLog)
                .order_by(AuditLog.created_at).all()]
    finally:
        db.close()


def _draai(nu=None):
    return _sessie_werk(lambda db: bp.draai_een_ronde(db, nu=nu))


# ── De schakelaar ────────────────────────────────────────────────────

@pytest.mark.parametrize("waarde,verwacht", [
    (None, "toepassen"),
    ("", "toepassen"),
    ("uit", "uit"),
    ("off", "uit"),
    ("false", "uit"),
    ("nee", "uit"),
    ("droog", "droog"),
    ("DROOG", "droog"),
    ("onzin", "toepassen"),
])
def test_stand_leest_de_omgeving(monkeypatch, waarde, verwacht):
    if waarde is None:
        monkeypatch.delenv("BILLING_RECONCILIATIE", raising=False)
    else:
        monkeypatch.setenv("BILLING_RECONCILIATIE", waarde)
    assert bp.stand() == verwacht


def test_standaard_is_toepassen(monkeypatch):
    """Zonder dwang bestaat de controle niet.

    Een planner die standaard alleen rapporteert laat een organisatie die niet
    betaalt gewoon doorwerken, en dan is er niets opgelost.
    """
    monkeypatch.delenv("BILLING_RECONCILIATIE", raising=False)
    assert bp.stand() == "toepassen"


def test_uit_doet_niets(monkeypatch):
    monkeypatch.setenv("BILLING_RECONCILIATIE", "uit")
    org_id = _wanbetaler()
    assert _draai(_middag()) is None
    assert _acties() == []
    assert _status(org_id) == "active"


# ── Het moment ───────────────────────────────────────────────────────

def test_voor_het_uur_gebeurt_er_niets():
    vroeg = datetime.now(timezone.utc).replace(hour=bp.UUR_UTC - 1)
    assert _sessie_werk(lambda db: bp.is_het_moment(db, nu=vroeg)) is False


def test_na_het_uur_mag_het():
    assert _sessie_werk(lambda db: bp.is_het_moment(db, nu=_middag())) is True


def test_gemiste_nacht_wordt_ingehaald():
    """Een instantie die de nacht miste draait alsnog, niet pas morgen.

    Anders slaat een herstart om half vier een hele dag over, en loopt een klant
    een dag langer door zonder te betalen dan de bedoeling is.
    """
    laat = datetime.now(timezone.utc).replace(hour=23, minute=59)
    assert _sessie_werk(lambda db: bp.is_het_moment(db, nu=laat)) is True


# ── Claimen ──────────────────────────────────────────────────────────

def test_ronde_claimt_voordat_hij_werkt(monkeypatch):
    monkeypatch.setenv("BILLING_RECONCILIATIE", "droog")
    _wanbetaler()
    _draai(_middag())
    assert _acties() == [bp.ACTIE_START, bp.ACTIE_KLAAR]


def test_tweede_ronde_op_dezelfde_dag_doet_niets(monkeypatch):
    """De kern: twee keer draaien mag geen tweede mail opleveren."""
    monkeypatch.setenv("BILLING_RECONCILIATIE", "droog")
    _wanbetaler()

    assert _draai(_middag()) is not None
    assert _draai(_middag()) is None

    assert _acties().count(bp.ACTIE_START) == 1


def test_morgen_mag_weer(monkeypatch):
    """De claim van gisteren mag vandaag niet blokkeren."""
    monkeypatch.setenv("BILLING_RECONCILIATIE", "droog")
    _wanbetaler()

    db = SessionLocal()
    try:
        db.add(AuditLog(
            action=bp.ACTIE_START,
            created_at=_naief(datetime.now(timezone.utc)) - timedelta(days=1)))
        db.commit()
    finally:
        db.close()

    assert _draai(_middag()) is not None


def test_al_gedraaid_kijkt_alleen_naar_vandaag():
    db = SessionLocal()
    try:
        db.add(AuditLog(
            action=bp.ACTIE_START,
            created_at=_naief(datetime.now(timezone.utc)) - timedelta(days=3)))
        db.commit()
    finally:
        db.close()

    assert _sessie_werk(bp.al_gedraaid_vandaag) is False


# ── Wat een ronde wel en niet verandert ──────────────────────────────

def test_droogloop_sluit_niemand_af(monkeypatch):
    """Een droogloop mag rapporteren, maar niemand buitensluiten."""
    monkeypatch.setenv("BILLING_RECONCILIATIE", "droog")
    org_id = _wanbetaler()

    resultaat = _draai(_middag())
    assert resultaat is not None
    assert resultaat["toegepast"] is False
    assert _status(org_id) == "active"


def test_toepassen_sluit_wel_af(monkeypatch):
    """En dit is waarom de planner bestaat."""
    monkeypatch.setenv("BILLING_RECONCILIATIE", "toepassen")
    org_id = _wanbetaler()

    resultaat = _draai(_middag())
    assert resultaat["toegepast"] is True
    assert _status(org_id) == "expired"


def test_uitkomst_staat_in_het_logboek(monkeypatch):
    """Zonder leesbare uitkomst kun je achteraf niets uitleggen."""
    monkeypatch.setenv("BILLING_RECONCILIATIE", "toepassen")
    _wanbetaler()
    _draai(_middag())

    db = SessionLocal()
    try:
        klaar = db.query(AuditLog).filter(
            AuditLog.action == bp.ACTIE_KLAAR).first()
        assert klaar is not None
        assert "organisaties_bekeken" in (klaar.details or "")
    finally:
        db.close()


# ── Het volgende moment ──────────────────────────────────────────────

def test_volgend_moment_ligt_altijd_vooruit():
    nu = datetime.now(timezone.utc)
    assert bp.volgende_moment(nu) > nu


# ── Het status-endpoint ──────────────────────────────────────────────

def test_status_alleen_voor_de_eigenaar(client, admin_user):
    """Wanneer wij afsluiten is geen informatie voor een klantbeheerder."""
    from .conftest import auth
    assert client.get("/api/billing/reconciliatie/status",
                      headers=auth(admin_user)).status_code == 403


def test_status_zegt_dat_er_nog_niets_gedraaid_is(client, platform_owner,
                                                  monkeypatch):
    """Een lege planner mag niet lijken alsof hij gedraaid heeft."""
    from .conftest import auth
    monkeypatch.delenv("BILLING_RECONCILIATIE", raising=False)

    r = client.get("/api/billing/reconciliatie/status",
                   headers=auth(platform_owner))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["stand"] == "toepassen"
    assert d["vandaag_al_gedraaid"] is False
    assert d["laatste_ronde"] is None
    assert d["volgende_moment"]


def test_status_toont_de_laatste_ronde(client, platform_owner, monkeypatch):
    from .conftest import auth
    monkeypatch.setenv("BILLING_RECONCILIATIE", "droog")
    _wanbetaler()
    _draai(_middag())

    d = client.get("/api/billing/reconciliatie/status",
                   headers=auth(platform_owner)).json()
    assert d["vandaag_al_gedraaid"] is True
    assert d["laatste_ronde"] is not None
    assert d["laatste_details"]["extra"]["stand"] == "droog"
