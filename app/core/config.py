"""Application settings: core/edge layer (pydantic-settings allowed).

JWT and database configuration are modelled here. ``get_settings()`` is lazy
(``lru_cache``) so nothing reads the environment at import time, keeping CI,
which has no ``.env`` / ``JWT_SECRET_KEY``, safe.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """JWT and database configuration loaded from the environment or a local ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    jwt_secret_key: SecretStr
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_expire_minutes: int = Field(default=30, gt=0)
    database_url: str = Field(repr=False)

    @field_validator("jwt_secret_key")
    @classmethod
    def _secret_min_length(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("JWT secret key must be at least 32 characters.")
        return value

    @field_validator("database_url")
    @classmethod
    def _must_be_sync_postgresql(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use the postgresql+psycopg:// driver.")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings, built once on first use."""
    return Settings()
