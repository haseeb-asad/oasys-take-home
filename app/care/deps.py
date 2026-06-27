"""Care request-scoped dependencies: the two-layer authorization gate.

Web/edge layer (FastAPI lives here, never in the pure authz/domain layers). Two
dependency FACTORIES realize the project's two-layer authz model
(``planning/auth-authz-design.md``):

* ``require_profile(surface)`` - Layer 1 ONLY: authenticate, then require the
  caller to hold an active profile of the single, fixed ``surface``. Returns the
  server-owned ``ActorContext``. Used by the create route (provider-only
  bootstrap: there is no episode yet, so no Layer-2 PDP decision).
* ``require_episode_capability(capability, *surfaces)`` - Layer 1 + load + Layer 2:
  resolve and validate the acting surface, confirm it is held, load the Episode
  (``NotFound`` -> 404 if absent), then run the contextual ``Pdp`` over the REAL
  ``ProfileDirectory`` and require ``capability`` (``Forbidden`` -> 403 otherwise).
  Returns the AUTHORIZED ``Episode`` to the thin router.

AM1 - the acting surface is explicit on multi-surface routes: when exactly one
surface is allowed it is FIXED (``acting_as`` is ignored); when more than one is
allowed, ``acting_as`` is REQUIRED and must be one of them (a missing or
not-allowed value raises ``ProfileSurfaceRequired``, never a silent default to
the first). The resolved surface must also actually be HELD by the caller.

Check order matches the design's status table (404-before-403 oracle): wrong /
not-held surface is stopped at Layer 1 (403) BEFORE the episode is loaded; a
missing episode is 404; a held, correct-surface caller who simply lacks the
capability on an EXISTING episode is 403. ``now`` is threaded from ``get_now`` so
the PDP's temporal checks are test-overridable.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.authz.adapters import build_profile_directory
from app.authz.capabilities import Capability
from app.authz.context import ActorContext, ProfileType, ResourceRef
from app.authz.exceptions import ProfileSurfaceRequired
from app.authz.policy import Pdp
from app.care.domain.episode import Episode
from app.care.repository import SqlAlchemyEpisodeRepository
from app.care.service import get_episode
from app.core.deps import get_now, get_session
from app.core.exceptions import NotFound
from app.identity.deps import get_current_user
from app.identity.domain.entities import Identity
from app.identity.domain.repository import ProfileRepository
from app.identity.domain.value_objects import ProfileType as IdentityProfileType
from app.identity.repository import SqlAlchemyProfileRepository
from app.identity.service import has_active_profile


def _resolve_surface(
    acting_as: ProfileType | None, allowed: tuple[ProfileType, ...]
) -> ProfileType:
    """Resolve the acting surface for a route (AM1).

    Single allowed surface -> fixed (``acting_as`` ignored). Multiple allowed ->
    ``acting_as`` is required and must be one of ``allowed``; otherwise raise
    ``ProfileSurfaceRequired`` (no silent default-to-first).
    """
    if len(allowed) == 1:
        return allowed[0]
    if acting_as is None or acting_as not in allowed:
        raise ProfileSurfaceRequired()
    return acting_as


def _assert_holds_surface(
    profiles: ProfileRepository, identity_id: UUID, surface: ProfileType
) -> None:
    """Require ``identity_id`` to hold an active profile of ``surface`` (else 403).

    The authz acting surface and the identity profile-type share byte-identical
    string values, so the conversion is a direct ``IdentityProfileType(surface.value)``.
    """
    if not has_active_profile(profiles, identity_id, IdentityProfileType(surface.value)):
        raise ProfileSurfaceRequired()


def require_profile(surface: ProfileType) -> Callable[..., ActorContext]:
    """Layer 1 only: authenticate + require the (fixed) ``surface`` be held.

    Returns a dependency yielding the server-owned ``ActorContext``. The create
    route uses this: there is no episode yet, so no Layer-2 PDP decision runs.
    """

    def dependency(
        current_user: Annotated[Identity, Depends(get_current_user)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ActorContext:
        _assert_holds_surface(SqlAlchemyProfileRepository(session), current_user.id, surface)
        return ActorContext(identity_id=current_user.id, profile_type=surface)

    return dependency


def require_episode_capability(
    capability: Capability, *surfaces: ProfileType
) -> Callable[..., Episode]:
    """Layer 1 + load + Layer 2: gate ``capability`` on the path episode.

    Resolves + validates the acting surface (AM1), confirms the caller holds it,
    loads the episode (404 if absent), then requires ``capability`` via the PDP
    over the real ``ProfileDirectory`` (403 ``Forbidden`` otherwise). Returns the
    authorized ``Episode`` to the thin router (which makes a single service call).
    """

    def dependency(
        episode_id: UUID,
        current_user: Annotated[Identity, Depends(get_current_user)],
        session: Annotated[Session, Depends(get_session)],
        now: Annotated[datetime, Depends(get_now)],
        acting_as: Annotated[ProfileType | None, Query()] = None,
    ) -> Episode:
        surface = _resolve_surface(acting_as, surfaces)
        _assert_holds_surface(SqlAlchemyProfileRepository(session), current_user.id, surface)
        actor = ActorContext(identity_id=current_user.id, profile_type=surface)
        episode = get_episode(SqlAlchemyEpisodeRepository(session), episode_id)
        if episode is None:
            raise NotFound()
        Pdp(build_profile_directory(session)).require(
            actor, capability, ResourceRef.for_episode(episode), now
        )
        return episode

    return dependency
