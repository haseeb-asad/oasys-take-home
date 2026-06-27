"""Outbound ports for Organization persistence: PURE (stdlib Protocol only).

Two ports, one per aggregate root (A5): an ``Organization`` is a small root, and
an ``OrgStaffMembership`` is its own append-only root (no invariant binds the set
of memberships into an Organization consistency boundary, and the hot authz read
needs only memberships, never the org row). The SQLAlchemy adapters
(``app/organization/repository.py``) implement these; the application service
depends on the ports, never the adapters.

``list_for`` returns ALL rows for the ``(identity_id, org_id)`` pair with NO
role/time filter: the role + temporal (half-open) decision is single-homed in the
domain (``OrgStaffMembership`` + the service), not in SQL.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.organization.domain.entities import Organization, OrgStaffMembership


class OrganizationRepository(Protocol):
    """Reads and stores Organization aggregate roots by id."""

    def get_by_id(self, org_id: UUID) -> Organization | None: ...

    def add(self, organization: Organization) -> None: ...


class OrgStaffMembershipRepository(Protocol):
    """Reads and stores org-staff membership rows (append-only)."""

    def list_for(self, identity_id: UUID, org_id: UUID) -> list[OrgStaffMembership]: ...

    def add(self, membership: OrgStaffMembership) -> None: ...
