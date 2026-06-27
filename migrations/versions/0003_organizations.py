"""create organization tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-27

The organization context tables, in FK order per decision A2 (extensions ->
identity -> organization -> care). ``organizations`` first, then
``org_staff_memberships`` (which references both ``identities`` and
``organizations``). Hand-authored per A2. ``type`` / ``role`` are ``VARCHAR``
with a named CHECK (A18); the enum vocabulary is owned by the value objects in
``app/organization/domain/value_objects.py``. ``effective_from`` carries no
server default (it is supplied business time), and the membership rows have no
``created_at`` (append-only effective-dated rows). The explicit constraint names
match the Base naming convention so the models and this migration agree.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        # The ``ck`` convention is ``ck_%(table_name)s_%(constraint_name)s``; op.create_table
        # applies Base.metadata's convention, so the SHORT token "type" resolves to the full
        # ``ck_organizations_type`` (matching the ORM model). A full literal here would double.
        sa.CheckConstraint("type IN ('gym', 'clinic', 'solo_practice')", name="type"),
    )
    op.create_table(
        "org_staff_memberships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_org_staff_memberships"),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["identities.id"],
            name="fk_org_staff_memberships_identity_id_identities",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_org_staff_memberships_org_id_organizations",
        ),
        # Short token "role" resolves to ``ck_org_staff_memberships_role`` via the convention.
        sa.CheckConstraint("role IN ('admin', 'member')", name="role"),
    )


def downgrade() -> None:
    op.drop_table("org_staff_memberships")
    op.drop_table("organizations")
