"""Outbound port for Identity persistence: PURE (stdlib Protocol only).

The application service depends on this ``Protocol`` (a port); the SQLAlchemy
adapter (``app/identity/repository.py``) implements it. No injectable ``now``: an
Identity simply exists or not; it is not effective-dated (only Profiles and
memberships are).
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.identity.domain.entities import Identity


class IdentityRepository(Protocol):
    """Reads and stores Identity records by login key (email) or id."""

    def get_by_email(self, email: str) -> Identity | None: ...

    def get_by_id(self, identity_id: UUID) -> Identity | None: ...

    def add(self, identity: Identity) -> None: ...
