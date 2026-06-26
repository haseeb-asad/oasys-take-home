"""Shared fixtures for the cross-context tests, incl. the DB integration harness.

The DB-backed fixture fails closed in CI (a missing database is a CI error, not a
silent skip) and skips only locally when no Postgres is reachable. The offline
Alembic fixtures self-seed the environment via monkeypatch so they run anywhere,
with both ``lru_cache`` factories cleared on the way in and out.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.core.database import get_engine, get_sessionmaker

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
    return cfg


def _in_ci() -> bool:
    return bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))


def _skip_or_fail(reason: str) -> NoReturn:
    if _in_ci():
        pytest.fail(f"DB-backed test could not run in CI: {reason}")  # fail closed
    pytest.skip(reason)


@pytest.fixture
def alembic_cfg() -> Config:
    return _alembic_config()


@pytest.fixture
def offline_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    for fn in (get_settings, get_engine, get_sessionmaker):
        fn.cache_clear()
    yield
    for fn in (get_settings, get_engine, get_sessionmaker):
        fn.cache_clear()


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    for fn in (get_settings, get_engine, get_sessionmaker):
        fn.cache_clear()
    # Only an unreachable DB is a legitimate local skip. A ValidationError means
    # misconfigured settings (wrong driver, missing secret); let it surface as an
    # error rather than a false-green skip. The credential is a SecretStr, so it
    # is masked in any error that propagates.
    try:
        engine = get_engine()
        with engine.connect():
            pass
    except OperationalError as exc:
        _skip_or_fail(f"Postgres not reachable: {exc}")
    command.upgrade(_alembic_config(), "head")
    yield engine
    engine.dispose()
    for fn in (get_settings, get_engine, get_sessionmaker):
        fn.cache_clear()
