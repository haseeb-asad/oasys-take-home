"""Identity application layer: authentication use case (no infrastructure).

Orchestrates the ``IdentityRepository`` port + the ``verify_password`` primitive;
the SQLAlchemy adapter and the login route are wired in commit 7.
"""

from __future__ import annotations

from app.core.security import verify_password
from app.identity.domain.entities import Identity
from app.identity.domain.repository import IdentityRepository


def authenticate(repo: IdentityRepository, email: str, password: str) -> Identity | None:
    """Return the Identity iff the email exists and the password matches; else None.

    The commit-7 login route maps ``None`` -> 401 with a generic message (unknown
    email and wrong password are deliberately indistinguishable). No equalized
    dummy-verify (A12 cuts the timing-oracle hardening).
    """
    identity = repo.get_by_email(email)
    if identity is None:
        return None
    if not verify_password(password, identity.password_hash):
        return None
    return identity
