"""Unit tests for the identity Pydantic schemas (edge DTOs; no DB)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.identity.schemas import IdentityCreate, IdentityOut, Token

from .conftest import make_identity


def test_identity_create_valid() -> None:
    payload = IdentityCreate(email="ada@example.com", display_name="Ada", password="s3cretpw")
    assert payload.email == "ada@example.com"
    assert payload.display_name == "Ada"
    assert payload.password.get_secret_value() == "s3cretpw"


def test_identity_create_invalid_email_rejected() -> None:
    with pytest.raises(ValidationError):
        IdentityCreate(email="not-an-email", display_name="Ada", password="s3cretpw")


@pytest.mark.parametrize(("length", "ok"), [(7, False), (8, True), (128, True), (129, False)])
def test_identity_create_password_length_policy(length: int, ok: bool) -> None:
    password = "p" * length
    if ok:
        payload = IdentityCreate(email="ada@example.com", display_name="Ada", password=password)
        assert payload.password.get_secret_value() == password
    else:
        with pytest.raises(ValidationError):
            IdentityCreate(email="ada@example.com", display_name="Ada", password=password)


def test_identity_create_empty_display_name_rejected() -> None:
    with pytest.raises(ValidationError):
        IdentityCreate(email="ada@example.com", display_name="", password="s3cretpw")


def test_identity_create_whitespace_only_display_name_rejected() -> None:
    # StringConstraints strips whitespace first, then enforces min_length=1.
    with pytest.raises(ValidationError):
        IdentityCreate(email="ada@example.com", display_name="   ", password="s3cretpw")


def test_identity_create_display_name_is_stripped() -> None:
    payload = IdentityCreate(email="ada@example.com", display_name="  Ada  ", password="s3cretpw")
    assert payload.display_name == "Ada"


def test_identity_create_password_secret_not_in_repr() -> None:
    payload = IdentityCreate(email="ada@example.com", display_name="Ada", password="supersecret")
    assert "supersecret" not in repr(payload)


def test_identity_out_keys_omit_password_hash() -> None:
    identity = make_identity("ada@example.com", "s3cret")
    out = IdentityOut.model_validate(identity)
    assert set(out.model_dump().keys()) == {"id", "email", "display_name", "created_at"}
    assert out.id == identity.id
    assert out.email == identity.email
    assert not hasattr(out, "password_hash")


def test_token_defaults_to_bearer() -> None:
    token = Token(access_token="abc.def.ghi")
    assert token.access_token == "abc.def.ghi"
    assert token.token_type == "bearer"


def test_token_rejects_non_bearer_type() -> None:
    with pytest.raises(ValidationError):
        Token(access_token="abc", token_type="basic")
