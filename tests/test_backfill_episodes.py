"""Tests for migration 0008: the legacy link table + the episode backfill.

Migration 0008 (a) CREATEs the enriched migration staging table
``legacy_provider_links`` and (b) backfills each legacy one-provider-per-client
pairing into one episode + member + responsible + face. It is purely additive
(no ALTER/DROP of any existing prod table).

The backfill is exercised through the factored, frozen-table function
``backfill_episodes_from_legacy(connection)`` (and the revert through
``revert_backfilled_episodes(connection)``), reached via the digit-prefixed
module object, because ``command.upgrade(head)`` only ever runs the backfill over
an EMPTY legacy table (the session harness upgrades once at start). Each test
runs inside the per-test transaction (``db_session`` joined to ``db_connection``)
and is rolled back at teardown, so the shared dev DB stays order-independent
(A19); rows are scoped to their own ``uuid4`` ids / unique-suffixed emails, never
global counts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.care.domain.value_objects import Role
from app.care.repository import SqlAlchemyEpisodeRepository
from app.care.service import open_episode
from app.organization.domain.value_objects import OrgType
from tests._world import make_identity, make_org

# A fixed tz-aware instant used as the legacy ``created_at`` (and therefore the
# minted episode's ``opened_at`` / every child's ``effective_from``).
_CREATED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _Parents:
    """The FK parents one legacy pairing needs: a client, a provider, an org."""

    client: UUID
    provider: UUID
    org: UUID


def _make_parents(session: Session) -> _Parents:
    """Persist the FK parents (unique emails) and flush so the raw conn sees them."""
    suffix = uuid4().hex[:8]
    parents = _Parents(
        client=make_identity(session, f"client-{suffix}@example.com"),
        provider=make_identity(session, f"prov-{suffix}@example.com"),
        org=make_org(session, f"Org {suffix}", OrgType.CLINIC, _CREATED_AT),
    )
    session.flush()
    return parents


def _insert_legacy_pairing(
    conn: Connection,
    *,
    client: UUID,
    provider: UUID,
    org: UUID,
    role: str = "physiotherapist",
    created_at: datetime = _CREATED_AT,
) -> None:
    """Raw INSERT of one legacy link row (the export shape) via ``sa.text``."""
    conn.execute(
        sa.text(
            "INSERT INTO legacy_provider_links "
            "(id, client_id, provider_id, role, managing_org_id, created_at) "
            "VALUES (:id, :client, :provider, :role, :org, :created_at)"
        ),
        {
            "id": uuid4(),
            "client": client,
            "provider": provider,
            "role": role,
            "org": org,
            "created_at": created_at,
        },
    )


def _backfill_fn(cfg: Config) -> Callable[[Connection], list[UUID]]:
    """Reach ``backfill_episodes_from_legacy`` via the digit-prefixed 0008 module."""
    module = ScriptDirectory.from_config(cfg).get_revision("0008").module
    return cast("Callable[[Connection], list[UUID]]", module.backfill_episodes_from_legacy)


def _revert_fn(cfg: Config) -> Callable[[Connection], None]:
    """Reach ``revert_backfilled_episodes`` via the digit-prefixed 0008 module."""
    module = ScriptDirectory.from_config(cfg).get_revision("0008").module
    return cast("Callable[[Connection], None]", module.revert_backfilled_episodes)


def _count(conn: Connection, table: str, column: str, value: UUID) -> int:
    """Count rows of ``table`` whose ``column`` equals ``value`` (own-row scoped)."""
    return cast(
        int,
        conn.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE {column} = :value"), {"value": value}
        ).scalar_one(),
    )


# --- revision chain + additive schema ---------------------------------------- #


def test_head_is_0008_and_chains_to_0007(alembic_cfg: Config) -> None:
    script = ScriptDirectory.from_config(alembic_cfg)
    assert script.get_current_head() == "0008"
    assert script.get_revision("0008").down_revision == "0007"


def test_legacy_provider_links_table_is_additive(db_engine: sa.Engine) -> None:
    expected_columns = {
        "id",
        "client_id",
        "provider_id",
        "role",
        "managing_org_id",
        "created_at",
        "migrated_episode_id",
    }
    with db_engine.connect() as conn:
        columns = {
            row[0]: row[1]
            for row in conn.execute(
                sa.text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = 'legacy_provider_links'"
                )
            ).all()
        }
        constraints = set(
            conn.scalars(
                sa.text(
                    "SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE table_name = 'legacy_provider_links'"
                )
            ).all()
        )
        episodes_columns = set(
            conn.scalars(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'episodes'"
                )
            ).all()
        )
        exclude_names = set(
            conn.scalars(
                sa.text(
                    "SELECT conname FROM pg_constraint WHERE contype = 'x' "
                    "AND conrelid::regclass::text IN "
                    "('responsibility_assignments', 'booking_contacts')"
                )
            ).all()
        )
        version = conn.scalar(sa.text("SELECT version_num FROM alembic_version"))
    assert set(columns) == expected_columns
    # ``created_at`` is TIMESTAMPTZ (the naive-read trap the value objects reject).
    assert columns["created_at"] == "timestamp with time zone"
    assert {
        "pk_legacy_provider_links",
        "fk_legacy_provider_links_client_id_identities",
        "fk_legacy_provider_links_provider_id_identities",
        "fk_legacy_provider_links_managing_org_id_organizations",
        "uq_legacy_provider_links_client_id",
        "ck_legacy_provider_links_role",
        "ck_legacy_provider_links_no_self",
    } <= constraints
    assert version == "0008"
    # Purely additive: the existing prod schema is untouched (episodes still 7 cols,
    # the two 0005 no-overlap EXCLUDEs still present).
    assert episodes_columns == {
        "id",
        "client_id",
        "reason",
        "status",
        "managing_org_id",
        "opened_at",
        "closed_at",
    }
    assert exclude_names == {"responsibility_assignments_no_overlap", "booking_contacts_no_overlap"}


# --- backfill: row-level + aggregate-level ----------------------------------- #


def test_backfill_creates_one_full_episode_per_pairing(
    db_session: Session, alembic_cfg: Config
) -> None:
    parents = _make_parents(db_session)
    conn = db_session.connection()
    _insert_legacy_pairing(conn, client=parents.client, provider=parents.provider, org=parents.org)
    created = _backfill_fn(alembic_cfg)(conn)
    assert len(created) == 1
    episode_id = created[0]

    episode = (
        conn.execute(
            sa.text(
                "SELECT client_id, reason, status, managing_org_id, opened_at, closed_at "
                "FROM episodes WHERE id = :id"
            ),
            {"id": episode_id},
        )
        .mappings()
        .one()
    )
    assert episode["client_id"] == parents.client
    assert episode["reason"] == "general_care"
    assert episode["status"] == "active"
    assert episode["managing_org_id"] == parents.org
    assert episode["opened_at"] == _CREATED_AT
    assert episode["closed_at"] is None

    membership = (
        conn.execute(
            sa.text(
                "SELECT provider_id, role, effective_from, effective_to, change_reason "
                "FROM episode_memberships WHERE episode_id = :id"
            ),
            {"id": episode_id},
        )
        .mappings()
        .all()
    )
    assert len(membership) == 1
    assert membership[0]["provider_id"] == parents.provider
    assert membership[0]["role"] == "physiotherapist"
    assert membership[0]["effective_from"] == _CREATED_AT
    assert membership[0]["effective_to"] is None
    assert membership[0]["change_reason"] == "backfill"

    for table in ("responsibility_assignments", "booking_contacts"):
        rows = (
            conn.execute(
                sa.text(
                    f"SELECT provider_id, effective_from, effective_to, change_reason "
                    f"FROM {table} WHERE episode_id = :id"
                ),
                {"id": episode_id},
            )
            .mappings()
            .all()
        )
        assert len(rows) == 1
        assert rows[0]["provider_id"] == parents.provider
        assert rows[0]["effective_from"] == _CREATED_AT
        assert rows[0]["effective_to"] is None
        assert rows[0]["change_reason"] == "backfill"


def test_backfilled_episode_reconstitutes_through_care_domain(
    db_session: Session, alembic_cfg: Config
) -> None:
    parents = _make_parents(db_session)
    conn = db_session.connection()
    _insert_legacy_pairing(conn, client=parents.client, provider=parents.provider, org=parents.org)
    (episode_id,) = _backfill_fn(alembic_cfg)(conn)

    episode = SqlAlchemyEpisodeRepository(db_session).get(episode_id)
    assert episode is not None
    assert episode.is_active
    assert episode.is_current_member(parents.provider, _CREATED_AT)
    membership = episode.current_membership(parents.provider, _CREATED_AT)
    assert membership is not None and membership.role is Role.PHYSIOTHERAPIST
    responsibility = episode.current_responsibility(_CREATED_AT)
    assert responsibility is not None and responsibility.provider_id == parents.provider
    face = episode.current_face(_CREATED_AT)
    assert face is not None and face.provider_id == parents.provider
    assert episode.opened_at == _CREATED_AT
    for row in (membership, responsibility, face):
        assert row.period.effective_from == _CREATED_AT == episode.opened_at


def test_backfill_multiple_pairings_one_episode_each(
    db_session: Session, alembic_cfg: Config
) -> None:
    conn = db_session.connection()
    suffix = uuid4().hex[:8]
    org = make_org(db_session, f"Org {suffix}", OrgType.CLINIC, _CREATED_AT)
    expected: dict[UUID, UUID] = {}
    for index in range(3):
        client = make_identity(db_session, f"client-{index}-{suffix}@example.com")
        provider = make_identity(db_session, f"prov-{index}-{suffix}@example.com")
        expected[client] = provider
    db_session.flush()
    for client, provider in expected.items():
        _insert_legacy_pairing(conn, client=client, provider=provider, org=org)

    created = _backfill_fn(alembic_cfg)(conn)
    assert len(created) == 3
    assert len(set(created)) == 3
    for episode_id in created:
        episode_client = conn.execute(
            sa.text("SELECT client_id FROM episodes WHERE id = :id"), {"id": episode_id}
        ).scalar_one()
        member = conn.execute(
            sa.text("SELECT provider_id FROM episode_memberships WHERE episode_id = :id"),
            {"id": episode_id},
        ).scalar_one()
        responsible = conn.execute(
            sa.text("SELECT provider_id FROM responsibility_assignments WHERE episode_id = :id"),
            {"id": episode_id},
        ).scalar_one()
        face = conn.execute(
            sa.text("SELECT provider_id FROM booking_contacts WHERE episode_id = :id"),
            {"id": episode_id},
        ).scalar_one()
        assert expected[episode_client] == member == responsible == face


def test_backfill_empty_legacy_is_noop(db_session: Session, alembic_cfg: Config) -> None:
    conn = db_session.connection()
    before = conn.execute(sa.text("SELECT count(*) FROM episodes")).scalar_one()
    created = _backfill_fn(alembic_cfg)(conn)
    after = conn.execute(sa.text("SELECT count(*) FROM episodes")).scalar_one()
    assert created == []
    assert before == after


def test_backfill_is_idempotent(db_session: Session, alembic_cfg: Config) -> None:
    parents = _make_parents(db_session)
    conn = db_session.connection()
    _insert_legacy_pairing(conn, client=parents.client, provider=parents.provider, org=parents.org)
    backfill = _backfill_fn(alembic_cfg)
    first = backfill(conn)
    second = backfill(conn)
    assert len(first) == 1
    assert second == []
    episode_id = first[0]
    assert _count(conn, "episodes", "id", episode_id) == 1
    assert _count(conn, "episode_memberships", "episode_id", episode_id) == 1
    assert _count(conn, "responsibility_assignments", "episode_id", episode_id) == 1
    assert _count(conn, "booking_contacts", "episode_id", episode_id) == 1
    migrated = conn.execute(
        sa.text("SELECT migrated_episode_id FROM legacy_provider_links WHERE client_id = :c"),
        {"c": parents.client},
    ).scalar_one()
    assert migrated == episode_id


# --- backfill respects the EXCLUDE no-overlap constraints (split, AM3) -------- #


def test_backfilled_responsibility_is_sole_open_holder(
    db_session: Session, alembic_cfg: Config
) -> None:
    # The backfill's single open responsibility row makes a deliberate SECOND open
    # row for the same episode overlap -> the non-deferrable EXCLUDE rejects it.
    # Split from the booking case: in Postgres the first IntegrityError aborts the
    # transaction, so each EXCLUDE violation needs its own test (AM3).
    parents = _make_parents(db_session)
    conn = db_session.connection()
    _insert_legacy_pairing(conn, client=parents.client, provider=parents.provider, org=parents.org)
    (episode_id,) = _backfill_fn(alembic_cfg)(conn)
    with pytest.raises(IntegrityError) as exc_info:
        conn.execute(
            sa.text(
                "INSERT INTO responsibility_assignments "
                "(id, episode_id, provider_id, effective_from, effective_to, change_reason) "
                "VALUES (gen_random_uuid(), :episode_id, :provider_id, :ts, NULL, 'overlap')"
            ),
            {
                "episode_id": episode_id,
                "provider_id": parents.provider,
                "ts": _CREATED_AT + timedelta(days=1),
            },
        )
    assert "responsibility_assignments_no_overlap" in str(exc_info.value.orig)


def test_backfilled_face_is_sole_open_holder(db_session: Session, alembic_cfg: Config) -> None:
    # The booking-face analogue of the EXCLUDE crux (its own transaction, AM3).
    parents = _make_parents(db_session)
    conn = db_session.connection()
    _insert_legacy_pairing(conn, client=parents.client, provider=parents.provider, org=parents.org)
    (episode_id,) = _backfill_fn(alembic_cfg)(conn)
    with pytest.raises(IntegrityError) as exc_info:
        conn.execute(
            sa.text(
                "INSERT INTO booking_contacts "
                "(id, episode_id, provider_id, effective_from, effective_to, change_reason) "
                "VALUES (gen_random_uuid(), :episode_id, :provider_id, :ts, NULL, 'overlap')"
            ),
            {
                "episode_id": episode_id,
                "provider_id": parents.provider,
                "ts": _CREATED_AT + timedelta(days=1),
            },
        )
    assert "booking_contacts_no_overlap" in str(exc_info.value.orig)


# --- downgrade / revert: deletes backfilled, preserves pre-existing ---------- #


def test_revert_deletes_backfilled_but_preserves_preexisting(
    db_session: Session, alembic_cfg: Config
) -> None:
    parents = _make_parents(db_session)
    conn = db_session.connection()
    repo = SqlAlchemyEpisodeRepository(db_session)

    # A SEPARATE pre-existing episode (created via the repository, NOT backfilled):
    # the revert must preserve it AND its three children.
    pre_provider = make_identity(db_session, f"pre-prov-{uuid4().hex[:8]}@example.com")
    db_session.flush()
    pre_episode = open_episode(
        repo,
        client_id=parents.client,
        reason="pre-existing",
        managing_org_id=parents.org,
        responsible_provider_id=pre_provider,
        responsible_role=Role.PHYSICIAN,
        change_reason="opened",
        now=_CREATED_AT,
        new_id=uuid4(),
    )
    db_session.flush()

    # A backfilled pairing (a DIFFERENT client, so UNIQUE(client_id) is happy).
    bf_client = make_identity(db_session, f"bf-client-{uuid4().hex[:8]}@example.com")
    db_session.flush()
    _insert_legacy_pairing(conn, client=bf_client, provider=parents.provider, org=parents.org)
    (backfilled_id,) = _backfill_fn(alembic_cfg)(conn)

    # A legacy row that was NEVER migrated (migrated_episode_id IS NULL): inserted
    # AFTER the backfill so it stays unmigrated, and must be IGNORED by the revert.
    null_client = make_identity(db_session, f"null-client-{uuid4().hex[:8]}@example.com")
    db_session.flush()
    _insert_legacy_pairing(conn, client=null_client, provider=parents.provider, org=parents.org)

    _revert_fn(alembic_cfg)(conn)

    # The backfilled episode + all three children are gone.
    assert _count(conn, "episodes", "id", backfilled_id) == 0
    assert _count(conn, "episode_memberships", "episode_id", backfilled_id) == 0
    assert _count(conn, "responsibility_assignments", "episode_id", backfilled_id) == 0
    assert _count(conn, "booking_contacts", "episode_id", backfilled_id) == 0
    # The pre-existing episode + its three children survive untouched.
    assert _count(conn, "episodes", "id", pre_episode.id) == 1
    assert _count(conn, "episode_memberships", "episode_id", pre_episode.id) == 1
    assert _count(conn, "responsibility_assignments", "episode_id", pre_episode.id) == 1
    assert _count(conn, "booking_contacts", "episode_id", pre_episode.id) == 1
    # The never-migrated legacy row is left alone (still NULL pointer, no deletion).
    null_pointer = conn.execute(
        sa.text("SELECT migrated_episode_id FROM legacy_provider_links WHERE client_id = :c"),
        {"c": null_client},
    ).scalar_one()
    assert null_pointer is None


def test_revert_deletes_clinical_children_of_backfilled_episode(
    db_session: Session, alembic_cfg: Config
) -> None:
    # A backfilled episode can accrue clinical_records / rehab_assessments (0006,
    # non-cascading FK -> episodes) AFTER the backfill. The revert must delete those
    # children too, else DELETE FROM episodes hits the FK. Regression: the 0006
    # episode children must be in the revert, not just the 0005 care rows.
    parents = _make_parents(db_session)
    conn = db_session.connection()
    _insert_legacy_pairing(conn, client=parents.client, provider=parents.provider, org=parents.org)
    (episode_id,) = _backfill_fn(alembic_cfg)(conn)
    for table in ("clinical_records", "rehab_assessments"):
        conn.execute(
            sa.text(
                f"INSERT INTO {table} (id, episode_id, author_provider_id, body, created_at) "
                "VALUES (gen_random_uuid(), :episode_id, :author, 'note', :ts)"
            ),
            {"episode_id": episode_id, "author": parents.provider, "ts": _CREATED_AT},
        )
    # Must NOT raise (children deleted before the parent) and must remove them.
    _revert_fn(alembic_cfg)(conn)
    assert _count(conn, "episodes", "id", episode_id) == 0
    assert _count(conn, "clinical_records", "episode_id", episode_id) == 0
    assert _count(conn, "rehab_assessments", "episode_id", episode_id) == 0


# --- legacy-table constraint behaviour (edges + defaults) -------------------- #


def test_legacy_table_rejects_self_pairing(db_session: Session) -> None:
    parents = _make_parents(db_session)
    conn = db_session.connection()
    with pytest.raises(IntegrityError) as exc_info:
        conn.execute(
            sa.text(
                "INSERT INTO legacy_provider_links "
                "(id, client_id, provider_id, role, managing_org_id, created_at) "
                "VALUES (:id, :person, :person, 'physiotherapist', :org, :ts)"
            ),
            {"id": uuid4(), "person": parents.client, "org": parents.org, "ts": _CREATED_AT},
        )
    assert "ck_legacy_provider_links_no_self" in str(exc_info.value.orig)


def test_legacy_table_enforces_one_provider_per_client(db_session: Session) -> None:
    parents = _make_parents(db_session)
    other_provider = make_identity(db_session, f"prov2-{uuid4().hex[:8]}@example.com")
    db_session.flush()
    conn = db_session.connection()
    _insert_legacy_pairing(conn, client=parents.client, provider=parents.provider, org=parents.org)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_legacy_pairing(
            conn, client=parents.client, provider=other_provider, org=parents.org
        )
    assert "uq_legacy_provider_links_client_id" in str(exc_info.value.orig)


def test_legacy_unknown_reference_rejected(db_session: Session) -> None:
    parents = _make_parents(db_session)
    conn = db_session.connection()
    with pytest.raises(IntegrityError) as exc_info:
        conn.execute(
            sa.text(
                "INSERT INTO legacy_provider_links "
                "(id, client_id, provider_id, role, managing_org_id, created_at) "
                "VALUES (:id, :client, :provider, 'physiotherapist', :org, :ts)"
            ),
            {
                "id": uuid4(),
                "client": parents.client,
                "provider": parents.provider,
                "org": uuid4(),  # a non-existent organization
                "ts": _CREATED_AT,
            },
        )
    assert "fk_legacy_provider_links_managing_org_id_organizations" in str(exc_info.value.orig)


def test_legacy_table_rejects_invalid_role(db_session: Session) -> None:
    parents = _make_parents(db_session)
    conn = db_session.connection()
    with pytest.raises(IntegrityError) as exc_info:
        conn.execute(
            sa.text(
                "INSERT INTO legacy_provider_links "
                "(id, client_id, provider_id, role, managing_org_id, created_at) "
                "VALUES (:id, :client, :provider, 'wizard', :org, :ts)"
            ),
            {
                "id": uuid4(),
                "client": parents.client,
                "provider": parents.provider,
                "org": parents.org,
                "ts": _CREATED_AT,
            },
        )
    assert "ck_legacy_provider_links_role" in str(exc_info.value.orig)


def test_legacy_row_defaults(db_session: Session) -> None:
    # ``migrated_episode_id`` defaults to NULL (so the backfill's idempotency guard
    # sees fresh rows); ``id`` + ``created_at`` server defaults populate themselves.
    parents = _make_parents(db_session)
    conn = db_session.connection()
    conn.execute(
        sa.text(
            "INSERT INTO legacy_provider_links (client_id, provider_id, role, managing_org_id) "
            "VALUES (:client, :provider, 'physiotherapist', :org)"
        ),
        {"client": parents.client, "provider": parents.provider, "org": parents.org},
    )
    row = (
        conn.execute(
            sa.text(
                "SELECT id, created_at, migrated_episode_id "
                "FROM legacy_provider_links WHERE client_id = :c"
            ),
            {"c": parents.client},
        )
        .mappings()
        .one()
    )
    assert row["id"] is not None
    assert row["created_at"] is not None
    assert row["migrated_episode_id"] is None


# --- offline (--sql) renderability (D12 + offline-safe downgrade) ------------ #


def test_0008_upgrade_offline_emits_create_table_and_skips_backfill(
    offline_env: None, alembic_cfg: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    command.upgrade(alembic_cfg, "head", sql=True)
    out = capsys.readouterr().out
    assert "CREATE TABLE legacy_provider_links" in out
    # The Python-loop backfill is guarded by ``not context.is_offline_mode()``, so
    # no data-migration INSERT is ever rendered under --sql.
    assert "INSERT INTO episodes" not in out


def test_0008_downgrade_offline_emits_deletes_before_drop(
    offline_env: None, alembic_cfg: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    command.downgrade(alembic_cfg, "0008:0007", sql=True)
    out = capsys.readouterr().out
    assert "DELETE FROM episodes" in out
    # Every episode-scoped child delete is rendered too (0005 care + 0006 clinical).
    assert "DELETE FROM clinical_records" in out
    assert "DELETE FROM rehab_assessments" in out
    assert "DROP TABLE legacy_provider_links" in out
    # Children-and-parent deletes are emitted BEFORE the table is dropped (so the
    # ``migrated_episode_id`` subquery is still resolvable).
    assert out.index("DELETE FROM episodes") < out.index("DROP TABLE legacy_provider_links")
