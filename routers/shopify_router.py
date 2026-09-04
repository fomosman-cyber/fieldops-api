from fastapi import APIRouter, Depends, Request, HTTPException, Header
from sqlalchemy.orm import Session
from database import get_db
from models import (
    Organization, User, AccountStatus, SubscriptionPlan, PORTAL_MODULES,
    is_reserved_org_name,
)
from auth import hash_password
import json
from dotenv import load_dotenv
import hashlib
import hmac
import base64
import os
import secrets

load_dotenv()

router = APIRouter(prefix="/api/shopify", tags=["Shopify Integratie"])


# Wat een licentieproduct geeft, per SKU. Moet gelijk blijven aan de metafields
# fieldops.plan / fieldops.modules / fieldops.seats op het Shopify-product; de
# webhook-payload bevat zelf geen metafields, dus die staan hier nog een keer.
# `plan` is puur een label — max_users en enabled_modules doen het echte werk.
LICENSE_SKUS: dict[str, dict] = {
    # FieldOps - per gebruiker, per maand (EUR 9)
    "FO-INSP-1M": {
        "plan": SubscriptionPlan.PROFESSIONAL,
        "modules": list(PORTAL_MODULES.keys()),
        "seats_per_unit": 1,
    },
    # Founding Inspector - 2 maanden pilot. Gearchiveerd in de shop, maar blijft
    # hier staan zodat oude bestellingen nog correct worden afgehandeld.
    "FO-FOUND-2M": {
        "plan": SubscriptionPlan.PROFESSIONAL,
        "modules": list(PORTAL_MODULES.keys()),
        "seats_per_unit": 1,
    },
}


def _entitlements(line_items: list) -> dict | None:
    """Wat geeft deze bestelling? None als er geen licentieproduct in zit.

    Bewust op SKU en niet op producttitel: titels veranderen en een
    substring-match ("pro" in "Proefmaand") kent per ongeluk het verkeerde
    pakket toe. Regels zonder bekende SKU worden genegeerd, zodat een
    bestelling met een los artikel erbij geen org aanmaakt.
    """
    modules: set[str] = set()
    seats = 0
    plan: SubscriptionPlan | None = None

    for item in line_items or []:
        spec = LICENSE_SKUS.get((item.get("sku") or "").strip().upper())
        if not spec:
            continue
        try:
            qty = int(item.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1
        seats += spec["seats_per_unit"] * max(1, qty)
        modules.update(spec["modules"])
        if plan is None or spec["plan"] == SubscriptionPlan.PROFESSIONAL:
            plan = spec["plan"]

    if plan is None:
        return None
    return {"plan": plan, "modules": sorted(modules), "seats": max(1, seats)}


def _shopify_secret() -> str:
    """Secret op request-tijd lezen (niet cachen) zodat config-wijzigingen
    en tests direct doorwerken."""
    return os.getenv("SHOPIFY_API_SECRET", "")


def verify_shopify_webhook(body: bytes, hmac_header: str) -> bool:
    """Verify dat de webhook echt van Shopify komt.

    SECURITY: fail-closed. Zonder geconfigureerde secret of zonder
    HMAC-header is de webhook NOOIT geldig — deze endpoint maakt
    org-admin-accounts aan en mag dus nooit ongeauthenticeerd werken.
    """
    secret = _shopify_secret()
    if not secret or not hmac_header:
        return False
    digest = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    computed_hmac = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed_hmac, hmac_header)


def _require_valid_webhook(body: bytes, hmac_header: str) -> None:
    """Gemeenschappelijke guard voor alle Shopify-webhooks.

    - Secret niet geconfigureerd -> 503 (integratie staat uit; nooit
      stilletjes ongeauthenticeerd accepteren).
    - Ongeldige/ontbrekende signature -> 401.
    """
    if not _shopify_secret():
        raise HTTPException(
            status_code=503,
            detail="Shopify-integratie is niet geconfigureerd op deze omgeving")
    if not verify_shopify_webhook(body, hmac_header):
        raise HTTPException(status_code=401, detail="Ongeldige webhook signature")


