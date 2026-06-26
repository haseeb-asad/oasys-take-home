"""Exhaustive unit tests for the actor-context-scoped PDP (pure, no DB).

Covers every surface branch (client self-access / provider membership +
responsible / org-staff admin), the port gates, temporal threading (delegated to
the Episode), the closed-episode act-capability overlay, cross-surface
isolation, the ``ResourceRef`` guards, ``can``/``require``/``Forbidden``, and the
internal capability partition. Capability outcomes are asserted as EXACT sets
against the authoritative ``capabilities_for`` grid (never duplicated here).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
from uuid import UUID

import pytest

from app.authz import (
    ActorContext,
    Capability,
    Forbidden,
    GrantRole,
    Pdp,
    ProfileType,
    ResourceRef,
    capabilities_for,
)
from app.authz.policy import _ACT_CAPABILITIES, _CLIENT_SELF_ACCESS
from app.care.domain.episode import Episode
from app.care.domain.exceptions import SelfTreatment
from app.care.domain.value_objects import Role
from app.core.exceptions import DomainError

from .conftest import (
    CLIENT,
    EPISODE_ID,
    MULTI,
    ORG_ID,
    ORG_STAFF,
    OTHER_ORG_ID,
    PROVIDER_A,
    PROVIDER_B,
    PROVIDER_C,
    FakeProfileDirectory,
    _uid,
    at,
)

# The four pure "see" capabilities; everything else is an "act" capability that
# the closed-episode overlay strips.
_VIEW_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.VIEW_BASIC_PROFILE,
        Capability.VIEW_SCHEDULE,
        Capability.VIEW_CLINICAL,
        Capability.VIEW_REHAB_ASSESSMENT,
    }
)

# A non-owning second client, and ids for ad-hoc second episodes in scoping tests.
OTHER_CLIENT = _uid(2)
OTHER_EPISODE_ID = _uid(101)


def _client(identity_id: UUID = CLIENT) -> ActorContext:
    return ActorContext(identity_id=identity_id, profile_type=ProfileType.CLIENT)


def _provider(identity_id: UUID) -> ActorContext:
    return ActorContext(identity_id=identity_id, profile_type=ProfileType.PROVIDER)


def _org_staff(identity_id: UUID = ORG_STAFF) -> ActorContext:
    return ActorContext(identity_id=identity_id, profile_type=ProfileType.ORG_STAFF)


# --------------------------------------------------------------------------- #
# ProfileType / ActorContext
# --------------------------------------------------------------------------- #
class TestProfileTypeAndActorContext:
    def test_profile_type_values(self) -> None:
        assert {p.value for p in ProfileType} == {"client", "provider", "org_staff"}

    def test_profile_type_is_str_enum(self) -> None:
        assert issubclass(ProfileType, str)  # StrEnum -> a str-valued enum
        assert ProfileType.CLIENT.value == "client"

    def test_actor_context_stores_fields(self) -> None:
        actor = ActorContext(identity_id=PROVIDER_A, profile_type=ProfileType.PROVIDER)
        assert actor.identity_id == PROVIDER_A
        assert actor.profile_type is ProfileType.PROVIDER

    def test_actor_context_is_frozen(self) -> None:
        actor = ActorContext(identity_id=PROVIDER_A, profile_type=ProfileType.PROVIDER)
        with pytest.raises(FrozenInstanceError):
            actor.identity_id = PROVIDER_B  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# ResourceRef shape + exactly-one guard
# --------------------------------------------------------------------------- #
class TestResourceRef:
    def test_for_episode(self, active_episode: Episode) -> None:
        ref = ResourceRef.for_episode(active_episode)
        assert ref.episode is active_episode
        assert ref.client_id is None
        assert ref.is_episode_scoped is True
        assert ref.owner_client_id == active_episode.client_id == CLIENT

    def test_for_client(self) -> None:
        ref = ResourceRef.for_client(CLIENT)
        assert ref.client_id == CLIENT
        assert ref.episode is None
        assert ref.is_episode_scoped is False
        assert ref.owner_client_id == CLIENT

    def test_neither_set_rejected(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            ResourceRef()

    def test_both_set_rejected(self, active_episode: Episode) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            ResourceRef(episode=active_episode, client_id=CLIENT)

    def test_frozen(self) -> None:
        ref = ResourceRef.for_client(CLIENT)
        with pytest.raises(FrozenInstanceError):
            ref.client_id = OTHER_CLIENT  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Client self-access branch
# --------------------------------------------------------------------------- #
class TestClientSelfAccess:
    def test_owner_gets_self_view_set_on_client_scope(
        self, pdp: Pdp, directory: FakeProfileDirectory, t0: datetime
    ) -> None:
        directory.active_clients.add(CLIENT)
        caps = pdp.allowed_capabilities(_client(), ResourceRef.for_client(CLIENT), t0)
        assert caps == frozenset({Capability.VIEW_BASIC_PROFILE, Capability.VIEW_SCHEDULE})

    def test_owner_gets_self_view_set_on_own_episode(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        directory.active_clients.add(CLIENT)
        ref = ResourceRef.for_episode(active_episode)  # episode.client_id == CLIENT
        caps = pdp.allowed_capabilities(_client(), ref, t0)
        assert caps == frozenset({Capability.VIEW_BASIC_PROFILE, Capability.VIEW_SCHEDULE})

    def test_client_never_gets_clinical_rehab_or_act(
        self, pdp: Pdp, directory: FakeProfileDirectory, t0: datetime
    ) -> None:
        directory.active_clients.add(CLIENT)
        caps = pdp.allowed_capabilities(_client(), ResourceRef.for_client(CLIENT), t0)
        for denied in (
            Capability.VIEW_CLINICAL,
            Capability.WRITE_CLINICAL,
            Capability.VIEW_REHAB_ASSESSMENT,
            Capability.RUN_SESSION,
            Capability.MESSAGE_CLIENT,
            Capability.BILL,
            Capability.MANAGE_TEAM,
        ):
            assert denied not in caps

    def test_non_owner_client_denied(
        self, pdp: Pdp, directory: FakeProfileDirectory, t0: datetime
    ) -> None:
        # MULTI is an active client, but the resource is owned by CLIENT.
        directory.active_clients.add(MULTI)
        caps = pdp.allowed_capabilities(_client(MULTI), ResourceRef.for_client(CLIENT), t0)
        assert caps == frozenset()

    def test_inactive_client_denied(self, pdp: Pdp, t0: datetime) -> None:
        # CLIENT was never added to active_clients.
        caps = pdp.allowed_capabilities(_client(), ResourceRef.for_client(CLIENT), t0)
        assert caps == frozenset()

    def test_closed_episode_still_gives_views(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode
    ) -> None:
        directory.active_clients.add(CLIENT)
        active_episode.close(now=at(16))
        ref = ResourceRef.for_episode(active_episode)
        caps = pdp.allowed_capabilities(_client(), ref, at(20))
        assert caps == frozenset({Capability.VIEW_BASIC_PROFILE, Capability.VIEW_SCHEDULE})


# --------------------------------------------------------------------------- #
# Provider membership branch (branch 2) — the role grid
# --------------------------------------------------------------------------- #
class TestProviderMembershipBranch:
    @pytest.mark.parametrize("role", list(Role))
    def test_member_gets_exactly_role_grid(
        self,
        role: Role,
        pdp: Pdp,
        directory: FakeProfileDirectory,
        active_episode: Episode,
        t0: datetime,
    ) -> None:
        # B is a plain member (NOT responsible), so the result is exactly the grid.
        directory.active_providers.add(PROVIDER_B)
        active_episode.add_member(provider_id=PROVIDER_B, role=role, now=t0, change_reason="add b")
        caps = pdp.allowed_capabilities(
            _provider(PROVIDER_B), ResourceRef.for_episode(active_episode), t0
        )
        assert caps == capabilities_for(GrantRole(role.value))

    def test_massage_member_acts_without_clinical_visibility(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        directory.active_providers.add(PROVIDER_B)
        active_episode.add_member(
            provider_id=PROVIDER_B, role=Role.MASSAGE_THERAPIST, now=t0, change_reason="x"
        )
        caps = pdp.allowed_capabilities(
            _provider(PROVIDER_B), ResourceRef.for_episode(active_episode), t0
        )
        assert Capability.RUN_SESSION in caps
        assert Capability.MESSAGE_CLIENT in caps
        assert Capability.VIEW_CLINICAL not in caps
        assert Capability.VIEW_REHAB_ASSESSMENT not in caps

    def test_non_member_active_provider_denied(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        directory.active_providers.add(PROVIDER_C)  # active, but not on this episode
        caps = pdp.allowed_capabilities(
            _provider(PROVIDER_C), ResourceRef.for_episode(active_episode), t0
        )
        assert caps == frozenset()

    def test_member_but_inactive_provider_denied(
        self, pdp: Pdp, active_episode: Episode, t0: datetime
    ) -> None:
        # B is a member with a role, but holds no active provider profile.
        active_episode.add_member(
            provider_id=PROVIDER_B, role=Role.PHYSICIAN, now=t0, change_reason="x"
        )
        caps = pdp.allowed_capabilities(
            _provider(PROVIDER_B), ResourceRef.for_episode(active_episode), t0
        )
        assert caps == frozenset()

    def test_membership_is_scoped_to_the_resource_episode(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        # A is responsible + member of active_episode, but NOT of `other`.
        directory.active_providers.add(PROVIDER_A)
        other = Episode.open(
            id=OTHER_EPISODE_ID,
            client_id=OTHER_CLIENT,
            reason="r",
            managing_org_id=ORG_ID,
            now=t0,
            responsible_provider_id=PROVIDER_C,
            responsible_role=Role.PHYSICIAN,
            change_reason="open",
        )
        caps = pdp.allowed_capabilities(_provider(PROVIDER_A), ResourceRef.for_episode(other), t0)
        assert caps == frozenset()


# --------------------------------------------------------------------------- #
# Provider temporal gating (delegated to the Episode; PDP only threads `now`)
# --------------------------------------------------------------------------- #
class TestProviderTemporalGating:
    def test_future_dated_membership_not_yet_effective_then_effective(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode
    ) -> None:
        directory.active_providers.add(PROVIDER_B)
        active_episode.add_member(
            provider_id=PROVIDER_B,
            role=Role.PHYSICIAN,
            now=at(1),
            change_reason="x",
            effective_from=at(6),
        )
        ref = ResourceRef.for_episode(active_episode)
        assert pdp.allowed_capabilities(_provider(PROVIDER_B), ref, at(5)) == frozenset()
        assert pdp.allowed_capabilities(_provider(PROVIDER_B), ref, at(6)) == capabilities_for(
            GrantRole.PHYSICIAN
        )  # effective exactly at effective_from (half-open lower bound is inclusive)

    def test_coverage_window_grants_in_window_and_expires_at_to(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode
    ) -> None:
        directory.active_providers.add(PROVIDER_B)
        active_episode.start_coverage(
            provider_id=PROVIDER_B,
            role=Role.PHYSIOTHERAPIST,
            effective_from=at(4),
            effective_to=at(8),
            now=at(4),
            change_reason="cover",
        )
        ref = ResourceRef.for_episode(active_episode)
        assert pdp.allowed_capabilities(_provider(PROVIDER_B), ref, at(5)) == capabilities_for(
            GrantRole.PHYSIOTHERAPIST
        )
        # Half-open: at exactly effective_to the cover is gone.
        assert pdp.allowed_capabilities(_provider(PROVIDER_B), ref, at(8)) == frozenset()


# --------------------------------------------------------------------------- #
# Responsible-provider branch (branch 3) — the relationship MANAGE_TEAM grant
# --------------------------------------------------------------------------- #
class TestResponsibleProviderBranch:
    def test_responsible_provider_gets_manage_team(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        directory.active_providers.add(PROVIDER_A)  # A is responsible from t0
        caps = pdp.allowed_capabilities(
            _provider(PROVIDER_A), ResourceRef.for_episode(active_episode), t0
        )
        assert Capability.MANAGE_TEAM in caps

    def test_non_responsible_member_has_no_manage_team(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        directory.active_providers.add(PROVIDER_B)
        active_episode.add_member(
            provider_id=PROVIDER_B, role=Role.PHYSICIAN, now=t0, change_reason="x"
        )
        caps = pdp.allowed_capabilities(
            _provider(PROVIDER_B), ResourceRef.for_episode(active_episode), t0
        )
        assert Capability.MANAGE_TEAM not in caps

    def test_responsible_but_inactive_provider_denied(
        self, pdp: Pdp, active_episode: Episode, t0: datetime
    ) -> None:
        # A is responsible, but holds no active provider profile -> the gate denies.
        caps = pdp.allowed_capabilities(
            _provider(PROVIDER_A), ResourceRef.for_episode(active_episode), t0
        )
        assert caps == frozenset()

    def test_manage_team_is_a_relationship_grant_not_a_role_grid_cell(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        # A's role (physiotherapist) grid does NOT contain MANAGE_TEAM; A holds it
        # only by being the responsible provider — independent of the grid.
        assert Capability.MANAGE_TEAM not in capabilities_for(GrantRole.PHYSIOTHERAPIST)
        directory.active_providers.add(PROVIDER_A)
        caps = pdp.allowed_capabilities(
            _provider(PROVIDER_A), ResourceRef.for_episode(active_episode), t0
        )
        assert Capability.MANAGE_TEAM in caps


# --------------------------------------------------------------------------- #
# Provider surface union (branch 2 UNION branch 3, within the surface)
# --------------------------------------------------------------------------- #
class TestProviderSurfaceUnion:
    def test_responsible_member_is_role_grid_union_manage_team(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        directory.active_providers.add(PROVIDER_A)
        caps = pdp.allowed_capabilities(
            _provider(PROVIDER_A), ResourceRef.for_episode(active_episode), t0
        )
        assert caps == capabilities_for(GrantRole.PHYSIOTHERAPIST) | {Capability.MANAGE_TEAM}

    def test_union_for_non_clinical_responsible_role(
        self, pdp: Pdp, directory: FakeProfileDirectory, t0: datetime
    ) -> None:
        # A personal-trainer-led episode: the responsible PT gets the PT grid plus
        # the responsible-provider MANAGE_TEAM grant.
        ep = Episode.open(
            id=EPISODE_ID,
            client_id=CLIENT,
            reason="general_training",
            managing_org_id=ORG_ID,
            now=t0,
            responsible_provider_id=PROVIDER_A,
            responsible_role=Role.PERSONAL_TRAINER,
            change_reason="open",
        )
        directory.active_providers.add(PROVIDER_A)
        caps = pdp.allowed_capabilities(_provider(PROVIDER_A), ResourceRef.for_episode(ep), t0)
        assert caps == capabilities_for(GrantRole.PERSONAL_TRAINER) | {Capability.MANAGE_TEAM}


# --------------------------------------------------------------------------- #
# Org-staff admin branch (branch 4)
# --------------------------------------------------------------------------- #
class TestOrgStaffAdminBranch:
    def test_org_admin_gets_org_admin_grid(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        directory.org_admins.add((ORG_STAFF, ORG_ID))
        caps = pdp.allowed_capabilities(_org_staff(), ResourceRef.for_episode(active_episode), t0)
        assert caps == capabilities_for(GrantRole.ORG_ADMIN)

    def test_non_admin_denied(self, pdp: Pdp, active_episode: Episode, t0: datetime) -> None:
        caps = pdp.allowed_capabilities(_org_staff(), ResourceRef.for_episode(active_episode), t0)
        assert caps == frozenset()

    def test_admin_of_other_org_denied(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        # Admin of a DIFFERENT org than the episode's managing_org.
        directory.org_admins.add((ORG_STAFF, OTHER_ORG_ID))
        caps = pdp.allowed_capabilities(_org_staff(), ResourceRef.for_episode(active_episode), t0)
        assert caps == frozenset()

    def test_org_admin_lacks_provider_capabilities(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        directory.org_admins.add((ORG_STAFF, ORG_ID))
        caps = pdp.allowed_capabilities(_org_staff(), ResourceRef.for_episode(active_episode), t0)
        for denied in (
            Capability.RUN_SESSION,
            Capability.WRITE_CLINICAL,
            Capability.VIEW_CLINICAL,
            Capability.VIEW_REHAB_ASSESSMENT,
            Capability.MESSAGE_CLIENT,
        ):
            assert denied not in caps


# --------------------------------------------------------------------------- #
# Closed-episode overlay (act caps dropped, VIEW_* survive) — per surface
# --------------------------------------------------------------------------- #
class TestClosedEpisodeOverlay:
    def test_provider_loses_act_keeps_view(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode
    ) -> None:
        directory.active_providers.add(PROVIDER_A)  # physiotherapist + responsible
        active_episode.close(now=at(16))
        caps = pdp.allowed_capabilities(
            _provider(PROVIDER_A), ResourceRef.for_episode(active_episode), at(20)
        )
        # physio holds all four views; act caps (incl. responsible MANAGE_TEAM) are gone.
        assert caps == _VIEW_CAPABILITIES
        assert caps & _ACT_CAPABILITIES == frozenset()

    def test_org_staff_loses_act_keeps_view(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode
    ) -> None:
        directory.org_admins.add((ORG_STAFF, ORG_ID))
        active_episode.close(now=at(16))
        caps = pdp.allowed_capabilities(
            _org_staff(), ResourceRef.for_episode(active_episode), at(20)
        )
        # org_admin loses BILL + MANAGE_TEAM, keeps the two views.
        assert caps == frozenset({Capability.VIEW_BASIC_PROFILE, Capability.VIEW_SCHEDULE})

    def test_client_keeps_views_on_closed_episode(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode
    ) -> None:
        directory.active_clients.add(CLIENT)
        active_episode.close(now=at(16))
        caps = pdp.allowed_capabilities(_client(), ResourceRef.for_episode(active_episode), at(20))
        assert caps == frozenset({Capability.VIEW_BASIC_PROFILE, Capability.VIEW_SCHEDULE})


# --------------------------------------------------------------------------- #
# No self-treatment — enforced at the Episode boundary, surfaced as empty here
# --------------------------------------------------------------------------- #
class TestNoSelfTreatment:
    def test_provider_on_own_episode_gets_nothing(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        # CLIENT owns the episode; even acting as an ACTIVE provider, CLIENT has no
        # membership (self-treatment is barred at the membership boundary), so the
        # provider surface yields nothing — the PDP holds no special-case for it.
        directory.active_providers.add(CLIENT)
        caps = pdp.allowed_capabilities(
            _provider(CLIENT), ResourceRef.for_episode(active_episode), t0
        )
        assert caps == frozenset()

    def test_episode_bars_client_membership(self, active_episode: Episode, t0: datetime) -> None:
        # Documents WHERE the invariant lives: the aggregate, not the PDP.
        with pytest.raises(SelfTreatment):
            active_episode.add_member(
                provider_id=CLIENT, role=Role.PHYSICIAN, now=t0, change_reason="x"
            )


# --------------------------------------------------------------------------- #
# Cross-surface isolation — a multi-hat identity gets only the acting surface
# --------------------------------------------------------------------------- #
class TestCrossSurfaceIsolation:
    @staticmethod
    def _seed_multi_everything(
        directory: FakeProfileDirectory, episode: Episode, t0: datetime
    ) -> None:
        """MULTI holds every hat: active provider + member, active client, org admin."""
        directory.active_providers.add(MULTI)
        directory.active_clients.add(MULTI)
        directory.org_admins.add((MULTI, ORG_ID))
        episode.add_member(provider_id=MULTI, role=Role.PHYSICIAN, now=t0, change_reason="multi")

    def test_acting_as_provider_ignores_org_and_client_hats(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        self._seed_multi_everything(directory, active_episode, t0)
        caps = pdp.allowed_capabilities(
            _provider(MULTI), ResourceRef.for_episode(active_episode), t0
        )
        # Exactly the physician grid (MULTI is a member, not responsible); the
        # org_admin hat would have added MANAGE_TEAM — it must not.
        assert caps == capabilities_for(GrantRole.PHYSICIAN)
        assert Capability.MANAGE_TEAM not in caps

    def test_acting_as_org_staff_ignores_provider_and_client_hats(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        self._seed_multi_everything(directory, active_episode, t0)
        caps = pdp.allowed_capabilities(
            _org_staff(MULTI), ResourceRef.for_episode(active_episode), t0
        )
        # Exactly the org_admin grid; the provider hat would have added RUN_SESSION.
        assert caps == capabilities_for(GrantRole.ORG_ADMIN)
        assert Capability.RUN_SESSION not in caps

    def test_acting_as_client_on_unowned_episode_ignores_other_hats(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        # active_episode is owned by CLIENT, not MULTI; the client surface requires
        # ownership, so MULTI's provider/org hats grant nothing here.
        self._seed_multi_everything(directory, active_episode, t0)
        caps = pdp.allowed_capabilities(_client(MULTI), ResourceRef.for_episode(active_episode), t0)
        assert caps == frozenset()

    def test_acting_as_provider_uses_provider_activeness_not_other_hats(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        # MULTI is a member + active client + org admin, but NOT an active provider.
        directory.active_clients.add(MULTI)
        directory.org_admins.add((MULTI, ORG_ID))
        active_episode.add_member(
            provider_id=MULTI, role=Role.PHYSICIAN, now=t0, change_reason="multi"
        )
        caps = pdp.allowed_capabilities(
            _provider(MULTI), ResourceRef.for_episode(active_episode), t0
        )
        assert caps == frozenset()

    def test_acting_as_org_staff_uses_org_admin_state_not_other_hats(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        # MULTI is an active provider + member + active client, but NOT an org admin.
        directory.active_providers.add(MULTI)
        directory.active_clients.add(MULTI)
        active_episode.add_member(
            provider_id=MULTI, role=Role.PHYSICIAN, now=t0, change_reason="multi"
        )
        caps = pdp.allowed_capabilities(
            _org_staff(MULTI), ResourceRef.for_episode(active_episode), t0
        )
        assert caps == frozenset()

    def test_unrecognized_surface_fails_closed(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        # A malformed actor (surface not a real ProfileType) must DENY — never fall
        # through to a real surface's branches. The PDP fails closed by default.
        directory.active_providers.add(MULTI)
        directory.org_admins.add((MULTI, ORG_ID))
        bogus = ActorContext(identity_id=MULTI, profile_type="ghost")  # type: ignore[arg-type]
        caps = pdp.allowed_capabilities(bogus, ResourceRef.for_episode(active_episode), t0)
        assert caps == frozenset()


# --------------------------------------------------------------------------- #
# Resource scoping — episode vs client scope routing
# --------------------------------------------------------------------------- #
class TestResourceScoping:
    def test_provider_on_client_scope_gets_nothing(
        self, pdp: Pdp, directory: FakeProfileDirectory, t0: datetime
    ) -> None:
        # The provider->client-scoped path is documented-not-built: no episode -> empty.
        directory.active_providers.add(PROVIDER_A)
        caps = pdp.allowed_capabilities(_provider(PROVIDER_A), ResourceRef.for_client(CLIENT), t0)
        assert caps == frozenset()

    def test_org_staff_on_client_scope_gets_nothing(
        self, pdp: Pdp, directory: FakeProfileDirectory, t0: datetime
    ) -> None:
        directory.org_admins.add((ORG_STAFF, ORG_ID))
        caps = pdp.allowed_capabilities(_org_staff(), ResourceRef.for_client(CLIENT), t0)
        assert caps == frozenset()


# --------------------------------------------------------------------------- #
# can / require
# --------------------------------------------------------------------------- #
class TestCanAndRequire:
    def test_can_true_for_held_capability(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        directory.active_providers.add(PROVIDER_A)
        ref = ResourceRef.for_episode(active_episode)
        assert pdp.can(_provider(PROVIDER_A), Capability.RUN_SESSION, ref, t0) is True

    def test_can_false_for_unheld_capability(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        directory.active_providers.add(PROVIDER_B)
        active_episode.add_member(
            provider_id=PROVIDER_B, role=Role.MASSAGE_THERAPIST, now=t0, change_reason="x"
        )
        ref = ResourceRef.for_episode(active_episode)
        assert pdp.can(_provider(PROVIDER_B), Capability.WRITE_CLINICAL, ref, t0) is False

    def test_require_passes_when_capability_held(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        directory.active_providers.add(PROVIDER_A)
        ref = ResourceRef.for_episode(active_episode)
        pdp.require(_provider(PROVIDER_A), Capability.RUN_SESSION, ref, t0)  # must not raise

    def test_require_raises_forbidden_when_missing(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        directory.active_providers.add(PROVIDER_B)
        active_episode.add_member(
            provider_id=PROVIDER_B, role=Role.MASSAGE_THERAPIST, now=t0, change_reason="x"
        )
        ref = ResourceRef.for_episode(active_episode)
        with pytest.raises(Forbidden):
            pdp.require(_provider(PROVIDER_B), Capability.WRITE_CLINICAL, ref, t0)


# --------------------------------------------------------------------------- #
# Forbidden exception
# --------------------------------------------------------------------------- #
class TestForbidden:
    def test_is_domain_error(self) -> None:
        ref = ResourceRef.for_client(CLIENT)
        err = Forbidden(_provider(PROVIDER_B), Capability.WRITE_CLINICAL, ref)
        assert isinstance(err, DomainError)

    def test_stores_actor_capability_resource(self) -> None:
        actor = _provider(PROVIDER_B)
        ref = ResourceRef.for_client(CLIENT)
        err = Forbidden(actor, Capability.WRITE_CLINICAL, ref)
        assert err.actor is actor
        assert err.capability is Capability.WRITE_CLINICAL
        assert err.resource is ref

    def test_message_names_capability_surface_and_identity(self) -> None:
        ref = ResourceRef.for_client(CLIENT)
        err = Forbidden(_provider(PROVIDER_B), Capability.WRITE_CLINICAL, ref)
        message = str(err)
        assert Capability.WRITE_CLINICAL.value in message
        assert ProfileType.PROVIDER.value in message
        assert str(PROVIDER_B) in message

    def test_require_raises_with_populated_fields(
        self, pdp: Pdp, directory: FakeProfileDirectory, active_episode: Episode, t0: datetime
    ) -> None:
        directory.active_providers.add(PROVIDER_B)
        active_episode.add_member(
            provider_id=PROVIDER_B, role=Role.MASSAGE_THERAPIST, now=t0, change_reason="x"
        )
        actor = _provider(PROVIDER_B)
        ref = ResourceRef.for_episode(active_episode)
        with pytest.raises(Forbidden) as exc_info:
            pdp.require(actor, Capability.WRITE_CLINICAL, ref, t0)
        assert exc_info.value.actor is actor
        assert exc_info.value.capability is Capability.WRITE_CLINICAL
        assert exc_info.value.resource is ref


# --------------------------------------------------------------------------- #
# Internal capability partition (white-box: the two private constants)
# --------------------------------------------------------------------------- #
class TestInternalCapabilityPartition:
    def test_act_capabilities_is_the_complement_of_the_four_views(self) -> None:
        assert _ACT_CAPABILITIES == set(Capability) - {
            Capability.VIEW_BASIC_PROFILE,
            Capability.VIEW_SCHEDULE,
            Capability.VIEW_CLINICAL,
            Capability.VIEW_REHAB_ASSESSMENT,
        }

    def test_client_self_access_is_the_two_client_views(self) -> None:
        assert _CLIENT_SELF_ACCESS == frozenset(
            {Capability.VIEW_BASIC_PROFILE, Capability.VIEW_SCHEDULE}
        )

    def test_self_access_and_act_capabilities_are_disjoint(self) -> None:
        assert _CLIENT_SELF_ACCESS & _ACT_CAPABILITIES == frozenset()
