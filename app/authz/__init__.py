"""Authorization bounded context: capability vocabulary and role -> grid.

Public surface is the capability model only; the policy decision point
(``policy.py``) is added in a later commit and is not re-exported here. The
``_ROLE_CAPABILITIES`` grid stays private to ``capabilities``.
"""

from __future__ import annotations

from app.authz.capabilities import Capability, GrantRole, capabilities_for

__all__ = ["Capability", "GrantRole", "capabilities_for"]
