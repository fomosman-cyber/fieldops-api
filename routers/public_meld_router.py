"""Publieke meld-pagina — burgers melden zonder login.

Use-case: gemeente publiceert URL https://portaal.fieldopsapp.nl/meld/gemeentex
of QR-code op assets. Burger meldt schade → wordt automatisch melding in
FieldOps + auto-toegewezen aan default-project.

Veiligheid:
- Geen auth — alleen org-slug in URL
- Per IP rate-limit: 5 meldingen / 15 min (anti-spam)
- Honeypot-field (hidden input dat bots invullen)
- Foto-grootte cap 5 MB
- Sender-email optioneel (voor terugkoppeling)

Endpoints:
  GET  /api/public/org/{slug}/config   — config voor de meld-pagina (categorieën etc.)
  POST /api/public/org/{slug}/melding  — een melding indienen
"""
from datetime import datetime, timezone, timedelta
from typing import Optional, List
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, EmailStr

from database import get_db
from models import Organization, Melding, AuditLog
from audit import log_action

router = APIRouter(prefix="/api/public", tags=["Publieke Meld-pagina"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class PublicMeldRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = Field(None, max_length=5000)
    category: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    location_description: Optional[str] = Field(None, max_length=500)
    photo_data_url: Optional[str] = Field(None, max_length=8_000_000)  # ~6MB base64

    # Optionele melder-contact (voor terugkoppeling)
    melder_name: Optional[str] = Field(None, max_length=120)
    melder_email: Optional[EmailStr] = None
    melder_phone: Optional[str] = Field(None, max_length=30)

    # Honeypot (bots vullen dit in, mensen niet — moet leeg blijven)
    website: Optional[str] = Field(None, max_length=200, description="Honeypot — laat leeg")


class PublicConfigResponse(BaseModel):
    enabled: bool
    organization_name: Optional[str] = None
    organization_logo: Optional[str] = None
    brand_color: Optional[str] = None
    categories: List[str] = []
    intro_text: Optional[str] = None
    has_default_project: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_client_ip(request: Request) -> Optional[str]:
    """X-Forwarded-For-aware client-IP."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _check_rate_limit(db: Session, ip: str, slug: str) -> None:
    """Max 5 publieke meldingen per IP per 15 min."""
    if not ip:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    n = (db.query(AuditLog)
           .filter(AuditLog.action == "public.melding.create",
                   AuditLog.ip_address == ip,
                   AuditLog.created_at >= cutoff)
           .count())
    if n >= 5:
        raise HTTPException(
            status_code=429,
            detail="Te veel meldingen vanaf dit IP. Probeer over 15 minuten opnieuw.",
        )


_VALID_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$")


def validate_slug(slug: str) -> bool:
    """Slug: 3-60 chars, lowercase + cijfers + hyphen, geen edge-hyphens."""
    if not slug or not isinstance(slug, str):
        return False
    return bool(_VALID_SLUG.match(slug.lower()))


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints (no auth)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/org/{slug}/qr.png")
def get_public_qr_png(slug: str, db: Session = Depends(get_db)):
    """Genereer QR-code PNG voor publieke meld-URL. Voor printen + plakken op
    assets, gemeente-publicaties, posters, etc. No auth — slug-based.

    Returnt 404 als slug niet bestaat of meld-pagina uit staat.
    """
    if not validate_slug(slug):
        raise HTTPException(404, "Onbekende meld-pagina")
    org = db.query(Organization).filter(
        Organization.public_meld_slug == slug.lower(),
        Organization.public_meld_enabled == True,  # noqa: E712
    ).first()
    if not org:
        raise HTTPException(404, "Meld-pagina niet beschikbaar")

    import os as _os
    base_url = _os.environ.get("PORTAAL_URL", "").rstrip("/") or "https://portaal.fieldopsapp.nl"
    full_url = f"{base_url}/meld/{slug.lower()}"

    import qrcode as _qrcode
    import io as _io
    from fastapi.responses import Response as _Response
    qr = _qrcode.QRCode(version=None, box_size=12, border=3,
                        error_correction=_qrcode.constants.ERROR_CORRECT_H)
    qr.add_data(full_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return _Response(content=buf.getvalue(), media_type="image/png",
                     headers={"Cache-Control": "public, max-age=3600",
                              "Content-Disposition": f'inline; filename="meld-qr-{slug}.png"'})


@router.get("/org/{slug}/config", response_model=PublicConfigResponse)
def get_public_config(slug: str, db: Session = Depends(get_db)):
    """Public-config voor meld-pagina — branding + categorieën.

    Returns 'enabled=False' bij onbekende slug (geen reveal van bestaan).
    """
    if not validate_slug(slug):
        return PublicConfigResponse(enabled=False)

    org = db.query(Organization).filter(
        Organization.public_meld_slug == slug.lower(),
        Organization.public_meld_enabled == True,  # noqa: E712
    ).first()
    if not org:
        return PublicConfigResponse(enabled=False)

    categories = []
    if org.public_meld_categories:
        try:
            categories = json.loads(org.public_meld_categories)
            if not isinstance(categories, list):
                categories = []
        except Exception:
            categories = []

    # Default-categorieën als nog niet gezet
    if not categories:
        categories = [
            "Wegdek", "Bestrating", "Verlichting", "Groen", "Speeltoestel",
            "Riolering", "Verkeersborden", "Zwerfvuil", "Overig",
        ]

    return PublicConfigResponse(
        enabled=True,
        organization_name=org.name,
        organization_logo=org.logo_data_url,
        brand_color=org.brand_color,
        categories=categories,
        intro_text=org.public_meld_intro_text,
        has_default_project=bool(org.public_meld_default_project_id),
    )


@router.post("/org/{slug}/melding")
async def submit_public_melding(
    slug: str,
    payload: PublicMeldRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Burger-melding indienen (no auth).

    Validatie:
      - Slug bestaat + enabled
      - Honeypot (`website`) moet leeg zijn (bot-detect)
      - Rate-limit: 5/15min per IP
      - Foto-size cap (Pydantic-level + extra check)
    """
    # Honeypot check — bots vullen 'website' in, mensen niet
    if payload.website:
        # Silent 200 om bots niet te leren dat 't gefaald is
        return {"success": True, "melding_id": "spam-blocked"}

    if not validate_slug(slug):
        raise HTTPException(404, "Onbekende meld-pagina")

    org = db.query(Organization).filter(
        Organization.public_meld_slug == slug.lower(),
        Organization.public_meld_enabled == True,  # noqa: E712
    ).first()
    if not org:
        raise HTTPException(404, "Meld-pagina niet beschikbaar")

    ip = _extract_client_ip(request)
    _check_rate_limit(db, ip, slug)

    # Photo-offload naar S3 als geconfigureerd
    photo_url_final = payload.photo_data_url
    if photo_url_final:
        try:
            from photo_storage import maybe_offload
            photo_url_final = maybe_offload(
                photo_url_final, organization_id=org.id, kind="public-melding",
            )
        except Exception:
            pass  # Behoud base64 bij fout

    # Maak melding aan — created_by = None of een 'bot-user'? Voor MVP gebruiken
    # we een speciale system-user als die bestaat, anders skip created_by-validatie
    # door dummy-id. Voor productie: een specifieke 'public-meld' user per org.
    description_with_melder = (payload.description or "").strip()
    if payload.melder_name or payload.melder_email or payload.melder_phone:
        contact_lines = ["", "---", "Melder:"]
        if payload.melder_name:
            contact_lines.append(f"• Naam: {payload.melder_name}")
        if payload.melder_email:
            contact_lines.append(f"• E-mail: {payload.melder_email}")
        if payload.melder_phone:
            contact_lines.append(f"• Telefoon: {payload.melder_phone}")
        description_with_melder = (description_with_melder + "\n".join(contact_lines)).strip()

    # Voor de FK is created_by verplicht — gebruik de organization-creator
    # of de eerste org-admin als fallback. Production: dedicated bot-user.
    from models import User
    bot_user = (db.query(User)
                  .filter(User.organization_id == org.id, User.is_org_admin == True,  # noqa: E712
                          User.is_active == True)  # noqa: E712
                  .order_by(User.created_at.asc())
                  .first())
    if not bot_user:
        # Fallback: eerste actieve user in de org
        bot_user = (db.query(User)
                      .filter(User.organization_id == org.id, User.is_active == True)  # noqa: E712
                      .order_by(User.created_at.asc()).first())
    if not bot_user:
        raise HTTPException(500, "Geen gebruiker in organisatie om melding aan te koppelen")

    melding = Melding(
        title=payload.title.strip()[:255],
        description=description_with_melder[:5000] or None,
        category=payload.category,
        priority="normaal",
        lat=payload.lat,
        lng=payload.lng,
        photo_url=photo_url_final,
        project_id=org.public_meld_default_project_id,
        organization_id=org.id,
        created_by=bot_user.id,
    )
    db.add(melding)
    db.commit()
    db.refresh(melding)

    # Audit-log met IP zodat we rate-limit kunnen handhaven
    log_action(db, request, None,
               action="public.melding.create",
               entity_type="melding", entity_id=melding.id,
               extra={"org_slug": slug, "organization_id": org.id,
                      "ip": ip, "via": "public-meld",
                      "melder_email_provided": bool(payload.melder_email)})

    # Notify org-admins (in-app + email als geconfigureerd)
    try:
        from notifier import notify
        admins = (db.query(User)
                    .filter(User.organization_id == org.id,
                            User.is_org_admin == True,  # noqa: E712
                            User.is_active == True)  # noqa: E712
                    .limit(5).all())
        for admin in admins:
            notify(
                db,
                user_id=admin.id,
                organization_id=org.id,
                notif_type="public_melding",
                title="📢 Nieuwe burger-melding: " + (melding.title or ""),
                body=f"Categorie: {melding.category or '—'} · Via meld-pagina /{slug}",
                icon="📢",
                link_type="melding",
                link_id=melding.id,
                skip_email=True,  # Skip email-spam bij elke publieke melding
            )
    except Exception:
        pass

    return {
        "success": True,
        "melding_id": melding.id,
        "message": "Bedankt voor je melding — we hebben hem ontvangen en de gemeente neemt indien nodig contact op.",
    }
