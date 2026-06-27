"""Integration tests for the ProfileDirectory adapter over real Postgres (rolled back).

Wires the real ``build_profile_directory(session)`` (SQLAlchemy Profile +
OrgStaffMembership repositories) and persists real rows, proving the PDP's port is
answered end to end against the database: provider/client activeness from the
``profiles`` table, and ``is_active_org_admin`` as the AND of an active ``org_staff``
profile and an active admin ``org_staff_memberships`` row (gated half-open on
``now``). Each test runs inside the per-test transaction and is rolled back (A19).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.authz.adapters import build_profile_directory
from app.identity.domain.entities import Identity
from app.identity.domain.value_objects import ProfileType
from app.identity.repository import SqlAlchemyIdentityRepository, SqlAlchemyProfileRepository
from app.identity.service import create_profile
from app.organization.domain.entities import Organization
from app.organization.domain.value_objects import OrgRole, OrgType
from app.organization.repository import (
    SqlAlchemyOrganizationRepository,
    SqlAlchemyOrgStaffMembershipRepository,
)
from app.organization.service import add_staff_membership

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = _T0 + timedelta(weeks=4)


def _persist_identity(session: Session, label: str) -> UUID:
    # Unique email per call (label + uuid suffix) so the row never collides with a
    # pre-existing committed identity (e.g. a seeded admin@example.com); the email
    # value is incidental to these org-admin / activeness assertions.
    identity = Identity(
        id=uuid4(),
        email=f"{label}-{uuid4().hex[:8]}@example.com",
        display_name="Person",
        password_hash="stub-hash",
        created_at=_T0,
    )
    SqlAlchemyIdentityRepository(session).add(identity)
    return identity.id


def _persist_org(session: Session) -> UUID:
    org = Organization(id=uuid4(), name="Acme Clinic", type=OrgType.CLINIC, created_at=_T0)
    SqlAlchemyOrganizationRepository(session).add(org)
    return org.id


def _add_profile(session: Session, identity_id: UUID, profile_type: ProfileType) -> None:
    create_profile(
        SqlAlchemyProfileRepository(session),
        identity_id=identity_id,
        profile_type=profile_type,
        new_id=uuid4(),
    )


def _add_admin_membership(
    session: Session, identity_id: UUID, org_id: UUID, *, to: datetime | None = None
) -> None:
    add_staff_membership(
        SqlAlchemyOrgStaffMembershipRepository(session),
        identity_id=identity_id,
        org_id=org_id,
        role=OrgRole.ADMIN,
        effective_from=_T0,
        effective_to=to,
        new_id=uuid4(),
    )


# --- provider / client ------------------------------------------------------


def test_is_active_provider_true_over_real_rows(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "prov")
    _add_profile(db_session, identity_id, ProfileType.PROVIDER)
    db_session.expunge_all()
    directory = build_profile_directory(db_session)
    assert directory.is_active_provider(identity_id, _T0) is True
    assert directory.is_active_client(identity_id, _T0) is False  # only a provider profile


def test_is_active_client_true_over_real_rows(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "client")
    _add_profile(db_session, identity_id, ProfileType.CLIENT)
    db_session.expunge_all()
    directory = build_profile_directory(db_session)
    assert directory.is_active_client(identity_id, _T0) is True


def test_is_active_provider_false_for_unknown_identity(db_session: Session) -> None:
    directory = build_profile_directory(db_session)
    assert directory.is_active_provider(uuid4(), _T0) is False


# --- is_active_org_admin: AND of profile + membership -----------------------


def test_org_admin_true_with_profile_and_active_admin_membership(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "admin")
    org_id = _persist_org(db_session)
    _add_profile(db_session, identity_id, ProfileType.ORG_STAFF)
    _add_admin_membership(db_session, identity_id, org_id)
    db_session.expunge_all()
    directory = build_profile_directory(db_session)
    assert directory.is_active_org_admin(identity_id, org_id, _T0) is True


def test_org_admin_false_with_profile_but_expired_membership(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "expired-admin")
    org_id = _persist_org(db_session)
    _add_profile(db_session, identity_id, ProfileType.ORG_STAFF)
    _add_admin_membership(db_session, identity_id, org_id, to=_T1)  # [t0, t1)
    db_session.expunge_all()
    directory = build_profile_directory(db_session)
    # Queried at the half-open end t1: the membership is no longer active.
    assert directory.is_active_org_admin(identity_id, org_id, _T1) is False


def test_org_admin_false_with_admin_membership_but_no_org_staff_profile(
    db_session: Session,
) -> None:
    identity_id = _persist_identity(db_session, "no-profile-admin")
    org_id = _persist_org(db_session)
    # An active admin membership but only a PROVIDER profile (no org_staff profile).
    _add_profile(db_session, identity_id, ProfileType.PROVIDER)
    _add_admin_membership(db_session, identity_id, org_id)
    db_session.expunge_all()
    directory = build_profile_directory(db_session)
    assert directory.is_active_org_admin(identity_id, org_id, _T0) is False
