"""SQLAlchemy models for the ``identities`` / ``profiles`` tables + mappers.

Infrastructure/edge layer: the persistence shape, kept separate from the pure
``Identity`` / ``Profile`` domain entities (``app/identity/domain/entities.py``).
The module-level mappers convert across the boundary so the repository never
leaks SQLAlchemy into the domain. ``created_at`` / ``discarded_at`` are
TIMESTAMPTZ (``DateTime(timezone=True)``): a naive read-back would make the pure
entities reject it. The app supplies ``id`` and timestamps explicitly; the server
defaults are safety nets for any non-ORM insert (e.g. seeding). ``profile_type``
is stored as ``VARCHAR`` (A18) and mapped string <-> ``ProfileType`` here.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.identity.domain.entities import Identity, Profile
from app.identity.domain.value_objects import ProfileType


class IdentityModel(Base):
    """The ``identities`` table: one row per login credential."""

    __tablename__ = "identities"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    email: Mapped[str] = mapped_column(CITEXT(), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text(), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProfileModel(Base):
    """The ``profiles`` table: one row per persona an identity holds.

    Soft-discard (``discarded_at``), not effective-dated; no ``created_at`` (the
    row's only time is its tombstone). ``profile_type`` is ``VARCHAR + CHECK``
    (A18); the short token ``profile_type`` resolves to ``ck_profiles_profile_type``
    via the Base naming convention.
    """

    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("profile_type IN ('client', 'provider', 'org_staff')", name="profile_type"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    identity_id: Mapped[UUID] = mapped_column(ForeignKey("identities.id"), nullable=False)
    profile_type: Mapped[str] = mapped_column(String(), nullable=False)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _to_domain(model: IdentityModel) -> Identity:
    """Map a persisted row to the pure domain entity."""
    return Identity(
        id=model.id,
        email=model.email,
        display_name=model.display_name,
        password_hash=model.password_hash,
        created_at=model.created_at,
    )


def _to_model(identity: Identity) -> IdentityModel:
    """Map a domain entity to a new ORM row (every column set explicitly)."""
    return IdentityModel(
        id=identity.id,
        email=identity.email,
        display_name=identity.display_name,
        password_hash=identity.password_hash,
        created_at=identity.created_at,
    )


def _profile_to_domain(model: ProfileModel) -> Profile:
    """Map a persisted profiles row to the pure domain entity."""
    return Profile(
        id=model.id,
        identity_id=model.identity_id,
        profile_type=ProfileType(model.profile_type),
        discarded_at=model.discarded_at,
    )


def _profile_to_model(profile: Profile) -> ProfileModel:
    """Map a domain Profile to a new ORM row (every column set explicitly)."""
    return ProfileModel(
        id=profile.id,
        identity_id=profile.identity_id,
        profile_type=profile.profile_type.value,
        discarded_at=profile.discarded_at,
    )
