"""create identities table

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-27

The first context table (the auth slice). Hand-authored per decision A2:
autogenerate never emits CITEXT correctly. ``email`` is CITEXT (case-insensitive)
with a unique constraint; ``created_at`` is TIMESTAMPTZ with a server default. The
explicit ``pk_identities`` / ``uq_identities_email`` names match the Base naming
convention so the model and this migration agree.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "identities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_identities"),
        sa.UniqueConstraint("email", name="uq_identities_email"),
    )


def downgrade() -> None:
    op.drop_table("identities")
