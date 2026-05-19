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
    # Tijdstempel waarop alle uitstaande JWT-tokens van deze user als ongeldig
    # gelden. Wordt gezet bij anonymisatie (Art.17), wachtwoordwijziging en
    # admin-deactivatie. JWT.iat < tokens_invalidated_at -> 401.
    tokens_invalidated_at = Column(DateTime, nullable=True)
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

    # Inspectie-cyclus (auto-berekend op basis van asset_type + condition_score
    # bij sign_inspection). Norm-conform NEN 2767-2 / CROW 134 / NEN 3399 /
    # NEN-EN 1176 / NEN 3140 / VTA. Zie inspection_cycle.py voor de regels.
    last_inspection_id = Column(String, ForeignKey("inspections.id"), nullable=True)
    next_inspection_due = Column(DateTime, nullable=True, index=True)  # index voor overdue-queries
    inspection_cycle_months = Column(Integer, nullable=True)

    # Vrije eigenschappen per asset-type (JSON-string)
    properties_json = Column(Text, nullable=True)

    # Geometry & NWB-koppeling (sinds v3.4 — wegvak-architectuur)
    geometry_geojson = Column(Text, nullable=True)            # LineString voor wegvakken, Point voor andere assets (later)
    length_m = Column(Float, nullable=True)                   # Berekend uit geometry — voor predictive + offerte
    is_segment = Column(Boolean, default=False, nullable=False)  # True = wegvak (child); False = parent-weg of standalone
    nwb_wvk_id = Column(String(32), nullable=True, index=True) # NWB Wegvak-ID — stabiel over jaren
    nwb_wvk_begdat = Column(DateTime, nullable=True)          # NWB versie-datum (audit-traceability)
    nwb_jte_id_beg = Column(String(32), nullable=True)        # Junction-ID begin
    nwb_jte_id_end = Column(String(32), nullable=True)        # Junction-ID einde

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
    photo_url = Column(Text, nullable=True)           # inline base64 of externe URL
    photo_after_url = Column(Text, nullable=True)     # inline base64 of externe URL
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

    # Job Orchestration Engine — sinds v3.0
    assigned_to = Column(String, ForeignKey("users.id"), nullable=True, index=True)  # specifieke uitvoerder
    job_cluster_id = Column(String, ForeignKey("job_clusters.id"), nullable=True, index=True)  # batch-koppeling

    project = relationship("Project")
    assignee = relationship("User", foreign_keys=[assigned_to])
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


# ════════════════════════════════════════════════════════════
# Job Orchestration Engine — sinds v3.0 (mei 2026)
# ════════════════════════════════════════════════════════════

class UserSkill(Base):
    """Skills per medewerker. Meerdere skills per user mogelijk.

    Skill-codes komen uit crow_kosten.SKILL_CODES (b.v. VULLEN_POLYMEER,
    VOEGVULLING, SLEMBEHANDELING, KLINKER_LEGGEN, ASFALT_DEKLAAG, ...).
    Proficiency 1-5: 1=junior, 5=expert/lead.
    """
    __tablename__ = "user_skills"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    skill_code = Column(String(40), nullable=False, index=True)
    proficiency = Column(Integer, default=3)  # 1-5
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", foreign_keys=[user_id])


