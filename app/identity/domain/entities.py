"""The Identity domain entity: PURE (stdlib + frozen dataclass only).

No FastAPI / SQLAlchemy / Pydantic imports (project std 1). The SQLAlchemy model
(``app/identity/orm.py``) and its repository adapter (``app/identity/repository.py``)
map to/from this type at the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.identity.domain.value_objects import ProfileType


@dataclass(frozen=True, slots=True)
class Identity:
    """Login credential (the authenticating subject).

    Mirrors the design's ``identities`` table; persistence lives in
    ``app/identity/orm.py``. ``id`` shares the identity id-space so authz
    identity-equality checks stay plain.
    """

    id: UUID
    email: str
    display_name: str
    password_hash: str
    created_at: datetime

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware.")


@dataclass(frozen=True, slots=True)
class Profile:
    """A persona an identity holds (client / provider / org_staff).

    Mirrors the design's ``profiles`` table; persistence lives in
    ``app/identity/orm.py``. Activeness is a soft-discard tombstone, NOT
    effective-dating: a profile is active iff it has not been discarded
    (``discarded_at is None``), so the read needs no ``now`` (unlike memberships).
    ``identity_id`` references ``identities.id``.
    """

    id: UUID
    identity_id: UUID
    profile_type: ProfileType
    discarded_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.discarded_at is not None and (
            self.discarded_at.tzinfo is None or self.discarded_at.utcoffset() is None
        ):
            raise ValueError("discarded_at must be timezone-aware.")

    @property
    def is_active(self) -> bool:
        """True iff this profile has not been soft-discarded."""
        return self.discarded_at is None
