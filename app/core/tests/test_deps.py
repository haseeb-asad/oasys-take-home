"""Unit tests for the request-scoped dependencies (session + injectable clock).

No real database: ``get_sessionmaker`` is monkeypatched to a mock factory so we
can assert the yield/close lifecycle without connecting. ``get_now`` is the
single tz-aware clock threaded into the PDP and services (A19).
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import MagicMock

import pytest

from app.core.deps import get_now, get_session


def _patched_session_gen(monkeypatch: pytest.MonkeyPatch, session: MagicMock) -> Generator[object]:
    factory = MagicMock(return_value=session)
    monkeypatch.setattr("app.core.deps.get_sessionmaker", MagicMock(return_value=factory))
    return cast(Generator[object], get_session())


def test_get_session_yields_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = MagicMock()
    gen = _patched_session_gen(monkeypatch, fake)
    assert next(gen) is fake
    fake.close.assert_not_called()
    with pytest.raises(StopIteration):
        next(gen)
    fake.close.assert_called_once_with()


def test_get_session_closes_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = MagicMock()
    gen = _patched_session_gen(monkeypatch, fake)
    next(gen)
    with pytest.raises(RuntimeError, match="boom"):
        gen.throw(RuntimeError("boom"))
    fake.close.assert_called_once_with()


def test_get_now_is_utc_aware() -> None:
    before = datetime.now(UTC)
    now = get_now()
    after = datetime.now(UTC)
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert before <= now <= after
