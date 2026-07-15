from fastapi import APIRouter, Depends, Request, HTTPException, Header
from sqlalchemy.orm import Session
from database import get_db
from models import (
    Organization, User, AccountStatus, SubscriptionPlan, is_reserved_org_name,
)
from auth import hash_password
from dotenv import load_dotenv
import hashlib
import hmac
import base64
import os
import secrets

load_dotenv()

router = APIRouter(prefix="/api/shopify", tags=["Shopify Integratie"])


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

    # Bepaal welk plan op basis van het product
    plan = SubscriptionPlan.STARTER
    max_users = 10
    for item in line_items:
        title = (item.get("title", "") or "").lower()
        if "professional" in title or "pro" in title:
            plan = SubscriptionPlan.PROFESSIONAL
            max_users = 999  # Onbeperkt
            break

    # Check of er al een account is
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        # Upgrade bestaande organisatie
        org = existing_user.organization
        org.plan = plan
        org.status = AccountStatus.ACTIVE
        org.max_users = max_users
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
        organization_id=org.id,
    )
    db.add(user)
    db.commit()

    # TODO: Stuur welkomst email met inlog gegevens (temp_password).
    # SECURITY: het tijdelijke wachtwoord NOOIT in de HTTP-response teruggeven —
    # webhook-responses kunnen gelogd/onderschept worden.
    return {
        "status": "created",
        "organization_id": org.id,
        "email": email,
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
