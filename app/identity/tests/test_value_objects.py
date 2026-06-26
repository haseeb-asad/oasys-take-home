"""Unit tests for the identity ProfileType value object (no DB)."""

from __future__ import annotations

from app.authz.context import ProfileType as AuthzProfileType
from app.identity.domain.value_objects import ProfileType


class TestProfileType:
    def test_values_and_str_enum(self) -> None:
        assert isinstance(ProfileType.CLIENT, str)  # StrEnum members are str instances
        assert {p.value for p in ProfileType} == {"client", "provider", "org_staff"}

    def test_string_agreement_with_authz_context(self) -> None:
        # The stored profile_type strings must equal the PDP's acting-surface
        # vocabulary (``app/authz/context.py``): the adapter answers the port's
        # is_active_<surface> in terms of these profiles. The agreement is asserted
        # here only (no production cross-import keeps the contexts decoupled, A3).
        assert {p.value for p in ProfileType} == {p.value for p in AuthzProfileType}
