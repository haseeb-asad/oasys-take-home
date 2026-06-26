"""Security primitives: bcrypt password hashing + JWT access-token logic.

Core/edge layer (functional core) — depends only on stdlib, ``bcrypt``, ``jwt``
(pyjwt), and the shared-kernel ``NotAuthenticated``. No FastAPI / SQLAlchemy /
Pydantic, no ``Settings``, no ``Identity`` / port import, and no hidden clock: the
caller injects a timezone-aware ``now`` (naive datetimes are rejected).
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta
from typing import TypeGuard

import bcrypt
import jwt

from app.core.exceptions import NotAuthenticated


def _prehash(password: str) -> bytes:
    """SHA-256(password) -> base64 (44 ASCII bytes, no NULs, < bcrypt's 72-byte cap).

    Supports passwords of ANY length without bcrypt truncation / collisions.
    """
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def _epoch_seconds(now: datetime) -> int:
    """Convert an aware ``now`` to UNIX seconds; reject naive datetimes."""
    if now.tzinfo is None or now.utcoffset() is None:  # robust aware check
        raise ValueError("now must be timezone-aware.")
    return int(now.timestamp())


def _is_numeric_date(value: object) -> TypeGuard[int]:
    """A JWT NumericDate is a real int — bools are ints in Python but not valid here."""
    return isinstance(value, int) and not isinstance(value, bool)


def hash_password(password: str) -> str:
    """Hash a password with a fresh per-call salt; returns the bcrypt string."""
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """True iff ``password`` matches ``password_hash``; False on a malformed hash."""
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode("ascii"))
    except ValueError:
        return False  # malformed stored hash -> not a match (never leak an error through login)


def create_access_token(
    *,
    subject: str,
    secret: str,
    now: datetime,
    expires_minutes: int,
    algorithm: str = "HS256",
) -> str:
    """Issue a signed JWT with ``sub`` / ``iat`` / ``exp`` from the injected ``now``."""
    issued = _epoch_seconds(now)
    payload: dict[str, str | int] = {
        "sub": subject,
        "iat": issued,
        "exp": _epoch_seconds(now + timedelta(minutes=expires_minutes)),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(
    token: str,
    *,
    secret: str,
    now: datetime,
    algorithm: str = "HS256",
) -> str:
    """Verify a token and return its subject, or raise ``NotAuthenticated``.

    The signature IS verified by pyjwt; its real-wall-clock temporal checks are
    disabled so we validate ``exp`` / ``iat`` against the INJECTED ``now``
    (codebase-wide injectable-clock rule; pyjwt validates ``exp`` AND ``iat`` vs
    the real clock, which would make fixed-date tests flaky). ``algorithms``
    pins one algorithm — the alg-confusion defense. We never issue ``nbf``, but
    if a (validly signed) token carries one it is honoured against the injected
    ``now`` too, so a not-yet-valid token is rejected rather than silently
    accepted (pyjwt's own ``nbf`` check is disabled for the same clock reason).
    """
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            options={"verify_exp": False, "verify_iat": False, "verify_nbf": False},
        )
    except jwt.PyJWTError as exc:
        raise NotAuthenticated() from exc
    subject = claims.get("sub")
    iat = claims.get("iat")
    exp = claims.get("exp")
    nbf = claims.get("nbf")
    if not isinstance(subject, str) or not subject:  # missing / empty / non-str sub
        raise NotAuthenticated()
    if not _is_numeric_date(iat):  # missing / non-int (or bool) iat
        raise NotAuthenticated()
    if not _is_numeric_date(exp):  # missing / non-int (or bool) exp
        raise NotAuthenticated()
    now_s = _epoch_seconds(now)
    if now_s >= exp:  # expired (exp instant already invalid — half-open)
        raise NotAuthenticated()
    if iat > now_s:  # issued in the future
        raise NotAuthenticated()
    if nbf is not None:  # honour not-before if a (signed) token carries one
        if not _is_numeric_date(nbf):
            raise NotAuthenticated()
        if now_s < nbf:  # not yet valid
            raise NotAuthenticated()
    return subject
