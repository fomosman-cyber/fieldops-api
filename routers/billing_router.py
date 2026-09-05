"""Abonnementen via Mollie.

De keten in het kort:

1. Een org-admin start het abonnement. Wij maken een Mollie-klant en zetten een
   **eerste betaling van één cent** klaar. Die cent is geen tarief maar de prijs
   van het incassomandaat: iDEAL kan zelf niet incasseren, maar levert bij een
   ``first``-betaling wel een SEPA-mandaat op het gebruikte IBAN.
2. Mollie roept onze webhook aan zodra die betaling betaald is. Pas dan maken we
   het echte abonnement aan: aantal actieve gebruikers × tarief, elke maand.
3. Bij elke volgende incasso komt dezelfde webhook opnieuw langs. Lukt de
   incasso, dan verlengen we de betaalde termijn. Lukt hij niet, dan gaat de
   organisatie naar ``past_due`` en krijgt de admin bericht; pas als de betaalde
   termijn ook echt verlopen is, verliest de organisatie toegang.

**Waarom de webhook geen handtekening controleert.** Mollie ondertekent zijn
webhooks niet — de body is enkel ``id=tr_...``. Het enige dat we vertrouwen is
wat de Mollie-API zélf over die betaling zegt. Daar bovenop staat een geheim in
het pad, zodat de endpoint niet zomaar te vinden is, en weigeren we betalingen
die niet bij een organisatie van ons horen.

**De webhook antwoordt altijd met 200 zodra hij begrepen is.** Elk ander
antwoord laat Mollie ruim een dag lang opnieuw proberen.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

import mollie
from auth import get_current_user
from database import get_db
from models import AccountStatus, Organization, Payment, User
from permissions import is_platform_owner, require_org_admin

router = APIRouter(prefix="/api/billing", tags=["Abonnement"])

# Tarief per gebruiker per maand. Staat op de website; hier instelbaar zodat we
# een prijswijziging niet hoeven te deployen.
STANDAARD_TARIEF = "9.00"
# Bedrag van de eerste betaling. Alleen bedoeld om het mandaat te krijgen;
# Mollie staat minimaal één cent toe bij iDEAL.
MANDAAT_BEDRAG = "0.01"
# Zoveel dagen blijft een organisatie na een mislukte incasso nog werken.
COULANCE_DAGEN = 7


def tarief_per_gebruiker() -> Decimal:
    ruw = (os.getenv("FIELDOPS_TARIEF_PER_GEBRUIKER") or STANDAARD_TARIEF).strip()
    try:
        return Decimal(ruw)
    except Exception:  # noqa: BLE001 — foute config mag de betaling niet slopen
        return Decimal(STANDAARD_TARIEF)


def _bedrag(waarde: Decimal) -> str:
    """Mollie wil een string met exact twee decimalen."""
    return str(waarde.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _webhook_token() -> str:
    return (os.getenv("MOLLIE_WEBHOOK_TOKEN") or "").strip()


def _portaal_url() -> str:
    return (os.getenv("PORTAAL_URL")
            or os.getenv("RENDER_EXTERNAL_URL")
            or "https://portaal.fieldopsapp.nl").rstrip("/")


def _site_url() -> str:
    return (os.getenv("FRONTEND_URL") or "https://www.fieldopsapp.nl").rstrip("/")


def _webhook_url() -> str:
    return f"{_portaal_url()}/api/billing/webhook/{_webhook_token()}"


def actieve_gebruikers(db: Session, org_id: str) -> int:
    return db.query(User).filter(
        User.organization_id == org_id,
        User.is_active == True,  # noqa: E712 — SQLAlchemy-vergelijking
    ).count()


def maandbedrag(db: Session, org: Organization) -> tuple[int, Decimal]:
    """Aantal te betalen gebruikers en het maandbedrag.

    Minimaal één gebruiker: een organisatie zonder actieve gebruikers hoort
    geen abonnement van nul euro te krijgen, want Mollie weigert dat.
    """
    seats = max(1, actieve_gebruikers(db, org.id))
    return seats, tarief_per_gebruiker() * seats


def _nu() -> datetime:
    return datetime.now(timezone.utc)


def _naief(moment: datetime | None) -> datetime | None:
    """Kolommen in deze database zijn tijdzone-loos; zo blijft vergelijken veilig."""
    if moment is None:
        return None
    if moment.tzinfo is not None:
        return moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get("/status")
def abonnement_status(
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Wat de org-admin in het instellingenscherm te zien krijgt."""
    org = current_user.organization
    if org is None:
        raise HTTPException(status_code=404, detail="Geen organisatie gevonden")

    seats, bedrag = maandbedrag(db, org)
    return {
        "geconfigureerd": mollie.is_geconfigureerd(),
        "testmodus": mollie.is_testmodus(),
        "organisatie": org.name,
        "account_status": getattr(org.status, "value", org.status),
        "billing_status": org.billing_status or "geen",
        "heeft_abonnement": bool(org.mollie_subscription_id),
        "actieve_gebruikers": seats,
        "tarief_per_gebruiker": _bedrag(tarief_per_gebruiker()),
        "maandbedrag": _bedrag(bedrag),
        "in_rekening_gebracht_voor": org.billing_seats,
        "betaald_tot": org.paid_until.isoformat() if org.paid_until else None,
        "proef_tot": org.trial_ends_at.isoformat() if org.trial_ends_at else None,
    }


