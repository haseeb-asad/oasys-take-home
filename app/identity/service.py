"""Identity application layer: authentication and registration use cases.

Orchestrates the ``IdentityRepository`` port and the password primitives; holds
no infrastructure (no FastAPI / SQLAlchemy / Pydantic). The SQLAlchemy adapter
and the ``/v1/auth`` routes wire these use cases to HTTP at the edge.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.core.security import hash_password, verify_password
from app.identity.domain.entities import Identity, Profile
from app.identity.domain.repository import IdentityRepository, ProfileRepository
from app.identity.domain.value_objects import ProfileType


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


def create_profile(
    repo: ProfileRepository,
    *,
    identity_id: UUID,
    profile_type: ProfileType,
    new_id: UUID,
) -> Profile:
    """Build an active Profile and persist it via the port.

    ``new_id`` is injected so the id is deterministic and testable. A profile is
    born active (``discarded_at is None``); discarding is a later use case.
    """
    profile = Profile(id=new_id, identity_id=identity_id, profile_type=profile_type)
    repo.add(profile)
    return profile


def has_active_profile(
    repo: ProfileRepository, identity_id: UUID, profile_type: ProfileType
) -> bool:
    """True iff ``identity_id`` holds an active profile of ``profile_type``.

    Reads every profile for the identity (the repo applies no filter) and lets the
    domain decide: active iff some row matches the type and is not discarded. No
    ``now`` - profile activeness is a soft-discard tombstone, not effective-dated.
    This backs the authz ``ProfileDirectory`` adapter's provider/client checks and
    the profile half of its ``is_active_org_admin``.
    """
    return any(
        profile.profile_type is profile_type and profile.is_active
        for profile in repo.list_for(identity_id)
    )
