"""Shared fixtures/helpers for the pure Care-domain unit tests (no DB)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.care.domain.episode import Episode
from app.care.domain.value_objects import Role


def _uid(n: int) -> UUID:
    """Deterministic UUID for readable, exact-match assertions in tests."""
    return UUID(int=n)


@dataclass(slots=True)
class FakeEpisodeRepository:
    """In-memory ``EpisodeRepository`` adapter backed by a dict (no DB).

    Structurally satisfies the ``EpisodeRepository`` port; ``save`` upserts the
    whole aggregate by id (last write wins), mirroring the SQLAlchemy adapter's
    upsert contract without a database.
    """

    episodes: dict[UUID, Episode] = field(default_factory=dict)

    def get(self, episode_id: UUID) -> Episode | None:
        return self.episodes.get(episode_id)

    def save(self, episode: Episode) -> None:
        self.episodes[episode.id] = episode


# Stable identities used across the suite.
CLIENT = _uid(1)
PROVIDER_A = _uid(10)
PROVIDER_B = _uid(11)
PROVIDER_C = _uid(12)
EPISODE_ID = _uid(100)
ORG_ID = _uid(200)


def at(week: int) -> datetime:
    """A tz-aware UTC instant, ``week`` weeks after a fixed epoch."""
    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    return epoch + timedelta(weeks=week)


@pytest.fixture
def t0() -> datetime:
    return at(0)


@pytest.fixture
def episode(t0: datetime) -> Episode:
    """An active episode opened at t0 with provider A as responsible + face."""
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
