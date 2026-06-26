"""The Identity domain entity — PURE (stdlib + frozen dataclass only).

No FastAPI / SQLAlchemy / Pydantic imports (project std 1). The SQLAlchemy model
and its repository adapter live elsewhere (commit 7) and map to/from this type at
the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Identity:
    """Login credential (the authenticating subject).

    Mirrors the design's ``identities`` table; persistence is commit 7. ``id``
    shares the identity id-space so authz identity-equality checks stay plain.
    """

    id: UUID
    email: str
    display_name: str
    password_hash: str
    created_at: datetime

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware.")
