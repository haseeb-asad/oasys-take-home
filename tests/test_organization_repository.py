"""Integration tests for the SQLAlchemy organization repositories (real Postgres).

Each test runs inside the per-test transaction (``db_session``) and is rolled
back at teardown, so the shared database stays order-independent (A19). These
prove the Postgres-only behaviour the pure unit tests cannot: the TIMESTAMPTZ
round trip, the ``type``/``role`` CHECK constraints, the two foreign keys, the
``effective_from`` ordering, and that ``list_for`` applies NO role/time filter
(the activeness decision is the domain's alone). FK parents (an Identity, an
Organization) are persisted first via their repositories; each IntegrityError is
the test's terminal DB action (the plain ``flush`` poisons the session, which the
per-test rollback then recovers).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.identity.domain.entities import Identity
from app.identity.repository import SqlAlchemyIdentityRepository
from app.organization.domain.entities import Organization, OrgStaffMembership
from app.organization.domain.value_objects import OrgRole, OrgType
from app.organization.repository import (
    SqlAlchemyOrganizationRepository,
    SqlAlchemyOrgStaffMembershipRepository,
)
from app.organization.service import has_active_admin_membership

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = datetime(2026, 6, 1, tzinfo=UTC)
_T_FUTURE = datetime(2027, 1, 1, tzinfo=UTC)


def _persist_identity(session: Session, email: str) -> UUID:
    """Persist an FK-parent Identity with a plain stub hash; return its id."""
    identity = Identity(
        id=uuid4(),
        email=email,
        display_name="Org Staff",
        password_hash="stub-hash",
        created_at=_T0,
    )
    SqlAlchemyIdentityRepository(session).add(identity)
    return identity.id


def _persist_org(session: Session, *, name: str = "Acme Clinic") -> UUID:
    """Persist an FK-parent Organization; return its id."""
    org = Organization(id=uuid4(), name=name, type=OrgType.CLINIC, created_at=_T0)
    SqlAlchemyOrganizationRepository(session).add(org)
    return org.id


# --- Organization repository -------------------------------------------------


def test_org_add_then_get_by_id_round_trip(db_session: Session) -> None:
    repo = SqlAlchemyOrganizationRepository(db_session)
    org = Organization(id=uuid4(), name="Acme Clinic", type=OrgType.CLINIC, created_at=_T0)
    repo.add(org)
    # Force a real DB read (not the identity map) so the TIMESTAMPTZ round trip is
    # genuinely exercised: a naive read-back would make Organization reject it.
    db_session.expunge_all()
    fetched = repo.get_by_id(org.id)
    assert fetched is not None
    assert fetched.id == org.id
    assert fetched.name == "Acme Clinic"
    assert fetched.type is OrgType.CLINIC
    assert fetched.created_at == _T0
    assert fetched.created_at.tzinfo is not None
    assert fetched.created_at.utcoffset() is not None


def test_org_get_by_id_missing_returns_none(db_session: Session) -> None:
    repo = SqlAlchemyOrganizationRepository(db_session)
    assert repo.get_by_id(uuid4()) is None


def test_all_org_types_accepted_by_check(db_session: Session) -> None:
    repo = SqlAlchemyOrganizationRepository(db_session)
    for org_type in OrgType:
        org = Organization(id=uuid4(), name=f"Org {org_type.value}", type=org_type, created_at=_T0)
        repo.add(org)
        db_session.expunge_all()
        fetched = repo.get_by_id(org.id)
        assert fetched is not None
        assert fetched.type is org_type


def test_org_type_check_rejects_bad_value_raw_insert(db_session: Session) -> None:
    # Bypass the domain (which the OrgType enum would block) to hit the DB CHECK.
    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO organizations (id, name, type, created_at) "
                "VALUES (gen_random_uuid(), 'Bad Org', 'spaceship', now())"
            )
        )
    assert "ck_organizations_type" in str(exc_info.value.orig)


# --- OrgStaffMembership repository -------------------------------------------


def test_membership_add_then_list_for_round_trip(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "member-rt@example.com")
    org_id = _persist_org(db_session)
    repo = SqlAlchemyOrgStaffMembershipRepository(db_session)
    membership = OrgStaffMembership(
        id=uuid4(),
        identity_id=identity_id,
        org_id=org_id,
        role=OrgRole.ADMIN,
        effective_from=_T0,
    )
    repo.add(membership)
    db_session.expunge_all()
    rows = repo.list_for(identity_id, org_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.id == membership.id
    assert row.identity_id == identity_id
    assert row.org_id == org_id
    assert row.role is OrgRole.ADMIN
    assert row.effective_from == _T0
    assert row.effective_to is None
    assert row.effective_from.tzinfo is not None
    assert row.effective_from.utcoffset() is not None


def test_membership_bounded_period_round_trip(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "bounded-rt@example.com")
    org_id = _persist_org(db_session)
    repo = SqlAlchemyOrgStaffMembershipRepository(db_session)
    membership = OrgStaffMembership(
        id=uuid4(),
        identity_id=identity_id,
        org_id=org_id,
        role=OrgRole.MEMBER,
        effective_from=_T0,
        effective_to=_T1,
    )
    repo.add(membership)
    db_session.expunge_all()
    rows = repo.list_for(identity_id, org_id)
    assert len(rows) == 1
    assert rows[0].effective_to == _T1
    assert rows[0].effective_to is not None
    assert rows[0].effective_to.tzinfo is not None
    assert rows[0].effective_to.utcoffset() is not None


def test_list_for_empty_returns_empty_list(db_session: Session) -> None:
    repo = SqlAlchemyOrgStaffMembershipRepository(db_session)
    assert repo.list_for(uuid4(), uuid4()) == []


def test_list_for_orders_by_effective_from(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "ordering@example.com")
    org_id = _persist_org(db_session)
    repo = SqlAlchemyOrgStaffMembershipRepository(db_session)
    # Insert the later period first; list_for must return it ordered by effective_from.
    repo.add(
        OrgStaffMembership(
            id=uuid4(),
            identity_id=identity_id,
            org_id=org_id,
            role=OrgRole.ADMIN,
            effective_from=_T1,
        )
    )
    repo.add(
        OrgStaffMembership(
            id=uuid4(),
            identity_id=identity_id,
            org_id=org_id,
            role=OrgRole.MEMBER,
            effective_from=_T0,
            effective_to=_T1,
        )
    )
    db_session.expunge_all()
    rows = repo.list_for(identity_id, org_id)
    assert [row.effective_from for row in rows] == [_T0, _T1]


def test_list_for_isolates_by_identity(db_session: Session) -> None:
    identity_a = _persist_identity(db_session, "iso-a@example.com")
    identity_b = _persist_identity(db_session, "iso-b@example.com")
    org_id = _persist_org(db_session)
    repo = SqlAlchemyOrgStaffMembershipRepository(db_session)
    repo.add(
        OrgStaffMembership(
            id=uuid4(),
            identity_id=identity_a,
            org_id=org_id,
            role=OrgRole.ADMIN,
            effective_from=_T0,
        )
    )
    repo.add(
        OrgStaffMembership(
            id=uuid4(),
            identity_id=identity_b,
            org_id=org_id,
            role=OrgRole.ADMIN,
            effective_from=_T0,
        )
    )
    db_session.expunge_all()
    rows = repo.list_for(identity_a, org_id)
    assert len(rows) == 1
    assert rows[0].identity_id == identity_a


def test_list_for_isolates_by_org(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "iso-org@example.com")
    org_a = _persist_org(db_session, name="Org A")
    org_b = _persist_org(db_session, name="Org B")
    repo = SqlAlchemyOrgStaffMembershipRepository(db_session)
    repo.add(
        OrgStaffMembership(
            id=uuid4(),
            identity_id=identity_id,
            org_id=org_a,
            role=OrgRole.ADMIN,
            effective_from=_T0,
        )
    )
    repo.add(
        OrgStaffMembership(
            id=uuid4(),
            identity_id=identity_id,
            org_id=org_b,
            role=OrgRole.ADMIN,
            effective_from=_T0,
        )
    )
    db_session.expunge_all()
    rows = repo.list_for(identity_id, org_a)
    assert len(rows) == 1
    assert rows[0].org_id == org_a


def test_list_for_returns_all_rows_unfiltered(db_session: Session) -> None:
    # AM4: for ONE (identity, org), store four memberships spanning every
    # role/temporal case; list_for must return ALL four (no SQL role/time filter).
    identity_id = _persist_identity(db_session, "all-rows@example.com")
    org_id = _persist_org(db_session)
    repo = SqlAlchemyOrgStaffMembershipRepository(db_session)
    repo.add(  # expired admin [t0, t1)
        OrgStaffMembership(
            id=uuid4(),
            identity_id=identity_id,
            org_id=org_id,
            role=OrgRole.ADMIN,
            effective_from=_T0,
            effective_to=_T1,
        )
    )
    repo.add(  # future admin [t_future, None)
        OrgStaffMembership(
            id=uuid4(),
            identity_id=identity_id,
            org_id=org_id,
            role=OrgRole.ADMIN,
            effective_from=_T_FUTURE,
        )
    )
    repo.add(  # active member [t0, None)
        OrgStaffMembership(
            id=uuid4(),
            identity_id=identity_id,
            org_id=org_id,
            role=OrgRole.MEMBER,
            effective_from=_T0,
        )
    )
    repo.add(  # active admin [t0, None)
        OrgStaffMembership(
            id=uuid4(),
            identity_id=identity_id,
            org_id=org_id,
            role=OrgRole.ADMIN,
            effective_from=_T0,
        )
    )
    db_session.expunge_all()
    assert len(repo.list_for(identity_id, org_id)) == 4


def test_all_org_roles_accepted_by_check(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "all-roles@example.com")
    org_id = _persist_org(db_session)
    repo = SqlAlchemyOrgStaffMembershipRepository(db_session)
    for role in OrgRole:
        repo.add(
            OrgStaffMembership(
                id=uuid4(),
                identity_id=identity_id,
                org_id=org_id,
                role=role,
                effective_from=_T0,
            )
        )
    db_session.expunge_all()
    rows = repo.list_for(identity_id, org_id)
    assert {row.role for row in rows} == set(OrgRole)


def test_membership_fk_violation_missing_identity_raises(db_session: Session) -> None:
    org_id = _persist_org(db_session)
    repo = SqlAlchemyOrgStaffMembershipRepository(db_session)
    membership = OrgStaffMembership(
        id=uuid4(), identity_id=uuid4(), org_id=org_id, role=OrgRole.ADMIN, effective_from=_T0
    )
    with pytest.raises(IntegrityError) as exc_info:
        repo.add(membership)
    assert "fk_org_staff_memberships_identity_id_identities" in str(exc_info.value.orig)


def test_membership_fk_violation_missing_org_raises(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "fk-org@example.com")
    repo = SqlAlchemyOrgStaffMembershipRepository(db_session)
    membership = OrgStaffMembership(
        id=uuid4(), identity_id=identity_id, org_id=uuid4(), role=OrgRole.ADMIN, effective_from=_T0
    )
    with pytest.raises(IntegrityError) as exc_info:
        repo.add(membership)
    assert "fk_org_staff_memberships_org_id_organizations" in str(exc_info.value.orig)


def test_membership_role_check_rejects_bad_value_raw_insert(db_session: Session) -> None:
    # FK parents satisfied so the ONLY violation is the role CHECK; raw insert
    # bypasses the domain (which the OrgRole enum would block).
    identity_id = _persist_identity(db_session, "role-check@example.com")
    org_id = _persist_org(db_session)
    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO org_staff_memberships "
                "(id, identity_id, org_id, role, effective_from) "
                "VALUES (gen_random_uuid(), :identity_id, :org_id, 'superuser', now())"
            ),
            {"identity_id": identity_id, "org_id": org_id},
        )
    assert "ck_org_staff_memberships_role" in str(exc_info.value.orig)


# --- has_active_admin_membership (service read over real Postgres) -----------


def test_has_active_admin_membership_true_for_active_admin_row(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "active-admin@example.com")
    org_id = _persist_org(db_session)
    repo = SqlAlchemyOrgStaffMembershipRepository(db_session)
    repo.add(
        OrgStaffMembership(
            id=uuid4(),
            identity_id=identity_id,
            org_id=org_id,
            role=OrgRole.ADMIN,
            effective_from=_T0,
        )
    )
    db_session.expunge_all()
    assert has_active_admin_membership(repo, identity_id, org_id, _T1) is True


def test_has_active_admin_membership_false_for_member_only_row(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "member-only@example.com")
    org_id = _persist_org(db_session)
    repo = SqlAlchemyOrgStaffMembershipRepository(db_session)
    repo.add(
        OrgStaffMembership(
            id=uuid4(),
            identity_id=identity_id,
            org_id=org_id,
            role=OrgRole.MEMBER,
            effective_from=_T0,
        )
    )
    db_session.expunge_all()
    assert has_active_admin_membership(repo, identity_id, org_id, _T1) is False


def test_has_active_admin_membership_false_for_expired_admin_row(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "expired-admin@example.com")
    org_id = _persist_org(db_session)
    repo = SqlAlchemyOrgStaffMembershipRepository(db_session)
    repo.add(
        OrgStaffMembership(
            id=uuid4(),
            identity_id=identity_id,
            org_id=org_id,
            role=OrgRole.ADMIN,
            effective_from=_T0,
            effective_to=_T1,
        )
    )
    db_session.expunge_all()
    # Queried at the half-open end _T1: the [t0, t1) row is no longer active.
    assert has_active_admin_membership(repo, identity_id, org_id, _T1) is False


def test_has_active_admin_membership_false_for_other_org(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "other-org@example.com")
    org_a = _persist_org(db_session, name="Org A")
    org_b = _persist_org(db_session, name="Org B")
    repo = SqlAlchemyOrgStaffMembershipRepository(db_session)
    repo.add(
        OrgStaffMembership(
            id=uuid4(),
            identity_id=identity_id,
            org_id=org_a,
            role=OrgRole.ADMIN,
            effective_from=_T0,
        )
    )
    db_session.expunge_all()
    assert has_active_admin_membership(repo, identity_id, org_b, _T1) is False
