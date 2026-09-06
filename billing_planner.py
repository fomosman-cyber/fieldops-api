"""De nachtelijke reconciliatie laten draaien zonder dat iemand eraan denkt.

`billing_reconciliatie` bestond al en deed het werk, maar niets riep hem aan.
Een controle die alleen draait als je hem handmatig start, is geen controle --
dan is het een knop die je op een slechte dag vergeet. En juist de twee gaten
die deze module dicht, geven geen foutmelding: een organisatie die niet meer
betaalt houdt gewoon toegang, en een organisatie die tien collega's toevoegt
betaalt gewoon voor een. Er is geen moment waarop dat opvalt.

**Waarom in het proces en niet als Render Cron Job.** Een aparte cron-service
op Render heeft een eigen omgeving: DATABASE_URL, de Mollie-sleutels, de
SMTP-gegevens -- allemaal opnieuw, met de hand. Die twee lijsten lopen daarna
uit elkaar zonder dat iemand het merkt, en dan draait de controle maanden met
een oude sleutel of tegen de verkeerde database. Dit draait in dezelfde
instantie met dezelfde configuratie, net als `keep_alive_ping`. Het kost niets
extra en kan niet uit de pas lopen.

De prijs daarvan is dat het meelift op de webservice: staat die stil, dan
draait de controle niet. Voor een dienst op een always-on plan is dat geen
echte beperking, en het inhaalgedrag hieronder vangt het op -- na een nacht
downtime draait hij alsnog, zodra de instantie er weer is.

**Claimen voordat er gewerkt wordt.** Elke ronde schrijft eerst een
`billing.reconciliatie.start` in het audit-logboek en pas daarna een
`.klaar` met de uitkomst. Die eerste regel is de claim: een tweede instantie
ziet hem staan en slaat de dag over. Het audit-logboek is append-only, dus
claimen en afronden zijn twee regels en geen update.

**De schakelaar.** `BILLING_RECONCILIATIE` bepaalt wat er gebeurt:

    (leeg)  toepassen -- de standaard, want anders bestaat de dwang niet
    droog   alleen rapporteren, niets veranderen
    uit     helemaal niet draaien

Er is bewust geen stille tussenstand: elke ronde eindigt in het logboek, ook
een droogloop en ook een ronde zonder werk.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from models import AuditLog

PLANNER_VERSIE = "billing-planner.v1-2026-09"

# Draaien na dit uur (UTC). Vijf uur 's ochtends Nederlandse zomertijd: de
# waarschuwingsmail komt dan aan het begin van de werkdag binnen en niet
# midden in de nacht.
UUR_UTC = 3

# Hoe vaak we kijken of er nog werk is. Ruim genoeg om geen belasting te zijn,
# fijn genoeg om na een herstart snel in te halen.
INTERVAL_SECONDEN = 30 * 60

# Even wachten na het opstarten: eerst de migraties en de eerste verzoeken.
STARTVERTRAGING_SECONDEN = 120

ACTIE_START = "billing.reconciliatie.start"
ACTIE_KLAAR = "billing.reconciliatie.klaar"


def stand() -> str:
    """Wat de omgeving wil: ``toepassen``, ``droog`` of ``uit``."""
    ruw = (os.getenv("BILLING_RECONCILIATIE") or "").strip().lower()
    if ruw in ("uit", "off", "false", "0", "nee"):
        return "uit"
    if ruw in ("droog", "dry", "droogloop"):
        return "droog"
    return "toepassen"


def _nu() -> datetime:
    return datetime.now(timezone.utc)


def _dagbegin(moment: datetime) -> datetime:
    """Middernacht UTC van de dag waar dit moment in valt, tijdzone-loos.

    Tijdzone-loos omdat de kolommen in deze database dat ook zijn; vergelijken
    met een tijdzone-bewuste waarde werpt op PostgreSQL een TypeError.
    """
    return datetime.combine(moment.date(), time.min)


def al_gedraaid_vandaag(db: Session, *, nu: Optional[datetime] = None) -> bool:
    """Staat de claim van vandaag al in het logboek?"""
    nu = nu or _nu()
    return db.query(AuditLog).filter(
        AuditLog.action == ACTIE_START,
        AuditLog.created_at >= _dagbegin(nu),
    ).first() is not None


def is_het_moment(db: Session, *, nu: Optional[datetime] = None) -> bool:
    """Mag er nu gedraaid worden?

    Ja als het na het afgesproken uur is en vandaag nog niets is geclaimd. Een
    instantie die de nacht heeft gemist draait daardoor alsnog zodra hij er is,
    in plaats van een dag over te slaan.
    """
    nu = nu or _nu()
    if nu.hour < UUR_UTC:
        return False
    return not al_gedraaid_vandaag(db, nu=nu)


def _log(db: Session, actie: str, extra: dict) -> None:
    """Een regel in het audit-logboek, zonder gebruiker en zonder request."""
    import audit
    audit.log_action(db, None, None, action=actie,
                     entity_type="billing", extra=extra)


def draai_een_ronde(db: Session, *, nu: Optional[datetime] = None) -> Optional[dict]:
    """Een ronde, als het moment daar is. Geeft ``None`` als er niets gebeurde.

    Deze functie is bewust synchroon en zonder asyncio: zo is hij te testen
    zonder event loop, en de aanroeper bepaalt of hij in een thread draait.
    """
    huidige_stand = stand()
    if huidige_stand == "uit":
        return None
    if not is_het_moment(db, nu=nu):
        return None

    # Eerst claimen, dan pas werken. Twee instanties die tegelijk wakker worden
    # zien hierna allebei een claim staan; de tweede stopt bij de volgende
    # ronde. Werkt er toch een tweede doorheen, dan is dat niet erg: de
    # reconciliatie is idempotent.
    _log(db, ACTIE_START, {"versie": PLANNER_VERSIE, "stand": huidige_stand})

    import billing_reconciliatie
    resultaat = billing_reconciliatie.reconcilieer(
        db, toepassen=(huidige_stand == "toepassen"))

    _log(db, ACTIE_KLAAR, {
        "versie": PLANNER_VERSIE,
        "stand": huidige_stand,
        "organisaties_bekeken": resultaat.get("organisaties_bekeken"),
        "organisaties_met_acties": resultaat.get("organisaties_met_acties"),
        "uitgevoerd": resultaat.get("uitgevoerd"),
    })
    return resultaat


async def reconciliatie_lus() -> None:
    """De achtergrondtaak die `main` bij het opstarten aanzet.

    Faalt nooit hard. Een reconciliatie die stukloopt mag de webservice niet
    meenemen -- dan is een fout in de facturatie ineens een storing voor
    iedereen die in het veld staat.
    """
    if stand() == "uit":
        print("[billing-planner] uitgeschakeld via BILLING_RECONCILIATIE")
        return

    from database import SessionLocal

    await asyncio.sleep(STARTVERTRAGING_SECONDEN)
    while True:
        try:
            def _werk():
                db = SessionLocal()
                try:
                    return draai_een_ronde(db)
                finally:
                    db.close()

            resultaat = await asyncio.to_thread(_werk)
            if resultaat is not None:
                print(f"[billing-planner] ronde klaar: "
                      f"{resultaat.get('organisaties_met_acties')} van "
                      f"{resultaat.get('organisaties_bekeken')} organisaties "
                      f"met acties, stand={stand()}")
        except Exception as fout:  # noqa: BLE001 -- zie docstring
            print(f"[billing-planner] ronde mislukt: {fout!r}")

        await asyncio.sleep(INTERVAL_SECONDEN)


def volgende_moment(nu: Optional[datetime] = None) -> datetime:
    """Wanneer de eerstvolgende ronde op zijn vroegst valt. Voor de status-API."""
    nu = nu or _nu()
    vandaag = nu.replace(hour=UUR_UTC, minute=0, second=0, microsecond=0)
    if nu < vandaag:
        return vandaag
    return vandaag + timedelta(days=1)
