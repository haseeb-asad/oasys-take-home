"""create profiles table

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-27

The identity context's Profiles slice (the personas an identity holds). Hand-authored
per decision A2. ``profile_type`` is ``VARCHAR`` with a named CHECK (A18); the enum
vocabulary is owned by ``app/identity/domain/value_objects.py`` (``ProfileType``).
Activeness is a soft-discard tombstone (``discarded_at``), not effective-dating, so
there is no ``created_at`` and no effective window. The explicit constraint names
match the Base naming convention so the model and this migration agree.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_type", sa.String(), nullable=False),
        sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_profiles"),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["identities.id"],
            name="fk_profiles_identity_id_identities",
        ),
        # Short token "profile_type" resolves to ``ck_profiles_profile_type`` via the
        # convention (op.create_table applies Base.metadata's naming convention).
        sa.CheckConstraint(
            "profile_type IN ('client', 'provider', 'org_staff')", name="profile_type"
        ),
    )


def downgrade() -> None:
    op.drop_table("profiles")
