"""Unit tests for the identities ORM model <-> domain mappers (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.identity.domain.entities import Identity
from app.identity.orm import IdentityModel, _to_domain, _to_model

_ID = UUID(int=7)
_CREATED_AT = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


def _identity() -> Identity:
    return Identity(
        id=_ID,
        email="ada@example.com",
        display_name="Ada",
        password_hash="$2b$12$abc",
        created_at=_CREATED_AT,
    )


def test_to_model_sets_all_columns_explicitly() -> None:
    model = _to_model(_identity())
    assert model.id == _ID
    assert model.email == "ada@example.com"
    assert model.display_name == "Ada"
    assert model.password_hash == "$2b$12$abc"
    assert model.created_at == _CREATED_AT


def test_to_domain_maps_all_columns() -> None:
    model = IdentityModel(
        id=_ID,
        email="ada@example.com",
        display_name="Ada",
        password_hash="$2b$12$abc",
        created_at=_CREATED_AT,
    )
    assert _to_domain(model) == _identity()


def test_round_trip_preserves_tz_aware_created_at() -> None:
    round_tripped = _to_domain(_to_model(_identity()))
    assert round_tripped == _identity()
    assert round_tripped.created_at.tzinfo is not None
    assert round_tripped.created_at.utcoffset() is not None
