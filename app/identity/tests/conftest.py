"""Shared fixtures/helpers for the pure identity unit tests (no DB)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from app.core.security import hash_password
from app.identity.domain.entities import Identity

_IDENTITY_ID = UUID(int=1)
_CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def make_identity(email: str, password: str) -> Identity:
    """Build an Identity with a real hashed password (fixed id + tz-aware created_at)."""
    return Identity(
        id=_IDENTITY_ID,
        email=email,
        display_name="Test User",
        password_hash=hash_password(password),
        created_at=_CREATED_AT,
    )


@dataclass(slots=True)
class FakeIdentityRepository:
    """In-memory ``IdentityRepository`` adapter backed by a dict (no DB).

    Structurally satisfies the ``IdentityRepository`` port; tests seed only the
    rows they need.
    """

    by_email: dict[str, Identity] = field(default_factory=dict)

    def get_by_email(self, email: str) -> Identity | None:
        return self.by_email.get(email)
