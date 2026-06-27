"""create the legacy link staging table and backfill it into episodes

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-27

The expand step of an expand/contract migration (the contract/drop is a later,
separate migration). It is PURELY ADDITIVE: it ``CREATE``s one new table and only
ever ``INSERT``s into the existing care tables - no ``ALTER``/``DROP`` of any
existing prod table.

``legacy_provider_links`` is an ENRICHED MIGRATION STAGING TABLE, not the literal
minimal old one-provider-per-client link. It deliberately carries the minimum
needed to project each legacy relationship into the episode model - the provider,
the membership ``role``, the ``managing_org_id``, and the original ``created_at`` -
plus a bookkeeping ``migrated_episode_id`` pointer the backfill writes back. Those
enrichment columns (``role`` / ``managing_org_id``) are sourced ON the staging row
so the backfill is a deterministic copy (a pure ETL projection), not a conjuring
policy that would synthesize orgs/roles or write into ``organizations``.

``role`` is ``VARCHAR`` + a named CHECK and ``client_id <> provider_id`` is a named
CHECK (A18), mirroring the 0005 care vocabulary / the aggregate's no-self rule at
the database; ``UNIQUE(client_id)`` encodes one-provider-per-client. Short-token
CHECK names (``role`` / ``no_self``) are expanded by the
``ck_%(table_name)s_%(constraint_name)s`` convention on ``Base.metadata``; PK/FK/UQ
carry explicit literal names matching the 0005 style.

The backfill is a factored, frozen-table function
``backfill_episodes_from_legacy(connection)`` (imports no app ORM/domain, so it is
drift-proof and matches 0005's no-ORM posture). It is EXACTLY what ``upgrade()``
invokes, so the function's tests are the migration's data-step tests. ``upgrade()``
always runs the offline-renderable ``create_table`` then runs the backfill over
whatever rows are STAGED in ``legacy_provider_links``, but ONLY when not in offline
(``--sql``) mode (a Python-loop data migration cannot render to static SQL).

Operational model: in a real expand/contract the legacy export is loaded into this
staging table (by a prior step or within the same migration window) and
``upgrade()`` projects it into episodes. The backfill is idempotent (it selects
only rows whose ``migrated_episode_id IS NULL`` and writes the new episode id
back), so loading more rows and re-running converges. On a GREENFIELD upgrade the
staging table is created empty, so the in-``upgrade`` call is a structural no-op:
the projection is proven by direct invocation in the tests, NOT by
``alembic upgrade head`` (which has nothing staged to migrate).

``downgrade()`` is offline-safe: the data revert is a fixed sequence of single
``op.execute`` DELETE statements (each renderable under ``--sql``), then
``drop_table`` LAST so the ``migrated_episode_id`` subquery is still resolvable.
The revert deletes every backfilled episode AND ALL of its episode-scoped children
keyed by ``episode_id`` (the 0005 care rows membership/responsibility/booking AND
the 0006 ``clinical_records``/``rehab_assessments``, so the non-cascading FKs never
block the parent DELETE and any child later added to a backfilled episode is
removed too); it touches no episode whose id is not pointed at by a migrated legacy
row, so a pre-existing (non-backfilled) episode is never deleted. The same SQL
constants back ``revert_backfilled_episodes(connection)`` so a DB test can exercise
the revert directly.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import Connection
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: None = None
depends_on: None = None

# Reused (not imported) from 0005 - migrations stay self-contained: the same five
# role vocabulary the care tables enforce, copied here for the staging CHECK.
_ROLE_CHECK = (
    "role IN ('physician', 'physiotherapist', 'personal_trainer', "
    "'massage_therapist', 'nutrition_coach')"
)

# Frozen table/column handles for the data migration. Deliberately NOT imported
# from app.care.orm: a migration is a snapshot in time and must not drift with the
# ORM. Only the columns the backfill reads/writes are declared.
_legacy = sa.table(
    "legacy_provider_links",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("client_id", postgresql.UUID(as_uuid=True)),
    sa.column("provider_id", postgresql.UUID(as_uuid=True)),
    sa.column("role", sa.String()),
    sa.column("managing_org_id", postgresql.UUID(as_uuid=True)),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("migrated_episode_id", postgresql.UUID(as_uuid=True)),
)
_episodes = sa.table(
    "episodes",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("client_id", postgresql.UUID(as_uuid=True)),
    sa.column("reason", sa.Text()),
    sa.column("status", sa.String()),
    sa.column("managing_org_id", postgresql.UUID(as_uuid=True)),
    sa.column("opened_at", sa.DateTime(timezone=True)),
    sa.column("closed_at", sa.DateTime(timezone=True)),
)
_memberships = sa.table(
    "episode_memberships",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("episode_id", postgresql.UUID(as_uuid=True)),
    sa.column("provider_id", postgresql.UUID(as_uuid=True)),
    sa.column("role", sa.String()),
    sa.column("effective_from", sa.DateTime(timezone=True)),
    sa.column("effective_to", sa.DateTime(timezone=True)),
    sa.column("change_reason", sa.Text()),
)
_responsibility = sa.table(
    "responsibility_assignments",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("episode_id", postgresql.UUID(as_uuid=True)),
    sa.column("provider_id", postgresql.UUID(as_uuid=True)),
    sa.column("effective_from", sa.DateTime(timezone=True)),
    sa.column("effective_to", sa.DateTime(timezone=True)),
    sa.column("change_reason", sa.Text()),
)
_booking = sa.table(
    "booking_contacts",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("episode_id", postgresql.UUID(as_uuid=True)),
    sa.column("provider_id", postgresql.UUID(as_uuid=True)),
    sa.column("effective_from", sa.DateTime(timezone=True)),
    sa.column("effective_to", sa.DateTime(timezone=True)),
    sa.column("change_reason", sa.Text()),
)

# The data revert, as single static SQL statements (offline-renderable). Keyed on
# the backfill's ``migrated_episode_id`` pointer: EVERY episode-scoped child is
# deleted before the parent ``episodes`` row, so the non-cascading FKs never block
# the DELETE. That means the 0005 effective-dated care rows (membership /
# responsibility / booking) AND the 0006 clinical rows (``clinical_records`` /
# ``rehab_assessments``), since a backfilled episode can accrue clinical/rehab rows
# after the backfill. A non-backfilled episode (its id is never a
# ``migrated_episode_id``) is never touched. Shared by downgrade() and
# revert_backfilled_episodes().
_MIGRATED = (
    "SELECT migrated_episode_id FROM legacy_provider_links WHERE migrated_episode_id IS NOT NULL"
)
_REVERT_STATEMENTS: tuple[str, ...] = (
    f"DELETE FROM episode_memberships WHERE episode_id IN ({_MIGRATED})",
    f"DELETE FROM responsibility_assignments WHERE episode_id IN ({_MIGRATED})",
    f"DELETE FROM booking_contacts WHERE episode_id IN ({_MIGRATED})",
    f"DELETE FROM clinical_records WHERE episode_id IN ({_MIGRATED})",
    f"DELETE FROM rehab_assessments WHERE episode_id IN ({_MIGRATED})",
    f"DELETE FROM episodes WHERE id IN ({_MIGRATED})",
)


def backfill_episodes_from_legacy(connection: Connection) -> list[UUID]:
    """Project every un-migrated legacy pairing into one full episode + 3 children.

    For each ``legacy_provider_links`` row with ``migrated_episode_id IS NULL``,
    inserts one ``episodes`` row (``reason='general_care'``, ``status='active'``,
    org + ``opened_at`` sourced from the legacy row) and one open child row in each
    of ``episode_memberships`` (carrying the legacy ``role``),
    ``responsibility_assignments`` and ``booking_contacts`` (the provider as the
    sole responsible + face), all with ``effective_from == opened_at`` and
    ``change_reason='backfill'``; then writes the new episode id back to the legacy
    row. A single open responsibility / face row never trips the per-episode
    no-overlap EXCLUDE. Idempotent (re-running skips already-migrated rows).
    Returns the minted episode ids.
    """
    rows = (
        connection.execute(
            sa.select(
                _legacy.c.id,
                _legacy.c.client_id,
                _legacy.c.provider_id,
                _legacy.c.role,
                _legacy.c.managing_org_id,
                _legacy.c.created_at,
            ).where(_legacy.c.migrated_episode_id.is_(None))
        )
        .mappings()
        .all()  # materialize before the loop: we UPDATE _legacy inside it
    )
    created: list[UUID] = []
    for row in rows:
        episode_id = uuid4()
        opened_at = row["created_at"]
        provider_id = row["provider_id"]
        connection.execute(
            sa.insert(_episodes).values(
                id=episode_id,
                client_id=row["client_id"],
                reason="general_care",
                status="active",
                managing_org_id=row["managing_org_id"],
                opened_at=opened_at,
                closed_at=None,
            )
        )
        connection.execute(
            sa.insert(_memberships).values(
                id=uuid4(),
                episode_id=episode_id,
                provider_id=provider_id,
                role=row["role"],
                effective_from=opened_at,
                effective_to=None,
                change_reason="backfill",
            )
        )
        connection.execute(
            sa.insert(_responsibility).values(
                id=uuid4(),
                episode_id=episode_id,
                provider_id=provider_id,
                effective_from=opened_at,
                effective_to=None,
                change_reason="backfill",
            )
        )
        connection.execute(
            sa.insert(_booking).values(
                id=uuid4(),
                episode_id=episode_id,
                provider_id=provider_id,
                effective_from=opened_at,
                effective_to=None,
                change_reason="backfill",
            )
        )
        connection.execute(
            sa.update(_legacy)
            .where(_legacy.c.id == row["id"])
            .values(migrated_episode_id=episode_id)
        )
        created.append(episode_id)
    return created


def revert_backfilled_episodes(connection: Connection) -> None:
    """Run the data revert (the shared DELETE statements) against ``connection``.

    The DB-test counterpart of downgrade()'s ``op.execute`` revert: deletes every
    backfilled episode and ALL of its children (keyed by ``migrated_episode_id``)
    while preserving any non-backfilled episode. Does NOT drop the staging table.
    """
    for statement in _REVERT_STATEMENTS:
        connection.execute(sa.text(statement))


def upgrade() -> None:
    op.create_table(
        "legacy_provider_links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("managing_org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Bookkeeping pointer set by the backfill: a PLAIN UUID with NO FK, so the
        # downgrade can DELETE the pointed-at episodes without an FK block.
        sa.Column("migrated_episode_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_legacy_provider_links"),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["identities.id"],
            name="fk_legacy_provider_links_client_id_identities",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["identities.id"],
            name="fk_legacy_provider_links_provider_id_identities",
        ),
        sa.ForeignKeyConstraint(
            ["managing_org_id"],
            ["organizations.id"],
            name="fk_legacy_provider_links_managing_org_id_organizations",
        ),
        # One provider per client (one-provider-per-client), enforced at the DB.
        sa.UniqueConstraint("client_id", name="uq_legacy_provider_links_client_id"),
        # Short tokens "role"/"no_self" resolve via the convention to
        # ck_legacy_provider_links_role / ck_legacy_provider_links_no_self.
        sa.CheckConstraint(_ROLE_CHECK, name="role"),
        sa.CheckConstraint("client_id <> provider_id", name="no_self"),
    )
    # The Python-loop backfill cannot render under --sql, so it runs only online.
    # At command.upgrade(head) the legacy table is empty -> a structural no-op.
    if not context.is_offline_mode():
        backfill_episodes_from_legacy(op.get_bind())


def downgrade() -> None:
    # Offline-safe: each revert statement is a single static SQL string (renderable
    # under --sql), then the staging table is dropped LAST so its subquery resolves.
    for statement in _REVERT_STATEMENTS:
        op.execute(statement)
    op.drop_table("legacy_provider_links")
