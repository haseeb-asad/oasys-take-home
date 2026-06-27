"""Care application layer: the episode-lifecycle use cases.

Orchestrates the ``EpisodeRepository`` port and the ``Episode`` aggregate; holds no
infrastructure (no FastAPI / SQLAlchemy / Pydantic). ``now`` (tz-aware) and
``new_id`` are injected so opened_at / the episode id are deterministic and
testable (no hidden clock or uuid). The SQLAlchemy adapter and any future ``/v1``
routes wire these use cases at the edge; this commit ships the persistence +
open/read surface only (the mutation orchestrators - reassign, end-member, close -
land in a later commit).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.care.domain.episode import Episode
from app.care.domain.repository import EpisodeRepository
from app.care.domain.value_objects import Role


def open_episode(
    repo: EpisodeRepository,
    *,
    client_id: UUID,
    reason: str,
    managing_org_id: UUID,
    responsible_provider_id: UUID,
    responsible_role: Role,
    change_reason: str,
    now: datetime,
    new_id: UUID,
    face_provider_id: UUID | None = None,
    face_role: Role | None = None,
) -> Episode:
    """Open an episode (bootstrapping its invariants at ``now``) and persist it.

    Delegates the invariant set-up to ``Episode.open`` (member + responsible +
    face, with an optional divergent face), then saves the whole aggregate via the
    port. ``new_id`` / ``now`` are injected for determinism.
    """
    episode = Episode.open(
        id=new_id,
        client_id=client_id,
        reason=reason,
        managing_org_id=managing_org_id,
        now=now,
        responsible_provider_id=responsible_provider_id,
        responsible_role=responsible_role,
        change_reason=change_reason,
        face_provider_id=face_provider_id,
        face_role=face_role,
    )
    repo.save(episode)
    return episode


def get_episode(repo: EpisodeRepository, episode_id: UUID) -> Episode | None:
    """Return the Episode aggregate with ``episode_id`` if it exists, else ``None``."""
    return repo.get(episode_id)
