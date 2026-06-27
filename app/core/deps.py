"""Request-scoped dependencies: a DB session per request, a clock, an id factory.

``get_session`` opens a session from the lazy factory and always closes it,
even on error, so connections never leak. ``get_now`` is the single tz-aware
clock (A19) injected into the PDP and services and overridden in tests.
``get_new_id`` is the shared-kernel uuid factory (overridable in tests for
determinism), mirroring ``get_now``: it lives here, alongside the other
request-scoped primitives, so each bounded context's edge can inject a fresh id
without reaching into another context's web layer.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

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


def get_new_id() -> UUID:
    """Provide a fresh uuid (overridable in tests for determinism)."""
    return uuid4()
