"""create clinical tables (clinical_records + rehab_assessments)

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-27

The two episode-scoped clinical resources, in FK order per decision A2 (each
references ``episodes`` and ``identities``, both already created upstream).
Hand-authored per A2.

Unlike the effective-dated care child tables (``episode_memberships`` etc.),
these are write-once EVENT rows: each is its own tiny aggregate root (zero
invariants beyond a tz-aware ``created_at``; access is decided by the PDP against
the parent ``Episode`` via ``episode_id``), so it carries a ``created_at``
(TIMESTAMPTZ, server default ``now()``) recording wall-clock authoring time, not
a business-effective window. ``body`` is free text (no CHECK). The explicit
PK/FK names match the Base naming convention so the models and this migration
agree.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: None = None
depends_on: None = None


def _clinical_table(name: str) -> None:
    op.create_table(
        name,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{name}"),
        sa.ForeignKeyConstraint(
            ["episode_id"], ["episodes.id"], name=f"fk_{name}_episode_id_episodes"
        ),
        sa.ForeignKeyConstraint(
            ["author_provider_id"],
            ["identities.id"],
            name=f"fk_{name}_author_provider_id_identities",
        ),
    )


def upgrade() -> None:
    _clinical_table("clinical_records")
    _clinical_table("rehab_assessments")


def downgrade() -> None:
    op.drop_table("rehab_assessments")
    op.drop_table("clinical_records")
