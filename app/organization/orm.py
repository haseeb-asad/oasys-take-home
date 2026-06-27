"""SQLAlchemy models for the organization tables + domain <-> model mappers.

Infrastructure/edge layer: the persistence shape, kept separate from the pure
``Organization`` / ``OrgStaffMembership`` domain entities
(``app/organization/domain/entities.py``). The four module-level mappers convert
across the boundary so the repository never leaks SQLAlchemy into the domain;
``type`` / ``role`` are stored as ``VARCHAR`` (A18) and mapped string <-> StrEnum
here. All timestamps are TIMESTAMPTZ (``DateTime(timezone=True)``): a naive
read-back would make the pure entities reject it.

``IdentityModel`` is imported (and re-exported under ``noqa: F401``) so the
``org_staff_memberships -> identities`` foreign key resolves on ``Base.metadata``
even under isolated test runs that import only this module. The reverse
dependency (domain -> other contexts) does not exist: the domain layer imports
nothing from identity.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.identity.orm import IdentityModel  # noqa: F401  (resolves the FK target)
from app.organization.domain.entities import Organization, OrgStaffMembership
from app.organization.domain.value_objects import OrgRole, OrgType


class OrganizationModel(Base):
    """The ``organizations`` table: one row per managing organization."""

    __tablename__ = "organizations"
    __table_args__ = (CheckConstraint("type IN ('gym', 'clinic', 'solo_practice')", name="type"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(Text(), nullable=False)
    type: Mapped[str] = mapped_column(String(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OrgStaffMembershipModel(Base):
    """The ``org_staff_memberships`` table: append-only effective-dated rows.

    No ``created_at`` (the row carries only business time); ``effective_from`` has
    no server default (it is supplied business time, never wall-clock now()).
    """

    __tablename__ = "org_staff_memberships"
    __table_args__ = (CheckConstraint("role IN ('admin', 'member')", name="role"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    identity_id: Mapped[UUID] = mapped_column(ForeignKey("identities.id"), nullable=False)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _org_to_domain(model: OrganizationModel) -> Organization:
    """Map a persisted organizations row to the pure domain entity."""
    return Organization(
        id=model.id,
        name=model.name,
        type=OrgType(model.type),
        created_at=model.created_at,
    )


def _org_to_model(organization: Organization) -> OrganizationModel:
    """Map a domain Organization to a new ORM row (every column set explicitly)."""
    return OrganizationModel(
        id=organization.id,
        name=organization.name,
        type=organization.type.value,
        created_at=organization.created_at,
    )


def _membership_to_domain(model: OrgStaffMembershipModel) -> OrgStaffMembership:
    """Map a persisted org_staff_memberships row to the pure domain entity."""
    return OrgStaffMembership(
        id=model.id,
        identity_id=model.identity_id,
        org_id=model.org_id,
        role=OrgRole(model.role),
        effective_from=model.effective_from,
        effective_to=model.effective_to,
    )


def _membership_to_model(membership: OrgStaffMembership) -> OrgStaffMembershipModel:
    """Map a domain OrgStaffMembership to a new ORM row (every column set explicitly)."""
    return OrgStaffMembershipModel(
        id=membership.id,
        identity_id=membership.identity_id,
        org_id=membership.org_id,
        role=membership.role.value,
        effective_from=membership.effective_from,
        effective_to=membership.effective_to,
    )
