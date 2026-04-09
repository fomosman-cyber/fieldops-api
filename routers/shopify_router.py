from fastapi import APIRouter, Depends, Request, HTTPException, Header
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from database import get_db
from models import Organization, User, AccountStatus, SubscriptionPlan
from auth import hash_password
from dotenv import load_dotenv
import hashlib
import hmac
import base64
import os
import secrets

load_dotenv()

router = APIRouter(prefix="/api/shopify", tags=["Shopify Integratie"])

SHOPIFY_API_SECRET = os.getenv("SHOPIFY_API_SECRET", "")


def verify_shopify_webhook(body: bytes, hmac_header: str) -> bool:
    """Verify dat de webhook echt van Shopify komt."""
    if not SHOPIFY_API_SECRET:
        return True  # Skip verificatie in development
    digest = hmac.new(
        SHOPIFY_API_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    computed_hmac = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed_hmac, hmac_header)


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

    if SHOPIFY_API_SECRET and not verify_shopify_webhook(body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="Ongeldige webhook signature")

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
    company = customer.get("company", f"{first_name} {last_name}")

    org = Organization(
        name=company or "Mijn Organisatie",
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

    # TODO: Stuur welkomst email met inlog gegevens
    return {
        "status": "created",
        "organization_id": org.id,
        "email": email,
        "temp_password": temp_password,
    }


@router.post("/webhook/subscription-cancelled")
async def subscription_cancelled_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_shopify_hmac_sha256: str = Header(default=""),
):
    """Shopify webhook: abonnement opgezegd."""
    body = await request.body()
    if SHOPIFY_API_SECRET and not verify_shopify_webhook(body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="Ongeldige webhook signature")

    data = await request.json()
    email = data.get("email", "")

    user = db.query(User).filter(User.email == email, User.is_org_admin == True).first()
    if user:
        org = user.organization
        org.status = AccountStatus.SUSPENDED
        db.commit()
        return {"status": "suspended", "organization_id": org.id}

    return {"status": "skipped", "reason": "user not found"}
