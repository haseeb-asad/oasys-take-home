"""Authorization domain exception(s) — PURE (no infrastructure imports).

``Forbidden`` is the single way the PDP says "no": it subclasses the
shared-kernel ``DomainError`` so the central handler can map it (to HTTP 403) in
the web-layer commit — that mapping is deliberately NOT wired here.
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
