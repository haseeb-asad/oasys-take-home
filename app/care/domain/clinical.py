"""Episode-scoped clinical resources: ``ClinicalRecord`` and ``RehabAssessment``.

Pure domain layer (project std 1): plain Python only (frozen dataclasses +
stdlib). No FastAPI / SQLAlchemy / Pydantic imports.

Each is its OWN tiny, write-once aggregate root (A5), NOT a child of the
``Episode`` aggregate: it has zero invariants beyond a tz-aware ``created_at`` and
is never mutated after authoring, so it carries no membership/role logic. Access
is decided by the policy decision point against the PARENT ``Episode`` (resolved
via ``episode_id``), never by the record itself. ``created_at`` is wall-clock
authoring time (the row IS the event), unlike the effective-dated care child
rows which carry only a business-effective window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ClinicalRecord:
    """A write-once clinical note authored against an episode by a provider."""

    id: UUID
    episode_id: UUID
    author_provider_id: UUID
    body: str
    created_at: datetime

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware.")


@dataclass(frozen=True, slots=True)
class RehabAssessment:
    """A write-once rehab assessment authored against an episode by a provider."""

    id: UUID
    episode_id: UUID
    author_provider_id: UUID
    body: str
    created_at: datetime

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware.")
