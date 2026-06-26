"""create care tables (episodes + the three effective-dated child tables)

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-27

The care context tables, in FK order per decision A2: the ``episodes`` root first,
then ``episode_memberships`` / ``responsibility_assignments`` / ``booking_contacts``
(each references ``episodes`` and ``identities``). Hand-authored per A2.

``status`` / ``role`` are ``VARCHAR`` with a named CHECK (A18); the enum vocabularies
are owned by the care value objects / aggregate. The three child tables are
append-only effective-dated rows, so they carry no ``created_at`` (only business
time) and a half-open ``[effective_from, effective_to)`` window. Each child table
also carries a ``period`` CHECK mirroring ``EffectivePeriod``'s positive-length rule
(``effective_to IS NULL OR effective_from < effective_to``): the EXCLUDE below
rejects OVERLAP but treats a zero-length range as EMPTY, so the CHECK forbids that
degenerate/inverted case directly.

``responsibility_assignments`` and ``booking_contacts`` additionally carry a Postgres
``EXCLUDE USING gist (episode_id WITH =, tstzrange(effective_from, effective_to, '[)')
WITH &&)`` no-overlap constraint (one responsible provider / one face per episode at
any instant). It is added via raw ``op.execute`` with a full-literal name (Alembic /
SQLAlchemy cannot express a gist EXCLUDE in ``create_table`` portably); the half-open
``'[)'`` bound matches ``EffectivePeriod`` so contiguous periods do not collide at the
boundary instant. ``btree_gist`` (migration ``0001``) backs the ``uuid =`` operator
inside the gist index. The explicit PK/FK names match the Base naming convention so
the models and this migration agree.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: None = None
depends_on: None = None

_ROLE_CHECK = (
    "role IN ('physician', 'physiotherapist', 'personal_trainer', "
    "'massage_therapist', 'nutrition_coach')"
)
_PERIOD_CHECK = "effective_to IS NULL OR effective_from < effective_to"


def upgrade() -> None:
    op.create_table(
        "episodes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("managing_org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_episodes"),
        sa.ForeignKeyConstraint(
            ["client_id"], ["identities.id"], name="fk_episodes_client_id_identities"
        ),
        sa.ForeignKeyConstraint(
            ["managing_org_id"],
            ["organizations.id"],
            name="fk_episodes_managing_org_id_organizations",
        ),
        # Short token "status" resolves to ``ck_episodes_status`` via the convention.
        sa.CheckConstraint("status IN ('active', 'closed')", name="status"),
    )
    op.create_table(
        "episode_memberships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_episode_memberships"),
        sa.ForeignKeyConstraint(
            ["episode_id"], ["episodes.id"], name="fk_episode_memberships_episode_id_episodes"
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["identities.id"],
            name="fk_episode_memberships_provider_id_identities",
        ),
        sa.CheckConstraint(_ROLE_CHECK, name="role"),
        sa.CheckConstraint(_PERIOD_CHECK, name="period"),
    )
    op.create_table(
        "responsibility_assignments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_responsibility_assignments"),
        sa.ForeignKeyConstraint(
            ["episode_id"],
            ["episodes.id"],
            name="fk_responsibility_assignments_episode_id_episodes",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["identities.id"],
            name="fk_responsibility_assignments_provider_id_identities",
        ),
        sa.CheckConstraint(_PERIOD_CHECK, name="period"),
    )
    op.create_table(
        "booking_contacts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_booking_contacts"),
        sa.ForeignKeyConstraint(
            ["episode_id"], ["episodes.id"], name="fk_booking_contacts_episode_id_episodes"
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["identities.id"], name="fk_booking_contacts_provider_id_identities"
        ),
        sa.CheckConstraint(_PERIOD_CHECK, name="period"),
    )
    # The temporal no-overlap EXCLUDE constraints (not expressible in create_table):
    # half-open '[)' matches EffectivePeriod, so contiguous [a, b) / [b, c) periods
    # do not collide at the boundary instant b. Full-literal constraint names.
    op.execute(
        "ALTER TABLE responsibility_assignments "
        "ADD CONSTRAINT responsibility_assignments_no_overlap "
        "EXCLUDE USING gist (episode_id WITH =, "
        "tstzrange(effective_from, effective_to, '[)') WITH &&)"
    )
    op.execute(
        "ALTER TABLE booking_contacts "
        "ADD CONSTRAINT booking_contacts_no_overlap "
        "EXCLUDE USING gist (episode_id WITH =, "
        "tstzrange(effective_from, effective_to, '[)') WITH &&)"
    )


def downgrade() -> None:
    op.drop_table("booking_contacts")
    op.drop_table("responsibility_assignments")
    op.drop_table("episode_memberships")
    op.drop_table("episodes")
