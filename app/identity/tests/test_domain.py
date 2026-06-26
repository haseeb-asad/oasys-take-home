"""Unit tests for the pure Identity / Profile entities and the NotAuthenticated exception."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.core.exceptions import DomainError, NotAuthenticated
from app.identity.domain.entities import Identity, Profile
from app.identity.domain.exceptions import EmailAlreadyRegistered
from app.identity.domain.value_objects import ProfileType

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_ID = UUID(int=1)
_IDENTITY_ID = UUID(int=2)


def _identity() -> Identity:
    return Identity(
        id=_ID,
        email="ada@example.com",
        display_name="Ada",
        password_hash="hashed-pw-stub",
        created_at=_T0,
    )


def test_identity_round_trips_fields() -> None:
    identity = _identity()
    assert identity.id == _ID
    assert identity.email == "ada@example.com"
    assert identity.display_name == "Ada"
    assert identity.password_hash == "hashed-pw-stub"
    assert identity.created_at == _T0


def test_identity_is_frozen() -> None:
    identity = _identity()
    with pytest.raises(FrozenInstanceError):
        identity.email = "eve@example.com"  # type: ignore[misc]


def test_identity_rejects_naive_created_at() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Identity(
            id=_ID,
            email="ada@example.com",
            display_name="Ada",
            password_hash="hashed-pw-stub",
            created_at=datetime(2026, 1, 1),  # noqa: DTZ001
        )


def _profile(*, discarded_at: datetime | None = None) -> Profile:
    return Profile(
        id=_ID,
        identity_id=_IDENTITY_ID,
        profile_type=ProfileType.PROVIDER,
        discarded_at=discarded_at,
    )


def test_profile_round_trips_fields() -> None:
    profile = _profile()
    assert profile.id == _ID
    assert profile.identity_id == _IDENTITY_ID
    assert profile.profile_type is ProfileType.PROVIDER
    assert profile.discarded_at is None


def test_profile_active_when_not_discarded() -> None:
    assert _profile().is_active is True


def test_profile_inactive_when_discarded() -> None:
    profile = _profile(discarded_at=_T0)
    assert profile.is_active is False
    assert profile.discarded_at == _T0


def test_profile_is_frozen() -> None:
    profile = _profile()
    with pytest.raises(FrozenInstanceError):
        profile.discarded_at = _T0  # type: ignore[misc]


def test_profile_rejects_naive_discarded_at() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Profile(
            id=_ID,
            identity_id=_IDENTITY_ID,
            profile_type=ProfileType.CLIENT,
            discarded_at=datetime(2026, 1, 1),  # noqa: DTZ001
        )


def test_not_authenticated_is_domain_error_and_generic() -> None:
    exc = NotAuthenticated()
    assert isinstance(exc, DomainError)
    message = str(exc)
    assert message  # non-empty default detail
    # Generic: reveals neither an email nor which credential check failed.
    lowered = message.lower()
    assert "@" not in message
    assert "password" not in lowered
    assert "email" not in lowered
    assert str(NotAuthenticated("x")) == "x"


def test_email_already_registered_is_domain_error_and_hides_address() -> None:
    exc = EmailAlreadyRegistered("ada@example.com")
    assert isinstance(exc, DomainError)
    # The address is kept for structured logging but never echoed in the message
    # (the central 409 body must not become a registration-enumeration oracle).
    assert exc.email == "ada@example.com"
    assert "ada@example.com" not in str(exc)
    assert str(exc)  # non-empty human-facing detail
