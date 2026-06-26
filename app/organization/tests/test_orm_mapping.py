"""Unit tests for the organization ORM model <-> domain mappers (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.organization.domain.entities import Organization, OrgStaffMembership
from app.organization.domain.value_objects import OrgRole, OrgType
from app.organization.orm import (
    OrganizationModel,
    OrgStaffMembershipModel,
    _membership_to_domain,
    _membership_to_model,
    _org_to_domain,
    _org_to_model,
)

_ORG_ID = UUID(int=300)
_IDENTITY_ID = UUID(int=10)
_MEMBERSHIP_ID = UUID(int=301)
_CREATED_AT = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)
_FROM = datetime(2026, 1, 1, tzinfo=UTC)
_TO = datetime(2026, 6, 1, tzinfo=UTC)


def _organization() -> Organization:
    return Organization(id=_ORG_ID, name="Acme Clinic", type=OrgType.CLINIC, created_at=_CREATED_AT)


def _open_membership() -> OrgStaffMembership:
    return OrgStaffMembership(
        id=_MEMBERSHIP_ID,
        identity_id=_IDENTITY_ID,
        org_id=_ORG_ID,
        role=OrgRole.ADMIN,
        effective_from=_FROM,
    )


def _bounded_membership() -> OrgStaffMembership:
    return OrgStaffMembership(
        id=_MEMBERSHIP_ID,
        identity_id=_IDENTITY_ID,
        org_id=_ORG_ID,
        role=OrgRole.MEMBER,
        effective_from=_FROM,
        effective_to=_TO,
    )


# --- Organization mappers ---------------------------------------------------


def test_org_to_model_sets_all_columns() -> None:
    model = _org_to_model(_organization())
    assert model.id == _ORG_ID
    assert model.name == "Acme Clinic"
    assert model.type == "clinic"
    assert model.created_at == _CREATED_AT


def test_org_to_model_writes_type_value_string() -> None:
    # The column stores the raw VARCHAR value, never the StrEnum member.
    model = _org_to_model(_organization())
    assert model.type == "clinic"
    assert not isinstance(model.type, OrgType)


def test_org_to_domain_maps_all_columns() -> None:
    model = OrganizationModel(id=_ORG_ID, name="Acme Clinic", type="clinic", created_at=_CREATED_AT)
    assert _org_to_domain(model) == _organization()


def test_org_round_trip_preserves_tz_aware_created_at() -> None:
    round_tripped = _org_to_domain(_org_to_model(_organization()))
    assert round_tripped == _organization()
    assert round_tripped.created_at.tzinfo is not None
    assert round_tripped.created_at.utcoffset() is not None


# --- OrgStaffMembership mappers ---------------------------------------------


def test_membership_to_model_sets_all_columns() -> None:
    model = _membership_to_model(_bounded_membership())
    assert model.id == _MEMBERSHIP_ID
    assert model.identity_id == _IDENTITY_ID
    assert model.org_id == _ORG_ID
    assert model.role == "member"
    assert model.effective_from == _FROM
    assert model.effective_to == _TO


def test_membership_to_model_writes_role_value_string() -> None:
    model = _membership_to_model(_open_membership())
    assert model.role == "admin"
    assert not isinstance(model.role, OrgRole)


def test_membership_to_domain_maps_all_columns() -> None:
    model = OrgStaffMembershipModel(
        id=_MEMBERSHIP_ID,
        identity_id=_IDENTITY_ID,
        org_id=_ORG_ID,
        role="member",
        effective_from=_FROM,
        effective_to=_TO,
    )
    assert _membership_to_domain(model) == _bounded_membership()


def test_membership_round_trip_open_preserves_tz_and_none_to() -> None:
    round_tripped = _membership_to_domain(_membership_to_model(_open_membership()))
    assert round_tripped == _open_membership()
    assert round_tripped.effective_to is None
    assert round_tripped.effective_from.tzinfo is not None
    assert round_tripped.effective_from.utcoffset() is not None


def test_membership_round_trip_bounded_preserves_tz_aware_to() -> None:
    round_tripped = _membership_to_domain(_membership_to_model(_bounded_membership()))
    assert round_tripped == _bounded_membership()
    assert round_tripped.effective_to is not None
    assert round_tripped.effective_to.tzinfo is not None
    assert round_tripped.effective_to.utcoffset() is not None
