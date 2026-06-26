"""Unit tests for the pure Identity entity and the NotAuthenticated exception."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.core.exceptions import DomainError, NotAuthenticated
from app.identity.domain.entities import Identity
from app.identity.domain.exceptions import EmailAlreadyRegistered

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_ID = UUID(int=1)


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
