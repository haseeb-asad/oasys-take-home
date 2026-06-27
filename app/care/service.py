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

from app.care.domain.clinical import ClinicalRecord, RehabAssessment
from app.care.domain.episode import Episode
from app.care.domain.repository import (
    ClinicalRecordRepository,
    EpisodeRepository,
    RehabAssessmentRepository,
)
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


# --- team-management orchestrators ------------------------------------------ #
# Each receives the ALREADY-loaded-and-authorized aggregate (the request-scoped
# dependency loaded it for the PDP check), mutates it via the aggregate method
# (which threads ``now`` and enforces the invariants), and persists the whole
# aggregate via the port. Authorization stays OUT of the service: the dependency
# gates before the handler ever calls these.


def add_member(
    repo: EpisodeRepository,
    episode: Episode,
    *,
    provider_id: UUID,
    role: Role,
    change_reason: str,
    now: datetime,
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
) -> Episode:
    """Add a member (optionally a bounded coverage window) and persist the aggregate."""
    episode.add_member(
        provider_id=provider_id,
        role=role,
        now=now,
        change_reason=change_reason,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    repo.save(episode)
    return episode


def reassign_responsible(
    repo: EpisodeRepository,
    episode: Episode,
    *,
    provider_id: UUID,
    change_reason: str,
    now: datetime,
) -> Episode:
    """Hand clinical responsibility to ``provider_id`` (close-old/open-new) and persist."""
    episode.assign_responsible(provider_id=provider_id, now=now, change_reason=change_reason)
    repo.save(episode)
    return episode


def set_face(
    repo: EpisodeRepository,
    episode: Episode,
    *,
    provider_id: UUID,
    change_reason: str,
    now: datetime,
) -> Episode:
    """Set the booking face to ``provider_id`` (close-old/open-new) and persist."""
    episode.set_face(provider_id=provider_id, now=now, change_reason=change_reason)
    repo.save(episode)
    return episode


def end_member(
    repo: EpisodeRepository,
    episode: Episode,
    *,
    provider_id: UUID,
    effective_to: datetime,
    change_reason: str,
    now: datetime,
    successor_face_id: UUID | None = None,
) -> Episode:
    """End ``provider_id``'s membership at ``effective_to`` (with face handoff) and persist."""
    episode.end_member(
        provider_id=provider_id,
        effective_to=effective_to,
        now=now,
        change_reason=change_reason,
        successor_face_id=successor_face_id,
    )
    repo.save(episode)
    return episode


def close_episode(repo: EpisodeRepository, episode: Episode, *, now: datetime) -> Episode:
    """Close the episode (immutable thereafter) and persist."""
    episode.close(now=now)
    repo.save(episode)
    return episode


# --- clinical / rehab use cases --------------------------------------------- #


def add_clinical_record(
    repo: ClinicalRecordRepository,
    *,
    episode_id: UUID,
    author_provider_id: UUID,
    body: str,
    now: datetime,
    new_id: UUID,
) -> ClinicalRecord:
    """Author a write-once clinical record and persist it via the port.

    ``now`` (tz-aware authoring time) and ``new_id`` are injected for determinism.
    The author is the authenticated provider; access was gated upstream by the PDP.
    """
    record = ClinicalRecord(
        id=new_id,
        episode_id=episode_id,
        author_provider_id=author_provider_id,
        body=body,
        created_at=now,
    )
    repo.add(record)
    return record


def list_clinical_records(repo: ClinicalRecordRepository, episode_id: UUID) -> list[ClinicalRecord]:
    """Return the episode's clinical records (PDP gated the read upstream)."""
    return repo.list_for_episode(episode_id)


def list_rehab_assessments(
    repo: RehabAssessmentRepository, episode_id: UUID
) -> list[RehabAssessment]:
    """Return the episode's rehab assessments (PDP gated the read upstream)."""
    return repo.list_for_episode(episode_id)
