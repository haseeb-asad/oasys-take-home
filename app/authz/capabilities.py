"""Capability vocabulary and the role -> capability grid (authz context).

Pure authorization layer — plain Python only (stdlib ``enum`` /
``collections.abc``). No FastAPI / SQLAlchemy / Pydantic imports (project std 1)
and no import of ``app.care``: the value-identity between care's ``Role`` and
this module's ``GrantRole`` is asserted in a test, never via a production
cross-import.

The grid ``_ROLE_CAPABILITIES`` is the single source of truth for which static
capabilities a grant role confers. Relationship-scoped grants (e.g. the
responsible provider's ``MANAGE_TEAM``) are NOT grid cells — they are decided by
the policy decision point and stay out of this table.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class Capability(StrEnum):
    """An atomic, checkable permission (its single home in code).

    Names are ``UPPER_SNAKE``; each value is the lowercased name.
    """

    VIEW_CLINICAL = "view_clinical"
    WRITE_CLINICAL = "write_clinical"
    VIEW_REHAB_ASSESSMENT = "view_rehab_assessment"
    RUN_SESSION = "run_session"
    MESSAGE_CLIENT = "message_client"
    BILL = "bill"
    VIEW_SCHEDULE = "view_schedule"
    VIEW_BASIC_PROFILE = "view_basic_profile"
    MANAGE_TEAM = "manage_team"


class GrantRole(StrEnum):
    """The grid-key vocabulary: the five care provider roles + ``org_admin``.

    The five provider-role string values are byte-identical to care's ``Role``
    (so the policy layer can convert via ``GrantRole(role.value)``); ``org_admin``
    is an org-staff capability-grant key, not an episode ``Membership.role``.
    """

    PHYSICIAN = "physician"
    PHYSIOTHERAPIST = "physiotherapist"
    PERSONAL_TRAINER = "personal_trainer"
    MASSAGE_THERAPIST = "massage_therapist"
    NUTRITION_COACH = "nutrition_coach"
    ORG_ADMIN = "org_admin"


_ROLE_CAPABILITIES: Mapping[GrantRole, frozenset[Capability]] = {
    GrantRole.PHYSICIAN: frozenset(
        {
            Capability.VIEW_BASIC_PROFILE,
            Capability.VIEW_SCHEDULE,
            Capability.RUN_SESSION,
            Capability.MESSAGE_CLIENT,
            Capability.VIEW_REHAB_ASSESSMENT,
            Capability.VIEW_CLINICAL,
            Capability.WRITE_CLINICAL,
            Capability.BILL,
        }
    ),
    GrantRole.PHYSIOTHERAPIST: frozenset(
        {
            Capability.VIEW_BASIC_PROFILE,
            Capability.VIEW_SCHEDULE,
            Capability.RUN_SESSION,
            Capability.MESSAGE_CLIENT,
            Capability.VIEW_REHAB_ASSESSMENT,
            Capability.VIEW_CLINICAL,
            Capability.WRITE_CLINICAL,
            Capability.BILL,
        }
    ),
    GrantRole.PERSONAL_TRAINER: frozenset(
        {
            Capability.VIEW_BASIC_PROFILE,
            Capability.VIEW_SCHEDULE,
            Capability.RUN_SESSION,
            Capability.MESSAGE_CLIENT,
        }
    ),
    GrantRole.MASSAGE_THERAPIST: frozenset(
        {
            Capability.VIEW_BASIC_PROFILE,
            Capability.VIEW_SCHEDULE,
            Capability.RUN_SESSION,
            Capability.MESSAGE_CLIENT,
        }
    ),
    GrantRole.NUTRITION_COACH: frozenset(
        {
            Capability.VIEW_BASIC_PROFILE,
            Capability.VIEW_SCHEDULE,
            Capability.RUN_SESSION,
            Capability.MESSAGE_CLIENT,
        }
    ),
    GrantRole.ORG_ADMIN: frozenset(
        {
            Capability.VIEW_BASIC_PROFILE,
            Capability.VIEW_SCHEDULE,
            Capability.BILL,
            Capability.MANAGE_TEAM,
        }
    ),
}


def capabilities_for(role: GrantRole) -> frozenset[Capability]:
    """Return the static capabilities granted to ``role``.

    The grid is total over ``GrantRole``; an unknown runtime value raises
    ``KeyError`` (fail loud — no ``.get`` default, no role translation here).
    """
    return _ROLE_CAPABILITIES[role]
