from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, Enum as SQLEnum, Text, Float
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime, timezone
import enum
import uuid


def generate_uuid():
    return str(uuid.uuid4())


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    CONTRACTOR = "contractor"
    INSPECTOR = "inspector"
    TECHNICIAN = "technician"
    VIEWER = "viewer"


class AccountStatus(str, enum.Enum):
    ACTIVE = "active"
    TRIAL = "trial"
    EXPIRED = "expired"
    SUSPENDED = "suspended"


class SubscriptionPlan(str, enum.Enum):
    STARTER = "starter"
    PROFESSIONAL = "professional"


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    plan = Column(SQLEnum(SubscriptionPlan), default=SubscriptionPlan.STARTER)
    status = Column(SQLEnum(AccountStatus), default=AccountStatus.TRIAL)
    max_users = Column(Integer, default=10)
    trial_ends_at = Column(DateTime, nullable=True)
    shopify_customer_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    users = relationship("User", back_populates="organization")
    invitations = relationship("Invitation", back_populates="organization")


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    phone = Column(String(50), nullable=True)
    role = Column(SQLEnum(UserRole), default=UserRole.VIEWER)
    is_active = Column(Boolean, default=True)
    is_org_admin = Column(Boolean, default=False)
    must_change_password = Column(Boolean, default=False, nullable=False)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = Column(DateTime, nullable=True)

    organization = relationship("Organization", back_populates="users")


class Invitation(Base):
    __tablename__ = "invitations"

    id = Column(String, primary_key=True, default=generate_uuid)
    email = Column(String(255), nullable=False)
    role = Column(SQLEnum(UserRole), default=UserRole.VIEWER)
    token = Column(String(255), unique=True, nullable=False)
    invited_by = Column(String, ForeignKey("users.id"), nullable=False)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    accepted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=False)

    organization = relationship("Organization", back_populates="invitations")
    inviter = relationship("User", foreign_keys=[invited_by])


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    gemeente = Column(String(255), nullable=True)  # municipality
    status = Column(String(50), default="active")  # active, completed, archived
    boundary_geojson = Column(Text, nullable=True)  # GeoJSON polygon for project area
    color = Column(String(7), default="#00d4ff")  # hex color for map display
    categories = Column(Text, nullable=True)  # JSON array of category strings
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    organization = relationship("Organization")
    creator = relationship("User", foreign_keys=[created_by])


class Asset(Base):
    """Een fysiek object in de infrastructuur — bijv. lantaarnpaal, put, wegvak,
    duiker, brug, sluis. Meldingen worden aan assets gekoppeld zodat je per
    object onderhoud, inspecties en levensduur kan volgen.

    Hierarchie via `parent_asset_id`: een wegvak bevat 12 putten, elke put
    heeft 3 deksels. Self-referencing FK; de tree-API komt uit het router-laag.

    `properties_json` is een vrije JSON-string (SQLite-vriendelijk) met asset-
    type-specifieke velden — bijv. {"materiaal": "beton", "diameter_mm": 800}.
    """
    __tablename__ = "assets"

    id = Column(String, primary_key=True, default=generate_uuid)

    # Identificatie
    code = Column(String(64), nullable=False, index=True)  # interne/extern code; uniek per organisatie via constraint
    name = Column(String(255), nullable=True)              # leesbare naam (optioneel — code is leidend)
    asset_type = Column(String(64), nullable=False, index=True)  # bv. "lantaarnpaal", "put", "wegvak"

    # Locatie
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    location_description = Column(String(500), nullable=True)  # "A12 km 45.2 noord-zijde"

    # Hierarchie & scope
    parent_asset_id = Column(String, ForeignKey("assets.id"), nullable=True, index=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)

    # Levensduur & conditie
    installed_at = Column(DateTime, nullable=True)
    expected_lifespan_years = Column(Integer, nullable=True)
    condition_score = Column(Integer, nullable=True)   # 1-5 (NEN 2767-achtig); validatie in schema
    last_inspection_at = Column(DateTime, nullable=True)

    # Vrije eigenschappen per asset-type (JSON-string)
    properties_json = Column(Text, nullable=True)

    # Audit
    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    archived_at = Column(DateTime, nullable=True)  # soft-delete

    # Relationships
    organization = relationship("Organization")
    project = relationship("Project", foreign_keys=[project_id])
    creator = relationship("User", foreign_keys=[created_by])
    parent = relationship("Asset", remote_side=[id], backref="children", foreign_keys=[parent_asset_id])
    meldingen = relationship("Melding", back_populates="asset", foreign_keys="Melding.asset_id")


