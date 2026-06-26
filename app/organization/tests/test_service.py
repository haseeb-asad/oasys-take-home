"""Unit tests for the organization use cases (no DB).

``create_organization`` / ``add_staff_membership`` build + persist via the port;
``has_active_admin_membership`` reads ALL rows for an ``(identity, org)`` pair and
applies the role/temporal decision in the domain (the fake repo never filters by
role or time). The half-open ``[from, to)`` boundaries are asserted exactly.
"""

from __future__ import annotations

from uuid import UUID

from app.organization.domain.value_objects import OrgRole, OrgType
from app.organization.service import (
    add_staff_membership,
    create_organization,
    has_active_admin_membership,
)

from .conftest import (
    FakeOrganizationRepository,
    FakeOrgStaffMembershipRepository,
    at,
    make_membership,
)

_NEW_ORG_ID = UUID(int=900)
_NEW_MEMBERSHIP_ID = UUID(int=901)
_IDENTITY_ID = UUID(int=10)
_ORG_ID = UUID(int=200)
_OTHER_ORG_ID = UUID(int=201)


# --- create_organization ----------------------------------------------------


def test_create_organization_builds_and_persists() -> None:
    repo = FakeOrganizationRepository()
    org = create_organization(repo, "Acme Clinic", OrgType.CLINIC, now=at(0), new_id=_NEW_ORG_ID)
    assert repo.added == [org]
    assert repo.get_by_id(_NEW_ORG_ID) is org


def test_create_organization_uses_injected_id_and_now() -> None:
    repo = FakeOrganizationRepository()
    org = create_organization(repo, "Acme Gym", OrgType.GYM, now=at(3), new_id=_NEW_ORG_ID)
    assert org.id == _NEW_ORG_ID
    assert org.created_at == at(3)
    assert org.name == "Acme Gym"
    assert org.type is OrgType.GYM


# --- add_staff_membership ---------------------------------------------------


def test_add_staff_membership_builds_and_persists() -> None:
    repo = FakeOrgStaffMembershipRepository()
    membership = add_staff_membership(
        repo,
        identity_id=_IDENTITY_ID,
        org_id=_ORG_ID,
        role=OrgRole.ADMIN,
        effective_from=at(0),
        new_id=_NEW_MEMBERSHIP_ID,
    )
    assert repo.added == [membership]


def test_add_staff_membership_uses_injected_values() -> None:
    repo = FakeOrgStaffMembershipRepository()
    membership = add_staff_membership(
        repo,
        identity_id=_IDENTITY_ID,
        org_id=_ORG_ID,
        role=OrgRole.MEMBER,
        effective_from=at(2),
        new_id=_NEW_MEMBERSHIP_ID,
    )
    assert membership.id == _NEW_MEMBERSHIP_ID
    assert membership.identity_id == _IDENTITY_ID
    assert membership.org_id == _ORG_ID
    assert membership.role is OrgRole.MEMBER
    assert membership.effective_from == at(2)
    assert membership.effective_to is None


def test_add_staff_membership_with_bounded_period() -> None:
    repo = FakeOrgStaffMembershipRepository()
    membership = add_staff_membership(
        repo,
        identity_id=_IDENTITY_ID,
        org_id=_ORG_ID,
        role=OrgRole.ADMIN,
        effective_from=at(0),
        effective_to=at(4),
        new_id=_NEW_MEMBERSHIP_ID,
    )
    assert membership.effective_to == at(4)
    assert repo.added == [membership]


# --- has_active_admin_membership --------------------------------------------


def test_has_active_admin_membership_true_for_active_admin() -> None:
    repo = FakeOrgStaffMembershipRepository(
        rows=[make_membership(identity_id=_IDENTITY_ID, org_id=_ORG_ID, role=OrgRole.ADMIN)]
    )
    assert has_active_admin_membership(repo, _IDENTITY_ID, _ORG_ID, at(1)) is True


def test_has_active_admin_membership_false_when_no_rows() -> None:
    repo = FakeOrgStaffMembershipRepository()
    assert has_active_admin_membership(repo, _IDENTITY_ID, _ORG_ID, at(1)) is False


