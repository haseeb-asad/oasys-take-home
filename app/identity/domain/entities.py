"""The Identity domain entity: PURE (stdlib + frozen dataclass only).

No FastAPI / SQLAlchemy / Pydantic imports (project std 1). The SQLAlchemy model
(``app/identity/orm.py``) and its repository adapter (``app/identity/repository.py``)
map to/from this type at the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


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
