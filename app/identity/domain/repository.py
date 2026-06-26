"""Outbound ports for Identity persistence: PURE (stdlib Protocol only).

The application service depends on these ``Protocol`` ports; the SQLAlchemy
adapters (``app/identity/repository.py``) implement them. Neither takes an
injectable ``now``: an Identity simply exists or not, and a Profile's activeness
is a soft-discard tombstone, not effective-dating (only memberships are time-gated).
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.identity.domain.entities import Identity, Profile


class IdentityRepository(Protocol):
    """Reads and stores Identity records by login key (email) or id."""

    def get_by_email(self, email: str) -> Identity | None: ...

    def get_by_id(self, identity_id: UUID) -> Identity | None: ...

    def add(self, identity: Identity) -> None: ...


class ProfileRepository(Protocol):
    """Reads and stores Profile rows (append-only, soft-discard).

    ``list_for`` returns ALL profiles for an identity (active and discarded) with
    NO filter: the activeness decision is single-homed in the domain
    (``Profile.is_active`` + the service), not in SQL.
    """

    def list_for(self, identity_id: UUID) -> list[Profile]: ...

    def add(self, profile: Profile) -> None: ...
