from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from database import get_db
from models import User, Invitation, Organization
from schemas import (
    UserResponse, UserUpdate, UserSelfUpdate, PasswordChangeRequest,
    InvitationCreate, InvitationResponse, AcceptInvitationRequest,
)
from auth import (
    get_current_user, hash_password, verify_password,
    validate_password_strength, invalidate_user_sessions,
)
from permissions import require_org_admin, list_assignable_roles
from email_service import send_invitation_email, send_welcome_email
from audit import log_action, ACTION
from models import UserRole
import secrets

router = APIRouter(prefix="/api/users", tags=["Gebruikers"])


@router.get("/roles")
def get_assignable_roles(current_user: User = Depends(get_current_user)):
    """Lijst toewijsbare rollen voor de UI (alleen admins krijgen niet-lege lijst)."""
    return list_assignable_roles(current_user)


# ─── GDPR Article 15 — Right of access (DSAR) ────────────────────────────────
# Een betrokkene mag een kopie krijgen van alle persoonsgegevens die wij
# verwerken. Wij geven JSON met alle records waar de gebruiker actor of
# eigenaar is. OAuth-tokens worden gesanitiseerd: alleen metadata (provider,
# scope, koppeldatum), nooit de access/refresh-token zelf.

def _collect_user_data(db: Session, user: User) -> dict:
    """Aggregeer alle persoonsgegevens van deze gebruiker. Eén shot, geen
    pagination — DSAR is eenmalig en het volume per user is begrensd."""
    from models import (
        Melding, AIAnalysis, AuditLog, PushSubscription, PasswordResetToken,
        GoogleOAuthToken, MicrosoftOAuthToken, UserSkill, Invitation,
    )

    def _serialize(rec, drop=()):
        """Naar dict zonder relationship-loops; 'drop' om gevoelige velden te
        verwijderen (OAuth-tokens, hashes, etc.)."""
        out = {}
        for c in rec.__table__.columns:
            if c.name in drop:
                continue
            v = getattr(rec, c.name, None)
            if hasattr(v, "isoformat"):  # datetime
                v = v.isoformat()
            elif hasattr(v, "value"):    # enum
                v = v.value
            out[c.name] = v
        return out

    profile = _serialize(user, drop=("hashed_password",))

    meldingen_created = [
        _serialize(m) for m in
        db.query(Melding).filter(Melding.created_by == user.id).all()
    ]
    meldingen_assigned = [
        _serialize(m) for m in
        db.query(Melding).filter(Melding.assigned_to == user.id).all()
    ]

    ai_analyses_run = [
        _serialize(a) for a in
        db.query(AIAnalysis).filter(AIAnalysis.created_by == user.id).all()
    ]
    ai_analyses_accepted = [
        _serialize(a) for a in
        db.query(AIAnalysis).filter(AIAnalysis.accepted_by == user.id).all()
    ]

    audit_actor = [
        _serialize(a) for a in
        db.query(AuditLog).filter(AuditLog.user_id == user.id).all()
    ]
    audit_about = [
        _serialize(a) for a in
        db.query(AuditLog).filter(
            AuditLog.entity_type == "user",
            AuditLog.entity_id == user.id,
            AuditLog.user_id != user.id,
        ).all()
    ]

    push_subs = [
        # 'endpoint' is de browser-push-URL; 'p256dh'/'auth' zijn keys voor
        # encryptie naar de specifieke device. Niet uniek persoonlijk gevoelig
        # in DSAR-zin maar wel device-specifiek — meegegeven, zonder filter.
        _serialize(p) for p in
        db.query(PushSubscription).filter(PushSubscription.user_id == user.id).all()
    ]

    skills = [
        _serialize(s) for s in
        db.query(UserSkill).filter(UserSkill.user_id == user.id).all()
    ]

    invitations_sent = [
        _serialize(i) for i in
        db.query(Invitation).filter(Invitation.invited_by == user.id).all()
    ]

    # OAuth-tokens — secrets verwijderen, alleen metadata teruggeven.
    google_meta = []
    g = db.query(GoogleOAuthToken).filter(GoogleOAuthToken.user_id == user.id).first()
    if g:
        google_meta.append(_serialize(g, drop=("access_token", "refresh_token")))

    ms_meta = []
    m = db.query(MicrosoftOAuthToken).filter(MicrosoftOAuthToken.user_id == user.id).first()
    if m:
        ms_meta.append(_serialize(m, drop=("access_token", "refresh_token")))

    # Reset-tokens: hash + status, geen raw token (die is sowieso al gehashed
    # in DB, maar we zijn extra voorzichtig).
    reset_tokens = [
        _serialize(t, drop=("token_hash",)) for t in
        db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).all()
    ]

    return {
        "schema_version": "1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "subject": {
            "user_id": user.id,
            "email": user.email,
            "organization_id": user.organization_id,
        },
        "legal_basis": {
            "regulation": "EU 2016/679 (GDPR) Article 15 — Right of access",
            "controller": "Klant-organisatie (FieldOps tenant)",
            "processor": "FieldOps B.V.",
        },
        "profile": profile,
        "meldingen_created": meldingen_created,
        "meldingen_assigned": meldingen_assigned,
        "ai_analyses_run": ai_analyses_run,
        "ai_analyses_accepted": ai_analyses_accepted,
        "skills": skills,
        "invitations_sent": invitations_sent,
        "audit_actor": audit_actor,
        "audit_about_me": audit_about,
        "push_subscriptions": push_subs,
        "oauth_google_metadata": google_meta,
        "oauth_microsoft_metadata": ms_meta,
        "password_reset_history": reset_tokens,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Self-service profiel + wachtwoord
