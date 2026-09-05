"""Centrale rollen- en permissies-module.

Eén bron-van-waarheid voor:
- De zes DB-rollen (UserRole) en hun Nederlandse labels/omschrijvingen
- Wie wat mag doen in de applicatie (can_*-helpers)
- FastAPI Depends-fabrieken voor route-bescherming

Ergens anders rollen-strings hardcoderen ('viewer', 'admin', etc.) is een
anti-pattern — gebruik altijd de UserRole-enum of de helpers hieronder.
"""

from typing import Optional, Set
from fastapi import Depends, HTTPException

from models import User, UserRole
from auth import get_current_user

# ─────────────────────────────────────────────────────────────────────────────
# Display-mapping (NL) — gebruikt door emails, UI-dropdowns en docs
# ─────────────────────────────────────────────────────────────────────────────

ROLE_LABELS_NL: dict[UserRole, str] = {
    UserRole.ADMIN:      "Beheerder",
    UserRole.MANAGER:    "Projectleider",
    UserRole.CONTRACTOR: "Aannemer",
    UserRole.INSPECTOR:  "Toezichthouder",
    UserRole.TECHNICIAN: "Technicus",
    UserRole.VIEWER:     "Opdrachtgever",
}

ROLE_DESCRIPTIONS_NL: dict[UserRole, str] = {
    UserRole.ADMIN:      "Volledige toegang, gebruikers- en projectbeheer",
    UserRole.MANAGER:    "Projecten beheren, rapportages en teamoverzicht",
    UserRole.CONTRACTOR: "Meldingen afronden en status wijzigen",
    UserRole.INSPECTOR:  "Meldingen aanmaken, geen status wijzigen",
    UserRole.TECHNICIAN: "Meldingen aanmaken en veldregistratie",
    UserRole.VIEWER:     "Alleen inzien, geen bewerkingen",
}


def label_for(role: UserRole | str | None) -> str:
    """NL-label voor een rol. Accepteert enum, raw string of None."""
    if role is None:
        return "Onbekend"
    if isinstance(role, str):
        try:
            role = UserRole(role)
        except ValueError:
            return role
    return ROLE_LABELS_NL.get(role, role.value)


# ─────────────────────────────────────────────────────────────────────────────
# Permissies — sets per actie (eenvoudig te onderhouden)
# ─────────────────────────────────────────────────────────────────────────────

_CAN_CREATE_MELDINGEN: Set[UserRole] = {
    UserRole.ADMIN, UserRole.MANAGER, UserRole.INSPECTOR, UserRole.TECHNICIAN,
}

_CAN_CHANGE_STATUS: Set[UserRole] = {
    UserRole.ADMIN, UserRole.MANAGER, UserRole.CONTRACTOR, UserRole.TECHNICIAN,
}

_CAN_EDIT_MELDING_FULL: Set[UserRole] = {
    UserRole.ADMIN, UserRole.MANAGER, UserRole.TECHNICIAN,
}

# Wie mag assets aanmaken/wijzigen — strenger dan meldingen want assets
# zijn de master-data van de organisatie
_CAN_MANAGE_ASSETS: Set[UserRole] = {
    UserRole.ADMIN, UserRole.MANAGER,
}


def _user_role(user: User) -> Optional[UserRole]:
    return user.role if user.role else None


def is_org_admin(user: User) -> bool:
    """Org-admin vlag — overstijgt de rol-enum (eigenaar van de organisatie)."""
    return bool(user.is_org_admin)


def is_platform_owner(user: User) -> bool:
    """Eigenaar van het platform — alleen FieldOps-org admins."""
    org = user.organization
    return bool(org and is_org_admin(user) and org.name == "FieldOps")


def can_create_meldingen(user: User) -> bool:
    if is_org_admin(user):
        return True
    role = _user_role(user)
    return role is not None and role in _CAN_CREATE_MELDINGEN


