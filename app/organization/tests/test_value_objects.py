"""Unit tests for the organization value-object vocabularies (no DB)."""

from __future__ import annotations

from app.organization.domain.value_objects import OrgRole, OrgType


def test_org_type_values_exact_set() -> None:
    assert {t.value for t in OrgType} == {"gym", "clinic", "solo_practice"}


def test_org_type_is_str_enum() -> None:
    assert issubclass(OrgType, str)  # StrEnum -> a str-valued enum
    assert OrgType.GYM.value == "gym"


def test_org_role_values_exact_set() -> None:
    # The stored grant role is "admin" (never the authz grid key "org_admin").
    assert {r.value for r in OrgRole} == {"admin", "member"}


def test_org_role_is_str_enum() -> None:
    assert issubclass(OrgRole, str)  # StrEnum -> a str-valued enum
    assert OrgRole.ADMIN.value == "admin"
