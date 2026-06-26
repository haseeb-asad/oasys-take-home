"""Pydantic v2 DTOs for the identity API (edge layer; A8/A9).

``IdentityCreate`` is the registration request (the password is a ``SecretStr`` so
the plaintext never lands in a repr/log); ``IdentityOut`` is the response and NEVER
exposes ``password_hash``; ``Token`` is the OAuth2 access-token response. Pydantic
lives only here at the boundary; the domain stays plain Python.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, StringConstraints

_DisplayName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
_Password = Annotated[SecretStr, Field(min_length=8, max_length=128)]


class IdentityCreate(BaseModel):
    """Registration request: email, display name, and a write-only password.

    The 8..128 password length is a registration policy only; ``max_length=128``
    is not a bcrypt limit (the security layer pre-hashes, so any length is safe).
    ``/v1/auth/token`` (login) does NOT re-apply this policy: it authenticates
    against the stored hash and returns a generic 401 on failure.
    """

    email: EmailStr
    display_name: _DisplayName
    password: _Password


class IdentityOut(BaseModel):
    """Registration / ``/me`` response. Never carries ``password_hash``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    display_name: str
    created_at: datetime


class Token(BaseModel):
    """OAuth2 bearer access-token response."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
