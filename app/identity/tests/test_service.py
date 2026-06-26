"""Unit tests for the identity use cases: authenticate, register, get_identity (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.core.security import verify_password
from app.identity.domain.exceptions import EmailAlreadyRegistered
from app.identity.service import authenticate, get_identity, register

from .conftest import FakeIdentityRepository, make_identity

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_NEW_ID = UUID(int=42)


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
