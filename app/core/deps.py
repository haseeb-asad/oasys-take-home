"""Request-scoped dependencies: a DB session per request and an injectable clock.

``get_session`` opens a session from the lazy factory and always closes it,
even on error, so connections never leak. ``get_now`` is the single tz-aware
clock (A19) injected into the PDP and services and overridden in tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.database import get_sessionmaker


def get_session() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


def get_now() -> datetime:
    return datetime.now(UTC)
