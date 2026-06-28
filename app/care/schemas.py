"""Pydantic v2 DTOs for the care API (edge layer; A8/A9).

Pydantic lives ONLY here at the boundary; the domain stays plain Python. Request
schemas (``XCreate`` / verb payloads) validate input with declarative
``Annotated`` / ``Field`` / ``StringConstraints`` constraints; response schemas
(``XOut``) shape the output and never leak storage internals.

The care ``Role`` value object is used DIRECTLY as a request field type, so an
unknown role is a free 422 (no custom validator). ``EpisodeCreate`` deliberately
omits ``responsible_provider_id``: the responsible provider is SERVER-OWNED (the
authenticated caller), so a client-supplied value is ignored (Pydantic drops
unknown fields by default). ``EpisodeOut.from_episode`` exposes the three
append-only collections as full history (audit) PLUS the derived "current"
responsible / face at ``now`` (the design move: derive, never store).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from app.care.domain.episode import Episode
from app.care.domain.value_objects import Role

_Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
_ChangeReason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
_Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]


# --- Request schemas --------------------------------------------------------- #


class EpisodeCreate(BaseModel):
    """Open-episode request.

    ``responsible_provider_id`` is intentionally ABSENT: it is the authenticated
    provider (server-owned, AM2). ``face_provider_id`` / ``face_role`` allow a
    divergent booking face from ``t0`` (both required together; the aggregate
    enforces the pairing).
    """

    client_id: UUID
    reason: _Reason
    managing_org_id: UUID
    responsible_role: Role
    change_reason: _ChangeReason
    face_provider_id: UUID | None = None
    face_role: Role | None = None


class MemberCreate(BaseModel):
    """Add-member request (an optional bounded window folds the coverage case).

    ``covering_for`` is an informational coverage marker: when set it names the
    provider being covered and routes the call through ``episode.add_coverage``
    instead of ``episode.add_member``. It is NOT persisted and NOT FK-checked.
    ``effective_to`` is REQUIRED when ``covering_for`` is set (coverage must have a
    hard end date so access expires automatically).
    """

    provider_id: UUID
    role: Role
    change_reason: _ChangeReason
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    covering_for: UUID | None = None

    @model_validator(mode="after")
    def _require_effective_to_when_covering(self) -> MemberCreate:
        if self.covering_for is not None and self.effective_to is None:
            raise ValueError(
                "effective_to is required when covering_for is set (coverage must be bounded)"
            )
        return self


class MemberEnd(BaseModel):
    """End-member request; ``successor_face_id`` is required only if the leaver is the face."""

    effective_to: datetime
    change_reason: _ChangeReason
    successor_face_id: UUID | None = None


class ResponsibilityReassign(BaseModel):
    """Reassign clinical responsibility to a current member."""

    provider_id: UUID
    change_reason: _ChangeReason


class FaceSet(BaseModel):
    """Set the booking face to a current member."""

    provider_id: UUID
    change_reason: _ChangeReason


class ClinicalRecordCreate(BaseModel):
    """Author a clinical record (free-text body)."""

    body: _Body


# --- Response schemas -------------------------------------------------------- #


class MembershipOut(BaseModel):
    """One effective-dated membership row (carries its role)."""

    provider_id: UUID
    role: Role
    effective_from: datetime
    effective_to: datetime | None
    change_reason: str


class ResponsibilityOut(BaseModel):
    """One effective-dated clinical-responsibility row."""

    provider_id: UUID
    effective_from: datetime
    effective_to: datetime | None
    change_reason: str


class FaceOut(BaseModel):
    """One effective-dated booking-face row."""

    provider_id: UUID
    effective_from: datetime
    effective_to: datetime | None
    change_reason: str


class EpisodeOut(BaseModel):
    """Episode response: root fields + append-only history + derived current state."""

    id: UUID
    client_id: UUID
    reason: str
    status: str
    managing_org_id: UUID
    opened_at: datetime
    closed_at: datetime | None
    responsible_provider_id: UUID | None
    face_provider_id: UUID | None
    members: list[MembershipOut]
    responsibility: list[ResponsibilityOut]
    faces: list[FaceOut]

    @classmethod
    def from_episode(cls, episode: Episode, now: datetime) -> EpisodeOut:
        """Map the pure aggregate to the wire shape, deriving "current" at ``now``."""
        current_responsibility = episode.current_responsibility(now)
        current_face = episode.current_face(now)
        return cls(
            id=episode.id,
            client_id=episode.client_id,
            reason=episode.reason,
            status=episode.status.value,
            managing_org_id=episode.managing_org_id,
            opened_at=episode.opened_at,
            closed_at=episode.closed_at,
            responsible_provider_id=(
                current_responsibility.provider_id if current_responsibility is not None else None
            ),
            face_provider_id=current_face.provider_id if current_face is not None else None,
            members=[
                MembershipOut(
                    provider_id=m.provider_id,
                    role=m.role,
                    effective_from=m.period.effective_from,
                    effective_to=m.period.effective_to,
                    change_reason=m.change_reason,
                )
                for m in episode.memberships
            ],
            responsibility=[
                ResponsibilityOut(
                    provider_id=r.provider_id,
                    effective_from=r.period.effective_from,
                    effective_to=r.period.effective_to,
                    change_reason=r.change_reason,
                )
                for r in episode.responsibility
            ],
            faces=[
                FaceOut(
                    provider_id=f.provider_id,
                    effective_from=f.period.effective_from,
                    effective_to=f.period.effective_to,
                    change_reason=f.change_reason,
                )
                for f in episode.faces
            ],
        )


class ClinicalRecordOut(BaseModel):
    """A clinical record on the wire (maps from the domain record by attribute)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    episode_id: UUID
    author_provider_id: UUID
    body: str
    created_at: datetime


class RehabAssessmentOut(BaseModel):
    """A rehab assessment on the wire (maps from the domain record by attribute)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    episode_id: UUID
    author_provider_id: UUID
    body: str
    created_at: datetime
