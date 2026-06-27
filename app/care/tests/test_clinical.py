"""Unit tests for the pure clinical/rehab record value objects (no DB)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.care.domain.clinical import ClinicalRecord, RehabAssessment

_ID = UUID(int=1)
_EPISODE = UUID(int=2)
_AUTHOR = UUID(int=3)
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("record_cls", [ClinicalRecord, RehabAssessment])
def test_record_constructs_with_tz_aware_created_at(
    record_cls: type[ClinicalRecord] | type[RehabAssessment],
) -> None:
    record = record_cls(
        id=_ID, episode_id=_EPISODE, author_provider_id=_AUTHOR, body="note", created_at=_NOW
    )
    assert record.id == _ID
    assert record.episode_id == _EPISODE
    assert record.author_provider_id == _AUTHOR
    assert record.body == "note"
    assert record.created_at == _NOW


@pytest.mark.parametrize("record_cls", [ClinicalRecord, RehabAssessment])
def test_record_is_frozen(
    record_cls: type[ClinicalRecord] | type[RehabAssessment],
) -> None:
    record = record_cls(
        id=_ID, episode_id=_EPISODE, author_provider_id=_AUTHOR, body="note", created_at=_NOW
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.body = "tampered"  # type: ignore[misc]


@pytest.mark.parametrize("record_cls", [ClinicalRecord, RehabAssessment])
def test_record_rejects_naive_created_at(
    record_cls: type[ClinicalRecord] | type[RehabAssessment],
) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        record_cls(
            id=_ID,
            episode_id=_EPISODE,
            author_provider_id=_AUTHOR,
            body="note",
            created_at=datetime(2026, 1, 1),  # naive
        )
