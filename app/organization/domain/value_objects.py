"""Value objects for the Organization bounded context.

Pure domain layer - plain Python only (frozen-by-nature ``StrEnum`` over stdlib).
No FastAPI / SQLAlchemy / Pydantic imports (project std 1). These two
vocabularies are the single home in code for the org ``type`` and the org-staff
``role`` columns; the persistence layer stores their string values under a
``VARCHAR + CHECK`` (A18), so the enum and the migration CHECK must agree.
"""

from __future__ import annotations

from enum import StrEnum


class OrgType(StrEnum):
    """The controlled organization-kind vocabulary (its single home in code).

    Exactly the three kinds the brief recognises. Stored as the raw string value
    in ``organizations.type`` under ``ck_organizations_type``.
    """

    GYM = "gym"
    CLINIC = "clinic"
    SOLO_PRACTICE = "solo_practice"


class OrgRole(StrEnum):
    """The controlled org-staff membership-role vocabulary (its single home).

    The minimal discriminating pair: ``admin`` (the role that grants org-level
    authority) and ``member`` (ordinary staff, no management authority). The
    stored grant value is ``"admin"``; the authz layer's ``org_admin`` grid key
    (``app/authz/capabilities.py``) is a DIFFERENT concept - a role->capability
    grid label - and is never written to this column. This context owns the
    stored vocabulary and does not import authz.
    """

    ADMIN = "admin"
    MEMBER = "member"