# Eigen gegevens aanpassen — geen email, geen rol (vereisen admin).
# ─────────────────────────────────────────────────────────────────────────────

@router.patch("/me/profile", response_model=UserResponse)
def update_my_profile(
    update: UserSelfUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Self-service profiel-edit (alleen first_name, last_name, phone).

    Email en role kunnen niet via deze endpoint gewijzigd worden — vereist
    een org-admin via PUT /api/users/{user_id}.
    """
    data = update.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="Geen velden om te wijzigen")

    before = {k: getattr(current_user, k) for k in data.keys()}

    # Whitelist enforcement — pak alleen de 3 toegestane velden
    allowed = {"first_name", "last_name", "phone"}
    for field, value in data.items():
        if field not in allowed:
            continue
        # Strip strings + zet lege strings naar None voor phone
        if isinstance(value, str):
            value = value.strip()
            if field == "phone" and value == "":
                value = None
        setattr(current_user, field, value)

    db.commit()
    db.refresh(current_user)

    log_action(db, request, current_user,
               action=ACTION.USER_UPDATE, entity_type="user-self-profile",
               entity_id=current_user.id, before=before, after=data)
    return current_user


@router.post("/me/password")
def change_my_password(
    payload: PasswordChangeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Self-service wachtwoord-wijziging.

    Vereist het huidige wachtwoord ter verificatie. Bij succes worden alle
    bestaande JWT-sessies van deze gebruiker ge-invalideerd (na save logt
    de gebruiker op andere apparaten automatisch uit).
    """
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Huidig wachtwoord is onjuist")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="Nieuw wachtwoord moet anders zijn dan huidige")

    # Validatie (lengte + complexiteit)
    validate_password_strength(payload.new_password)

    current_user.hashed_password = hash_password(payload.new_password)
    # Alle bestaande sessies/JWTs invalideren — voorkomt dat een gestolen
    # token na een password-change nog werkt (best practice).
    invalidate_user_sessions(current_user)
    db.commit()

    log_action(db, request, current_user,
               action=ACTION.USER_UPDATE, entity_type="user-self-password",
               entity_id=current_user.id, after={"password_changed": True})
    return {"message": "Wachtwoord succesvol gewijzigd", "logout_other_sessions": True}


@router.get("/me/export")
def export_my_data(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """GDPR Article 15 — Right of access. Geeft alle persoonsgegevens van
    de ingelogde gebruiker als JSON. Gevoelige velden (password-hash, OAuth-
    tokens) worden verwijderd; metadata (provider, scope, datum) blijft."""
    payload = _collect_user_data(db, current_user)
    log_action(db, request, current_user,
               action=ACTION.DATA_EXPORT_SELF,
               entity_type="user", entity_id=current_user.id,
               extra={"row_counts": {k: len(v) for k, v in payload.items()
                                      if isinstance(v, list)}})
    return payload


@router.get("/{user_id}/export")
def export_user_data_admin(
    user_id: str,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Admin draait DSAR namens de tenant — voor afhandeling van een
    Article 15-aanvraag van een werknemer. Alleen binnen eigen organisatie
    (platform-eigenaar mag cross-org)."""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")
    is_platform_owner = (current_user.is_org_admin and current_user.organization
                         and current_user.organization.name == "FieldOps")
    if (not is_platform_owner and
            target.organization_id != current_user.organization_id):
        raise HTTPException(status_code=403, detail="Geen toegang tot deze gebruiker")

    payload = _collect_user_data(db, target)
    log_action(db, request, current_user,
               action=ACTION.DATA_EXPORT_ADMIN,
               entity_type="user", entity_id=target.id,
               extra={"target_email": target.email,
                      "row_counts": {k: len(v) for k, v in payload.items()
                                      if isinstance(v, list)}})
    return payload


# ─── GDPR Article 17 — Right to erasure ──────────────────────────────────────
# Anonymisatie i.p.v. harde delete: business-records (meldingen, AI-analyses,
# audit-log) blijven, maar persoonsgegevens worden overschreven. Dit is
# GDPR-conform én bewaart de audit-trail die voor andere wetgeving (NEN7510,
# Archiefwet) juist verplicht is.
#
# Wat wel blijft:  meldingen, ai_analyses (auteurs-id wordt geanonimiseerde
#                  user-rij), audit-events (user_id blijft, user_email wordt
#                  overschreven met geredacteerd-id zodat sortering werkt).
# Wat wordt geredigeerd: email, voornaam, achternaam, telefoon, password-hash,
#                  must_change_password, last_login.
# Wat wordt verwijderd: OAuth-tokens, push-subscriptions, password-reset-tokens,
#                  user_skills, calendar-koppelingen.

ANONYMIZED_EMAIL_DOMAIN = "deleted.invalid"  # RFC 6761 reserved TLD


def _anonymize_user(db: Session, user: User) -> dict:
    """Voer de anonymisatie uit. Geeft een summary terug van wat er gebeurde."""
    from models import (
        PushSubscription, PasswordResetToken,
        GoogleOAuthToken, MicrosoftOAuthToken, UserSkill,
        CalendarLink, MicrosoftCalendarLink, AuditLog,
    )

    redacted_id = f"redacted-{user.id[:8]}"
    redacted_email = f"{redacted_id}@{ANONYMIZED_EMAIL_DOMAIN}"

    summary = {
        "user_id": user.id,
        "redacted_email": redacted_email,
        "deleted": {},
        "redacted_audit_rows": 0,
    }

    # 1. Hard-delete aan-user-gekoppelde records die GEEN compliance-waarde hebben
    for model, label in [
        (GoogleOAuthToken, "google_oauth_tokens"),
        (MicrosoftOAuthToken, "microsoft_oauth_tokens"),
        (PushSubscription, "push_subscriptions"),
        (PasswordResetToken, "password_reset_tokens"),
        (UserSkill, "user_skills"),
        (CalendarLink, "calendar_links"),
        (MicrosoftCalendarLink, "microsoft_calendar_links"),
    ]:
        n = db.query(model).filter(model.user_id == user.id).delete(
            synchronize_session=False)
        summary["deleted"][label] = n

    # 2. Audit-log: user_email overschrijven met redacted_email, user_id BEHOUDEN
    #    (die is alleen DB-id, geen PII). Op die manier blijft het spoor leesbaar
    #    en koppelbaar maar zonder persoonsgegevens. Idem voor entity_id-rijen
    #    waar deze user het onderwerp was.
    actor_rows = db.query(AuditLog).filter(AuditLog.user_id == user.id).all()
    for r in actor_rows:
        r.user_email = redacted_email
    about_rows = db.query(AuditLog).filter(
        AuditLog.entity_type == "user", AuditLog.entity_id == user.id,
    ).all()
    summary["redacted_audit_rows"] = len(actor_rows) + len(about_rows)
    # entity_id (user.id) blijft staan — anders kun je entity-historie niet
    # meer reconstrueren. user.id zelf is geen PII.

    # 3. User-record zelf overschrijven en deactiveren
    user.email = redacted_email
    user.first_name = "Verwijderd"
    user.last_name = ""
    user.phone = ""
    # Onbruikbare hash zodat login onmogelijk is, maar de NOT NULL kolom-eis
    # niet schendt. bcrypt-formaat — onmogelijk om matching plaintext te vinden.
    user.hashed_password = "$2b$12$" + ("a" * 53)
    user.is_active = False
    user.is_org_admin = False
    user.must_change_password = False
    user.last_login = None
    # 4. Direct alle uitstaande JWTs revoken — voorkomt dat de oude session
    # nog 24u na "vergetelheidsverzoek" data kan inzien (GDPR Art.17 vereist
    # onmiddellijke effectiviteit).
    invalidate_user_sessions(user)

    db.commit()
    return summary


@router.delete("/me/anonymize")
def erase_my_account(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """GDPR Article 17 — Right to erasure ('vergetelheidsrecht').

    Voert anonymisatie uit op het eigen account. Audit-log + business-records
    blijven, persoonsgegevens worden geredacteerd. Het account wordt
    gedeactiveerd; nieuwe login is onmogelijk.
    """
    user_id = current_user.id
    org_id = current_user.organization_id
    original_email = current_user.email
    summary = _anonymize_user(db, current_user)
    # Loggen ZONDER user — anders gaat het rate-limit-spoor terug naar
    # de zojuist geanonimiseerde rij (en de log-event krijgt de redacted
    # email). We loggen extra={"by"} apart.
    log_action(db, request, None,
               action=ACTION.USER_ANONYMIZE_SELF,
               entity_type="user", entity_id=user_id,
               extra={"organization_id": org_id,
                      "original_email_redacted": True,
                      "summary": summary})
    return {
        "message": "Uw account is geanonimiseerd. Persoonsgegevens zijn verwijderd; audit-records zijn bewaard zonder persoonsgegevens.",
        "summary": summary,
    }


@router.delete("/{user_id}/anonymize")
def erase_user_account_admin(
    user_id: str,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Admin verwerkt een Article 17-verzoek namens de tenant.

    Alleen binnen eigen organisatie; platform-eigenaar mag cross-org.
    Een admin kan zichzelf NIET via dit endpoint anonymiseren — gebruik
    DELETE /api/users/me daarvoor (preventie tegen 'click sluit account').
    """
    if user_id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail="Gebruik DELETE /api/users/me om uw eigen account te wissen.",
        )
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")
    is_platform_owner = (current_user.is_org_admin and current_user.organization
                         and current_user.organization.name == "FieldOps")
    if (not is_platform_owner and
            target.organization_id != current_user.organization_id):
        raise HTTPException(status_code=403, detail="Geen toegang tot deze gebruiker")

    target_id = target.id
    summary = _anonymize_user(db, target)
    log_action(db, request, current_user,
               action=ACTION.USER_ANONYMIZE_ADMIN,
               entity_type="user", entity_id=target_id,
               extra={"summary": summary})
    return {
        "message": "Account is geanonimiseerd. Audit-records zijn bewaard zonder persoonsgegevens.",
        "summary": summary,
    }


@router.get("/", response_model=list[UserResponse])
def list_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alle gebruikers van de organisatie ophalen."""
    return (
        db.query(User)
        .filter(User.organization_id == current_user.organization_id)
        .order_by(User.created_at)
        .all()
    )


@router.put("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    update: UserUpdate,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Gebruiker bijwerken (alleen admin)."""
    # Platform owner (FieldOps org) can edit ANY user cross-org
    if current_user.is_org_admin and current_user.organization and current_user.organization.name == "FieldOps":
        user = db.query(User).filter(User.id == user_id).first()
    else:
        user = db.query(User).filter(
            User.id == user_id,
            User.organization_id == current_user.organization_id,
        ).first()
    if not user:
        raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")

    update_data = update.model_dump(exclude_unset=True)
    role_before = user.role.value if user.role else None

    # Handle password update separately (needs hashing) — niet in audit-log
    password_changed = "password" in update_data
    if password_changed:
        new_pw = update_data.pop("password")
        validate_password_strength(new_pw)
        user.hashed_password = hash_password(new_pw)
        # Bij wachtwoordwijziging: revoke alle bestaande JWTs van deze user.
        # Voorkomt dat een gestolen token nog 24u na een reset-actie blijft
        # werken (best practice — Devise/Auth0 doen dit ook).
        invalidate_user_sessions(user)
    # Snapshot before/after voor de overige velden
    before = {k: getattr(user, k) for k in update_data.keys()
              if k not in ("password",)}
    # Coerce role-enum naar str voor logging
    if "role" in before and hasattr(before["role"], "value"):
        before["role"] = before["role"].value

    for field, value in update_data.items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)

    role_after = user.role.value if user.role else None
    if role_before != role_after:
        log_action(db, request, current_user,
                   action=ACTION.USER_ROLE_CHANGE, entity_type="user", entity_id=user.id,
                   before={"role": role_before}, after={"role": role_after})
    elif update_data:
        loggable_after = {k: (v.value if hasattr(v, "value") else v)
                          for k, v in update_data.items()}
        log_action(db, request, current_user,
                   action=ACTION.USER_UPDATE, entity_type="user", entity_id=user.id,
                   before=before, after=loggable_after)

    if password_changed:
        log_action(db, request, current_user,
                   action=ACTION.PASSWORD_CHANGED, entity_type="user", entity_id=user.id,
                   extra={"by_admin": True})

    return user


@router.delete("/{user_id}")
def deactivate_user(
    user_id: str,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Gebruiker deactiveren (alleen admin)."""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Je kunt jezelf niet deactiveren")

    # Platform owner can deactivate ANY user
    if current_user.is_org_admin and current_user.organization and current_user.organization.name == "FieldOps":
        user = db.query(User).filter(User.id == user_id).first()
    else:
        user = db.query(User).filter(
            User.id == user_id,
            User.organization_id == current_user.organization_id,
        ).first()
    if not user:
        raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")

    user.is_active = False
    # Revoke uitstaande tokens: gedeactiveerde users moeten direct uit kunnen
    # loggen, niet pas na 24u JWT-TTL.
    invalidate_user_sessions(user)
    db.commit()
    log_action(db, request, current_user,
               action=ACTION.USER_DEACTIVATE, entity_type="user", entity_id=user.id,
               before={"is_active": True, "email": user.email})
    return {"message": f"Gebruiker {user.email} is gedeactiveerd"}


# --- Direct account aanmaken (admin) ---

class AdminCreateUser(BaseModel):
    email: str
    password: str
    first_name: str
    last_name: str
    role: str = "viewer"
    phone: str = ""

@router.post("/create", response_model=UserResponse)
def admin_create_user(
    data: AdminCreateUser,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Direct een gebruiker aanmaken (alleen admin). Geen uitnodiging nodig."""
    validate_password_strength(data.password)
    existing = db.query(User).filter(User.email == data.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Dit e-mailadres is al in gebruik")

    # Valideer rol: moet één van de bekende UserRole-waarden zijn
    try:
        role_enum = UserRole(data.role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Ongeldige rol: {data.role}")

    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        first_name=data.first_name,
        last_name=data.last_name,
        phone=data.phone,
        role=role_enum,
        is_org_admin=(role_enum == UserRole.ADMIN),
        organization_id=current_user.organization_id,
        must_change_password=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log_action(db, request, current_user,
               action=ACTION.USER_CREATE, entity_type="user", entity_id=user.id,
               after={"email": user.email, "role": data.role,
                      "organization_id": user.organization_id})

    # Stuur welkom email
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    try:
        send_welcome_email(data.email, f"{data.first_name} {data.last_name}", org.name if org else "FieldOps")
    except Exception as e:
        print(f"Welcome email error: {e}")

    return user


# --- Uitnodigingen ---

@router.post("/invite", response_model=InvitationResponse)
def invite_user(
    invitation: InvitationCreate,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Gebruiker uitnodigen (alleen admin)."""
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()

    # Check max users
    current_count = db.query(User).filter(
        User.organization_id == org.id,
        User.is_active == True,
    ).count()
    if current_count >= org.max_users:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum aantal gebruikers ({org.max_users}) bereikt voor uw abonnement",
        )

    # Check of email al bestaat
    existing = db.query(User).filter(User.email == invitation.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Dit e-mailadres is al in gebruik")

    # Check op bestaande uitnodiging
    existing_inv = db.query(Invitation).filter(
        Invitation.email == invitation.email,
        Invitation.organization_id == org.id,
        Invitation.accepted == False,
    ).first()
    if existing_inv:
        raise HTTPException(status_code=400, detail="Er is al een uitnodiging verstuurd naar dit e-mailadres")

    token = secrets.token_urlsafe(32)
    inv = Invitation(
        email=invitation.email,
        role=invitation.role,
        token=token,
        invited_by=current_user.id,
        organization_id=org.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)

    log_action(db, request, current_user,
               action=ACTION.USER_INVITE, entity_type="invitation", entity_id=inv.id,
               after={"email": invitation.email,
                      "role": invitation.role.value if hasattr(invitation.role, 'value') else str(invitation.role)})

    # Stuur uitnodiging email
    inviter_name = f"{current_user.first_name} {current_user.last_name}".strip()
    send_invitation_email(
        to_email=invitation.email,
        inviter_name=inviter_name,
        org_name=org.name,
        role=invitation.role.value if hasattr(invitation.role, 'value') else str(invitation.role),
        token=token,
    )

    return inv


@router.get("/invitations", response_model=list[InvitationResponse])
def list_invitations(
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Alle uitnodigingen ophalen (alleen admin)."""
    return (
        db.query(Invitation)
        .filter(Invitation.organization_id == current_user.organization_id)
        .order_by(Invitation.created_at.desc())
        .all()
    )


@router.post("/accept-invitation", response_model=UserResponse)
def accept_invitation(
    request: AcceptInvitationRequest,
    db: Session = Depends(get_db),
):
    """Uitnodiging accepteren en account aanmaken."""
    inv = db.query(Invitation).filter(
        Invitation.token == request.token,
        Invitation.accepted == False,
    ).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Ongeldige of verlopen uitnodiging")

    if inv.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Deze uitnodiging is verlopen")

    # Maak gebruiker aan
    user = User(
        email=inv.email,
        hashed_password=hash_password(request.password),
        first_name=request.first_name,
        last_name=request.last_name,
        phone=request.phone,
        role=inv.role,
        is_org_admin=False,
        organization_id=inv.organization_id,
    )
    db.add(user)

    inv.accepted = True
    db.commit()
    db.refresh(user)

    # Stuur welkom email
    org = db.query(Organization).filter(Organization.id == inv.organization_id).first()
    send_welcome_email(
        to_email=user.email,
        user_name=f"{user.first_name} {user.last_name}".strip(),
        org_name=org.name if org else "",
    )

    return user
