"""Authorization domain exception(s) — PURE (no infrastructure imports).

``Forbidden`` is the single way the PDP says "no": it subclasses the
shared-kernel ``DomainError`` so the central handler can map it (to HTTP 403) in
the web-layer commit — that mapping is deliberately NOT wired here.

``ProfileSurfaceRequired`` is the Layer-1 (coarse) sibling: it is raised by the
request-scoped surface resolver (``app/care/deps.py``) BEFORE the PDP runs, and
is also mapped to 403. It stays here so the whole authz vocabulary (capability +
surface failures) is single-homed, while remaining pure: it imports no FastAPI.
"""

from __future__ import annotations

from app.authz.capabilities import Capability
from app.authz.context import ActorContext, ResourceRef
from app.core.exceptions import DomainError


class Forbidden(DomainError):
    """An actor lacks a required capability on a resource.

    Raised by ``Pdp.require``. Carries the ``actor``, the denied ``capability``,
    and the target ``resource`` (for structured logging / handler mapping); the
    message names the capability, the acting surface, and the identity.
    """

    def __init__(
        self,
        actor: ActorContext,
        capability: Capability,
        resource: ResourceRef,
    ) -> None:
        self.actor = actor
        self.capability = capability
        self.resource = resource
        super().__init__(
            f"Identity {actor.identity_id} acting as {actor.profile_type} "
            f"lacks capability {capability} on the target resource."
        )


class ProfileSurfaceRequired(DomainError):
    """The caller did not present a valid acting profile surface for the route.

    Raised by the coarse Layer-1 resolver in ``app/care/deps.py`` when a
    multi-surface route omits ``acting_as`` or names a surface the route does not
    allow, OR when the caller does not actually hold an active profile of the
    resolved surface. Mapped centrally to HTTP 403. Distinct from ``Forbidden``
    (the Layer-2 capability denial): this says "declare/hold a valid surface",
    not "you lack the capability". The message is GENERIC (it names no surface)
    to keep the wrong-hat vs lacks-capability oracle tiny.
    """

    def __init__(self, detail: str = "A valid acting profile surface is required.") -> None:
        super().__init__(detail)
