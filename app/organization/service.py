"""Organization application layer: org + org-staff-membership use cases.

Orchestrates the two repository ports; holds no infrastructure (no FastAPI /
SQLAlchemy / Pydantic). ``now`` (tz-aware) and ``new_id`` are injected so
id/created_at/effective_from are deterministic and testable (no hidden clock or
uuid). The SQLAlchemy adapters and any future ``/v1`` routes / seed wire these
use cases at the edge; this commit ships the persistence + use-case surface only.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.organization.domain.entities import Organization, OrgStaffMembership
from app.organization.domain.repository import (
    OrganizationRepository,
    OrgStaffMembershipRepository,
)
from app.organization.domain.value_objects import OrgRole, OrgType


def create_organization(
    repo: OrganizationRepository,
    name: str,
    type: OrgType,
    *,
    now: datetime,
    new_id: UUID,
) -> Organization:
    """Build the Organization and persist it via the port."""
    organization = Organization(id=new_id, name=name, type=type, created_at=now)
    repo.add(organization)
    return organization


def add_staff_membership(
    repo: OrgStaffMembershipRepository,
    *,
    identity_id: UUID,
    org_id: UUID,
    role: OrgRole,
    effective_from: datetime,
    effective_to: datetime | None = None,
    new_id: UUID,
) -> OrgStaffMembership:
    """Build an org-staff membership row and persist it via the port (append-only)."""
    membership = OrgStaffMembership(
        id=new_id,
        identity_id=identity_id,
        org_id=org_id,
        role=role,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    repo.add(membership)
    return membership


def has_active_admin_membership(
    repo: OrgStaffMembershipRepository,
    identity_id: UUID,
    org_id: UUID,
    now: datetime,
) -> bool:
    """True iff ``identity_id`` holds an active admin org-staff MEMBERSHIP in ``org_id``.

    Reads every row for the pair (the repo applies no filter) and lets the domain
    decide: an active admin exists iff some row ``is_admin and is_active_at(now)``
    (half-open ``[from, to)``). This proves an active admin *membership* only. The
    authz ``ProfileDirectory.is_active_org_admin`` port (wired in commit 9) means
    "active ``org_staff`` PROFILE **AND** active admin membership", so that
    adapter ANDs the identity/profile-state check with this read. The org context
    does not know about Profiles and never imports authz.
    """
    return any(
        membership.is_admin and membership.is_active_at(now)
        for membership in repo.list_for(identity_id, org_id)
    )
