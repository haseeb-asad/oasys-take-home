"""Unit tests for the identities / profiles ORM model <-> domain mappers (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.identity.domain.entities import Identity, Profile
from app.identity.domain.value_objects import ProfileType
from app.identity.orm import (
    IdentityModel,
    ProfileModel,
    _profile_to_domain,
    _profile_to_model,
    _to_domain,
    _to_model,
)

_ID = UUID(int=7)
_IDENTITY_ID = UUID(int=8)
_CREATED_AT = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)
_DISCARDED_AT = datetime(2026, 3, 1, 8, 15, tzinfo=UTC)


def _identity() -> Identity:
    return Identity(
        id=_ID,
        email="ada@example.com",
        display_name="Ada",
        password_hash="hashed-pw-stub",
        created_at=_CREATED_AT,
    )


def test_to_model_sets_all_columns_explicitly() -> None:
    model = _to_model(_identity())
    assert model.id == _ID
    assert model.email == "ada@example.com"
    assert model.display_name == "Ada"
    assert model.password_hash == "hashed-pw-stub"
    assert model.created_at == _CREATED_AT


def test_to_domain_maps_all_columns() -> None:
    model = IdentityModel(
        id=_ID,
        email="ada@example.com",
        display_name="Ada",
        password_hash="hashed-pw-stub",
        created_at=_CREATED_AT,
    )
    assert _to_domain(model) == _identity()


def test_round_trip_preserves_tz_aware_created_at() -> None:
    round_tripped = _to_domain(_to_model(_identity()))
    assert round_tripped == _identity()
    assert round_tripped.created_at.tzinfo is not None
    assert round_tripped.created_at.utcoffset() is not None


# --- Profile mappers --------------------------------------------------------


def _active_profile() -> Profile:
    return Profile(id=_ID, identity_id=_IDENTITY_ID, profile_type=ProfileType.PROVIDER)


def _discarded_profile() -> Profile:
    return Profile(
        id=_ID,
        identity_id=_IDENTITY_ID,
        profile_type=ProfileType.ORG_STAFF,
        discarded_at=_DISCARDED_AT,
    )


def test_profile_to_model_sets_all_columns_explicitly() -> None:
    model = _profile_to_model(_discarded_profile())
    assert model.id == _ID
    assert model.identity_id == _IDENTITY_ID
    assert model.profile_type == "org_staff"  # stored as the raw enum value
    assert model.discarded_at == _DISCARDED_AT


def test_profile_to_domain_maps_all_columns() -> None:
    model = ProfileModel(
        id=_ID,
        identity_id=_IDENTITY_ID,
        profile_type="provider",
        discarded_at=None,
    )
    assert _profile_to_domain(model) == _active_profile()


def test_profile_round_trip_active() -> None:
    round_tripped = _profile_to_domain(_profile_to_model(_active_profile()))
    assert round_tripped == _active_profile()
    assert round_tripped.is_active is True


def test_profile_round_trip_discarded_preserves_tz() -> None:
    round_tripped = _profile_to_domain(_profile_to_model(_discarded_profile()))
    assert round_tripped == _discarded_profile()
    assert round_tripped.is_active is False
    assert round_tripped.discarded_at is not None
    assert round_tripped.discarded_at.tzinfo is not None
    assert round_tripped.discarded_at.utcoffset() is not None
