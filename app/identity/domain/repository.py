"""Outbound port for reading Identity records — PURE (stdlib Protocol only).

The application service depends on this ``Protocol`` (a port); a SQLAlchemy
adapter implements it in commit 7 (``app/identity/repository.py``), which also adds
``add`` / ``get_by_id``. No injectable ``now``: an Identity simply exists or not —
it is not effective-dated (only Profiles / memberships are).
"""

from __future__ import annotations

from typing import Protocol

from app.identity.domain.entities import Identity


class IdentityRepository(Protocol):
    """Reads Identity records by login key (email)."""

    def get_by_email(self, email: str) -> Identity | None: ...