class Melding(Base):
    __tablename__ = "meldingen"

    id = Column(String, primary_key=True, default=generate_uuid)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)  # schade, inspectie, etc
    priority = Column(String(20), default="normaal")  # kritiek, hoog, normaal, laag
    status = Column(String(50), default="open")  # open, in_behandeling, afgerond
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    photo_url = Column(String(500), nullable=True)
    photo_after_url = Column(String(500), nullable=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)
    asset_id = Column(String, ForeignKey("assets.id"), nullable=True, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # CROW 146 classificatie — sinds v2.0-crow
    crow_schadegroep = Column(String(40), nullable=True)         # samenhang/textuur/vlakheid/voegen/...
    crow_schadebeeld = Column(String(60), nullable=True)         # scheurvorming-langs/rafeling/spoorvorming/...
    crow_ernst = Column(String(2), nullable=True)                # L/M/E
    crow_omvang = Column(String(2), nullable=True)               # 1/2/3
    crow_klasse = Column(String(4), nullable=True, index=True)   # L1..E3 — index voor predictive
    nen_2767_conditie = Column(Integer, nullable=True)           # 1-5 conditie-schaal
    onderhoud_categorie = Column(String(20), nullable=True, index=True)  # observatie/KO/GO/acuut
    gw_maatregel = Column(String(120), nullable=True)            # bv "Vullen polymeer"
    gw_term = Column(String(160), nullable=True)                 # CROW-RAW formele term voor bestek
    gw_kosten_orde = Column(String(40), nullable=True)           # "€5–15 / m¹"

    project = relationship("Project")
    asset = relationship("Asset", back_populates="meldingen", foreign_keys=[asset_id])
    organization = relationship("Organization")
    creator = relationship("User", foreign_keys=[created_by])


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(String, primary_key=True, default=generate_uuid)
    token_hash = Column(String(128), unique=True, nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User")


class AIAnalysis(Base):
    """Resultaat van een AI-foto-analyse (Claude vision).

    Onveranderlijk record voor compliance: prompt-versie + model-versie + raw
    response zitten erin zodat een resultaat altijd reproduceerbaar/uitlegbaar
    is. Mens blijft in de loop — het record is een SUGGESTIE, geen besluit.

    Wordt aan een asset of een melding gekoppeld (of beide). Optioneel ongekoppeld
    bij ad-hoc-analyses tijdens veldwerk.
    """
    __tablename__ = "ai_analyses"

    id = Column(String, primary_key=True, default=generate_uuid)

    # Wat geanalyseerd
    photo_url = Column(String(1024), nullable=True)        # als foto extern is opgeslagen
    photo_sha256 = Column(String(64), nullable=True, index=True)  # dedup + integrity
    asset_type_context = Column(String(64), nullable=True) # gebruikt voor prompt-keuze
    asset_id = Column(String, ForeignKey("assets.id"), nullable=True, index=True)
    melding_id = Column(String, ForeignKey("meldingen.id"), nullable=True, index=True)

    # AI-context (cruciaal voor compliance/audit)
    prompt_version = Column(String(32), nullable=False)
    model_id = Column(String(64), nullable=False)

    # Resultaat
    schade_aanwezig = Column(Boolean, nullable=True)
    schade_type = Column(String(64), nullable=True, index=True)
    ernst = Column(String(32), nullable=True, index=True)        # geen/licht/matig/ernstig/kritiek
    kosten_klasse = Column(String(64), nullable=True)
    aanbevolen_actie = Column(Text, nullable=True)
    bevindingen_json = Column(Text, nullable=True)               # ["string", "string"]
    confidence = Column(Float, nullable=True)                    # 0.0 - 1.0
    raw_response = Column(Text, nullable=True)                   # complete API-response voor traceability

    # CROW 146 classificatie + GWWkosten (sinds v2.0-crow)
    crow_schadegroep = Column(String(40), nullable=True)
    crow_schadebeeld = Column(String(60), nullable=True)
    crow_ernst = Column(String(2), nullable=True)
    crow_omvang = Column(String(2), nullable=True)
    crow_klasse = Column(String(4), nullable=True, index=True)
    nen_2767_conditie = Column(Integer, nullable=True)
    onderhoud_categorie = Column(String(20), nullable=True, index=True)
    gw_maatregel = Column(String(120), nullable=True)
    gw_term = Column(String(160), nullable=True)
    gw_kosten_orde = Column(String(40), nullable=True)
    termijn_weken = Column(Integer, nullable=True)

    # Mens-in-de-loop
    accepted_at = Column(DateTime, nullable=True)
    accepted_by = Column(String, ForeignKey("users.id"), nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    rejection_reason = Column(String(500), nullable=True)

    # Wie/wanneer
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    asset = relationship("Asset", foreign_keys=[asset_id])
    melding = relationship("Melding", foreign_keys=[melding_id])
    creator = relationship("User", foreign_keys=[created_by])
    acceptor = relationship("User", foreign_keys=[accepted_by])


class WebhookEndpoint(Base):
    """Een externe webhook waar events naar worden gestuurd.

    `events_json` is een JSON-lijst van action-patronen die deze hook abonneert,
    bv. ["melding.*", "ai.analysis.run", "asset.create"]. Het sterretje matcht
    één segment.

    `format_type`:
      - 'slack'   → Slack-incoming-webhook payload
      - 'teams'   → Microsoft Teams MessageCard
      - 'generic' → Ruwe FieldOps-event JSON met HMAC-signature header

    `secret` wordt gebruikt voor HMAC-SHA256 signering bij format='generic';
    voor Slack/Teams is de URL zelf al het geheim en wordt niet gesigneerd.
    """
    __tablename__ = "webhook_endpoints"

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    url = Column(String(1024), nullable=False)
    secret = Column(String(128), nullable=True)            # alleen voor 'generic'
    format_type = Column(String(16), nullable=False, default="generic")
    events_json = Column(Text, nullable=False, default="[]")
    enabled = Column(Boolean, nullable=False, default=True)

    last_triggered_at = Column(DateTime, nullable=True)
    last_status = Column(Integer, nullable=True)            # HTTP-status laatste delivery
    failure_count = Column(Integer, nullable=False, default=0)

    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    organization = relationship("Organization")
    creator = relationship("User", foreign_keys=[created_by])


class WebhookDelivery(Base):
    """Log van elke afgevuurde webhook (succesvol of niet) — voor debugging
    en retry-strategie. Ouder dan 30 dagen mag gepruned worden."""
    __tablename__ = "webhook_deliveries"

    id = Column(String, primary_key=True, default=generate_uuid)
    webhook_endpoint_id = Column(String, ForeignKey("webhook_endpoints.id"), nullable=False, index=True)
    action = Column(String(64), nullable=False, index=True)
    payload_json = Column(Text, nullable=True)
    status_code = Column(Integer, nullable=True)
    response_snippet = Column(String(500), nullable=True)
    error = Column(String(500), nullable=True)
    succeeded = Column(Boolean, nullable=False, default=False)
    duration_ms = Column(Integer, nullable=True)
    attempted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    endpoint = relationship("WebhookEndpoint")


class GoogleOAuthToken(Base):
    """OAuth2-tokens per user voor Google services (Calendar + Drive).

    Refresh-token blijft geldig tot user actively revokes; access-token wordt
    automatisch ververst door onze Google-helper voor elke API-call.
    """
    __tablename__ = "google_oauth_tokens"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)

    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    scope = Column(Text, nullable=True)            # space-separated lijst van scopes
    google_email = Column(String(255), nullable=True)

    # Default-doelen (voor Calendar/Drive sync)
    default_calendar_id = Column(String(255), nullable=True, default="primary")
    default_drive_folder_id = Column(String(255), nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    revoked_at = Column(DateTime, nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    organization = relationship("Organization", foreign_keys=[organization_id])


class CalendarLink(Base):
    """Koppeling tussen FieldOps-entity (melding/asset) en Google Calendar event.
    Uniek per (user, entity_type, entity_id) zodat dezelfde melding niet
    twee events oplevert."""
    __tablename__ = "calendar_links"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    entity_type = Column(String(32), nullable=False, index=True)   # 'melding' | 'asset_inspection'
    entity_id = Column(String(64), nullable=False, index=True)
    google_event_id = Column(String(255), nullable=False)
    calendar_id = Column(String(255), nullable=False, default="primary")

    last_synced_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class PushSubscription(Base):
    """Web Push-subscription per gebruiker per device.

    Browser/PWA roept `pushManager.subscribe()` aan en stuurt het resultaat hier
    naartoe. Server gebruikt deze data om met VAPID-signed POST naar de browser-
    leverancier (FCM/APNs/Mozilla) push-meldingen te sturen.

    Eén user kan meerdere subscriptions hebben (telefoon + laptop + tablet).
    Endpoint+p256dh+auth zijn samen uniek per device.
    """
    __tablename__ = "push_subscriptions"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)

    endpoint = Column(String(2048), unique=True, nullable=False)
    p256dh = Column(String(255), nullable=False)
    auth = Column(String(255), nullable=False)
    user_agent = Column(String(512), nullable=True)

    last_used_at = Column(DateTime, nullable=True)
    failure_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User", foreign_keys=[user_id])
    organization = relationship("Organization", foreign_keys=[organization_id])


class IncomingWebhook(Base):
    """Inkomend webhook-token voor IoT-sensoren e.d. Sensoren POSTen JSON
    naar `/api/incoming/{token}`, server maakt een melding aan in de gekoppelde
    organisatie / project / asset.

    Token = unieke key (zoals een API-key). Geen extra auth nodig — token is
    geheim genoeg en mag geroteerd worden. Beheer via admin-router.
    """
    __tablename__ = "incoming_webhooks"

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)

    # Defaults voor automatisch aangemaakte meldingen
    default_project_id = Column(String, ForeignKey("projects.id"), nullable=True)
    default_asset_id = Column(String, ForeignKey("assets.id"), nullable=True)
    default_priority = Column(String(20), nullable=False, default="normaal")
    default_category = Column(String(64), nullable=True)
    title_template = Column(String(255), nullable=True)  # bv. "Sensor {sensor_id}: {value} {unit}"

    enabled = Column(Boolean, nullable=False, default=True)
    last_received_at = Column(DateTime, nullable=True)
    received_count = Column(Integer, nullable=False, default=0)

    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class AuditLog(Base):
    """Onveranderlijk logboek van alle business-mutaties.

    Wordt automatisch gevuld via `audit.log_action()` (zie audit.py).
    Is read-only vanuit de app — alleen INSERT, geen UPDATE/DELETE.
    Gebruikt voor compliance (GDPR, ISO27001, NEN7510), incident-onderzoek,
    en als verkoopwapen richting B2B-klanten ('hier zie je wie wat wanneer
    heeft gewijzigd').
    """
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True, default=generate_uuid)
    # Wie
    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    user_email = Column(String(255), nullable=True)  # gedenormaliseerd; user kan later gedeactiveerd zijn
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=True, index=True)
    # Wat
    action = Column(String(64), nullable=False, index=True)        # bv. "melding.create", "user.role_change"
    entity_type = Column(String(64), nullable=True, index=True)    # bv. "melding", "project", "user"
    entity_id = Column(String, nullable=True, index=True)
    # Detail (jsonb-light: SQLite-vriendelijk via Text+JSON)
    details = Column(Text, nullable=True)                          # JSON-string: {"before": ..., "after": ...}
    # Context
    ip_address = Column(String(45), nullable=True)                 # IPv4 of IPv6
    user_agent = Column(String(512), nullable=True)
    request_id = Column(String(64), nullable=True, index=True)     # correlatie over requests
    # Wanneer
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    user = relationship("User", foreign_keys=[user_id])
    organization = relationship("Organization", foreign_keys=[organization_id])


class DemoRequest(Base):
    __tablename__ = "demo_requests"

    id = Column(String, primary_key=True, default=generate_uuid)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    company_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=True)
    plan = Column(SQLEnum(SubscriptionPlan), default=SubscriptionPlan.STARTER)
    num_users = Column(Integer, default=10)
    status = Column(String(20), default="pending")  # pending, approved
    processed = Column(Boolean, default=False)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
