"""Unit tests for the care surface resolver + held-check (no DB, no app).

Exercises the pure pieces of the Layer-1 gate: ``_resolve_surface`` (AM1: single
surface fixed, multi surface explicit + validated) and ``_assert_holds_surface``
(the active-profile held-check), against an in-memory ``ProfileRepository`` fake.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from app.authz.context import ProfileType
from app.authz.exceptions import ProfileSurfaceRequired
from app.care.deps import _assert_holds_surface, _resolve_surface
from app.identity.domain.entities import Profile
from app.identity.domain.value_objects import ProfileType as IdentityProfileType

_PROVIDER = ProfileType.PROVIDER
_CLIENT = ProfileType.CLIENT
_ORG_STAFF = ProfileType.ORG_STAFF


# --- _resolve_surface: AM1 (single = fixed; multi = explicit + validated) ----


def test_single_surface_ignores_missing_acting_as() -> None:
    assert _resolve_surface(None, (_PROVIDER,)) is _PROVIDER


def test_single_surface_ignores_supplied_acting_as() -> None:
    # The one allowed surface is fixed: even a different acting_as is ignored
    # (create + clinical/rehab routes), never a silent re-route.
    assert _resolve_surface(_CLIENT, (_PROVIDER,)) is _PROVIDER


def test_multi_surface_requires_acting_as() -> None:
    with pytest.raises(ProfileSurfaceRequired):
        _resolve_surface(None, (_PROVIDER, _ORG_STAFF))


def test_multi_surface_rejects_not_allowed_acting_as() -> None:
    with pytest.raises(ProfileSurfaceRequired):
        _resolve_surface(_CLIENT, (_PROVIDER, _ORG_STAFF))


def test_multi_surface_honors_allowed_acting_as() -> None:
    assert _resolve_surface(_ORG_STAFF, (_PROVIDER, _ORG_STAFF)) is _ORG_STAFF
    assert _resolve_surface(_PROVIDER, (_PROVIDER, _CLIENT, _ORG_STAFF)) is _PROVIDER


# --- _assert_holds_surface: the active-profile held-check --------------------


@dataclass(slots=True)
class _FakeProfiles:
    """Structural ``ProfileRepository`` fake (list_for + add) over plain rows."""

    rows: list[Profile] = field(default_factory=list)

    def list_for(self, identity_id: UUID) -> list[Profile]:
        return [p for p in self.rows if p.identity_id == identity_id]

    def add(self, profile: Profile) -> None:
        self.rows.append(profile)


def test_assert_holds_surface_passes_when_active_profile_held() -> None:
    identity_id = uuid4()
    repo = _FakeProfiles(
        [Profile(id=uuid4(), identity_id=identity_id, profile_type=IdentityProfileType.PROVIDER)]
    )
    _assert_holds_surface(repo, identity_id, _PROVIDER)  # does not raise


def test_assert_holds_surface_raises_when_surface_not_held() -> None:
    identity_id = uuid4()
    repo = _FakeProfiles(
        [Profile(id=uuid4(), identity_id=identity_id, profile_type=IdentityProfileType.CLIENT)]
    )
    with pytest.raises(ProfileSurfaceRequired):
        _assert_holds_surface(repo, identity_id, _PROVIDER)


def test_assert_holds_surface_raises_when_no_profiles() -> None:
    with pytest.raises(ProfileSurfaceRequired):
        _assert_holds_surface(_FakeProfiles(), uuid4(), _PROVIDER)
