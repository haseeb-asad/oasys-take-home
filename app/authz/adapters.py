"""Infrastructure-edge adapter wiring the PDP's ``ProfileDirectory`` port.

This is the ONLY authz module that may reach across bounded contexts and touch
SQLAlchemy concretes: it is the composition root that answers the pure PDP's
``ProfileDirectory`` port (``app/authz/ports.py``) from the identity Profiles slice
and the organization staff-membership context. The pure policy / ports / context /
capabilities modules stay infra-free and context-free.

``ProfileDirectoryAdapter`` itself imports no SQLAlchemy - it depends only on the
two repository PORTS, so it is unit-testable with fakes. ``build_profile_directory``
is the factory that injects the concrete SQLAlchemy repositories for a session.

Semantics (matching ``planning/auth-authz-design.md`` and the ``FakeProfileDirectory``
the policy unit tests use):
* ``is_active_provider`` / ``is_active_client`` = an active (non-discarded) profile
  of that type. Profile activeness is a soft-discard tombstone, so ``now`` is
  accepted (the port threads it) but not used by the profile half.
* ``is_active_org_admin`` = an active ``org_staff`` PROFILE **and** an active admin
  org-staff MEMBERSHIP in the target org at ``now`` (the membership half is the only
  time-gated check).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.authz.ports import ProfileDirectory
from app.identity.domain.repository import ProfileRepository
from app.identity.domain.value_objects import ProfileType
from app.identity.repository import SqlAlchemyProfileRepository
from app.identity.service import has_active_profile
from app.organization.domain.repository import OrgStaffMembershipRepository
from app.organization.repository import SqlAlchemyOrgStaffMembershipRepository
from app.organization.service import has_active_admin_membership


@dataclass(frozen=True, slots=True)
class ProfileDirectoryAdapter:
    """Answers the ``ProfileDirectory`` port by composing the two context ports."""

    profiles: ProfileRepository
    memberships: OrgStaffMembershipRepository

    def is_active_provider(self, identity_id: UUID, now: datetime) -> bool:
        return has_active_profile(self.profiles, identity_id, ProfileType.PROVIDER)

    def is_active_client(self, identity_id: UUID, now: datetime) -> bool:
        return has_active_profile(self.profiles, identity_id, ProfileType.CLIENT)

    def is_active_org_admin(self, identity_id: UUID, org_id: UUID, now: datetime) -> bool:
        return has_active_profile(
            self.profiles, identity_id, ProfileType.ORG_STAFF
        ) and has_active_admin_membership(self.memberships, identity_id, org_id, now)


def build_profile_directory(session: Session) -> ProfileDirectory:
    """Wire the concrete SQLAlchemy repositories into a ProfileDirectory for ``session``."""
    return ProfileDirectoryAdapter(
        profiles=SqlAlchemyProfileRepository(session),
        memberships=SqlAlchemyOrgStaffMembershipRepository(session),
    )
