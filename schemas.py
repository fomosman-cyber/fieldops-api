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
    organization_id: str
    created_at: datetime

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


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


# Shopify Webhook
class ShopifyWebhookOrder(BaseModel):
    id: int
    email: str
    customer: Optional[dict] = None
    line_items: Optional[list] = None
    financial_status: Optional[str] = None
