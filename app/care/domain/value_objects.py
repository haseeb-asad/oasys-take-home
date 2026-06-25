"""Value objects for the Care Coordination (Episode) aggregate.

Pure domain layer — plain Python only (frozen dataclasses + stdlib ``enum`` /
``datetime``). No FastAPI / SQLAlchemy / Pydantic imports (project std 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Role(StrEnum):
    """The controlled care-team membership vocabulary (its single home in code).

    Exactly five provider roles. Consumed by the role -> capability grid in the
    authz context; ``org_admin`` is deliberately NOT here (it is an org-staff
    membership, not an episode role).
    """

    PHYSICIAN = "physician"
    PHYSIOTHERAPIST = "physiotherapist"
    PERSONAL_TRAINER = "personal_trainer"
    MASSAGE_THERAPIST = "massage_therapist"
    NUTRITION_COACH = "nutrition_coach"


@dataclass(frozen=True, slots=True)
class EffectivePeriod:
    """A half-open effective interval ``[effective_from, effective_to)``.

    ``effective_to is None`` means the period is open/ongoing (extends to +inf).
    Half-open semantics match the Postgres ``tstzrange(from, to)`` ``EXCLUDE``
    constraint exactly: a point is effective iff ``from <= point < to``, so two
    contiguous periods ``[a, b)`` and ``[b, c)`` neither overlap nor leave a gap
    at the boundary instant ``b``.

    Invariants (enforced at construction):
    - both bounds must be timezone-aware (the schema is ``TIMESTAMPTZ``);
    - a bounded period must have positive length (``from < to``) — a
      zero-length or inverted ``[t, t)`` / ``[t1, t0)`` period is rejected.
    """

    effective_from: datetime
    effective_to: datetime | None = None

    def __post_init__(self) -> None:
        if self.effective_from.tzinfo is None:
            raise ValueError("effective_from must be timezone-aware (TIMESTAMPTZ).")
        if self.effective_to is not None:
            if self.effective_to.tzinfo is None:
                raise ValueError("effective_to must be timezone-aware (TIMESTAMPTZ).")
            if self.effective_from >= self.effective_to:
                raise ValueError(
                    "effective_from must be strictly before effective_to "
                    "(a zero-length or inverted period is invalid)."
                )

    @property
    def is_open(self) -> bool:
        """True iff the period has no end (extends indefinitely)."""
        return self.effective_to is None

    def is_effective_at(self, now: datetime) -> bool:
        """True iff ``now`` falls in the half-open interval ``[from, to)``."""
        if now < self.effective_from:
            return False
        return self.effective_to is None or now < self.effective_to

    def overlaps(self, other: EffectivePeriod) -> bool:
        """True iff the two half-open intervals share at least one instant.

        ``None`` ends are treated as +inf. Contiguous intervals (the end of one
        equal to the start of the next) do NOT overlap.
        """
        self_ends_before_other_starts = (
            self.effective_to is not None and self.effective_to <= other.effective_from
        )
        other_ends_before_self_starts = (
            other.effective_to is not None and other.effective_to <= self.effective_from
        )
        return not (self_ends_before_other_starts or other_ends_before_self_starts)

    def closed_at(self, at: datetime) -> EffectivePeriod:
        """Return a copy of this (open) period ended at ``at``.

        Used by the append-only close-old/open-new handoff. Rejects ending an
        already-bounded period, a naive ``at``, or an ``at`` that would yield a
        non-positive-length interval (``at <= effective_from``).
        """
        if self.effective_to is not None:
            raise ValueError("Cannot end an already-bounded period.")
        return EffectivePeriod(self.effective_from, at)
