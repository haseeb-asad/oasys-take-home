"""SQLAlchemy model for the ``identities`` table + domain <-> model mappers.

Infrastructure/edge layer: the persistence shape, kept separate from the pure
``Identity`` domain entity (``app/identity/domain/entities.py``). The module-level
``_to_domain`` / ``_to_model`` mappers convert across the boundary so the
repository never leaks SQLAlchemy into the domain. ``created_at`` is TIMESTAMPTZ
(``DateTime(timezone=True)``): a naive read-back would make the pure ``Identity``
reject it. The app supplies ``id`` and ``created_at`` explicitly; the server
defaults are safety nets for any non-ORM insert (e.g. seeding).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Text, func, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.identity.domain.entities import Identity


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
