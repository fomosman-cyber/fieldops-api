"""Nachtelijke controle op de abonnementen.

Er zaten twee gaten in de betaallaag die geen van beide een foutmelding
opleveren, en die daardoor maandenlang onopgemerkt kunnen blijven.

**Niemand sloot ooit een organisatie af.** `paid_until` werd wel geschreven maar
nergens gelezen. De enige plek die een organisatie op verlopen zette was de
webhook bij een mislukte incasso -- dus wie opzegde, wie zijn mandaat introk, of
wie nooit een abonnement startte, hield onbeperkt toegang. Er was geen enkele
commerciele dwang om te betalen.

**Het aantal gebruikers liep niet mee.** Het maandbedrag werd alleen bijgewerkt
als een beheerder zelf op een knop drukte. Wie tien collega's toevoegde betaalde
voor een.

Deze module loopt beide na. Hij draait als Render Cron Job (`python -m
billing_reconciliatie`) en is ook aan te roepen door de platform-eigenaar, zodat
je hem kunt draaien zonder een nacht te wachten.

**Alles is idempotent.** Twee keer draaien op een dag verandert niets extra: een
organisatie die al verlopen is wordt niet nog eens verlopen, en een waarschuwing
gaat een keer per termijn de deur uit. Dat is niet netjesheid maar noodzaak --
een cron die bij een herstart opnieuw begint mag geen tweede mail sturen.

**Droogloop is de standaard.** `reconcilieer(db)` rapporteert alleen; pas met
`toepassen=True` verandert er iets. Zo kun je zien wat er zou gebeuren voordat
je een organisatie afsluit.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

import mollie
from models import AccountStatus, Organization, User

RECONCILIATIE_VERSIE = "billing-reconciliatie.v1-2026-09"

# Zoveel dagen na het einde van de betaalde termijn gaat de toegang eraf.
# Gelijk aan COULANCE_DAGEN in billing_router; hier herhaald omdat die module
# FastAPI importeert en dit bestand als los script moet kunnen draaien.
COULANCE_DAGEN = 7
# Zoveel dagen van tevoren waarschuwen dat de termijn afloopt.
WAARSCHUW_DAGEN = 3


def _nu() -> datetime:
    return datetime.now(timezone.utc)


def _naief(moment: Optional[datetime]) -> Optional[datetime]:
    """Kolommen in deze database zijn tijdzone-loos."""
    if moment is None:
        return None
    if moment.tzinfo is not None:
        return moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def _actieve_gebruikers(db: Session, org_id: str) -> int:
    return db.query(User).filter(
        User.organization_id == org_id,
        User.is_active == True,  # noqa: E712
    ).count()


def _tarief() -> str:
    return (os.getenv("FIELDOPS_TARIEF_PER_GEBRUIKER") or "9.00").strip()


def _is_platform_org(org: Organization) -> bool:
    """De eigen organisatie sluiten we nooit af.

    Anders sluit de eigenaar zichzelf buiten en kan hij niets meer herstellen --
    dezelfde uitzondering als in auth.py.
    """
    from models import is_reserved_org_name
    return is_reserved_org_name(org.name)


def beoordeel(db: Session, org: Organization) -> dict:
    """Wat er met deze organisatie zou moeten gebeuren.

    Verandert niets. Geeft een besluit terug met een reden, zodat een droogloop
    leesbaar is en een beslissing achteraf uit te leggen.
    """
    nu = _naief(_nu())
    status = getattr(org.status, "value", org.status)
    tot = _naief(org.paid_until)
    proef = _naief(org.trial_ends_at)
    seats = _actieve_gebruikers(db, org.id)

    uit = {
        "organisatie_id": org.id,
        "naam": org.name,
        "status": status,
        "billing_status": org.billing_status,
        "paid_until": tot.isoformat() if tot else None,
        "actieve_gebruikers": seats,
        "in_rekening_gebracht_voor": org.billing_seats,
        "acties": [],
    }

    if _is_platform_org(org):
        uit["overgeslagen"] = "platform-organisatie"
        return uit
    if status == "suspended":
        uit["overgeslagen"] = "handmatig opgeschort"
        return uit

    # ── Toegang ─────────────────────────────────────────────────────
    einde = tot or proef
    if status != "expired" and einde is not None:
        if einde + timedelta(days=COULANCE_DAGEN) < nu:
            uit["acties"].append({
                "actie": "verlopen",
                "reden": ("betaalde termijn" if tot else "proefperiode")
                         + f" liep af op {einde.date()}, coulance van "
                           f"{COULANCE_DAGEN} dagen is voorbij",
            })
        elif einde < nu + timedelta(days=WAARSCHUW_DAGEN) and einde > nu:
            uit["acties"].append({
                "actie": "waarschuwen",
                "reden": f"termijn loopt af op {einde.date()}",
            })

    # Een organisatie zonder abonnement en zonder einddatum is nooit begonnen
    # met betalen. Die sluiten we niet af -- dat kan een pilot of een demo zijn
    # die bewust openstaat -- maar hij komt wel in de rapportage, want anders
    # verdwijnt hij uit beeld.
    if einde is None and not org.mollie_subscription_id and status != "expired":
        uit["acties"].append({
            "actie": "signaleren",
            "reden": "geen abonnement en geen einddatum; betaalt dus nooit",
        })

    # ── Seats ───────────────────────────────────────────────────────
    if org.mollie_subscription_id and org.billing_seats is not None:
        if seats != org.billing_seats:
            uit["acties"].append({
                "actie": "seats_bijwerken",
                "reden": f"{org.billing_seats} in rekening gebracht, "
                         f"{seats} actief",
                "van": org.billing_seats, "naar": seats,
            })

    return uit


def _verlopen(db: Session, org: Organization) -> None:
    org.status = AccountStatus.EXPIRED
    if org.billing_status not in ("canceled",):
        org.billing_status = "past_due"


def _waarschuw(db: Session, org: Organization, reden: str) -> bool:
    """Waarschuwingsmail, hoogstens een keer per termijn.

    Zonder die rem krijgt een klant elke nacht dezelfde mail tot hij betaalt, en
    dat is de snelste manier om ervoor te zorgen dat niemand je mails meer leest.
    """
    laatste = _naief(getattr(org, "billing_waarschuwing_op", None))
    nu = _naief(_nu())
    if laatste and (nu - laatste) < timedelta(days=WAARSCHUW_DAGEN):
        return False
    try:
        from email_service import send_incasso_mislukt
        admins = db.query(User).filter(
            User.organization_id == org.id,
            User.is_active == True,       # noqa: E712
            User.is_org_admin == True,    # noqa: E712
        ).all()
        for admin in admins:
            send_incasso_mislukt(admin, org, bedrag="")
    except Exception as exc:  # noqa: BLE001 -- een mail mag de job niet stoppen
        print(f"[reconciliatie] waarschuwing mislukt voor {org.id}: {exc}")
        return False
    org.billing_waarschuwing_op = nu
    return True


def _seats_bijwerken(org: Organization, naar: int) -> bool:
    """Maandbedrag bij Mollie gelijktrekken met het aantal gebruikers.

    Mollie verrekent niets naar rato: het nieuwe bedrag geldt vanaf de
    eerstvolgende incasso. Dat is precies waarom dit nachtelijk hoort en niet
    pas als iemand eraan denkt -- anders loopt een organisatie maanden achter.
    """
    from decimal import Decimal, ROUND_HALF_UP

    tarief = Decimal(_tarief())
    btw = Decimal((os.getenv("FIELDOPS_BTW_PERCENTAGE") or "21").strip())
    excl = (tarief * max(1, naar)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    bedrag = (excl + (excl * btw / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP))
    try:
        mollie.wijzig_abonnement(
            org.mollie_customer_id, org.mollie_subscription_id,
            bedrag=str(bedrag))
    except Exception as exc:  # noqa: BLE001 -- morgen weer een kans
        print(f"[reconciliatie] seats bijwerken mislukt voor {org.id}: {exc}")
        return False
    org.billing_seats = naar
    return True


def reconcilieer(db: Session, *, toepassen: bool = False) -> dict:
    """Alle organisaties nalopen.

    Zonder ``toepassen`` verandert er niets; dan is dit een droogloop die laat
    zien wat er zou gebeuren.
    """
    organisaties = db.query(Organization).all()
    beoordelingen = []
    uitgevoerd = {"verlopen": 0, "waarschuwingen": 0, "seats_bijgewerkt": 0,
                  "gesignaleerd": 0}

    for org in organisaties:
        b = beoordeel(db, org)
        if not b["acties"]:
            continue
        beoordelingen.append(b)
        if not toepassen:
            continue
        for actie in b["acties"]:
            soort = actie["actie"]
            if soort == "verlopen":
                _verlopen(db, org)
                uitgevoerd["verlopen"] += 1
            elif soort == "waarschuwen":
                if _waarschuw(db, org, actie["reden"]):
                    uitgevoerd["waarschuwingen"] += 1
            elif soort == "seats_bijwerken":
                if _seats_bijwerken(org, actie["naar"]):
                    uitgevoerd["seats_bijgewerkt"] += 1
            elif soort == "signaleren":
                uitgevoerd["gesignaleerd"] += 1

    if toepassen:
        db.commit()

    return {
        "versie": RECONCILIATIE_VERSIE,
        "gedraaid_op": _nu().isoformat(),
        "toegepast": toepassen,
        "organisaties_bekeken": len(organisaties),
        "organisaties_met_acties": len(beoordelingen),
        "uitgevoerd": uitgevoerd if toepassen else None,
        "beoordelingen": beoordelingen,
    }


def sync_seats(db: Session, org: Optional[Organization]) -> bool:
    """Het maandbedrag meteen gelijktrekken na een gebruikerswijziging.

    Best-effort en bewust stil: iemand die een collega toevoegt hoort daar geen
    Mollie-fout van te zien, en zijn actie hoort niet te mislukken omdat Mollie
    even niet bereikbaar is. Lukt het niet, dan pakt de nachtelijke controle het
    op -- dat is precies waarom die bestaat.
    """
    if org is None or not org.mollie_subscription_id:
        return False
    seats = _actieve_gebruikers(db, org.id)
    if org.billing_seats == seats:
        return False
    gelukt = _seats_bijwerken(org, seats)
    if gelukt:
        db.commit()
    return gelukt


if __name__ == "__main__":
    # CLI voor de Render Cron Job:
    #   python -m billing_reconciliatie            (droogloop)
    #   python -m billing_reconciliatie --toepassen
    import json as _json
    import sys as _sys

    from database import SessionLocal

    toepassen = "--toepassen" in _sys.argv
    _db = SessionLocal()
    try:
        resultaat = reconcilieer(_db, toepassen=toepassen)
    finally:
        _db.close()
    print(_json.dumps(resultaat, indent=2, ensure_ascii=False))
    raise SystemExit(0)