def test_has_active_admin_membership_false_for_active_member_only() -> None:
    repo = FakeOrgStaffMembershipRepository(
        rows=[make_membership(identity_id=_IDENTITY_ID, org_id=_ORG_ID, role=OrgRole.MEMBER)]
    )
    assert has_active_admin_membership(repo, _IDENTITY_ID, _ORG_ID, at(1)) is False


def test_has_active_admin_membership_false_for_expired_admin() -> None:
    repo = FakeOrgStaffMembershipRepository(
        rows=[
            make_membership(
                identity_id=_IDENTITY_ID,
                org_id=_ORG_ID,
                role=OrgRole.ADMIN,
                effective_from=at(0),
                effective_to=at(2),
            )
        ]
    )
    # at(3) is past the half-open end at(2): inactive.
    assert has_active_admin_membership(repo, _IDENTITY_ID, _ORG_ID, at(3)) is False


def test_has_active_admin_membership_false_for_future_admin() -> None:
    repo = FakeOrgStaffMembershipRepository(
        rows=[
            make_membership(
                identity_id=_IDENTITY_ID, org_id=_ORG_ID, role=OrgRole.ADMIN, effective_from=at(5)
            )
        ]
    )
    assert has_active_admin_membership(repo, _IDENTITY_ID, _ORG_ID, at(1)) is False


def test_has_active_admin_membership_true_when_member_and_admin_both_active() -> None:
    repo = FakeOrgStaffMembershipRepository(
        rows=[
            make_membership(
                membership_id=UUID(int=1),
                identity_id=_IDENTITY_ID,
                org_id=_ORG_ID,
                role=OrgRole.MEMBER,
            ),
            make_membership(
                membership_id=UUID(int=2),
                identity_id=_IDENTITY_ID,
                org_id=_ORG_ID,
                role=OrgRole.ADMIN,
            ),
        ]
    )
    assert has_active_admin_membership(repo, _IDENTITY_ID, _ORG_ID, at(1)) is True


def test_has_active_admin_membership_true_when_expired_and_active_admin() -> None:
    repo = FakeOrgStaffMembershipRepository(
        rows=[
            make_membership(
                membership_id=UUID(int=1),
                identity_id=_IDENTITY_ID,
                org_id=_ORG_ID,
                role=OrgRole.ADMIN,
                effective_from=at(0),
                effective_to=at(2),
            ),
            make_membership(
                membership_id=UUID(int=2),
                identity_id=_IDENTITY_ID,
                org_id=_ORG_ID,
                role=OrgRole.ADMIN,
                effective_from=at(2),
            ),
        ]
    )
    assert has_active_admin_membership(repo, _IDENTITY_ID, _ORG_ID, at(3)) is True


def test_has_active_admin_membership_true_at_effective_from_instant() -> None:
    repo = FakeOrgStaffMembershipRepository(
        rows=[
            make_membership(
                identity_id=_IDENTITY_ID, org_id=_ORG_ID, role=OrgRole.ADMIN, effective_from=at(1)
            )
        ]
    )
    # now == effective_from is active (half-open start is inclusive).
    assert has_active_admin_membership(repo, _IDENTITY_ID, _ORG_ID, at(1)) is True


def test_has_active_admin_membership_false_at_effective_to_instant() -> None:
    repo = FakeOrgStaffMembershipRepository(
        rows=[
            make_membership(
                identity_id=_IDENTITY_ID,
                org_id=_ORG_ID,
                role=OrgRole.ADMIN,
                effective_from=at(0),
                effective_to=at(2),
            )
        ]
    )
    # now == effective_to is inactive (half-open end is exclusive).
    assert has_active_admin_membership(repo, _IDENTITY_ID, _ORG_ID, at(2)) is False


def test_has_active_admin_membership_false_for_admin_in_other_org() -> None:
    repo = FakeOrgStaffMembershipRepository(
        rows=[make_membership(identity_id=_IDENTITY_ID, org_id=_OTHER_ORG_ID, role=OrgRole.ADMIN)]
    )
    assert has_active_admin_membership(repo, _IDENTITY_ID, _ORG_ID, at(1)) is False


def test_has_active_admin_membership_calls_list_for_with_identity_and_org() -> None:
    repo = FakeOrgStaffMembershipRepository()
    has_active_admin_membership(repo, _IDENTITY_ID, _ORG_ID, at(1))
    assert repo.list_for_calls == [(_IDENTITY_ID, _ORG_ID)]
