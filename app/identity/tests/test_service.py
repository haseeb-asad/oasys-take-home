"""Unit tests for the identity authentication use case (no DB)."""

from __future__ import annotations

from app.identity.service import authenticate

from .conftest import FakeIdentityRepository, make_identity


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
