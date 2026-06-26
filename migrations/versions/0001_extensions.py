"""create postgres extensions: pgcrypto, btree_gist, citext

Revision ID: 0001
Revises:
Create Date: 2026-06-26

Extensions-first per decisions A2 and A13. pgcrypto provides gen_random_uuid()
for UUID primary keys; btree_gist backs the tstzrange EXCLUDE no-overlap
constraints; citext gives case-insensitive email. Hand-authored because
autogenerate never emits CREATE EXTENSION.
"""

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: None = None
depends_on: None = None

_EXTENSIONS = ("pgcrypto", "btree_gist", "citext")


def upgrade() -> None:
    for extension in _EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')


def downgrade() -> None:
    for extension in reversed(_EXTENSIONS):
        op.execute(f'DROP EXTENSION IF EXISTS "{extension}"')
