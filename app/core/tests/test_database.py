"""Unit tests for the DB engine/session factory and the Base metadata.

No real database is touched: ``create_engine`` is lazy (it never connects on
construction), and ``get_settings`` is monkeypatched so the suite stays
env-independent. Both ``lru_cache`` factories are cleared in ``finally`` so a
mocked engine never leaks into another test.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.core.database import Base, get_engine, get_sessionmaker

_EXPECTED_NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

_FAKE_URL = "postgresql+psycopg://u:p@localhost:5432/db"


def _patch_settings_url(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setattr(
        "app.core.database.get_settings",
        lambda: SimpleNamespace(database_url=SecretStr(url)),
    )


def _patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings_url(monkeypatch, _FAKE_URL)


def test_base_metadata_naming_convention() -> None:
    assert dict(Base.metadata.naming_convention) == _EXPECTED_NAMING


def test_get_engine_uses_settings_url_and_sync_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch)
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    try:
        engine = get_engine()
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.database == "db"
        assert engine.dialect.driver == "psycopg"
        assert get_engine() is engine  # cached
    finally:
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()


def test_get_sessionmaker_bound_to_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch)
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    try:
        session = get_sessionmaker()()
        try:
            assert session.bind is get_engine()
        finally:
            session.close()
    finally:
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()


def test_get_engine_rejects_wrong_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings_url(monkeypatch, "postgresql+psycopg2://u:p@localhost:5432/db")
    get_engine.cache_clear()
    try:
        with pytest.raises(RuntimeError, match=r"postgresql\+psycopg"):
            get_engine()
    finally:
        get_engine.cache_clear()


def test_get_engine_error_does_not_leak_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    # The driver check raises a RuntimeError that carries no value, so a bad URL's
    # password and host never reach the error (unlike a pydantic validation error).
    bad = "postgresql+psycopg2://user:s3cr3t-pw@db-host.internal:5432/db"
    _patch_settings_url(monkeypatch, bad)
    get_engine.cache_clear()
    try:
        with pytest.raises(RuntimeError) as exc_info:
            get_engine()
        rendered = str(exc_info.value) + repr(exc_info.value)
        assert "s3cr3t-pw" not in rendered
        assert "db-host.internal" not in rendered
    finally:
        get_engine.cache_clear()