class JobCluster(Base):
    """Geclusterde meldingen met identieke maatregel + nabije locatie.

    Het orchestration-algoritme groepeert open meldingen op:
    - zelfde gw_term (b.v. "Vullen polymeer (cold-pour)")
    - geo-proximity (binnen X km of zelfde wegvak-segment)

    Een cluster is auto-gegenereerd door /api/clusters/generate, daarna
    handmatig toewijsbaar aan een medewerker met de juiste skill.
    """
    __tablename__ = "job_clusters"

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)

    gw_term = Column(String(160), nullable=False, index=True)              # homogene maatregel
    skill_code = Column(String(40), nullable=True)                         # vereist voor uitvoering
    melding_count = Column(Integer, default=0)                             # aantal meldingen in cluster
    estimated_hours = Column(Float, nullable=True)                         # geschatte werktijd
    estimated_revenue = Column(Float, nullable=True)                       # bestek-waarde indicatie

    # Geo-bounding box van het cluster
    geo_lat_min = Column(Float, nullable=True)
    geo_lat_max = Column(Float, nullable=True)
    geo_lng_min = Column(Float, nullable=True)
    geo_lng_max = Column(Float, nullable=True)
    geo_label = Column(String(120), nullable=True)                         # b.v. "Zone Noord · A12 km 40-50"

    # Planning
    planned_date = Column(DateTime, nullable=True)
    assigned_to = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    status = Column(String(20), default="proposed")  # proposed/assigned/in_progress/done/cancelled

    # Productiviteit-tracking
    productivity_baseline_hours = Column(Float, nullable=True)             # tijd zonder clustering
    productivity_savings_hours = Column(Float, nullable=True)              # tijd-besparing door batching

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    organization = relationship("Organization")
    assignee = relationship("User", foreign_keys=[assigned_to])
    meldingen_in_cluster = relationship("Melding", foreign_keys="Melding.job_cluster_id")


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


class MicrosoftOAuthToken(Base):
    """OAuth2-tokens per user voor Microsoft Graph (Outlook Calendar + OneDrive/SharePoint + Teams).

    Refresh-token blijft geldig tot user actively revokes (Microsoft Entra ID).
    Access-token wordt automatisch ververst voor elke Graph API call.
    """
    __tablename__ = "microsoft_oauth_tokens"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)

    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    scope = Column(Text, nullable=True)
    ms_email = Column(String(255), nullable=True)
    ms_tenant_id = Column(String(64), nullable=True)         # Entra-tenant van de gebruiker
    ms_user_id = Column(String(64), nullable=True)           # Graph user-id (UUID)

    # Default-doelen
    default_calendar_id = Column(String(255), nullable=True)         # null = primary
    default_drive_folder_id = Column(String(255), nullable=True)     # OneDrive folder-id
    default_sharepoint_site = Column(String(255), nullable=True)     # SharePoint site-url

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    revoked_at = Column(DateTime, nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    organization = relationship("Organization", foreign_keys=[organization_id])


class MicrosoftCalendarLink(Base):
    """Koppeling FieldOps-entity ↔ Microsoft Outlook Calendar event."""
    __tablename__ = "microsoft_calendar_links"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    entity_type = Column(String(32), nullable=False, index=True)
    entity_id = Column(String(64), nullable=False, index=True)
    ms_event_id = Column(String(255), nullable=False)
    calendar_id = Column(String(255), nullable=True)         # null = primary

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


class Oplevering(Base):
    """Oplevering / proces-verbaal van uitgevoerd werk.

    Een Oplevering is de formele afsluiting van een (deel)project: de aannemer
    overhandigt het werk aan de opdrachtgever. Bestaat uit een header met
    project-/locatie-gegevens en een lijst opleverpunten (OpleveringPunt) die
    per object/element zijn vastgelegd met foto + uitvoeringsmethode.

    Status-flow: concept -> opgeleverd -> aanvaard | afgewezen.
    """
    __tablename__ = "opleveringen"

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True, index=True)

    # Header — algemene oplevergegevens
    title = Column(String(255), nullable=False)              # "Oplevering KO Naaldwijk fase 2"
    description = Column(Text, nullable=True)
    locatie = Column(String(500), nullable=True)             # "Westland, kern Naaldwijk"
    datum_oplevering = Column(DateTime, nullable=True)       # geplande/uitgevoerde oplever-datum
    opdrachtgever_naam = Column(String(255), nullable=True)
    opdrachtgever_email = Column(String(255), nullable=True)
    aannemer_naam = Column(String(255), nullable=True)
    aannemer_email = Column(String(255), nullable=True)

    # Vrije aanvullende vragen — JSON-string voor flexibiliteit
    # bv. {"weersomstandigheden": "droog", "veiligheid_ok": true, "bouwbesluit": "voldoet"}
    extra_questions_json = Column(Text, nullable=True)

    notes = Column(Text, nullable=True)                       # opmerkingen / restpunten
    status = Column(String(30), default="concept", index=True)  # concept|opgeleverd|aanvaard|afgewezen
    signed_off_at = Column(DateTime, nullable=True)           # wanneer opdrachtgever aanvaard heeft

    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    organization = relationship("Organization")
    project = relationship("Project")
    creator = relationship("User", foreign_keys=[created_by])
    punten = relationship("OpleveringPunt", back_populates="oplevering",
                          cascade="all, delete-orphan",
                          order_by="OpleveringPunt.order_index")