def can_change_status(user: User) -> bool:
    if is_org_admin(user):
        return True
    role = _user_role(user)
    return role is not None and role in _CAN_CHANGE_STATUS


def can_edit_melding_full(user: User) -> bool:
    if is_org_admin(user):
        return True
    role = _user_role(user)
    return role is not None and role in _CAN_EDIT_MELDING_FULL


def is_inspector(user: User) -> bool:
    """Toezichthouder mag z'n eigen meldingen bewerken (zonder status)."""
    return _user_role(user) == UserRole.INSPECTOR


def can_manage_assets(user: User) -> bool:
    """Assets aanmaken, bijwerken, archiveren — alleen admin/manager."""
    if is_org_admin(user):
        return True
    role = _user_role(user)
    return role is not None and role in _CAN_MANAGE_ASSETS


def can_manage_toolbox(user: User) -> bool:
    """Een toolbox opstellen, wijzigen of afsluiten — alleen admin/manager.

    Zo gaat het op de bouwplaats: de uitvoerder of werkvoorbereider leidt de
    bespreking, de rest is erbij. Tekenen mag daarom iedereen die is ingelogd
    (en externen via de deelnemerslijst); alleen het opstellen en afsluiten is
    voorbehouden. Deelt bewust dezelfde rollenset als assets — het is dezelfde
    groep "wie mag hier iets vastleggen".
    """
    if is_org_admin(user):
        return True
    role = _user_role(user)
    return role is not None and role in _CAN_MANAGE_ASSETS


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Depends-fabrieken
# ─────────────────────────────────────────────────────────────────────────────

def require_roles(*roles: UserRole, allow_org_admin: bool = True):
    """Gebruik als FastAPI dependency:

        @router.post("/", dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.MANAGER))])

    Org-admin krijgt standaard altijd door (set allow_org_admin=False om dat te blokkeren).
    """
    allowed: Set[UserRole] = set(roles)

    def _dep(current_user: User = Depends(get_current_user)) -> User:
        if allow_org_admin and is_org_admin(current_user):
            return current_user
        role = _user_role(current_user)
        if role is None or role not in allowed:
            raise HTTPException(status_code=403, detail="Geen rechten voor deze actie")
        return current_user

    return _dep


def require_org_admin(current_user: User = Depends(get_current_user)) -> User:
    """Vereist org-admin. Direct als Depends bruikbaar (geen factory)."""
    if not is_org_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin-rechten vereist")
    return current_user


def require_platform_owner(current_user: User = Depends(get_current_user)) -> User:
    """Vereist platform-eigenaar (FieldOps-org admin)."""
    if not is_platform_owner(current_user):
        raise HTTPException(status_code=403, detail="Geen toegang — alleen platform eigenaar")
    return current_user


def require_module(module_key: str):
    """Depends-fabriek: blokkeer de route als de org deze portaal-module
    niet aan heeft staan (PORTAL_MODULES in models.py).

        router = APIRouter(..., dependencies=[Depends(require_module("kunstwerken"))])

    NULL in organizations.enabled_modules = alles aan (default). De
    FieldOps-org (platform) heeft altijd alles — ook als iemand per ongeluk
    modules uitvinkt op de platform-org zelf.
    """
    def _dep(current_user: User = Depends(get_current_user)) -> User:
        org = current_user.organization
        if org is None or org.name == "FieldOps":
            return current_user
        if not org.module_enabled(module_key):
            raise HTTPException(
                status_code=403,
                detail=f"Module '{module_key}' is niet actief voor jouw organisatie")
        return current_user

    return _dep


def list_assignable_roles(current_user: User) -> list[dict]:
    """Lijst rollen die deze gebruiker aan anderen mag toewijzen.
    Alleen org-admin mag rollen wijzigen."""
    if not is_org_admin(current_user):
        return []
    return [
        {"value": r.value, "label": ROLE_LABELS_NL[r], "description": ROLE_DESCRIPTIONS_NL[r]}
        for r in UserRole
    ]
