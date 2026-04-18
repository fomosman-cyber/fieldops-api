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
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    project = relationship("Project")
    organization = relationship("Organization")
    creator = relationship("User", foreign_keys=[created_by])


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
