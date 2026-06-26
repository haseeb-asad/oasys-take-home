"""Alembic environment: sync engine, with URL and metadata from app settings.

Hand-authored per decision A2. The database URL is read from get_settings() (a
SecretStr) and passed straight to Alembic, never through alembic.ini /
ConfigParser, so a percent-encoded password is not mangled by interpolation and
the credential never lands in a tracked config file. target_metadata is
Base.metadata; ORM model modules are imported below so autogenerate diffs see
their tables (migrations themselves stay hand-authored per A2).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.core.database import Base
from app.identity import orm  # noqa: F401  (registers identities on Base.metadata)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = get_settings().database_url.get_secret_value()
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(database_url, poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
