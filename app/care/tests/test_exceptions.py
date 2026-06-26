"""Unit tests for the typed domain exceptions (the contract the central HTTP handler relies on)."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.care.domain.exceptions import (
    EpisodeClosed,
    NotACurrentMember,
    OverlappingPeriod,
    SelfTreatment,
)
from app.core.exceptions import DomainError

_ID = UUID(int=42)


def test_each_exception_carries_context_and_renders() -> None:
    """Every domain exception stores its context id, renders it, and is a DomainError."""
    assert SelfTreatment(_ID).identity_id == _ID
    assert NotACurrentMember(_ID).provider_id == _ID
    assert EpisodeClosed(_ID).episode_id == _ID
    assert OverlappingPeriod(_ID).episode_id == _ID
    for exc in (
        SelfTreatment(_ID),
        NotACurrentMember(_ID),
        EpisodeClosed(_ID),
        OverlappingPeriod(_ID),
    ):
        assert isinstance(exc, DomainError)
        assert str(_ID) in str(exc)


def test_catchable_as_domain_error() -> None:
    """The central handler can map any breach by catching the base class alone."""
    with pytest.raises(DomainError):
        raise SelfTreatment(_ID)