# ---------------------------------------------------------------------------
# Starten
# ---------------------------------------------------------------------------

@router.post("/start")
def start_abonnement(
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Eerste betaling klaarzetten en de checkout-URL teruggeven.

    De frontend stuurt de browser met een gewone navigatie naar die URL. Een
    formulier-post of iframe werkt niet: het contentbeveiligingsbeleid van dit
    portaal staat ``form-action 'self'`` en heeft geen ``frame-src``.
    """
    if not mollie.is_geconfigureerd():
        raise HTTPException(
            status_code=503,
            detail="Betalen staat nog niet aan op deze omgeving")
    if not _webhook_token():
        raise HTTPException(
            status_code=503,
            detail="MOLLIE_WEBHOOK_TOKEN ontbreekt — betalen is uitgeschakeld")

    org = current_user.organization
    if org is None:
        raise HTTPException(status_code=404, detail="Geen organisatie gevonden")
    if org.mollie_subscription_id:
        raise HTTPException(
            status_code=409,
            detail="Er loopt al een abonnement voor deze organisatie")

    try:
        if not org.mollie_customer_id:
            klant = mollie.maak_customer(
                naam=org.name,
                email=org.contact_email or current_user.email,
                metadata={"organization_id": org.id},
            )
            org.mollie_customer_id = klant.get("id")
            db.commit()

        seats, bedrag = maandbedrag(db, org)
        betaling = mollie.maak_eerste_betaling(
            org.mollie_customer_id,
            bedrag=MANDAAT_BEDRAG,
            beschrijving=f"FieldOps machtiging {org.name}",
            redirect_url=f"{_site_url()}/betaling-gelukt",
            webhook_url=_webhook_url(),
            metadata={"organization_id": org.id, "doel": "mandaat"},
        )
    except mollie.MollieNietGeconfigureerd:
        raise HTTPException(status_code=503, detail="Betalen staat nog niet aan")
    except mollie.MollieFout as exc:
        raise HTTPException(status_code=502, detail=f"Mollie: {exc.detail}")

    _bewaar_betaling(db, betaling, org_id=org.id)
    org.billing_status = "pending"
    org.billing_seats = seats
    db.commit()

    checkout = ((betaling.get("_links") or {}).get("checkout") or {}).get("href")
    return {
        "checkout_url": checkout,
        "payment_id": betaling.get("id"),
        "maandbedrag": _bedrag(bedrag),
        "actieve_gebruikers": seats,
    }


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

@router.post("/webhook/{token}")
async def mollie_webhook(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Meldpunt voor Mollie. Geeft bewust altijd 200 bij een begrepen melding.

    Elk ander antwoord laat Mollie ruim een dag lang opnieuw proberen, en dan
    krijg je dezelfde verwerking tientallen keren.
    """
    verwacht = _webhook_token()
    if not verwacht:
        raise HTTPException(status_code=503, detail="Webhook staat uit")
    if not _veilige_vergelijking(token, verwacht):
        raise HTTPException(status_code=401, detail="Onbekende webhook")
    if not mollie.is_geconfigureerd():
        raise HTTPException(status_code=503, detail="Betalen staat niet aan")

    ruw = await request.body()
    payment_id = _payment_id_uit_body(ruw)
    if not payment_id:
        return {"status": "genegeerd", "reden": "geen payment id"}

    try:
        betaling = mollie.haal_betaling(payment_id)
    except mollie.MollieFout as exc:
        # 404 betekent: deze betaling bestaat niet bij ons account. Geen
        # retry-waardig probleem; alles anders wél, want dan is Mollie stuk.
        if exc.status_code == 404:
            return {"status": "genegeerd", "reden": "onbekend bij Mollie"}
        raise HTTPException(status_code=503, detail="Mollie tijdelijk onbereikbaar")

    try:
        return _verwerk_betaling(db, betaling)
    except Exception as exc:  # noqa: BLE001
        # Bewust breed: een onafgevangen fout wordt door de globale handler een
        # 500, en dat is bij Mollie een retry-lus van ruim een dag.
        db.rollback()
        print(f"[billing] webhook {payment_id} mislukt: {exc}")
        return {"status": "fout", "reden": "intern"}


def _veilige_vergelijking(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a or "", b or "")


def _payment_id_uit_body(ruw: bytes) -> str:
    """Mollie post ``id=tr_xxx`` als formulierdata."""
    from urllib.parse import parse_qs
    try:
        velden = parse_qs(ruw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    waarden = velden.get("id") or []
    return (waarden[0] if waarden else "").strip()


def _bewaar_betaling(db: Session, betaling: dict, *, org_id: str | None) -> Payment:
    """Rij bijwerken of aanmaken. Uniek op het Mollie-id, dus idempotent."""
    payment_id = betaling.get("id")
    rij = db.query(Payment).filter(Payment.mollie_payment_id == payment_id).first()
    if rij is None:
        rij = Payment(mollie_payment_id=payment_id)
        db.add(rij)

    bedrag = betaling.get("amount") or {}
    rij.organization_id = org_id or rij.organization_id
    rij.mollie_customer_id = betaling.get("customerId") or rij.mollie_customer_id
    rij.mollie_subscription_id = betaling.get("subscriptionId") or rij.mollie_subscription_id
    rij.sequence_type = betaling.get("sequenceType") or rij.sequence_type
    rij.amount = bedrag.get("value") or rij.amount
    rij.currency = bedrag.get("currency") or rij.currency or "EUR"
    rij.status = betaling.get("status") or rij.status
    rij.description = betaling.get("description") or rij.description
    if betaling.get("paidAt") and rij.paid_at is None:
        rij.paid_at = _parse_moment(betaling["paidAt"])
    db.flush()
    return rij


def _parse_moment(waarde: str) -> datetime | None:
    try:
        return _naief(datetime.fromisoformat(waarde.replace("Z", "+00:00")))
    except Exception:  # noqa: BLE001
        return None


def _org_bij_betaling(db: Session, betaling: dict) -> Organization | None:
    """Organisatie zoeken via metadata, klant-id of een eerder bewaarde rij."""
    meta = betaling.get("metadata") or {}
    if isinstance(meta, dict) and meta.get("organization_id"):
        org = db.query(Organization).filter(
            Organization.id == meta["organization_id"]).first()
        if org:
            return org

    klant = betaling.get("customerId")
    if klant:
        org = db.query(Organization).filter(
            Organization.mollie_customer_id == klant).first()
        if org:
            return org

    rij = db.query(Payment).filter(
        Payment.mollie_payment_id == betaling.get("id")).first()
    if rij and rij.organization_id:
        return db.query(Organization).filter(
            Organization.id == rij.organization_id).first()
    return None


def _verwerk_betaling(db: Session, betaling: dict) -> dict:
    org = _org_bij_betaling(db, betaling)
    if org is None:
        # Een betaling die bij geen enkele organisatie hoort verwerken we niet.
        return {"status": "genegeerd", "reden": "geen organisatie"}

    rij = _bewaar_betaling(db, betaling, org_id=org.id)
    status = betaling.get("status")
    soort = betaling.get("sequenceType")

    if status != "paid":
        if soort == "recurring":
            _incasso_mislukt(db, org, rij)
        db.commit()
        return {"status": "verwerkt", "betaling": status}

    if rij.processed_at is not None:
        # Mollie stuurt bij elke statuswissel opnieuw; twee keer verlengen
        # zou de klant een gratis maand geven.
        db.commit()
        return {"status": "al verwerkt"}

    if soort == "first":
        resultaat = _mandaat_binnen(db, org, rij)
    else:
        resultaat = _termijn_verlengen(db, org, rij)

    rij.processed_at = _naief(_nu())
    db.commit()
    return resultaat


def _mandaat_binnen(db: Session, org: Organization, rij: Payment) -> dict:
    """Eerste betaling gelukt: mandaat ophalen en het abonnement aanmaken."""
    mandaat = None
    try:
        mandaat = mollie.eerste_bruikbare_mandaat(org.mollie_customer_id)
    except mollie.MollieFout as exc:
        print(f"[billing] mandaat ophalen mislukt voor {org.id}: {exc}")

    if mandaat:
        org.mollie_mandate_id = mandaat.get("id")

    if org.mollie_subscription_id:
        return {"status": "abonnement bestond al"}

    seats, bedrag = maandbedrag(db, org)
    try:
        abonnement = mollie.maak_abonnement(
            org.mollie_customer_id,
            bedrag=_bedrag(bedrag),
            # Uniek per klant zolang er meerdere abonnementen kunnen lopen.
            beschrijving=f"FieldOps {org.name} ({org.id[:8]})",
            webhook_url=_webhook_url(),
            interval="1 month",
            # Eerste maand gratis: pas over een maand de eerste incasso.
            start_datum=(_nu() + timedelta(days=30)).date().isoformat(),
            mandate_id=org.mollie_mandate_id,
            metadata={"organization_id": org.id},
        )
    except mollie.MollieFout as exc:
        org.billing_status = "mandaat_ok_abonnement_mislukt"
        print(f"[billing] abonnement aanmaken mislukt voor {org.id}: {exc}")
        return {"status": "mandaat ontvangen, abonnement mislukt"}

    org.mollie_subscription_id = abonnement.get("id")
    org.billing_seats = seats
    org.billing_status = "active"
    org.status = AccountStatus.ACTIVE
    # De eerste maand is gratis; daarna incasseert Mollie.
    org.paid_until = _naief(_nu() + timedelta(days=30))

    _stuur_activatiemail(db, org, seats, bedrag)
    return {"status": "abonnement gestart", "subscription": org.mollie_subscription_id}


def _termijn_verlengen(db: Session, org: Organization, rij: Payment) -> dict:
    """Maandelijkse incasso gelukt."""
    basis = _naief(org.paid_until) or _naief(_nu())
    nu = _naief(_nu())
    if basis < nu:
        basis = nu
    org.paid_until = basis + timedelta(days=31)
    org.billing_status = "active"
    if getattr(org.status, "value", org.status) in ("expired", "trial"):
        org.status = AccountStatus.ACTIVE
    return {"status": "termijn verlengd", "betaald_tot": org.paid_until.isoformat()}


def _incasso_mislukt(db: Session, org: Organization, rij: Payment) -> None:
    """Incasso niet gelukt: coulance, waarschuwen, en pas daarna afsluiten."""
    org.billing_status = "past_due"
    einde = _naief(org.paid_until)
    nu = _naief(_nu())
    if einde is None or einde + timedelta(days=COULANCE_DAGEN) < nu:
        org.status = AccountStatus.EXPIRED
    _stuur_mislukt_mail(db, org, rij)


def _admins(db: Session, org: Organization) -> list[User]:
    return db.query(User).filter(
        User.organization_id == org.id,
        User.is_active == True,  # noqa: E712
        User.is_org_admin == True,  # noqa: E712
    ).all()


def _stuur_activatiemail(db: Session, org: Organization, seats: int, bedrag: Decimal) -> None:
    """Bevestiging dat het abonnement loopt.

    Best-effort: een haperende mailserver mag geen webhook-retry veroorzaken,
    want dan zou het abonnement een tweede keer worden aangemaakt.
    """
    try:
        from email_service import send_abonnement_actief
        for admin in _admins(db, org):
            send_abonnement_actief(
                admin, org, seats=seats, maandbedrag=_bedrag(bedrag))
    except Exception as exc:  # noqa: BLE001
        print(f"[billing] activatiemail mislukt voor {org.id}: {exc}")


def _stuur_mislukt_mail(db: Session, org: Organization, rij: Payment) -> None:
    try:
        from email_service import send_incasso_mislukt
        for admin in _admins(db, org):
            send_incasso_mislukt(admin, org, bedrag=rij.amount or "")
    except Exception as exc:  # noqa: BLE001
        print(f"[billing] waarschuwingsmail mislukt voor {org.id}: {exc}")


# ---------------------------------------------------------------------------
# Beheer
# ---------------------------------------------------------------------------

@router.post("/seats/sync")
def synchroniseer_seats(
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Maandbedrag gelijktrekken met het huidige aantal gebruikers.

    Mollie verrekent niets naar rato: het nieuwe bedrag geldt vanaf de
    eerstvolgende incasso. Dat zeggen we ook zo terug.
    """
    org = current_user.organization
    if org is None or not org.mollie_subscription_id:
        raise HTTPException(status_code=404, detail="Geen lopend abonnement")

    seats, bedrag = maandbedrag(db, org)
    if org.billing_seats == seats:
        return {"status": "ongewijzigd", "actieve_gebruikers": seats,
                "maandbedrag": _bedrag(bedrag)}

    try:
        mollie.wijzig_abonnement(
            org.mollie_customer_id, org.mollie_subscription_id,
            bedrag=_bedrag(bedrag))
    except mollie.MollieFout as exc:
        raise HTTPException(status_code=502, detail=f"Mollie: {exc.detail}")

    org.billing_seats = seats
    db.commit()
    return {
        "status": "bijgewerkt",
        "actieve_gebruikers": seats,
        "maandbedrag": _bedrag(bedrag),
        "ingangsdatum": "vanaf de eerstvolgende incasso",
    }


@router.post("/cancel")
def zeg_abonnement_op(
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Opzeggen. De organisatie houdt toegang tot het einde van de termijn."""
    org = current_user.organization
    if org is None or not org.mollie_subscription_id:
        raise HTTPException(status_code=404, detail="Geen lopend abonnement")

    try:
        mollie.zeg_abonnement_op(org.mollie_customer_id, org.mollie_subscription_id)
    except mollie.MollieFout as exc:
        if exc.status_code != 404:
            raise HTTPException(status_code=502, detail=f"Mollie: {exc.detail}")

    org.mollie_subscription_id = None
    org.billing_status = "canceled"
    db.commit()
    return {
        "status": "opgezegd",
        "toegang_tot": org.paid_until.isoformat() if org.paid_until else None,
    }


@router.get("/betalingen")
def betalingsoverzicht(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Betalingen van de eigen organisatie; de platform-eigenaar ziet alles."""
    query = db.query(Payment).order_by(Payment.created_at.desc())
    if not is_platform_owner(current_user):
        if current_user.organization_id is None:
            raise HTTPException(status_code=404, detail="Geen organisatie gevonden")
        query = query.filter(Payment.organization_id == current_user.organization_id)

    return [{
        "id": p.id,
        "organisatie_id": p.organization_id,
        "mollie_id": p.mollie_payment_id,
        "soort": p.sequence_type,
        "bedrag": p.amount,
        "valuta": p.currency,
        "status": p.status,
        "omschrijving": p.description,
        "betaald_op": p.paid_at.isoformat() if p.paid_at else None,
        "aangemaakt_op": p.created_at.isoformat() if p.created_at else None,
    } for p in query.limit(200).all()]