@router.post("/webhook/order-paid")
async def order_paid_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_shopify_hmac_sha256: str = Header(default=""),
):
    """
    Shopify webhook: wanneer een bestelling betaald is.
    Maakt automatisch een organisatie + admin account aan.
    """
    body = await request.body()
    _require_valid_webhook(body, x_shopify_hmac_sha256)

    data = await request.json()

    email = data.get("email", "")
    customer = data.get("customer", {})
    line_items = data.get("line_items", [])

    if not email:
        return {"status": "skipped", "reason": "no email"}

    ent = _entitlements(line_items)
    if ent is None:
        # Geen licentieproduct in deze bestelling — niets provisionen. Beter
        # dan een org met verkeerde rechten aanmaken op basis van een gok.
        return {"status": "skipped", "reason": "geen licentieproduct in de bestelling"}

    plan = ent["plan"]
    max_users = ent["seats"]

    # Check of er al een account is
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        # Upgrade bestaande organisatie
        org = existing_user.organization
        org.plan = plan
        org.status = AccountStatus.ACTIVE
        # Bijbestellen mag nooit seats afpakken: een order van 3 stuks verlaagt
        # een bestaande org van 10 gebruikers niet naar 3.
        org.max_users = max(org.max_users or 0, max_users)
        # Idem voor modules: bijkopen zet er alleen bij. NULL = alles aan, dus
        # dat laten we met rust in plaats van het alsnog te beperken.
        if org.enabled_modules is not None:
            try:
                huidig = set(json.loads(org.enabled_modules))
            except (ValueError, TypeError):
                huidig = set()
            org.enabled_modules = json.dumps(sorted(huidig | set(ent["modules"])))
        org.shopify_customer_id = str(customer.get("id", ""))
        db.commit()
        return {"status": "upgraded", "organization_id": org.id}

    # Maak nieuwe organisatie
    first_name = customer.get("first_name", "")
    last_name = customer.get("last_name", "")
    company = (customer.get("company") or "").strip() or f"{first_name} {last_name}".strip()

    # SECURITY: "FieldOps" (en varianten) is gereserveerd — een org met die
    # naam zou de aangemaakte org-admin platform-eigenaar maken. Het model
    # blokkeert dit ook hard (ReservedOrgNameError), maar hier vangen we het
    # netjes af met een veilige fallback-naam.
    if is_reserved_org_name(company):
        company = f"{first_name} {last_name}".strip()
    if not company or is_reserved_org_name(company):
        company = "Mijn Organisatie"

    org = Organization(
        name=company,
        plan=plan,
        status=AccountStatus.ACTIVE,
        max_users=max_users,
        # Alleen de modules waar de bestelling recht op geeft. Zonder dit blijft
        # het veld NULL en betekent dat "alles aan" — dan zou elke betalende
        # klant automatisch het volledige portaal krijgen.
        enabled_modules=json.dumps(ent["modules"]),
        shopify_customer_id=str(customer.get("id", "")),
    )
    db.add(org)
    db.flush()

    temp_password = secrets.token_urlsafe(12)
    user = User(
        email=email,
        hashed_password=hash_password(temp_password),
        first_name=first_name or "Admin",
        last_name=last_name or "",
        phone=customer.get("phone", ""),
        role="admin",
        is_org_admin=True,
        must_change_password=True,
        organization_id=org.id,
    )
    db.add(user)
    db.commit()

    # Welkomstmail met de inloggegevens. Best effort: een mailfout mag de
    # bestelling niet laten mislukken, want Shopify probeert dan opnieuw en
    # dat zou een tweede org opleveren.
    email_sent = False
    try:
        from email_service import send_license_welcome
        email_sent = bool(send_license_welcome(user, temp_password, org, seats=max_users))
    except Exception as e:  # noqa: BLE001 - mag de webhook nooit laten falen
        print(f"[SHOPIFY] Welkomstmail mislukt voor {email}: {e}")

    # SECURITY: het tijdelijke wachtwoord NOOIT in de HTTP-response teruggeven —
    # webhook-responses kunnen gelogd/onderschept worden.
    return {
        "status": "created",
        "organization_id": org.id,
        "email": email,
        "seats": max_users,
        "modules": ent["modules"],
        "welcome_email_sent": email_sent,
    }


@router.post("/webhook/subscription-cancelled")
async def subscription_cancelled_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_shopify_hmac_sha256: str = Header(default=""),
):
    """Shopify webhook: abonnement opgezegd."""
    body = await request.body()
    _require_valid_webhook(body, x_shopify_hmac_sha256)

    data = await request.json()
    email = data.get("email", "")

    user = db.query(User).filter(User.email == email, User.is_org_admin == True).first()
    if user:
        org = user.organization
        org.status = AccountStatus.SUSPENDED
        db.commit()
        return {"status": "suspended", "organization_id": org.id}

    return {"status": "skipped", "reason": "user not found"}
