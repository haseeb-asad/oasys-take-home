"""Outbound port for Episode persistence: PURE (stdlib Protocol only).

One port for the single aggregate root (A5): the ``Episode`` owns its membership,
responsibility, and booking-face child rows inside one consistency boundary, so
there is no separate child repository. The SQLAlchemy adapter
(``app/care/repository.py``) implements this; the application service
(``app/care/service.py``) depends on the port, never the adapter.

``save`` is an upsert of the whole aggregate (root + all child rows): it inserts
a new episode or syncs an existing one, appending newly-opened rows and closing
rows whose period changed. No injectable ``now`` - all business time already
lives on the aggregate (the service threads ``now`` into the mutators).
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.care.domain.clinical import ClinicalRecord, RehabAssessment
from app.care.domain.episode import Episode


class EpisodeRepository(Protocol):
    """Reads and stores the Episode aggregate root (with its child rows)."""

    def get(self, episode_id: UUID) -> Episode | None: ...

    def save(self, episode: Episode) -> None: ...


class ClinicalRecordRepository(Protocol):
    """Stores and lists write-once clinical records for an episode.

    ``list_for_episode`` applies NO access filter (ordered by ``created_at``): the
    PDP gates read access at the router against the parent ``Episode``, so the
    repository stays a dumb episode-scoped reader.
    """

    def add(self, record: ClinicalRecord) -> None: ...

    def list_for_episode(self, episode_id: UUID) -> list[ClinicalRecord]: ...


class RehabAssessmentRepository(Protocol):
    """Stores and lists write-once rehab assessments for an episode (PDP-gated at the edge)."""

    def add(self, assessment: RehabAssessment) -> None: ...

    def list_for_episode(self, episode_id: UUID) -> list[RehabAssessment]: ...
