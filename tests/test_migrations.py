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


def test_alembic_config_loads_and_revision_chain(alembic_cfg: Config) -> None:
    script = ScriptDirectory.from_config(alembic_cfg)
    assert script.get_current_head() == "0003"
    assert script.get_revision("0003").down_revision == "0002"
    assert script.get_revision("0002").down_revision == "0001"
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
    assert version == "0003"


def test_identities_table_created(db_engine: Engine) -> None:
    expected_columns = {"id", "email", "display_name", "password_hash", "created_at"}
    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, data_type, udt_name FROM information_schema.columns "
                "WHERE table_name = 'identities'"
            )
        ).all()
        constraints = set(
            conn.scalars(
                text(
                    "SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE table_name = 'identities'"
                )
            ).all()
        )
        version = conn.scalar(text("SELECT version_num FROM alembic_version"))
    columns = {row[0]: (row[1], row[2]) for row in rows}
    assert set(columns) == expected_columns
    assert version == "0003"
    # CITEXT email + TIMESTAMPTZ created_at (citext reports as a USER-DEFINED type
    # whose udt_name is 'citext'); these guard case-insensitivity + the naive trap.
    assert columns["email"][1] == "citext"
    assert columns["created_at"][0] == "timestamp with time zone"
    assert "uq_identities_email" in constraints


def test_organization_tables_created(db_engine: Engine) -> None:
    org_columns_expected = {"id", "name", "type", "created_at"}
    membership_columns_expected = {
        "id",
        "identity_id",
        "org_id",
        "role",
        "effective_from",
        "effective_to",
    }
    with db_engine.connect() as conn:
        # AM5: query each table separately so the column->type maps cannot collide
        # on the shared ``id`` (and the "no created_at on memberships" check stays
        # meaningful).
        org_rows = conn.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'organizations'"
            )
        ).all()
        membership_rows = conn.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'org_staff_memberships'"
            )
        ).all()
        constraints = set(
            conn.scalars(
                text(
                    "SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE table_name IN ('organizations', 'org_staff_memberships')"
                )
            ).all()
        )
        version = conn.scalar(text("SELECT version_num FROM alembic_version"))
    org_columns = {row[0]: row[1] for row in org_rows}
    membership_columns = {row[0]: row[1] for row in membership_rows}
    assert set(org_columns) == org_columns_expected
    assert set(membership_columns) == membership_columns_expected
    # Append-only effective-dated rows carry only business time: no created_at.
    assert "created_at" not in membership_columns
    # Every timestamp is TIMESTAMPTZ (the naive-read trap the pure entities reject).
    assert org_columns["created_at"] == "timestamp with time zone"
    assert membership_columns["effective_from"] == "timestamp with time zone"
    assert membership_columns["effective_to"] == "timestamp with time zone"
    # AM1: all six convention-resolved constraint names land in the database.
    assert {
        "pk_organizations",
        "ck_organizations_type",
        "pk_org_staff_memberships",
        "ck_org_staff_memberships_role",
        "fk_org_staff_memberships_identity_id_identities",
        "fk_org_staff_memberships_org_id_organizations",
    } <= constraints
    assert version == "0003"


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
