"""Unit tests for the pure Organization and OrgStaffMembership entities (no DB)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, tzinfo
from uuid import UUID

import pytest

from app.organization.domain.entities import Organization, OrgStaffMembership
from app.organization.domain.value_objects import OrgRole, OrgType

_ORG_ID = UUID(int=200)
_IDENTITY_ID = UUID(int=10)
_MEMBERSHIP_ID = UUID(int=500)

# A bounded period [_FROM, _TO) plus instants before/inside/after it.
_BEFORE = datetime(2026, 1, 1, tzinfo=UTC)
_FROM = datetime(2026, 1, 8, tzinfo=UTC)
_INSIDE = datetime(2026, 1, 15, tzinfo=UTC)
_TO = datetime(2026, 1, 22, tzinfo=UTC)
_AFTER = datetime(2026, 1, 29, tzinfo=UTC)


class _NoOffsetTz(tzinfo):
    """A tzinfo that is set but whose ``utcoffset()`` returns ``None`` (AM2).

    Such a datetime has ``tzinfo is not None`` yet is treated as naive: the
    one-part ``tzinfo is None`` guard would wrongly accept it, so the entities use
    the two-part ``tzinfo is None or utcoffset() is None`` guard instead.
    """

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None


_UNKNOWN_OFFSET = datetime(2026, 1, 8, tzinfo=_NoOffsetTz())


def _organization(*, created_at: datetime = _FROM) -> Organization:
    return Organization(id=_ORG_ID, name="Acme Clinic", type=OrgType.CLINIC, created_at=created_at)


def _membership(
    *,
    role: OrgRole = OrgRole.ADMIN,
    effective_from: datetime = _FROM,
    effective_to: datetime | None = None,
) -> OrgStaffMembership:
    return OrgStaffMembership(
        id=_MEMBERSHIP_ID,
        identity_id=_IDENTITY_ID,
        org_id=_ORG_ID,
        role=role,
        effective_from=effective_from,
        effective_to=effective_to,
    )


# --- Organization -----------------------------------------------------------


def test_organization_round_trips_fields() -> None:
    org = _organization()
    assert org.id == _ORG_ID
    assert org.name == "Acme Clinic"
    assert org.type is OrgType.CLINIC
    assert org.created_at == _FROM


def test_organization_is_frozen() -> None:
    org = _organization()
    with pytest.raises(FrozenInstanceError):
        org.name = "Other Clinic"  # type: ignore[misc]


def test_organization_rejects_naive_created_at() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _organization(created_at=datetime(2026, 1, 8))  # noqa: DTZ001


def test_organization_rejects_unknown_offset_created_at() -> None:
    # tzinfo is set but utcoffset() is None: the two-part guard still rejects it.
    with pytest.raises(ValueError, match="timezone-aware"):
        _organization(created_at=_UNKNOWN_OFFSET)


def test_organization_accepts_tz_aware_created_at() -> None:
    org = _organization(created_at=_FROM)
    assert org.created_at is _FROM


# --- OrgStaffMembership construction invariants ------------------------------


def test_open_membership_valid() -> None:
    membership = _membership(effective_from=_FROM)
    assert membership.effective_from == _FROM
    assert membership.effective_to is None


def test_bounded_membership_valid() -> None:
    membership = _membership(effective_from=_FROM, effective_to=_TO)
    assert membership.effective_to == _TO


def test_membership_rejects_naive_effective_from() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _membership(effective_from=datetime(2026, 1, 8))  # noqa: DTZ001


def test_membership_rejects_naive_effective_to() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _membership(effective_from=_FROM, effective_to=datetime(2026, 1, 22))  # noqa: DTZ001


def test_membership_rejects_unknown_offset_effective_from() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _membership(effective_from=_UNKNOWN_OFFSET)


def test_membership_rejects_unknown_offset_effective_to() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _membership(effective_from=_FROM, effective_to=_UNKNOWN_OFFSET)


def test_membership_rejects_zero_length_period() -> None:
    with pytest.raises(ValueError, match="strictly before"):
        _membership(effective_from=_FROM, effective_to=_FROM)


def test_membership_rejects_inverted_period() -> None:
    with pytest.raises(ValueError, match="strictly before"):
        _membership(effective_from=_TO, effective_to=_FROM)


def test_membership_is_frozen() -> None:
    membership = _membership()
    with pytest.raises(FrozenInstanceError):
        membership.role = OrgRole.MEMBER  # type: ignore[misc]


# --- OrgStaffMembership.is_active_at (half-open [from, to)) -------------------


def test_active_before_from_is_false() -> None:
    assert _membership(effective_from=_FROM, effective_to=_TO).is_active_at(_BEFORE) is False


def test_active_at_from_is_true() -> None:
    assert _membership(effective_from=_FROM, effective_to=_TO).is_active_at(_FROM) is True


def test_active_inside_is_true() -> None:
    assert _membership(effective_from=_FROM, effective_to=_TO).is_active_at(_INSIDE) is True


def test_active_at_to_is_false_half_open() -> None:
    assert _membership(effective_from=_FROM, effective_to=_TO).is_active_at(_TO) is False


def test_active_after_to_is_false() -> None:
    assert _membership(effective_from=_FROM, effective_to=_TO).is_active_at(_AFTER) is False


def test_active_open_at_from_is_true() -> None:
    assert _membership(effective_from=_FROM).is_active_at(_FROM) is True


def test_active_open_after_from_is_true() -> None:
    assert _membership(effective_from=_FROM).is_active_at(_INSIDE) is True


def test_active_open_before_from_is_false() -> None:
    assert _membership(effective_from=_FROM).is_active_at(_BEFORE) is False


# --- OrgStaffMembership.is_admin --------------------------------------------


def test_is_admin_true_for_admin_role() -> None:
    assert _membership(role=OrgRole.ADMIN).is_admin is True


def test_is_admin_false_for_member_role() -> None:
    assert _membership(role=OrgRole.MEMBER).is_admin is False
