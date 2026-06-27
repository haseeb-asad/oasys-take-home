"""Unit tests for the ProfileDirectory adapter (no DB; local fakes).

The adapter is the PDP's composition surface: it answers the ``ProfileDirectory``
port by combining the identity Profiles slice (``has_active_profile``) with the org
context's ``has_active_admin_membership``. These tests drive it through fake
ProfileRepository / OrgStaffMembershipRepository so the real service predicates run
over in-memory rows - asserting the provider/client checks, the AND-logic of
``is_active_org_admin`` (active org_staff PROFILE **and** active admin MEMBERSHIP),
and that ``now`` is threaded to the membership half only (profiles are a
soft-discard tombstone, not effective-dated).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.authz.adapters import ProfileDirectoryAdapter
from app.authz.ports import ProfileDirectory
from app.identity.domain.entities import Profile
from app.identity.domain.value_objects import ProfileType
from app.organization.domain.entities import OrgStaffMembership
from app.organization.domain.value_objects import OrgRole

_IDENTITY = UUID(int=1)
_OTHER = UUID(int=2)
_ORG = UUID(int=10)
_OTHER_ORG = UUID(int=11)
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = _T0 + timedelta(weeks=4)


@dataclass(slots=True)
class _FakeProfileRepo:
    profiles: list[Profile] = field(default_factory=list)

    def list_for(self, identity_id: UUID) -> list[Profile]:
        return [profile for profile in self.profiles if profile.identity_id == identity_id]

    def add(self, profile: Profile) -> None:
        self.profiles.append(profile)


@dataclass(slots=True)
class _FakeMembershipRepo:
    memberships: list[OrgStaffMembership] = field(default_factory=list)

    def list_for(self, identity_id: UUID, org_id: UUID) -> list[OrgStaffMembership]:
        return [
            membership
            for membership in self.memberships
            if membership.identity_id == identity_id and membership.org_id == org_id
        ]

    def add(self, membership: OrgStaffMembership) -> None:
        self.memberships.append(membership)


def _profile(profile_type: ProfileType, *, discarded: bool = False) -> Profile:
    return Profile(
        id=UUID(int=100),
        identity_id=_IDENTITY,
        profile_type=profile_type,
        discarded_at=_T0 if discarded else None,
    )


def _membership(
    *, role: OrgRole, to: datetime | None = None, org_id: UUID = _ORG
) -> OrgStaffMembership:
    return OrgStaffMembership(
        id=UUID(int=200),
        identity_id=_IDENTITY,
        org_id=org_id,
        role=role,
        effective_from=_T0,
        effective_to=to,
    )


def _adapter(
    *, profiles: list[Profile] | None = None, memberships: list[OrgStaffMembership] | None = None
) -> ProfileDirectoryAdapter:
    return ProfileDirectoryAdapter(
        profiles=_FakeProfileRepo(profiles or []),
        memberships=_FakeMembershipRepo(memberships or []),
    )


# --- is_active_provider / is_active_client ----------------------------------


def test_is_active_provider_true_for_active_provider_profile() -> None:
    adapter = _adapter(profiles=[_profile(ProfileType.PROVIDER)])
    assert adapter.is_active_provider(_IDENTITY, _T0) is True


def test_is_active_provider_false_when_discarded() -> None:
    adapter = _adapter(profiles=[_profile(ProfileType.PROVIDER, discarded=True)])
    assert adapter.is_active_provider(_IDENTITY, _T0) is False


def test_is_active_provider_false_when_absent_or_wrong_type() -> None:
    assert _adapter().is_active_provider(_IDENTITY, _T0) is False
    adapter = _adapter(profiles=[_profile(ProfileType.CLIENT)])
    assert adapter.is_active_provider(_IDENTITY, _T0) is False


def test_is_active_client_true_for_active_client_profile() -> None:
    adapter = _adapter(profiles=[_profile(ProfileType.CLIENT)])
    assert adapter.is_active_client(_IDENTITY, _T0) is True


def test_is_active_client_false_when_discarded() -> None:
    adapter = _adapter(profiles=[_profile(ProfileType.CLIENT, discarded=True)])
    assert adapter.is_active_client(_IDENTITY, _T0) is False


# --- is_active_org_admin: the AND of profile-state and membership -----------


def test_org_admin_true_when_profile_and_active_admin_membership() -> None:
    adapter = _adapter(
        profiles=[_profile(ProfileType.ORG_STAFF)],
        memberships=[_membership(role=OrgRole.ADMIN)],
    )
    assert adapter.is_active_org_admin(_IDENTITY, _ORG, _T0) is True


def test_org_admin_false_when_profile_but_no_admin_membership() -> None:
    adapter = _adapter(
        profiles=[_profile(ProfileType.ORG_STAFF)],
        memberships=[_membership(role=OrgRole.MEMBER)],  # member, not admin
    )
    assert adapter.is_active_org_admin(_IDENTITY, _ORG, _T0) is False


def test_org_admin_false_when_admin_membership_but_no_org_staff_profile() -> None:
    adapter = _adapter(
        profiles=[_profile(ProfileType.PROVIDER)],  # not org_staff
        memberships=[_membership(role=OrgRole.ADMIN)],
    )
    assert adapter.is_active_org_admin(_IDENTITY, _ORG, _T0) is False


def test_org_admin_false_when_neither() -> None:
    assert _adapter().is_active_org_admin(_IDENTITY, _ORG, _T0) is False


def test_org_admin_threads_now_to_membership_half_only() -> None:
    # An expired admin membership [t0, t1) + an (un-timed) org_staff profile: at t0
    # the membership is active -> True; at the half-open end t1 it is not -> False.
    # The profile half ignores now (tombstone), so only the membership gates on it.
    adapter = _adapter(
        profiles=[_profile(ProfileType.ORG_STAFF)],
        memberships=[_membership(role=OrgRole.ADMIN, to=_T1)],
    )
    assert adapter.is_active_org_admin(_IDENTITY, _ORG, _T0) is True
    assert adapter.is_active_org_admin(_IDENTITY, _ORG, _T1) is False


def test_org_admin_isolates_by_org() -> None:
    adapter = _adapter(
        profiles=[_profile(ProfileType.ORG_STAFF)],
        memberships=[_membership(role=OrgRole.ADMIN, org_id=_ORG)],
    )
    assert adapter.is_active_org_admin(_IDENTITY, _OTHER_ORG, _T0) is False


def test_isolates_by_identity() -> None:
    adapter = _adapter(profiles=[_profile(ProfileType.PROVIDER)])
    assert adapter.is_active_provider(_OTHER, _T0) is False


def test_adapter_satisfies_profile_directory_port() -> None:
    # Structural conformance to the PDP's port (checked by mypy via the annotation;
    # exercised at runtime through the three methods).
    directory: ProfileDirectory = _adapter()
    assert directory.is_active_provider(_IDENTITY, _T0) is False
    assert directory.is_active_client(_IDENTITY, _T0) is False
    assert directory.is_active_org_admin(_IDENTITY, _ORG, _T0) is False
