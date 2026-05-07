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

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


# Organization
class OrganizationResponse(BaseModel):
    id: str
    name: str
    plan: SubscriptionPlan
    status: AccountStatus
    max_users: int
    trial_ends_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


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

    model_config = {"from_attributes": True}


class InspectionAcceptRequest(BaseModel):
    """Bij accept kunnen velden worden overgenomen naar de gekoppelde melding."""
    apply_to_melding: bool = False  # update melding.category/priority/description?


class InspectionRejectRequest(BaseModel):
    reason: str


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