# ════════════════════════════════════════════════════════════
# Kunstwerken-inspecties — NEN 2767-2 + CROW 134 (sinds mei 2026)
# ════════════════════════════════════════════════════════════
#
# Een Inspection is een gestructureerde periodieke beoordeling van één
# kunstwerk (brug/viaduct/tunnel/sluis/duiker/kademuur) volgens NEN 2767-2.
#
# Hierarchie:
#   Inspection
#     ├── InspectionElement[]   (onderbouw, bovenbouw, voegovergangen, ...)
#     │     └── InspectionDefect[]  (per element 0..n gebreken)
#     └── Asset (het kunstwerk zelf)
#
# Conditiescore-schaal: 1-6 (NEN 2767-1, hergebruikt in NEN 2767-2):
#   1 = uitstekend, 2 = goed, 3 = redelijk, 4 = matig, 5 = slecht, 6 = zeer slecht.
# Per gebrek: ernst (1-3) × intensiteit (1-3) × omvangklasse (1-5).
# Element-conditie = worst-defect-rule (slechtste gebrek = score van element).
# Kunstwerk-conditie = gewogen agregatie van element-scores (zie nen2767_scoring.py).


class Inspection(Base):
    """Een formele inspectie van één kunstwerk volgens NEN 2767-2 + CROW 134.

    Gekoppeld aan precies één Asset (het kunstwerk). Bevat header-gegevens,
    een lijst InspectionElement-records met defecten per element, en een
    berekende eind-conditiescore.

    Status-flow:
        draft         → veldwerk bezig
        in_progress   → minimaal één element met defect ingevuld
        completed     → alle vereiste elementen beoordeeld, conditie berekend
        signed        → ondertekend door inspecteur (PDF gegenereerd)
        delivered     → verstuurd naar opdrachtgever
    """
    __tablename__ = "inspections"

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)

    # Kunstwerk waarop deze inspectie betrekking heeft
    asset_id = Column(String, ForeignKey("assets.id"), nullable=False, index=True)
    kunstwerk_type = Column(String(32), nullable=False, index=True)
    # bv. "brug", "viaduct", "tunnel", "sluis", "duiker", "kademuur"

    project_id = Column(String, ForeignKey("projects.id"), nullable=True, index=True)

    # Header
    title = Column(String(255), nullable=False)              # "Eerste-graads inspectie A20 Hooge Bergse Brug"
    inspectie_type = Column(String(32), nullable=False, default="visueel")
    # visueel / eerste-graads / tweede-graads / detail / oplevering
    norm_referenties = Column(String(255), nullable=True, default="NEN 2767-2; CROW 134")

    # NEN-EN 1176 inspectie-categorie (alleen voor kunstwerk_type=speeltoestel).
    # routine     = dagelijks/wekelijks visueel (vandalisme, schoonmaak)
    # operationeel = maandelijks functioneel (slijtage, beweegbare delen)
    # hoofd       = jaarlijks uitgebreid (structureel + onderdelen-vervanging)
    nen1176_inspectie_kind = Column(String(16), nullable=True, index=True)

    datum_inspectie = Column(DateTime, nullable=True)        # uitvoeringsdatum veldwerk
    inspecteur_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    inspecteur_naam = Column(String(120), nullable=True)     # gedenormaliseerd voor rapport
    inspecteur_certificaat = Column(String(120), nullable=True)  # bv. "VCA Inspecteur 2026"

    weersomstandigheden = Column(String(120), nullable=True) # "droog, 12°C, bewolkt"
    bijzonderheden = Column(Text, nullable=True)             # toegankelijkheid, gebruikte hulpmiddelen, e.d.

    opdrachtgever_naam = Column(String(255), nullable=True)
    opdrachtgever_email = Column(String(255), nullable=True)

    # Berekend (door nen2767_scoring.recompute)
    conditiescore_overall = Column(Integer, nullable=True)   # 1-6 (NEN 2767-2)
    conditiescore_methode = Column(String(40), nullable=True, default="worst-element")
    samenvatting = Column(Text, nullable=True)               # AI- of inspecteurs-samenvatting
    aanbevolen_acties = Column(Text, nullable=True)          # maatregelen-advies (vrije tekst)

    status = Column(String(30), nullable=False, default="draft", index=True)
    # draft | in_progress | completed | signed | delivered

    # Ondertekening
    signed_at = Column(DateTime, nullable=True)
    signed_by = Column(String, ForeignKey("users.id"), nullable=True)
    signature_data_url = Column(Text, nullable=True)         # base64 PNG van handtekening-canvas
    pdf_generated_at = Column(DateTime, nullable=True)

    # Volgende inspectie
    volgende_inspectie_op = Column(DateTime, nullable=True)  # geadviseerde her-inspectie-datum

    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    organization = relationship("Organization")
    asset = relationship("Asset", foreign_keys=[asset_id])
    project = relationship("Project", foreign_keys=[project_id])
    inspecteur = relationship("User", foreign_keys=[inspecteur_id])
    signer = relationship("User", foreign_keys=[signed_by])
    creator = relationship("User", foreign_keys=[created_by])
    elementen = relationship("InspectionElement", back_populates="inspection",
                             cascade="all, delete-orphan",
                             order_by="InspectionElement.order_index")


