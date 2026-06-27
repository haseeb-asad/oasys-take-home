"""Value objects for the Identity bounded context.

Pure domain layer - plain Python only (frozen-by-nature ``StrEnum`` over stdlib).
No FastAPI / SQLAlchemy / Pydantic imports (project std 1). ``ProfileType`` is the
single home in code for the ``profiles.profile_type`` column; the persistence
layer stores its string value under a ``VARCHAR + CHECK`` (A18), so the enum and
the migration CHECK must agree.
"""

from __future__ import annotations

from enum import StrEnum


class ProfileType(StrEnum):
    """The controlled profile-kind vocabulary (its single home in code).

    The three personas an identity may hold. The string values are byte-identical
    to the PDP's acting surface (``app/authz/context.py`` ``ProfileType``) so the
    ``ProfileDirectory`` adapter can answer the port in terms of these profiles;
    that agreement is asserted in a test, never via a production cross-import (A3).
    Values are ``snake_case`` (project naming).
    """

    CLIENT = "client"
    PROVIDER = "provider"
    ORG_STAFF = "org_staff"
