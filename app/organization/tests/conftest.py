"""Shared fixtures/helpers for the pure organization unit tests (no DB).

Mirrors the authz/identity test conftests: deterministic ``_uid`` ids and an
``at(week)`` clock for readable exact-match assertions, small factories
(``make_organization`` / ``make_membership``), and in-memory repository fakes
that structurally satisfy the two outbound ports. The membership fake records
``added`` rows and every ``list_for`` call so the service tests can prove the
domain (not the SQL) owns the role/temporal decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.organization.domain.entities import Organization, OrgStaffMembership
from app.organization.domain.value_objects import OrgRole, OrgType


def _uid(n: int) -> UUID:
    """Deterministic UUID for readable, exact-match assertions in tests."""
    return UUID(int=n)


def at(week: int) -> datetime:
    """A tz-aware UTC instant, ``week`` weeks after a fixed epoch."""
    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    return epoch + timedelta(weeks=week)


def make_organization(
    *,
    org_id: UUID | None = None,
    name: str = "Acme Clinic",
    org_type: OrgType = OrgType.CLINIC,
    created_at: datetime | None = None,
) -> Organization:
    """Build a valid Organization (fixed id + tz-aware created_at by default)."""
    return Organization(
        id=org_id if org_id is not None else _uid(200),
        name=name,
        type=org_type,
        created_at=created_at if created_at is not None else at(0),
    )


def make_membership(
    *,
    membership_id: UUID | None = None,
    identity_id: UUID | None = None,
    org_id: UUID | None = None,
    role: OrgRole = OrgRole.ADMIN,
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
) -> OrgStaffMembership:
    """Build a valid OrgStaffMembership (open admin period by default)."""
    return OrgStaffMembership(
        id=membership_id if membership_id is not None else _uid(500),
        identity_id=identity_id if identity_id is not None else _uid(10),
        org_id=org_id if org_id is not None else _uid(200),
        role=role,
        effective_from=effective_from if effective_from is not None else at(0),
        effective_to=effective_to,
    )


@dataclass(slots=True)
class FakeOrganizationRepository:
    """In-memory ``OrganizationRepository`` adapter backed by a dict (no DB).

    Structurally satisfies the port; ``added`` records every insert in order so a
    service test can assert what was persisted without a database.
    """

    by_id: dict[UUID, Organization] = field(default_factory=dict)
    added: list[Organization] = field(default_factory=list)

    def get_by_id(self, org_id: UUID) -> Organization | None:
        return self.by_id.get(org_id)

    def add(self, organization: Organization) -> None:
        self.added.append(organization)
        self.by_id[organization.id] = organization


@dataclass(slots=True)
class FakeOrgStaffMembershipRepository:
    """In-memory ``OrgStaffMembershipRepository`` adapter (no DB).

    ``list_for`` filters by the ``(identity_id, org_id)`` pair only (mirroring the
    SQL ``WHERE``), applying NO role/time filter, so the service owns the
    activeness decision. ``added`` records inserts; ``list_for_calls`` records the
    exact arguments of each read so a test can prove the service delegates the
    lookup with the right keys.
    """

    rows: list[OrgStaffMembership] = field(default_factory=list)
    added: list[OrgStaffMembership] = field(default_factory=list)
    list_for_calls: list[tuple[UUID, UUID]] = field(default_factory=list)

    def list_for(self, identity_id: UUID, org_id: UUID) -> list[OrgStaffMembership]:
        self.list_for_calls.append((identity_id, org_id))
        return [m for m in self.rows if m.identity_id == identity_id and m.org_id == org_id]

    def add(self, membership: OrgStaffMembership) -> None:
        self.added.append(membership)
        self.rows.append(membership)
