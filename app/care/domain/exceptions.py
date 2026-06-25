"""Typed domain exceptions for the Care Coordination (Episode) aggregate.

Pure domain layer — imports zero infrastructure (no FastAPI / SQLAlchemy /
Pydantic). Each exception is mapped centrally to an HTTP status by the API
layer (see ``planning/auth-authz-design.md`` — the domain-exception -> HTTP
table); the mapping itself lives outside this pure module.
"""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import DomainError


class SelfTreatment(DomainError):
    """A provider may not be on the care team of their own episode.

    Raised when ``provider_identity_id == client_identity_id`` on
    ``add_member`` / ``assign_responsible`` / ``start_coverage``. Both ids are
    ``identities.id`` values, so the check is plain identity equality (no
    cross-context lookup inside the aggregate).
    """

    def __init__(self, identity_id: UUID) -> None:
        self.identity_id = identity_id
        super().__init__(f"Identity {identity_id} cannot be on the care team of their own episode.")


class NotACurrentMember(DomainError):
    """A role that requires a current member named a non-/not-yet-/no-longer member.

    Raised by ``assign_responsible`` / ``set_face`` (and by ``end_member`` when a
    handoff names no valid successor) when the named provider is not a member of
    the episode effective at ``now``.
    """

    def __init__(self, provider_id: UUID) -> None:
        self.provider_id = provider_id
        super().__init__(f"Provider {provider_id} is not a current member of this episode.")


class EpisodeClosed(DomainError):
    """A mutation was attempted on a closed (immutable) episode.

    Raised by every mutator on a closed episode and by ``close`` when the
    episode is already closed.
    """

    def __init__(self, episode_id: UUID) -> None:
        self.episode_id = episode_id
        super().__init__(f"Episode {episode_id} is closed and cannot be modified.")


class OverlappingPeriod(DomainError):
    """A responsibility / face handoff would overlap or be degenerate in time.

    Raised when a contiguous close-old/open-new handoff cannot be performed
    without producing two overlapping rows, or a zero-length ``[t, t)`` row
    (e.g. handing off at the very instant the current holder began).
    """

    def __init__(self, episode_id: UUID) -> None:
        self.episode_id = episode_id
        super().__init__(
            f"Operation would create an overlapping or degenerate "
            f"effective period on episode {episode_id}."
        )
