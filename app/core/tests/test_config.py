"""Unit tests for Settings (pydantic-settings) and the cached get_settings().

All construction passes ``_env_file=None`` so a stray local ``.env`` never leaks
into assertions; the suite stays env-independent (CI has no ``.env``).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


def test_settings_reads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "s" * 40)
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "5")
    settings = Settings(_env_file=None)
    assert settings.jwt_secret_key.get_secret_value() == "s" * 40
    assert settings.access_token_expire_minutes == 5
    assert settings.jwt_algorithm == "HS256"


def test_settings_defaults_applied() -> None:
    settings = Settings(jwt_secret_key="k" * 32, _env_file=None)
    assert settings.jwt_algorithm == "HS256"
    assert settings.access_token_expire_minutes == 30


def test_secret_str_not_leaked() -> None:
    settings = Settings(jwt_secret_key="x" * 40, _env_file=None)
    assert "x" * 40 not in repr(settings)
    assert "x" * 40 not in str(settings.jwt_secret_key)
    assert settings.jwt_secret_key.get_secret_value() == "x" * 40


def test_short_secret_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(jwt_secret_key="short", _env_file=None)


def test_unsupported_algorithm_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(jwt_secret_key="k" * 32, jwt_algorithm="none", _env_file=None)


def test_non_hs256_algorithm_rejected() -> None:
    # Only HS256 is allowed (weak-key HS512 / asymmetric algs are out of scope).
    with pytest.raises(ValidationError):
        Settings(jwt_secret_key="k" * 32, jwt_algorithm="HS512", _env_file=None)


def test_non_positive_expiry_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(jwt_secret_key="k" * 32, access_token_expire_minutes=0, _env_file=None)


def test_get_settings_cached_and_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "k" * 32)
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
