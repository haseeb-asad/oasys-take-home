"""Identity application layer: authentication and registration use cases.

Orchestrates the ``IdentityRepository`` port and the password primitives; holds
no infrastructure (no FastAPI / SQLAlchemy / Pydantic). The SQLAlchemy adapter
and the ``/v1/auth`` routes wire these use cases to HTTP at the edge.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.core.security import hash_password, verify_password
from app.identity.domain.entities import Identity
from app.identity.domain.repository import IdentityRepository


def authenticate(repo: IdentityRepository, email: str, password: str) -> Identity | None:
    """Return the Identity iff the email exists and the password matches; else None.

    The login route maps ``None`` -> 401 with a generic message (unknown email and
    wrong password are deliberately indistinguishable). No equalized dummy-verify
    (A12 cuts the timing-oracle hardening).
    """
    identity = repo.get_by_email(email)
    if identity is None:
        return None
    if not verify_password(password, identity.password_hash):
        return None
    return identity


def register(
    repo: IdentityRepository,
    email: str,
    display_name: str,
    password: str,
    *,
    now: datetime,
    new_id: UUID,
) -> Identity:
    """Hash the password, build the Identity, and persist it via the port.

    ``now`` (tz-aware) and ``new_id`` are injected so id/created_at are
    deterministic and testable. A duplicate email surfaces as
    ``EmailAlreadyRegistered`` from ``repo.add`` (race-free; no pre-check here).
    """
    identity = Identity(
        id=new_id,
        email=email,
        display_name=display_name,
        password_hash=hash_password(password),
        created_at=now,
    )
    repo.add(identity)
    return identity


def get_identity(repo: IdentityRepository, identity_id: UUID) -> Identity | None:
    """Return the Identity with ``identity_id`` if it exists, else ``None``."""
    return repo.get_by_id(identity_id)