class InspectionElement(Base):
    """Eén beoordeeld element binnen een Inspection.

    Elementen komen uit de standaard-bibliotheek per kunstwerk-type
    (zie kunstwerken_taxonomy.py), bv. voor een brug:
      - onderbouw, bovenbouw, brugdek, voegovergangen, leuningen, opleggingen,
        bordessen, taluds, oeverbescherming, voorzieningen.
    """
    __tablename__ = "inspection_elements"

    id = Column(String, primary_key=True, default=generate_uuid)
    inspection_id = Column(String, ForeignKey("inspections.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)

    element_code = Column(String(32), nullable=False)        # bv. "BRUG.ONDERBOUW"
    element_naam = Column(String(120), nullable=False)       # bv. "Onderbouw / landhoofd"
    element_groep = Column(String(40), nullable=True)        # bv. "constructief", "afwerking"

    # Beoordeling per element
    beoordeeld = Column(Boolean, nullable=False, default=False)  # heeft inspecteur element gezien?
    niet_inspecteerbaar_reden = Column(String(255), nullable=True)
    # bv. "onder water", "geen toegang zonder hijswerk"
    conditiescore = Column(Integer, nullable=True)           # 1-6 (worst-defect rule)
    bevindingen = Column(Text, nullable=True)                # vrije bevindingen-tekst
    aanbevolen_actie = Column(Text, nullable=True)

    order_index = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    inspection = relationship("Inspection", back_populates="elementen")
    defecten = relationship("InspectionDefect", back_populates="element",
                            cascade="all, delete-orphan",
                            order_by="InspectionDefect.created_at")
    antwoorden = relationship("InspectionAnswer", back_populates="element",
                              cascade="all, delete-orphan",
                              order_by="InspectionAnswer.created_at")


class InspectionDefect(Base):
    """Eén geconstateerd gebrek binnen een InspectionElement.

    NEN 2767-2 classificatie:
      ernst       1 = gering, 2 = serieus, 3 = ernstig
      intensiteit 1 = beginstadium, 2 = gevorderd, 3 = eindstadium
      omvang_klasse  1 = <2% , 2 = 2-10%, 3 = 10-30%, 4 = 30-70%, 5 = >70%

    Defect-score wordt berekend via nen2767_scoring.defect_to_score(...) en
    bepaalt mee de element-conditie (slechtste gebrek wint).
    """
    __tablename__ = "inspection_defects"

    id = Column(String, primary_key=True, default=generate_uuid)
    element_id = Column(String, ForeignKey("inspection_elements.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)

    # Gebrek-typering
    gebrek_code = Column(String(40), nullable=True)          # bv. "betonschade.dekkingstekort"
    gebrek_naam = Column(String(120), nullable=False)        # bv. "Scheurvorming in voeg"
    omschrijving = Column(Text, nullable=True)               # vrije observatie

    # NEN 2767-2 classificatie
    ernst = Column(Integer, nullable=True)                   # 1-3
    intensiteit = Column(Integer, nullable=True)             # 1-3
    omvang_klasse = Column(Integer, nullable=True)           # 1-5
    omvang_percentage = Column(Float, nullable=True)         # optioneel exact percentage
    defect_score = Column(Integer, nullable=True)            # 1-6 — berekend uit ernst×int×omv

    # Locatie binnen element
    locatie_beschrijving = Column(String(255), nullable=True)
    # bv. "Noordzijde, ter hoogte van pijler 2"
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)

    # Bewijs
    photo_url = Column(Text, nullable=True)                  # inline base64 of externe URL
    photo_url_2 = Column(Text, nullable=True)                # optionele tweede foto
    ai_analysis_id = Column(String, ForeignKey("ai_analyses.id"), nullable=True, index=True)
    # ↑ als gebrek via AI-suggestie is geclassificeerd → audit-trail

    # CROW-koppeling (voor verharding-elementen op kunstwerken, bv. brugdek-asfalt)
    crow_klasse = Column(String(4), nullable=True)           # L1..E3
    gw_maatregel = Column(String(120), nullable=True)        # advies-maatregel uit GWWkosten

    # NEN-EN 1176 speeltoestel-classificatie (alleen bij kunstwerk_type=speeltoestel).
    # Vervangt NEN 2767-2 ernst×intensiteit×omvang voor speeltoestellen omdat
    # die methodiek niet geschikt is voor veiligheids-classificatie.
    #   A = veilig, geen actie
    #   B = klein gebrek, herstel binnen 30 dagen
    #   C = direct gebruik beperken (afzetting / waarschuwing)
    #   D = afgesloten / weggehaald (acute valgevaar, scherp, knelpunt)
    en1176_categorie = Column(String(1), nullable=True)
    en1176_acute_afsluiting = Column(Boolean, default=False, nullable=False)

    # VTA boom-classificatie (alleen bij kunstwerk_type=boom).
    # Risicoklasse volgens Mattheck/VTA: 1=geen aandacht .. 5=acuut velgevaar.
    # holte_pct = percentage stamholte (Tomografie / klopprobe).
    # t_r_ratio = wand/straal-verhouding; < 0.30 = breukrisico (Mattheck-criterium).
    vta_risicoklasse = Column(Integer, nullable=True)
    vta_holte_pct = Column(Float, nullable=True)
    vta_t_r_ratio = Column(Float, nullable=True)

    # NEN 3140 elektrische meetwaarden (alleen bij kunstwerk_type=verlichting).
    # Gemeten met 500V dc test conform NEN 3140 § 6.4 + NEN 1010.
    nen3140_isolatie_megaohm = Column(Float, nullable=True)         # minimaal 0.5 MΩ
    nen3140_aardingsweerstand_ohm = Column(Float, nullable=True)    # minimaal <100Ω typisch
    nen3140_aardlek_ms = Column(Integer, nullable=True)             # uitschakeltijd in ms
    nen3140_aardlek_ma = Column(Float, nullable=True)               # uitschakelstroom in mA

    # CROW 145 retroreflectie wegmarkering (alleen bij kunstwerk_type=wegmarkering).
    # Eenheid mcd/m²/lx. CROW 145 § 4.2: wit droog ≥ 100, nat ≥ 30 (stroomweg).
    # Vervang-drempel bij droog < 80 mcd.
    crow145_rl_droog_mcd = Column(Integer, nullable=True)
    crow145_rl_nat_mcd = Column(Integer, nullable=True)

    # NEN 3399 riolering-schadecodes (alleen bij kunstwerk_type=riolering).
    # 3-letter NEN-EN 13508-2 code: BAA langsscheur, BAB dwarsscheur, BAC scherf,
    # BAF chemische aantasting, BAH wortelingroei, BAJ vervorming, BAO infiltratie,
    # BAP exfiltratie. Eindklasse 1-5 op zwaarste schade per streng.
    nen3399_code = Column(String(4), nullable=True)
    nen3399_klasse = Column(Integer, nullable=True)

    # Eventueel auto-gegenereerde melding (orchestration-koppeling)
    melding_id = Column(String, ForeignKey("meldingen.id"), nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    element = relationship("InspectionElement", back_populates="defecten")
    ai_analysis = relationship("AIAnalysis", foreign_keys=[ai_analysis_id])
    melding = relationship("Melding", foreign_keys=[melding_id])


class InspectionAnswer(Base):
    """Antwoord op één NEN/CROW-vraag bij één element binnen een inspectie.

    Wordt automatisch aangemaakt (één rij per vraag) zodra een Inspection wordt
    gestart, zodat de inspecteur door een vaste checklist kan lopen. Een leeg
    antwoord blijft staan tot het is ingevuld.

    Antwoord-types (matchen kunstwerken_taxonomy.QUESTIONS schema):
      score_1_6   → answer_score (1-6, NEN conditie)
      ja_nee      → answer_bool
      ja_nee_nvt  → answer_value_text in {"ja","nee","nvt"}
      keuze       → answer_value_text (één van q.opties)
      meting      → answer_score (numeriek) + answer_value_text (eenheid)
      tekst       → answer_value_text

    `requires_attention` wordt server-side gezet zodat metrics-queries
    geïndexeerd kunnen filteren op aandachtspunten (zonder JSON-parsen).
    """
    __tablename__ = "inspection_answers"

    id = Column(String, primary_key=True, default=generate_uuid)
    element_id = Column(String, ForeignKey("inspection_elements.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)

    question_code = Column(String(40), nullable=False, index=True)   # bv. "GEN.STAAT"
    question_version = Column(String(40), nullable=False)            # voor audit
    question_text_snapshot = Column(String(500), nullable=True)
    # ↑ snapshot van de vraagtekst zoals getoond aan inspecteur — verplicht
    # bij audits ("welke vraag is wanneer beantwoord?")

    answer_type = Column(String(20), nullable=False)
    # score_1_6 | ja_nee | ja_nee_nvt | keuze | meting | tekst

    answer_score = Column(Integer, nullable=True)
    answer_bool = Column(Boolean, nullable=True)
    answer_value_text = Column(Text, nullable=True)

    toelichting = Column(Text, nullable=True)
    photo_url = Column(String(500), nullable=True)

    requires_attention = Column(Boolean, nullable=False, default=False, index=True)
    answered_at = Column(DateTime, nullable=True)
    answered_by = Column(String, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    element = relationship("InspectionElement", back_populates="antwoorden",
                           foreign_keys=[element_id])


class OpleveringPunt(Base):
    """Eén opleverpunt binnen een Oplevering.

    Elk punt staat voor één gecontroleerd/opgeleverd object of element:
    bv. "Lantaarnpaal WL-LP-001 vervangen", "Scheurvulling Dijkweg km 0.4-0.8".
    Per punt: code, omschrijving, uitvoeringsmethode, foto, status.
    """
    __tablename__ = "opleveringspunten"

    id = Column(String, primary_key=True, default=generate_uuid)
    oplevering_id = Column(String, ForeignKey("opleveringen.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)

    code = Column(String(64), nullable=False)                # eigen code, bv. "OP-001" of asset-code
    omschrijving = Column(Text, nullable=False)              # wat is gedaan / wat wordt opgeleverd
    uitvoeringsmethode = Column(Text, nullable=True)         # hoe is het uitgevoerd
    photo_url = Column(Text, nullable=True)                  # foto voor uitvoering — inline base64 of URL
    photo_url_after = Column(Text, nullable=True)            # foto na uitvoering — inline base64 of URL
    asset_id = Column(String, ForeignKey("assets.id"), nullable=True, index=True)  # optionele koppeling

    order_index = Column(Integer, default=0, nullable=False)  # volgorde in oplever-PV
    status = Column(String(20), default="gereed", nullable=False)  # gereed | restpunt | actiepunt | afgekeurd

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    oplevering = relationship("Oplevering", back_populates="punten")
    asset = relationship("Asset", foreign_keys=[asset_id])
