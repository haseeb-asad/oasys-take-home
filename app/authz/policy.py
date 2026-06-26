"""The policy decision point (PDP): actor-context-scoped capability decisions.

Pure authorization layer (project std 1) — plain Python + stdlib only, plus the
sanctioned in-context imports (``capabilities`` / ``context`` / ``ports`` /
``exceptions``) and the ``Episode`` aggregate it inspects. No FastAPI /
SQLAlchemy / Pydantic, and no hidden clock: every entry point takes an
injectable ``now``.

The PDP owns NO temporal logic of its own. "Who is a current member / the
responsible provider at ``now``" is delegated entirely to the ``Episode``
aggregate; the PDP only threads ``now`` and unions the per-surface grants. The
role -> capability grid is never duplicated here — role grants come straight from
``capabilities_for(GrantRole(role.value))``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.authz.capabilities import Capability, GrantRole, capabilities_for
from app.authz.context import ActorContext, ProfileType, ResourceRef
from app.authz.exceptions import Forbidden
from app.authz.ports import ProfileDirectory

# Read-only capabilities a client may exercise on their OWN data — never clinical
# / rehab data, never an "act" capability.
_CLIENT_SELF_ACCESS: frozenset[Capability] = frozenset(
    {Capability.VIEW_BASIC_PROFILE, Capability.VIEW_SCHEDULE}
)

# "Act" capabilities = everything that is not a pure VIEW_*. Defined as the
# complement of the four VIEW_* so it can never drift from the vocabulary. The
# closed-episode overlay strips exactly these (a closed episode keeps read
# access but permits no further action).
_ACT_CAPABILITIES: frozenset[Capability] = frozenset(Capability) - frozenset(
    {
        Capability.VIEW_BASIC_PROFILE,
        Capability.VIEW_SCHEDULE,
        Capability.VIEW_CLINICAL,
        Capability.VIEW_REHAB_ASSESSMENT,
    }
)


@dataclass(frozen=True, slots=True)
class Pdp:
    """Decides which capabilities an actor holds on a resource at ``now``.

    Depends only on the ``ProfileDirectory`` port for profile-state checks; all
    episode-relationship and temporal facts come from the ``Episode`` aggregate
    carried on the ``ResourceRef``.
    """

    directory: ProfileDirectory

    # ------------------------------------------------------------------ #
    # Public decisions
    # ------------------------------------------------------------------ #
    def allowed_capabilities(
        self, actor: ActorContext, resource: ResourceRef, now: datetime
    ) -> frozenset[Capability]:
        """All capabilities ``actor`` holds on ``resource`` at ``now``.

        Dispatches on the acting surface (``profile_type``) — only that surface's
        branch(es) run, so a multi-profile identity never unions hats. A closed
        episode then loses every "act" capability (read access survives).
        """
        if actor.profile_type is ProfileType.CLIENT:
            granted = self._client_self_access(actor, resource, now)
        elif actor.profile_type is ProfileType.PROVIDER:
            granted = self._provider_caps(actor, resource, now)
        else:
            granted = self._org_staff_caps(actor, resource, now)

        episode = resource.episode
        if episode is not None and not episode.is_active:
            granted -= _ACT_CAPABILITIES
        return granted

    def can(
        self,
        actor: ActorContext,
        capability: Capability,
        resource: ResourceRef,
        now: datetime,
    ) -> bool:
        """True iff ``actor`` holds ``capability`` on ``resource`` at ``now``."""
        return capability in self.allowed_capabilities(actor, resource, now)

    def require(
        self,
        actor: ActorContext,
        capability: Capability,
        resource: ResourceRef,
        now: datetime,
    ) -> None:
        """Raise ``Forbidden`` unless ``actor`` holds ``capability`` at ``now``."""
        if not self.can(actor, capability, resource, now):
            raise Forbidden(actor, capability, resource)

    # ------------------------------------------------------------------ #
    # Per-surface branches (no cross-surface leakage)
    # ------------------------------------------------------------------ #
    def _client_self_access(
        self, actor: ActorContext, resource: ResourceRef, now: datetime
    ) -> frozenset[Capability]:
        """Branch 1 — a client's read access to their OWN data."""
        if not self.directory.is_active_client(actor.identity_id, now):
            return frozenset()
        if actor.identity_id != resource.owner_client_id:
            return frozenset()
        return _CLIENT_SELF_ACCESS

    def _provider_caps(
        self, actor: ActorContext, resource: ResourceRef, now: datetime
    ) -> frozenset[Capability]:
        """Branches 2 UNION 3 — episode-membership role grant + responsible grant."""
        episode = resource.episode
        if episode is None:
            return frozenset()  # provider -> client-scoped path is documented-not-built
        if not self.directory.is_active_provider(actor.identity_id, now):
            return frozenset()  # the active-provider gate covers BOTH branches

        granted: set[Capability] = set()
        membership = episode.current_membership(actor.identity_id, now)
        if membership is not None:
            granted |= capabilities_for(GrantRole(membership.role.value))
        responsibility = episode.current_responsibility(now)
        if responsibility is not None and responsibility.provider_id == actor.identity_id:
            granted.add(Capability.MANAGE_TEAM)
        return frozenset(granted)

    def _org_staff_caps(
        self, actor: ActorContext, resource: ResourceRef, now: datetime
    ) -> frozenset[Capability]:
        """Branch 4 — managing-org admin authority over the episode."""
        episode = resource.episode
        if episode is None:
            return frozenset()
        if not self.directory.is_active_org_admin(actor.identity_id, episode.managing_org_id, now):
            return frozenset()
        return capabilities_for(GrantRole.ORG_ADMIN)
