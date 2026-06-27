"""Unit tests for the identity use cases: authenticate, register, get_identity (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.core.security import verify_password
from app.identity.domain.entities import Profile
from app.identity.domain.exceptions import EmailAlreadyRegistered
from app.identity.domain.value_objects import ProfileType
from app.identity.service import (
    authenticate,
    create_profile,
    get_identity,
    has_active_profile,
    register,
)

from .conftest import FakeIdentityRepository, FakeProfileRepository, make_identity

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_NEW_ID = UUID(int=42)
_IDENTITY_ID = UUID(int=7)
_OTHER_IDENTITY_ID = UUID(int=8)
_PROFILE_ID = UUID(int=9)


# --- authenticate -----------------------------------------------------------


def test_authenticate_success_returns_identity() -> None:
    identity = make_identity("ada@example.com", "s3cret")
    repo = FakeIdentityRepository(by_email={identity.email: identity})
    assert authenticate(repo, "ada@example.com", "s3cret") is identity


def test_authenticate_unknown_email_returns_none() -> None:
    repo = FakeIdentityRepository()
    assert authenticate(repo, "nobody@example.com", "s3cret") is None


def test_authenticate_wrong_password_returns_none() -> None:
    identity = make_identity("ada@example.com", "s3cret")
    repo = FakeIdentityRepository(by_email={identity.email: identity})
    assert authenticate(repo, "ada@example.com", "wrong") is None


# --- register ---------------------------------------------------------------


def test_register_builds_and_persists_hashed_identity() -> None:
    repo = FakeIdentityRepository()
    identity = register(repo, "ada@example.com", "Ada", "s3cretpw", now=_NOW, new_id=_NEW_ID)
    # Persisted and retrievable by both keys.
    assert repo.get_by_id(_NEW_ID) is identity
    assert repo.get_by_email("ada@example.com") is identity
    # The password is stored hashed (never plaintext) and verifies.
    assert identity.password_hash != "s3cretpw"
    assert verify_password("s3cretpw", identity.password_hash)


def test_register_uses_injected_id_and_now() -> None:
    repo = FakeIdentityRepository()
    identity = register(repo, "ada@example.com", "Ada", "s3cretpw", now=_NOW, new_id=_NEW_ID)
    assert identity.id == _NEW_ID
    assert identity.created_at == _NOW
    assert identity.email == "ada@example.com"
    assert identity.display_name == "Ada"


def test_register_duplicate_email_raises() -> None:
    repo = FakeIdentityRepository()
    register(repo, "ada@example.com", "Ada", "s3cretpw", now=_NOW, new_id=_NEW_ID)
    with pytest.raises(EmailAlreadyRegistered):
        register(repo, "ada@example.com", "Ada Two", "another1", now=_NOW, new_id=UUID(int=43))


# --- get_identity -----------------------------------------------------------


def test_get_identity_present_returns_identity() -> None:
    identity = make_identity("ada@example.com", "s3cret")
    repo = FakeIdentityRepository(by_id={identity.id: identity})
    assert get_identity(repo, identity.id) is identity


def test_get_identity_absent_returns_none() -> None:
    repo = FakeIdentityRepository()
    assert get_identity(repo, UUID(int=999)) is None


# --- create_profile ---------------------------------------------------------


def test_create_profile_builds_and_persists_active_profile() -> None:
    repo = FakeProfileRepository()
    profile = create_profile(
        repo, identity_id=_IDENTITY_ID, profile_type=ProfileType.PROVIDER, new_id=_PROFILE_ID
    )
    assert profile.id == _PROFILE_ID
    assert profile.identity_id == _IDENTITY_ID
    assert profile.profile_type is ProfileType.PROVIDER
    assert profile.is_active is True  # created profiles are never born discarded
    assert repo.list_for(_IDENTITY_ID) == [profile]


# --- has_active_profile (truth table) ---------------------------------------


def _discarded(profile_type: ProfileType) -> Profile:
    return Profile(
        id=UUID(int=100),
        identity_id=_IDENTITY_ID,
        profile_type=profile_type,
        discarded_at=_NOW,
    )


def test_has_active_profile_true_for_active_matching_profile() -> None:
    repo = FakeProfileRepository()
    create_profile(
        repo, identity_id=_IDENTITY_ID, profile_type=ProfileType.PROVIDER, new_id=_PROFILE_ID
    )
    assert has_active_profile(repo, _IDENTITY_ID, ProfileType.PROVIDER) is True


def test_has_active_profile_false_for_discarded_profile() -> None:
    repo = FakeProfileRepository(profiles=[_discarded(ProfileType.PROVIDER)])
    assert has_active_profile(repo, _IDENTITY_ID, ProfileType.PROVIDER) is False


def test_has_active_profile_false_for_wrong_type() -> None:
    repo = FakeProfileRepository()
    create_profile(
        repo, identity_id=_IDENTITY_ID, profile_type=ProfileType.CLIENT, new_id=_PROFILE_ID
    )
    assert has_active_profile(repo, _IDENTITY_ID, ProfileType.PROVIDER) is False


def test_has_active_profile_false_when_no_profiles() -> None:
    repo = FakeProfileRepository()
    assert has_active_profile(repo, _IDENTITY_ID, ProfileType.CLIENT) is False


def test_has_active_profile_true_when_active_duplicate_alongside_discarded() -> None:
    # An identity may carry both a discarded and an active row of the same type
    # (no partial-unique index); ``any`` over the rows still resolves to active.
    repo = FakeProfileRepository(profiles=[_discarded(ProfileType.PROVIDER)])
    create_profile(
        repo, identity_id=_IDENTITY_ID, profile_type=ProfileType.PROVIDER, new_id=_PROFILE_ID
    )
    assert has_active_profile(repo, _IDENTITY_ID, ProfileType.PROVIDER) is True


def test_has_active_profile_isolates_by_identity() -> None:
    repo = FakeProfileRepository()
    create_profile(
        repo, identity_id=_OTHER_IDENTITY_ID, profile_type=ProfileType.PROVIDER, new_id=_PROFILE_ID
    )
    assert has_active_profile(repo, _IDENTITY_ID, ProfileType.PROVIDER) is False
