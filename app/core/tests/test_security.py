"""Unit tests for the security primitives: bcrypt hashing + JWT token logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from app.core.exceptions import NotAuthenticated
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

S = "k" * 32
t0 = datetime(2026, 1, 1, tzinfo=UTC)


# --- password hashing -------------------------------------------------------


def test_hash_password_is_not_plaintext_and_is_bcrypt() -> None:
    hashed = hash_password("s3cret")
    assert hashed != "s3cret"
    assert hashed.startswith("$2b$")


def test_hash_password_is_salted_unique() -> None:
    assert hash_password("s3cret") != hash_password("s3cret")


def test_verify_password_true_for_correct() -> None:
    assert verify_password("s3cret", hash_password("s3cret")) is True


def test_verify_password_false_for_wrong() -> None:
    assert verify_password("wrong", hash_password("s3cret")) is False


def test_verify_password_false_for_malformed_hash() -> None:
    assert verify_password("x", "not-a-bcrypt-hash") is False


def test_long_password_hashes_and_verifies() -> None:
    password = "p" * 200
    assert verify_password(password, hash_password(password)) is True


def test_distinct_long_passwords_sharing_first_72_bytes_do_not_collide() -> None:
    a = "p" * 100 + "A"
    b = "p" * 100 + "B"
    hashed = hash_password(a)
    assert verify_password(a, hashed) is True
    assert verify_password(b, hashed) is False


# --- JWT round trip ---------------------------------------------------------


def test_create_then_decode_round_trip_returns_subject() -> None:
    token = create_access_token(subject="user-1", secret=S, now=t0, expires_minutes=30)
    assert decode_access_token(token, secret=S, now=t0 + timedelta(minutes=29)) == "user-1"


def test_round_trip_with_uuid_subject() -> None:
    subject = str(uuid4())
    token = create_access_token(subject=subject, secret=S, now=t0, expires_minutes=30)
    assert decode_access_token(token, secret=S, now=t0 + timedelta(minutes=1)) == subject


# --- JWT decode failures ----------------------------------------------------


def test_decode_expired_raises() -> None:
    token = create_access_token(subject="u", secret=S, now=t0, expires_minutes=30)
    with pytest.raises(NotAuthenticated):
        decode_access_token(token, secret=S, now=t0 + timedelta(minutes=31))


def test_decode_at_exact_expiry_raises() -> None:
    token = create_access_token(subject="u", secret=S, now=t0, expires_minutes=30)
    with pytest.raises(NotAuthenticated):
        decode_access_token(token, secret=S, now=t0 + timedelta(minutes=30))


def test_decode_wrong_secret_raises() -> None:
    token = create_access_token(subject="u", secret=S, now=t0, expires_minutes=30)
    with pytest.raises(NotAuthenticated):
        decode_access_token(token, secret="x" * 32, now=t0)


def test_decode_malformed_raises() -> None:
    with pytest.raises(NotAuthenticated):
        decode_access_token("a.b.c", secret=S, now=t0)


def test_decode_missing_exp_raises() -> None:
    token = jwt.encode({"sub": "u", "iat": int(t0.timestamp())}, S, algorithm="HS256")
    with pytest.raises(NotAuthenticated):
        decode_access_token(token, secret=S, now=t0)


def test_decode_missing_sub_raises() -> None:
    iat = int(t0.timestamp())
    token = jwt.encode({"iat": iat, "exp": iat + 1800}, S, algorithm="HS256")
    with pytest.raises(NotAuthenticated):
        decode_access_token(token, secret=S, now=t0)


def test_decode_empty_sub_raises() -> None:
    iat = int(t0.timestamp())
    token = jwt.encode({"sub": "", "iat": iat, "exp": iat + 1800}, S, algorithm="HS256")
    with pytest.raises(NotAuthenticated):
        decode_access_token(token, secret=S, now=t0)


def test_decode_non_int_iat_raises() -> None:
    iat = int(t0.timestamp())
    token = jwt.encode({"sub": "u", "iat": "soon", "exp": iat + 1800}, S, algorithm="HS256")
    with pytest.raises(NotAuthenticated):
        decode_access_token(token, secret=S, now=t0)


def test_decode_future_iat_raises() -> None:
    token = create_access_token(
        subject="u", secret=S, now=t0 + timedelta(minutes=10), expires_minutes=30
    )
    with pytest.raises(NotAuthenticated):
        decode_access_token(token, secret=S, now=t0)


# --- injectable clock: naive datetimes rejected -----------------------------


def test_create_rejects_naive_now() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        create_access_token(
            subject="u",
            secret=S,
            now=datetime(2026, 1, 1),  # noqa: DTZ001
            expires_minutes=30,
        )


def test_decode_rejects_naive_now() -> None:
    token = create_access_token(subject="u", secret=S, now=t0, expires_minutes=30)
    with pytest.raises(ValueError, match="timezone-aware"):
        decode_access_token(token, secret=S, now=datetime(2026, 1, 1))  # noqa: DTZ001
