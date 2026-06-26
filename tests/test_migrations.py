"""Tests for the Alembic setup and the 0001 extensions migration.

Four tests need no database: the script directory loads, ``0001`` is the head
with no parent, ``alembic.ini`` carries no real URL, and an offline (``sql=True``)
upgrade/downgrade emits the CREATE/DROP EXTENSION statements. One test hits real
Postgres to prove the extensions and version row actually land; it fails closed
in CI and skips locally when no database is reachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_EXTENSIONS = ("pgcrypto", "btree_gist", "citext")


def test_alembic_config_loads_and_revision_0001_is_head(alembic_cfg: Config) -> None:
    script = ScriptDirectory.from_config(alembic_cfg)
    assert script.get_current_head() == "0001"
    assert script.get_revision("0001").down_revision is None


def test_alembic_ini_script_location_and_no_url(alembic_cfg: Config) -> None:
    script_location = alembic_cfg.get_main_option("script_location")
    assert script_location is not None
    assert Path(script_location).resolve() == (_PROJECT_ROOT / "migrations").resolve()
    assert alembic_cfg.get_main_option("sqlalchemy.url") is None


def test_extensions_created_by_migration(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        extensions = set(conn.scalars(text("SELECT extname FROM pg_extension")).all())
        version = conn.scalar(text("SELECT version_num FROM alembic_version"))
    assert set(_EXTENSIONS) <= extensions
    assert version == "0001"


def test_upgrade_offline_emits_create_extension(
    offline_env: None, alembic_cfg: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    command.upgrade(alembic_cfg, "head", sql=True)
    out = capsys.readouterr().out
    assert "CREATE EXTENSION" in out
    for extension in _EXTENSIONS:
        assert extension in out


def test_downgrade_offline_emits_drop_extension(
    offline_env: None, alembic_cfg: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    command.downgrade(alembic_cfg, "head:base", sql=True)
    out = capsys.readouterr().out
    assert "DROP EXTENSION" in out
    for extension in _EXTENSIONS:
        assert extension in out
