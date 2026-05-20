from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime
from models import UserRole, SubscriptionPlan, AccountStatus


# Auth
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserResponse"
    # MFA: bij stage-1 login (na password OK maar voor TOTP) staat dit op True
    # en is access_token een tijdelijke 5-min token voor /login-mfa.
    mfa_required: bool = False


# Demo
class DemoRequestCreate(BaseModel):
    first_name: str
    last_name: str
    company_name: str
    email: EmailStr
    phone: Optional[str] = None
    plan: SubscriptionPlan = SubscriptionPlan.STARTER
    num_users: int = 10


class DemoRequestResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    company_name: str
    email: str
    plan: SubscriptionPlan
    num_users: int
    processed: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# User
class UserCreate(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    phone: Optional[str] = None


class UserResponse(BaseModel):
    id: str
    email: str
    first_name: str
    last_name: str
    phone: Optional[str]
    role: UserRole
    is_active: bool
    is_org_admin: bool
    must_change_password: bool = False
    organization_id: str
    created_at: datetime
    last_login: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


class UserSelfUpdate(BaseModel):
    """Self-service profiel-edit — alleen velden die user zelf mag wijzigen.

    Email en role bewust uitgesloten (vereisen admin-actie).
    """
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None


class PasswordChangeRequest(BaseModel):
    """Self-service wachtwoord-wijziging.

    Vereist het huidige wachtwoord voor verificatie (voorkomt dat een
    gestolen sessie het wachtwoord kan veranderen).
    """
    current_password: str
    new_password: str


# Organization
class OrganizationResponse(BaseModel):
    id: str
    name: str
    plan: SubscriptionPlan
    status: AccountStatus
    max_users: int
    trial_ends_at: Optional[datetime]
    created_at: datetime
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    billing_address: Optional[str] = None
    kvk_number: Optional[str] = None
    btw_number: Optional[str] = None
    logo_data_url: Optional[str] = None
    brand_color: Optional[str] = None
    streetview_provider_label: Optional[str] = None
    streetview_provider_url_template: Optional[str] = None

    model_config = {"from_attributes": True}


class OrganizationUpdate(BaseModel):
    """Self-service org-edit — alleen velden die org-admin zelf mag aanpassen.

    Plan, status, max_users en shopify_customer_id zijn voorbehouden aan
    super-admin / billing-side (separate flow).
    """
    name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    billing_address: Optional[str] = None
    kvk_number: Optional[str] = None
    btw_number: Optional[str] = None
    logo_data_url: Optional[str] = None
    brand_color: Optional[str] = None
    streetview_provider_label: Optional[str] = None
    streetview_provider_url_template: Optional[str] = None


# Invitation
class InvitationCreate(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.VIEWER


class InvitationResponse(BaseModel):
    id: str
    email: str
    role: UserRole
    accepted: bool
    created_at: datetime
    expires_at: datetime

    model_config = {"from_attributes": True}


class AcceptInvitationRequest(BaseModel):
    token: str
    password: str
    first_name: str
    last_name: str
    phone: Optional[str] = None


# Project
class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    gemeente: Optional[str] = None
    boundary_geojson: Optional[str] = None
    color: Optional[str] = "#00d4ff"
    categories: Optional[list[str]] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    gemeente: Optional[str]
    status: str
    boundary_geojson: Optional[str]
    color: Optional[str]
    categories: Optional[list[str]] = None
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    gemeente: Optional[str] = None
    status: Optional[str] = None
    boundary_geojson: Optional[str] = None
    color: Optional[str] = None
    categories: Optional[list[str]] = None


# Melding
class MeldingCreate(BaseModel):
    title: str
    description: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = "normaal"
    lat: Optional[float] = None
    lng: Optional[float] = None
    photo_url: Optional[str] = None
    photo_after_url: Optional[str] = None
    project_id: Optional[str] = None
    asset_id: Optional[str] = None
    # CROW 146 + GWWkosten classificatie (optioneel)
    crow_schadegroep: Optional[str] = None
    crow_schadebeeld: Optional[str] = None
    crow_ernst: Optional[str] = None
    crow_omvang: Optional[str] = None
    crow_klasse: Optional[str] = None
    nen_2767_conditie: Optional[int] = None
    onderhoud_categorie: Optional[str] = None
    gw_maatregel: Optional[str] = None
    gw_term: Optional[str] = None
    gw_kosten_orde: Optional[str] = None


class MeldingResponse(BaseModel):
    id: str
    title: str
    description: Optional[str]
    category: Optional[str]
    priority: str
    status: str
    lat: Optional[float]
    lng: Optional[float]
    photo_url: Optional[str] = None
    photo_after_url: Optional[str] = None
    project_id: Optional[str]
    created_by: str
    created_at: datetime
    creator_name: Optional[str] = None

    model_config = {"from_attributes": True}


class MeldingUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    photo_url: Optional[str] = None
    photo_after_url: Optional[str] = None
    asset_id: Optional[str] = None
    project_id: Optional[str] = None      # null = ontkoppel project


# Asset
class AssetCreate(BaseModel):
    code: str
    name: Optional[str] = None
    asset_type: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    location_description: Optional[str] = None
    parent_asset_id: Optional[str] = None
    project_id: Optional[str] = None
    installed_at: Optional[datetime] = None
    expected_lifespan_years: Optional[int] = None
    condition_score: Optional[int] = None  # 1-5
    last_inspection_at: Optional[datetime] = None
    properties: Optional[dict] = None


class AssetUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    asset_type: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    location_description: Optional[str] = None
    parent_asset_id: Optional[str] = None
    project_id: Optional[str] = None
    installed_at: Optional[datetime] = None
    expected_lifespan_years: Optional[int] = None
    condition_score: Optional[int] = None
    last_inspection_at: Optional[datetime] = None
    properties: Optional[dict] = None


class AssetResponse(BaseModel):
    id: str
    code: str
    name: Optional[str]
    asset_type: str
    lat: Optional[float]
    lng: Optional[float]
    location_description: Optional[str]
    parent_asset_id: Optional[str]
    project_id: Optional[str]
    installed_at: Optional[datetime]
    expected_lifespan_years: Optional[int]
    condition_score: Optional[int]
    last_inspection_at: Optional[datetime]
    properties: Optional[dict] = None
    created_at: datetime
    updated_at: datetime
    open_meldingen_count: Optional[int] = 0
    children_count: Optional[int] = 0

    model_config = {"from_attributes": True}


# AI-inspecties
class InspectionAnalyseUrlRequest(BaseModel):
    """Voor analyse op een al-geüploade foto-URL (i.p.v. multipart)."""
    photo_url: str
    asset_id: Optional[str] = None
    melding_id: Optional[str] = None
    asset_type: Optional[str] = None  # override; anders uit asset_id afgeleid
    extra_context: Optional[str] = None


class InspectionResponse(BaseModel):
    id: str
    photo_url: Optional[str]
    asset_type_context: Optional[str]
    asset_id: Optional[str]
    melding_id: Optional[str]

    schade_aanwezig: Optional[bool]
    schade_type: Optional[str]
    ernst: Optional[str]
    kosten_klasse: Optional[str]
    aanbevolen_actie: Optional[str]
    bevindingen: list[str] = []
    confidence: Optional[float]

    model_id: str
    prompt_version: str
    accepted_at: Optional[datetime]
    rejected_at: Optional[datetime]
    rejection_reason: Optional[str]
    created_at: datetime

    # CROW 146 + GWWkosten classificatie (v2.0-crow)
    crow_schadegroep: Optional[str] = None
    crow_schadebeeld: Optional[str] = None
    crow_ernst: Optional[str] = None
    crow_omvang: Optional[str] = None
    crow_klasse: Optional[str] = None
    nen_2767_conditie: Optional[int] = None
    onderhoud_categorie: Optional[str] = None
    gw_maatregel: Optional[str] = None
    gw_term: Optional[str] = None
    gw_kosten_orde: Optional[str] = None
    termijn_weken: Optional[int] = None

    model_config = {"from_attributes": True}


class InspectionAcceptRequest(BaseModel):
    """Bij accept kunnen velden worden overgenomen naar de gekoppelde melding."""
    apply_to_melding: bool = False  # update melding.category/priority/description?


class InspectionRejectRequest(BaseModel):
    reason: str


# ─────────────────────────────────────────────────────────────────────────────
# Kunstwerken-inspecties — NEN 2767-2 + CROW 134
# ─────────────────────────────────────────────────────────────────────────────

class KunstwerkInspectionCreate(BaseModel):
    """Nieuwe inspectie opzetten voor één kunstwerk (asset).

    De server haalt kunstwerk_type uit de Asset of valt terug op de meegegeven
    waarde. Elementen worden auto-aangemaakt op basis van de taxonomy als
    `auto_elements=True`.
    """
    asset_id: str
    title: str
    kunstwerk_type: Optional[str] = None        # override van Asset.asset_type
    project_id: Optional[str] = None
    inspectie_type: str = "visueel"
    datum_inspectie: Optional[datetime] = None
    inspecteur_id: Optional[str] = None          # default = current_user
    inspecteur_naam: Optional[str] = None
    inspecteur_certificaat: Optional[str] = None
    weersomstandigheden: Optional[str] = None
    bijzonderheden: Optional[str] = None
    opdrachtgever_naam: Optional[str] = None
    opdrachtgever_email: Optional[str] = None
    auto_elements: bool = True
    # NEN-EN 1176 — alleen voor kunstwerk_type=speeltoestel
    # routine | operationeel | hoofd
    nen1176_inspectie_kind: Optional[str] = None


class KunstwerkInspectionUpdate(BaseModel):
    title: Optional[str] = None
    inspectie_type: Optional[str] = None
    datum_inspectie: Optional[datetime] = None
    inspecteur_naam: Optional[str] = None
    inspecteur_certificaat: Optional[str] = None
    weersomstandigheden: Optional[str] = None
    bijzonderheden: Optional[str] = None
    opdrachtgever_naam: Optional[str] = None
    opdrachtgever_email: Optional[str] = None
    samenvatting: Optional[str] = None
    aanbevolen_acties: Optional[str] = None
    status: Optional[str] = None                 # draft|in_progress|completed|signed|delivered
    volgende_inspectie_op: Optional[datetime] = None


class InspectionElementUpdate(BaseModel):
    beoordeeld: Optional[bool] = None
    niet_inspecteerbaar_reden: Optional[str] = None
    bevindingen: Optional[str] = None
    aanbevolen_actie: Optional[str] = None
    order_index: Optional[int] = None


class InspectionElementCreate(BaseModel):
    """Handmatig element toevoegen (buiten taxonomy)."""
    element_code: str
    element_naam: str
    element_groep: Optional[str] = None
    order_index: int = 100


class InspectionDefectCreate(BaseModel):
    gebrek_naam: str
    gebrek_code: Optional[str] = None
    omschrijving: Optional[str] = None
    ernst: Optional[int] = None                 # 1-3
    intensiteit: Optional[int] = None           # 1-3
    omvang_klasse: Optional[int] = None         # 1-5
    omvang_percentage: Optional[float] = None
    locatie_beschrijving: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    photo_url: Optional[str] = None
    photo_url_2: Optional[str] = None
    ai_analysis_id: Optional[str] = None
    crow_klasse: Optional[str] = None
    gw_maatregel: Optional[str] = None
    # NEN-EN 1176 — speeltoestel (A=veilig, B=klein 30d, C=direct beperken, D=afgesloten)
    en1176_categorie: Optional[str] = None
    en1176_acute_afsluiting: Optional[bool] = None
    # VTA boom (Mattheck): risicoklasse 1-5, holte %, t/r-ratio
    vta_risicoklasse: Optional[int] = None
    vta_holte_pct: Optional[float] = None
    vta_t_r_ratio: Optional[float] = None
    # NEN 3140 verlichting: numerieke meetwaarden (500V dc test)
    nen3140_isolatie_megaohm: Optional[float] = None
    nen3140_aardingsweerstand_ohm: Optional[float] = None
    nen3140_aardlek_ms: Optional[int] = None
    nen3140_aardlek_ma: Optional[float] = None
    # CROW 145 wegmarkering: retroreflectie droog + nat in mcd/m²/lx
    crow145_rl_droog_mcd: Optional[int] = None
    crow145_rl_nat_mcd: Optional[int] = None
    # NEN 3399 riolering: BAA-BAQ schadecode + eindklasse 1-5 + streng-ID
    nen3399_code: Optional[str] = None
    nen3399_klasse: Optional[int] = None
    nen3399_streng_id: Optional[str] = None


class InspectionDefectUpdate(BaseModel):
    gebrek_naam: Optional[str] = None
    gebrek_code: Optional[str] = None
    omschrijving: Optional[str] = None
    ernst: Optional[int] = None
    intensiteit: Optional[int] = None
    omvang_klasse: Optional[int] = None
    omvang_percentage: Optional[float] = None
    locatie_beschrijving: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    photo_url: Optional[str] = None
    photo_url_2: Optional[str] = None
    ai_analysis_id: Optional[str] = None
    crow_klasse: Optional[str] = None
    gw_maatregel: Optional[str] = None
    en1176_categorie: Optional[str] = None
    en1176_acute_afsluiting: Optional[bool] = None
    vta_risicoklasse: Optional[int] = None
    vta_holte_pct: Optional[float] = None
    vta_t_r_ratio: Optional[float] = None
    nen3140_isolatie_megaohm: Optional[float] = None
    nen3140_aardingsweerstand_ohm: Optional[float] = None
    nen3140_aardlek_ms: Optional[int] = None
    nen3140_aardlek_ma: Optional[float] = None
    crow145_rl_droog_mcd: Optional[int] = None
    crow145_rl_nat_mcd: Optional[int] = None
    nen3399_code: Optional[str] = None
    nen3399_klasse: Optional[int] = None
    nen3399_streng_id: Optional[str] = None


class InspectionSignRequest(BaseModel):
    """Onderteken een afgeronde inspectie."""
    signature_data_url: str                     # base64 PNG van canvas
    volgende_inspectie_op: Optional[datetime] = None


class DefectToMeldingRequest(BaseModel):
    """Genereer een melding uit een gevonden defect.

    Het defect blijft in de inspectie staan; via melding_id wordt 't gekoppeld
    zodat opvolging (orchestration/cluster) automatisch wordt opgepakt.
    """
    priority: Optional[str] = None              # override; default = afgeleid van score
    extra_description: Optional[str] = None


class InspectionAnswerUpdate(BaseModel):
    """Antwoord op één NEN/CROW-vraag updaten.

    Velden zijn allemaal optioneel — alleen wat de inspecteur invult komt mee.
    De server bepaalt `requires_attention` op basis van de question-definitie
    + het antwoord (zie router._answer_attention).
    """
    answer_score: Optional[int] = None
    answer_bool: Optional[bool] = None
    answer_value_text: Optional[str] = None
    toelichting: Optional[str] = None
    photo_url: Optional[str] = None


# Webhooks
class WebhookEndpointCreate(BaseModel):
    name: str
    url: str
    format_type: str = "generic"          # 'slack' | 'teams' | 'generic'
    events: list[str] = []                # bv. ['melding.*', 'ai.analysis.run']
    secret: Optional[str] = None          # alleen relevant voor 'generic'
    enabled: bool = True


class WebhookEndpointUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    format_type: Optional[str] = None
    events: Optional[list[str]] = None
    secret: Optional[str] = None
    enabled: Optional[bool] = None


class WebhookEndpointResponse(BaseModel):
    id: str
    name: str
    url: str
    format_type: str
    events: list[str] = []
    enabled: bool
    last_triggered_at: Optional[datetime]
    last_status: Optional[int]
    failure_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class WebhookDeliveryResponse(BaseModel):
    id: str
    webhook_endpoint_id: str
    action: str
    status_code: Optional[int]
    response_snippet: Optional[str]
    error: Optional[str]
    succeeded: bool
    duration_ms: Optional[int]
    attempted_at: datetime

    model_config = {"from_attributes": True}


# Password Reset
class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str


# Shopify Webhook
class ShopifyWebhookOrder(BaseModel):
    id: int
    email: str
    customer: Optional[dict] = None
    line_items: Optional[list] = None
    financial_status: Optional[str] = None
