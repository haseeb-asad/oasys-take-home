"""Unit tests for EffectivePeriod and Role value objects."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.care.domain.value_objects import EffectivePeriod, Role


def _t(hours: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=hours)


class TestRole:
    def test_values_count_and_str_enum(self) -> None:
        assert len(Role) == 5
        assert isinstance(Role.PHYSICIAN, str)  # StrEnum members are str instances
        assert {r.value for r in Role} == {
            "physician",
            "physiotherapist",
            "personal_trainer",
            "massage_therapist",
            "nutrition_coach",
        }


class TestEffectivePeriodConstruction:
    def test_open_period_valid(self) -> None:
        p = EffectivePeriod(_t(0), None)
        assert p.is_open is True
        assert p.effective_to is None

    def test_bounded_period_valid(self) -> None:
        p = EffectivePeriod(_t(0), _t(10))
        assert p.is_open is False

    def test_reject_zero_length(self) -> None:
        with pytest.raises(ValueError, match="strictly before"):
            EffectivePeriod(_t(5), _t(5))

    def test_reject_inverted(self) -> None:
        with pytest.raises(ValueError, match="strictly before"):
            EffectivePeriod(_t(10), _t(5))

    def test_reject_naive_from(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            EffectivePeriod(datetime(2026, 1, 1), None)  # noqa: DTZ001

    def test_reject_naive_to(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            EffectivePeriod(_t(0), datetime(2026, 1, 2))  # noqa: DTZ001


class TestIsEffectiveAt:
    def test_before_from_is_false(self) -> None:
        assert EffectivePeriod(_t(5), _t(10)).is_effective_at(_t(4)) is False

    def test_at_from_is_true(self) -> None:
        assert EffectivePeriod(_t(5), _t(10)).is_effective_at(_t(5)) is True

    def test_inside_is_true(self) -> None:
        assert EffectivePeriod(_t(5), _t(10)).is_effective_at(_t(7)) is True

    def test_at_to_is_false_half_open(self) -> None:
        assert EffectivePeriod(_t(5), _t(10)).is_effective_at(_t(10)) is False

    def test_after_to_is_false(self) -> None:
        assert EffectivePeriod(_t(5), _t(10)).is_effective_at(_t(11)) is False

    def test_open_after_from_is_true(self) -> None:
        assert EffectivePeriod(_t(5), None).is_effective_at(_t(100)) is True

    def test_open_at_from_is_true(self) -> None:
        assert EffectivePeriod(_t(5), None).is_effective_at(_t(5)) is True

    def test_open_before_from_is_false(self) -> None:
        assert EffectivePeriod(_t(5), None).is_effective_at(_t(4)) is False


class TestOverlaps:
    def test_disjoint_no_overlap(self) -> None:
        a = EffectivePeriod(_t(0), _t(5))
        b = EffectivePeriod(_t(10), _t(15))
        assert a.overlaps(b) is False
        assert b.overlaps(a) is False

    def test_contiguous_do_not_overlap(self) -> None:
        a = EffectivePeriod(_t(0), _t(5))
        b = EffectivePeriod(_t(5), _t(10))
        assert a.overlaps(b) is False
        assert b.overlaps(a) is False

    def test_true_overlap(self) -> None:
        a = EffectivePeriod(_t(0), _t(8))
        b = EffectivePeriod(_t(5), _t(10))
        assert a.overlaps(b) is True
        assert b.overlaps(a) is True

    def test_one_open_overlaps(self) -> None:
        a = EffectivePeriod(_t(0), None)
        b = EffectivePeriod(_t(5), _t(10))
        assert a.overlaps(b) is True
        assert b.overlaps(a) is True

    def test_one_open_disjoint(self) -> None:
        a = EffectivePeriod(_t(20), None)
        b = EffectivePeriod(_t(5), _t(10))
        assert a.overlaps(b) is False
        assert b.overlaps(a) is False

    def test_both_open_overlap(self) -> None:
        a = EffectivePeriod(_t(0), None)
        b = EffectivePeriod(_t(5), None)
        assert a.overlaps(b) is True

    def test_identical_overlap(self) -> None:
        a = EffectivePeriod(_t(0), _t(10))
        b = EffectivePeriod(_t(0), _t(10))
        assert a.overlaps(b) is True


class TestClosedAt:
    def test_closes_open_period(self) -> None:
        p = EffectivePeriod(_t(0), None)
        closed = p.closed_at(_t(5))
        assert closed.effective_from == _t(0)
        assert closed.effective_to == _t(5)

    def test_reject_close_already_bounded(self) -> None:
        p = EffectivePeriod(_t(0), _t(5))
        with pytest.raises(ValueError, match="already-bounded"):
            p.closed_at(_t(3))

    def test_reject_close_at_start_zero_length(self) -> None:
        p = EffectivePeriod(_t(0), None)
        with pytest.raises(ValueError, match="strictly before"):
            p.closed_at(_t(0))
