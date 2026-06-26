"""Authorization bounded context: capability vocabulary, role grid, and the PDP.

Public surface: the capability model (``Capability`` / ``GrantRole`` /
``capabilities_for``) and the policy decision point (``Pdp`` with its
actor/resource context, the ``ProfileDirectory`` port, and the ``Forbidden``
exception). The ``_ROLE_CAPABILITIES`` grid stays private to ``capabilities``.
"""

from __future__ import annotations

from app.authz.capabilities import Capability, GrantRole, capabilities_for
from app.authz.context import ActorContext, ProfileType, ResourceRef
from app.authz.exceptions import Forbidden
from app.authz.policy import Pdp
from app.authz.ports import ProfileDirectory

__all__ = [
    "ActorContext",
    "Capability",
    "Forbidden",
    "GrantRole",
    "Pdp",
    "ProfileDirectory",
    "ProfileType",
    "ResourceRef",
    "capabilities_for",
]
