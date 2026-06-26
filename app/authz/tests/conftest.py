"""Shared fixtures/helpers for the pure authz unit tests (no DB)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.authz.policy import Pdp
from app.care.domain.episode import Episode
from app.care.domain.value_objects import Role


def _uid(n: int) -> UUID:
    """Deterministic UUID for readable, exact-match assertions in tests."""
    return UUID(int=n)


# Stable identities used across the suite.
CLIENT = _uid(1)
PROVIDER_A = _uid(10)
PROVIDER_B = _uid(11)
PROVIDER_C = _uid(12)
ORG_STAFF = _uid(20)
MULTI = _uid(30)  # a single Identity holding several profiles (cross-surface tests)
EPISODE_ID = _uid(100)
ORG_ID = _uid(200)
OTHER_ORG_ID = _uid(201)


def at(week: int) -> datetime:
    """A tz-aware UTC instant, ``week`` weeks after a fixed epoch."""
    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    return epoch + timedelta(weeks=week)


@dataclass(slots=True)
class FakeProfileDirectory:
    """In-memory ``ProfileDirectory`` adapter backed by plain sets (no DB).

    Structurally satisfies the ``ProfileDirectory`` port. Presence in a set means
    "active"; ``now`` is accepted (the port threads it) but ignored here —
    profile activeness is modelled as a simple as-of-test set, while the temporal
    gating of EPISODE relationships stays the ``Episode`` aggregate's job.
    """

    active_providers: set[UUID] = field(default_factory=set)
    active_clients: set[UUID] = field(default_factory=set)
    org_admins: set[tuple[UUID, UUID]] = field(default_factory=set)

    def is_active_provider(self, identity_id: UUID, now: datetime) -> bool:
        return identity_id in self.active_providers

    def is_active_client(self, identity_id: UUID, now: datetime) -> bool:
        return identity_id in self.active_clients

    def is_active_org_admin(self, identity_id: UUID, org_id: UUID, now: datetime) -> bool:
        return (identity_id, org_id) in self.org_admins


@pytest.fixture
def t0() -> datetime:
    return at(0)


@pytest.fixture
def directory() -> FakeProfileDirectory:
    """An empty profile directory; each test seeds only the rows it needs."""
    return FakeProfileDirectory()


@pytest.fixture
def pdp(directory: FakeProfileDirectory) -> Pdp:
    return Pdp(directory)


@pytest.fixture
def active_episode(t0: datetime) -> Episode:
    """Active episode at t0: provider A is member + responsible + face; client = CLIENT."""
    return Episode.open(
        id=EPISODE_ID,
        client_id=CLIENT,
        reason="shoulder_rehab",
        managing_org_id=ORG_ID,
        now=t0,
        responsible_provider_id=PROVIDER_A,
        responsible_role=Role.PHYSIOTHERAPIST,
        change_reason="episode opened",
    )
