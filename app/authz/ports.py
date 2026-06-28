"""Outbound port for the PDP's profile-state queries.

The policy decision point is pure and must not reach into the database; it
depends on this ``Protocol`` (a port), which infrastructure adapters implement.
Every method takes an injectable ``now`` so the
adapter can answer as-of a point in time (effective-dated profiles / org-staff
memberships), never via a hidden clock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID


class ProfileDirectory(Protocol):
    """Answers whether an identity holds a given active profile at ``now``."""

    def is_active_provider(self, identity_id: UUID, now: datetime) -> bool: ...

    def is_active_client(self, identity_id: UUID, now: datetime) -> bool: ...

    def is_active_org_admin(self, identity_id: UUID, org_id: UUID, now: datetime) -> bool: ...
