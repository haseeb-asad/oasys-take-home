"""Shared fixtures for the cross-context tests, incl. the DB integration harness.

The DB-backed fixtures fail closed in CI (a missing database is a CI error, not a
silent skip) and skip only locally when no Postgres is reachable. The offline
Alembic fixtures self-seed the environment via monkeypatch so they run anywhere,
with both ``lru_cache`` factories cleared on the way in and out.

The integration harness builds on the session-scoped ``db_engine`` (which runs
``alembic upgrade head`` once): each test gets a ``db_connection`` wrapping an
outer transaction, a ``db_session`` joined to it via a SAVEPOINT, and a ``client``
whose ``get_session`` / ``get_now`` / ``get_settings`` are overridden so the real
app runs against the rolled-back per-test transaction with a fixed clock and a
known JWT secret.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_engine, get_sessionmaker
from app.core.deps import get_now, get_session
from app.core.security import create_access_token
from app.main import create_app

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_JWT_SECRET = "x" * 40
_FAKE_DB_URL = "postgresql+psycopg://u:p@localhost:5432/db"


def _alembic_config() -> Config:
    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
    return cfg


def _in_ci() -> bool:
    return bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))


def _skip_or_fail(reason: str) -> NoReturn:
    # Fail closed in CI; pytrace=False keeps any chained DB-driver traceback (which
    # can carry the connection string / password) out of the CI failure output.
    if _in_ci():
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


@pytest.fixture
def alembic_cfg() -> Config:
    return _alembic_config()


@pytest.fixture
def offline_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET_KEY", _JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", _FAKE_DB_URL)
    for fn in (get_settings, get_engine, get_sessionmaker):
        fn.cache_clear()
    yield
    for fn in (get_settings, get_engine, get_sessionmaker):
        fn.cache_clear()


@pytest.fixture(scope="session")
def _valid_jwt_secret_for_session() -> Iterator[None]:
    """Guarantee a valid ``JWT_SECRET_KEY`` in the env for the whole test session.

    Decouples the DB harness from the developer's local ``.env`` (whose secret may
    be shorter than 32 chars): ``get_settings()`` inside ``get_engine()`` then
    validates regardless. ``DATABASE_URL`` is left to the real env/.env, so a
    genuinely missing or unreachable database still fails closed. A session-scoped
    ``pytest.MonkeyPatch`` is used because the function-scoped ``monkeypatch``
    fixture cannot be requested by a session fixture.
    """
    mp = pytest.MonkeyPatch()
    mp.setenv("JWT_SECRET_KEY", _JWT_SECRET)
    get_settings.cache_clear()
    try:
        yield
    finally:
        mp.undo()
        get_settings.cache_clear()


@pytest.fixture(scope="session")
def db_engine(_valid_jwt_secret_for_session: None) -> Iterator[Engine]:
    # Misconfigured settings (a ValidationError, or the wrong-driver RuntimeError
    # from get_engine) surface as an error rather than a false-green skip; only an
    # unreachable DB is a legitimate local skip. The try/finally guarantees the
    # engine is disposed and every lru_cache factory is cleared on every exit path,
    # including the skip path.
    for fn in (get_settings, get_engine, get_sessionmaker):
        fn.cache_clear()
    engine: Engine | None = None
    try:
        engine = get_engine()
        reachable = True
        try:
            with engine.connect():
                pass
        except OperationalError:
            reachable = False
        # Raise the skip/fail OUTSIDE the except block and with a generic message,
        # so the psycopg exception is not chained as __context__ (its traceback
        # carries the conninfo / password).
        if not reachable:
            _skip_or_fail("Postgres is not reachable.")
        command.upgrade(_alembic_config(), "head")
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        for fn in (get_settings, get_engine, get_sessionmaker):
            fn.cache_clear()


@pytest.fixture
def db_connection(db_engine: Engine) -> Iterator[Connection]:
    """A connection wrapping an outer transaction that is rolled back per test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture
def db_session(db_connection: Connection) -> Iterator[Session]:
    """A Session joined to the per-test transaction via a SAVEPOINT.

    ``join_transaction_mode="create_savepoint"`` makes the app's ``commit()`` a
    SAVEPOINT release rather than a real commit, so the outer connection
    transaction (rolled back in ``db_connection``) still undoes every write at end
    of test, keeping the shared database order-independent (A19).
    """
    session = Session(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
        autoflush=False,
    )
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def clock() -> datetime:
    """A fixed tz-aware instant injected as ``get_now`` in API tests."""
    return datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def auth_settings() -> Settings:
    """Settings with a known JWT secret, decoupled from the developer's .env."""
    return Settings(
        jwt_secret_key=SecretStr(_JWT_SECRET),
        database_url=SecretStr(_FAKE_DB_URL),
        _env_file=None,
    )


@pytest.fixture
def client(db_session: Session, clock: datetime, auth_settings: Settings) -> Iterator[TestClient]:
    """A ``TestClient`` on the real app, wired to the per-test session/clock/secret.

    ``get_session`` yields the per-test ``db_session`` and does NOT close it (the
    ``db_session`` fixture owns the lifecycle), so multiple requests in one test
    share the same transaction: a committed write stays visible across requests
    while still being rolled back at end of test. ``get_current_user`` is NOT
    overridden, so the real authentication path runs end to end.
    """
    app = create_app()

    def _override_get_session() -> Iterator[Session]:
        yield db_session

    def _override_get_now() -> datetime:
        return clock

    def _override_get_settings() -> Settings:
        return auth_settings

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_now] = _override_get_now
    app.dependency_overrides[get_settings] = _override_get_settings
    try:
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def mint_token(clock: datetime) -> Callable[..., str]:
    """Forge a bearer token with the test secret (to exercise /me without login)."""

    def _mint(
        subject: str,
        *,
        now: datetime | None = None,
        expires_minutes: int = 30,
        secret: str = _JWT_SECRET,
    ) -> str:
        return create_access_token(
            subject=subject,
            secret=secret,
            now=now if now is not None else clock,
            expires_minutes=expires_minutes,
        )

    return _mint
