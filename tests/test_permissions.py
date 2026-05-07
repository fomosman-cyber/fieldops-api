"""Tests voor permissions.py — helpers + Depends-fabrieken."""

import pytest
from models import UserRole
import permissions as P


class FakeOrg:
    def __init__(self, name="Acme"):
        self.name = name


class FakeUser:
    def __init__(self, role, is_org_admin=False, org_name="Acme"):
        self.role = role
        self.is_org_admin = is_org_admin
        self.organization = FakeOrg(org_name)


def test_label_for_enum():
    assert P.label_for(UserRole.ADMIN) == "Beheerder"
    assert P.label_for(UserRole.VIEWER) == "Opdrachtgever"


def test_label_for_string():
    assert P.label_for("manager") == "Projectleider"
    assert P.label_for("inspector") == "Toezichthouder"


def test_label_for_none_or_unknown():
    assert P.label_for(None) == "Onbekend"
    assert P.label_for("not-a-role") == "not-a-role"


@pytest.mark.parametrize("role,expected", [
    (UserRole.ADMIN, True),
    (UserRole.MANAGER, True),
    (UserRole.INSPECTOR, True),
    (UserRole.TECHNICIAN, True),
    (UserRole.CONTRACTOR, False),
    (UserRole.VIEWER, False),
])
def test_can_create_meldingen(role, expected):
    assert P.can_create_meldingen(FakeUser(role)) is expected


def test_can_create_meldingen_org_admin_overrides():
    # Viewer normaal niet, maar als is_org_admin=True wel
    assert P.can_create_meldingen(FakeUser(UserRole.VIEWER, is_org_admin=True)) is True


@pytest.mark.parametrize("role,expected", [
    (UserRole.ADMIN, True),
    (UserRole.MANAGER, True),
    (UserRole.CONTRACTOR, True),
    (UserRole.TECHNICIAN, True),
    (UserRole.INSPECTOR, False),
    (UserRole.VIEWER, False),
])
def test_can_change_status(role, expected):
    assert P.can_change_status(FakeUser(role)) is expected


@pytest.mark.parametrize("role,expected", [
    (UserRole.ADMIN, True),
    (UserRole.MANAGER, True),
    (UserRole.TECHNICIAN, True),
    (UserRole.INSPECTOR, False),
    (UserRole.CONTRACTOR, False),
    (UserRole.VIEWER, False),
])
def test_can_edit_melding_full(role, expected):
    assert P.can_edit_melding_full(FakeUser(role)) is expected


def test_is_inspector():
    assert P.is_inspector(FakeUser(UserRole.INSPECTOR)) is True
    assert P.is_inspector(FakeUser(UserRole.ADMIN)) is False


def test_can_manage_assets():
    # Alleen admin/manager
    assert P.can_manage_assets(FakeUser(UserRole.ADMIN)) is True
    assert P.can_manage_assets(FakeUser(UserRole.MANAGER)) is True
    assert P.can_manage_assets(FakeUser(UserRole.TECHNICIAN)) is False
    assert P.can_manage_assets(FakeUser(UserRole.INSPECTOR)) is False
    # Org-admin override
    assert P.can_manage_assets(FakeUser(UserRole.VIEWER, is_org_admin=True)) is True


def test_is_platform_owner():
    assert P.is_platform_owner(FakeUser(UserRole.ADMIN, True, "FieldOps")) is True
    assert P.is_platform_owner(FakeUser(UserRole.ADMIN, True, "Acme")) is False
    assert P.is_platform_owner(FakeUser(UserRole.ADMIN, False, "FieldOps")) is False


def test_list_assignable_roles_for_admin():
    rows = P.list_assignable_roles(FakeUser(UserRole.ADMIN, is_org_admin=True))
    assert len(rows) == 6
    assert any(r["value"] == "admin" for r in rows)


def test_list_assignable_roles_for_non_admin_empty():
    rows = P.list_assignable_roles(FakeUser(UserRole.MANAGER))
    assert rows == []
