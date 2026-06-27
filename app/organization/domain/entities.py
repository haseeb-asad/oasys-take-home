"""The Organization domain entities: PURE (stdlib + frozen dataclasses only).

No FastAPI / SQLAlchemy / Pydantic imports (project std 1). The SQLAlchemy models
(``app/organization/orm.py``) and their repository adapters
(``app/organization/repository.py``) map to/from these types at the boundary.

``OrgStaffMembership`` carries its effective-dating INLINE (it does not import
care's ``EffectivePeriod``): the two contexts stay decoupled (A3), with no shared
domain types. The interval is half-open ``[effective_from, effective_to)`` -
identical semantics to care, but single-homed here so the org context owns its
own temporal rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.organization.domain.value_objects import OrgRole, OrgType


@dataclass(frozen=True, slots=True)
class Organization:
    """A managing organization (gym / clinic / solo practice).

    Mirrors the design's ``organizations`` table; persistence lives in
    ``app/organization/orm.py``. ``type`` is an ``OrgType`` value object, stored
    as its string value under a ``VARCHAR + CHECK`` (A18).
    """

    id: UUID
    name: str
    type: OrgType
    created_at: datetime

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware.")


@dataclass(frozen=True, slots=True)
class OrgStaffMembership:
    """An effective-dated, append-only org-staff membership row.

    Mirrors ``org_staff_memberships``. The half-open interval
    ``[effective_from, effective_to)`` follows the same rule as the rest of the
    system: a point is active iff ``effective_from <= now < effective_to``, so
    two contiguous periods ``[a, b)`` and ``[b, c)`` neither overlap nor gap at
    the boundary instant ``b``. ``effective_to is None`` means the period is
    open/ongoing. Unlike the per-episode tables there is NO one-at-a-time
    constraint: an identity may hold overlapping or multi-org memberships.

    Invariants (enforced at construction):
    - both bounds must be timezone-aware (the schema is ``TIMESTAMPTZ``);
    - a bounded period must have positive length (``effective_from < effective_to``).
    """

    id: UUID
    identity_id: UUID
    org_id: UUID
    role: OrgRole
    effective_from: datetime
    effective_to: datetime | None = None

    def __post_init__(self) -> None:
        if self.effective_from.tzinfo is None or self.effective_from.utcoffset() is None:
            raise ValueError("effective_from must be timezone-aware (TIMESTAMPTZ).")
        if self.effective_to is not None:
            if self.effective_to.tzinfo is None or self.effective_to.utcoffset() is None:
                raise ValueError("effective_to must be timezone-aware (TIMESTAMPTZ).")
            if self.effective_from >= self.effective_to:
                raise ValueError(
                    "effective_from must be strictly before effective_to "
                    "(a zero-length or inverted period is invalid)."
                )

    @property
    def is_admin(self) -> bool:
        """True iff this membership carries the org-admin grant role."""
        return self.role is OrgRole.ADMIN

    def is_active_at(self, now: datetime) -> bool:
        """True iff ``now`` falls in the half-open interval ``[from, to)``."""
        if now < self.effective_from:
            return False
        return self.effective_to is None or now < self.effective_to
