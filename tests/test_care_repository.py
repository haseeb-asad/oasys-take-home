"""Integration tests for the SQLAlchemy Episode repository (real Postgres, rolled back).

Each test runs inside the per-test transaction (``db_session``) and is rolled back
at teardown, so the shared database stays order-independent (A19). These prove the
Postgres-only behaviour the pure aggregate tests cannot: the whole-aggregate
upsert (root + three child tables), the TIMESTAMPTZ round trip, the role/status/
period CHECK constraints, the foreign keys, and - the crux - that the repository's
TWO-PHASE FLUSH (close-old UPDATE then open-new INSERT) keeps a contiguous
close-old/open-new handoff clear of the NON-deferrable, per-statement
``EXCLUDE USING gist`` no-overlap constraints on responsibility / booking-face.

FK parents (a client Identity, three provider Identities, a managing Organization)
are persisted first via their repositories. Every raw-``IntegrityError`` test makes
that violation its terminal DB action (one per test): the plain ``flush`` poisons
the session, which the per-test rollback then recovers (AM3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.care.domain.episode import Episode, EpisodeStatus
from app.care.domain.exceptions import EpisodeClosed
from app.care.domain.value_objects import Role
from app.care.repository import SqlAlchemyEpisodeRepository
from app.identity.domain.entities import Identity
from app.identity.repository import SqlAlchemyIdentityRepository
from app.organization.domain.entities import Organization
from app.organization.domain.value_objects import OrgType
from app.organization.repository import SqlAlchemyOrganizationRepository


def _t(weeks: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(weeks=weeks)


_T0, _T1, _T2, _T3, _T4 = _t(0), _t(1), _t(2), _t(3), _t(4)


def _persist_identity(session: Session, email: str) -> UUID:
    identity = Identity(
        id=uuid4(),
        email=email,
        display_name="Person",
        password_hash="stub-hash",
        created_at=_T0,
    )
    SqlAlchemyIdentityRepository(session).add(identity)
    return identity.id


def _persist_org(session: Session) -> UUID:
    org = Organization(id=uuid4(), name="Acme Clinic", type=OrgType.CLINIC, created_at=_T0)
    SqlAlchemyOrganizationRepository(session).add(org)
    return org.id


@dataclass(frozen=True)
class _World:
    client_id: UUID
    org_id: UUID
    provider_a: UUID
    provider_b: UUID
    provider_c: UUID


def _world(session: Session) -> _World:
    """Persist all FK parents an episode needs, with unique emails."""
    suffix = uuid4().hex[:8]
    return _World(
        client_id=_persist_identity(session, f"client-{suffix}@example.com"),
        org_id=_persist_org(session),
        provider_a=_persist_identity(session, f"prov-a-{suffix}@example.com"),
        provider_b=_persist_identity(session, f"prov-b-{suffix}@example.com"),
        provider_c=_persist_identity(session, f"prov-c-{suffix}@example.com"),
    )


def _open(world: _World, **overrides: object) -> Episode:
    """An episode opened at _T0 with provider A as responsible + face + member."""
    kwargs: dict[str, object] = {
        "id": uuid4(),
        "client_id": world.client_id,
        "reason": "shoulder_rehab",
        "managing_org_id": world.org_id,
        "now": _T0,
        "responsible_provider_id": world.provider_a,
        "responsible_role": Role.PHYSIOTHERAPIST,
        "change_reason": "opened",
    }
    kwargs.update(overrides)
    return Episode.open(**kwargs)  # type: ignore[arg-type]


# --- save -> get round trips -------------------------------------------------


def test_save_then_get_open_episode_round_trip(db_session: Session) -> None:
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = _open(world)
    repo.save(episode)
    db_session.expunge_all()
    reloaded = repo.get(episode.id)
    assert reloaded is not None
    assert reloaded.id == episode.id
    assert reloaded.client_id == world.client_id
    assert reloaded.reason == "shoulder_rehab"
    assert reloaded.managing_org_id == world.org_id
    assert reloaded.status is EpisodeStatus.ACTIVE
    assert reloaded.opened_at == _T0
    assert reloaded.closed_at is None
    # A is member + responsible + face at t0.
    responsibility = reloaded.current_responsibility(_T0)
    face = reloaded.current_face(_T0)
    assert responsibility is not None and responsibility.provider_id == world.provider_a
    assert face is not None and face.provider_id == world.provider_a
    assert reloaded.is_current_member(world.provider_a, _T0) is True
    # TIMESTAMPTZ survives the round trip.
    assert reloaded.opened_at.tzinfo is not None
    assert responsibility.period.effective_from.tzinfo is not None


def test_save_get_divergent_face_round_trip(db_session: Session) -> None:
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = _open(world, face_provider_id=world.provider_b, face_role=Role.PHYSICIAN)
    repo.save(episode)
    db_session.expunge_all()
    reloaded = repo.get(episode.id)
    assert reloaded is not None
    responsibility = reloaded.current_responsibility(_T0)
    face = reloaded.current_face(_T0)
    assert responsibility is not None and responsibility.provider_id == world.provider_a
    assert face is not None and face.provider_id == world.provider_b
    # Both the responsible and the divergent face are members.
    assert reloaded.is_current_member(world.provider_a, _T0) is True
    assert reloaded.is_current_member(world.provider_b, _T0) is True
    assert len(reloaded.memberships) == 2


def test_get_missing_returns_none(db_session: Session) -> None:
    repo = SqlAlchemyEpisodeRepository(db_session)
    assert repo.get(uuid4()) is None


def test_save_persists_added_member(db_session: Session) -> None:
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = _open(world)
    repo.save(episode)
    db_session.expunge_all()
    loaded = repo.get(episode.id)
    assert loaded is not None
    loaded.add_member(
        provider_id=world.provider_b, role=Role.NUTRITION_COACH, now=_T1, change_reason="add b"
    )
    repo.save(loaded)
    db_session.expunge_all()
    reloaded = repo.get(episode.id)
    assert reloaded is not None
    assert reloaded.is_current_member(world.provider_b, _T1) is True
    b_rows = [m for m in reloaded.memberships if m.provider_id == world.provider_b]
    assert len(b_rows) == 1
    assert b_rows[0].role is Role.NUTRITION_COACH


def test_start_coverage_membership_round_trip(db_session: Session) -> None:
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = _open(world)
    episode.start_coverage(
        provider_id=world.provider_b,
        role=Role.MASSAGE_THERAPIST,
        effective_from=_T1,
        effective_to=_T3,
        now=_T0,
        change_reason="covering",
    )
    repo.save(episode)
    db_session.expunge_all()
    reloaded = repo.get(episode.id)
    assert reloaded is not None
    b_rows = [m for m in reloaded.memberships if m.provider_id == world.provider_b]
    assert len(b_rows) == 1
    assert b_rows[0].period.effective_from == _T1
    assert b_rows[0].period.effective_to == _T3
    assert b_rows[0].role is Role.MASSAGE_THERAPIST


def test_save_is_idempotent_no_op(db_session: Session) -> None:
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = _open(world)
    repo.save(episode)
    # Saving the same unchanged aggregate again must not duplicate rows or trip the
    # per-statement EXCLUDE (the diff matches every child id -> no inserts).
    repo.save(episode)
    db_session.expunge_all()
    reloaded = repo.get(episode.id)
    assert reloaded is not None
    assert len(reloaded.memberships) == 1
    assert len(reloaded.responsibility) == 1
    assert len(reloaded.faces) == 1


def test_all_membership_roles_accepted(db_session: Session) -> None:
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = _open(world)  # provider A is a PHYSIOTHERAPIST member
    for role in (r for r in Role if r is not Role.PHYSIOTHERAPIST):
        provider_id = _persist_identity(
            db_session, f"role-{role.value}-{uuid4().hex[:6]}@example.com"
        )
        episode.add_member(provider_id=provider_id, role=role, now=_T1, change_reason="role")
    repo.save(episode)
    db_session.expunge_all()
    reloaded = repo.get(episode.id)
    assert reloaded is not None
    assert {m.role for m in reloaded.memberships} == set(Role)


# --- the EXCLUDE crux: contiguous close-old / open-new via the two-phase flush


def test_reassign_responsible_close_old_open_new_round_trip(db_session: Session) -> None:
    # THE EXCLUDE CRUX. A reassignment closes A's open responsibility [t0, None) ->
    # [t0, t2) and opens B's [t2, None). A naive single flush would INSERT B's open
    # row while A's was still open -> a transient overlap the non-deferrable EXCLUDE
    # rejects. The two-phase flush (close then open) keeps them disjoint at t2.
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = _open(world)
    repo.save(episode)
    db_session.expunge_all()
    loaded = repo.get(episode.id)
    assert loaded is not None
    loaded.add_member(
        provider_id=world.provider_b, role=Role.PHYSICIAN, now=_T1, change_reason="add b"
    )
    loaded.assign_responsible(provider_id=world.provider_b, now=_T2, change_reason="handoff")
    repo.save(loaded)  # must NOT raise IntegrityError on responsibility_assignments_no_overlap
    db_session.expunge_all()
    reloaded = repo.get(episode.id)
    assert reloaded is not None
    responsibility = sorted(reloaded.responsibility, key=lambda r: r.period.effective_from)
    assert len(responsibility) == 2
    assert responsibility[0].provider_id == world.provider_a
    assert responsibility[0].period.effective_to == _T2  # old row closed at the boundary
    assert responsibility[1].provider_id == world.provider_b
    assert responsibility[1].period.effective_to is None  # new row open
    current = reloaded.current_responsibility(_T2)
    assert current is not None and current.provider_id == world.provider_b


def test_set_face_handoff_round_trip(db_session: Session) -> None:
    # The booking-face analogue of the EXCLUDE crux (booking_contacts_no_overlap).
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = _open(world)
    repo.save(episode)
    db_session.expunge_all()
    loaded = repo.get(episode.id)
    assert loaded is not None
    loaded.add_member(
        provider_id=world.provider_b, role=Role.PHYSICIAN, now=_T1, change_reason="add b"
    )
    loaded.set_face(provider_id=world.provider_b, now=_T2, change_reason="face handoff")
    repo.save(loaded)
    db_session.expunge_all()
    reloaded = repo.get(episode.id)
    assert reloaded is not None
    faces = sorted(reloaded.faces, key=lambda f: f.period.effective_from)
    assert len(faces) == 2
    assert faces[0].provider_id == world.provider_a
    assert faces[0].period.effective_to == _T2
    assert faces[1].provider_id == world.provider_b
    assert faces[1].period.effective_to is None
    current = reloaded.current_face(_T2)
    assert current is not None and current.provider_id == world.provider_b


def test_combined_membership_and_face_handoff_round_trip(db_session: Session) -> None:
    # AM2: A starts responsible AND the face. B joins; responsibility moves to B;
    # then A (still the face) leaves, handing the face to B in ONE end_member call.
    # The single save closes A's membership + face + responsibility and opens B's
    # face + responsibility - the strongest combined diff + two-phase + EXCLUDE case.
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = _open(world)
    repo.save(episode)
    db_session.expunge_all()
    loaded = repo.get(episode.id)
    assert loaded is not None
    loaded.add_member(
        provider_id=world.provider_b, role=Role.PHYSICIAN, now=_T1, change_reason="add b"
    )
    loaded.assign_responsible(provider_id=world.provider_b, now=_T2, change_reason="resp handoff")
    loaded.end_member(
        provider_id=world.provider_a,
        effective_to=_T3,
        now=_T3,
        change_reason="A leaves",
        successor_face_id=world.provider_b,
    )
    repo.save(loaded)
    db_session.expunge_all()
    reloaded = repo.get(episode.id)
    assert reloaded is not None
    # A's membership closed at t3.
    a_membership = [m for m in reloaded.memberships if m.provider_id == world.provider_a]
    assert len(a_membership) == 1
    assert a_membership[0].period.effective_to == _T3
    # Face: old A face closed at t3, new B face open from t3, B is the current face.
    faces = sorted(reloaded.faces, key=lambda f: f.period.effective_from)
    assert len(faces) == 2
    assert faces[0].provider_id == world.provider_a
    assert faces[0].period.effective_to == _T3
    assert faces[1].provider_id == world.provider_b
    assert faces[1].period.effective_to is None
    current_face = reloaded.current_face(_T3)
    assert current_face is not None and current_face.provider_id == world.provider_b
    # Responsibility was handed off to B earlier (at t2) and survives.
    current_responsibility = reloaded.current_responsibility(_T3)
    assert current_responsibility is not None
    assert current_responsibility.provider_id == world.provider_b
    assert reloaded.is_current_member(world.provider_a, _T4) is False


def test_end_member_closes_membership_round_trip(db_session: Session) -> None:
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = _open(world)
    episode.add_member(
        provider_id=world.provider_b, role=Role.PHYSICIAN, now=_T1, change_reason="add b"
    )
    repo.save(episode)
    db_session.expunge_all()
    loaded = repo.get(episode.id)
    assert loaded is not None
    # B is neither responsible nor the face, so ending is a plain membership close.
    loaded.end_member(provider_id=world.provider_b, effective_to=_T3, now=_T2, change_reason="left")
    repo.save(loaded)
    db_session.expunge_all()
    reloaded = repo.get(episode.id)
    assert reloaded is not None
    b_rows = [m for m in reloaded.memberships if m.provider_id == world.provider_b]
    assert len(b_rows) == 1
    assert b_rows[0].period.effective_to == _T3
    assert reloaded.is_current_member(world.provider_b, _T4) is False


def test_close_episode_then_reload_is_immutable(db_session: Session) -> None:
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = _open(world)
    repo.save(episode)
    db_session.expunge_all()
    loaded = repo.get(episode.id)
    assert loaded is not None
    loaded.close(now=_T3)
    repo.save(loaded)
    db_session.expunge_all()
    reloaded = repo.get(episode.id)
    assert reloaded is not None
    assert reloaded.status is EpisodeStatus.CLOSED
    assert reloaded.closed_at == _T3
    assert reloaded.is_active is False
    # The reloaded closed episode is immutable.
    with pytest.raises(EpisodeClosed):
        reloaded.add_member(
            provider_id=world.provider_b, role=Role.PHYSICIAN, now=_T4, change_reason="late"
        )


# --- EXCLUDE / CHECK / FK rejections (each the test's terminal DB action, AM3) -


def test_exclude_rejects_overlapping_responsibility_raw_insert(db_session: Session) -> None:
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = _open(world)  # responsibility A [t0, None) (open)
    repo.save(episode)
    # A second OPEN responsibility row for the same episode overlaps [t0, None).
    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO responsibility_assignments "
                "(id, episode_id, provider_id, effective_from, effective_to, change_reason) "
                "VALUES (gen_random_uuid(), :episode_id, :provider_id, :ts, NULL, 'overlap')"
            ),
            {"episode_id": episode.id, "provider_id": world.provider_b, "ts": _T1},
        )
    assert "responsibility_assignments_no_overlap" in str(exc_info.value.orig)


def test_exclude_rejects_overlapping_booking_contact_raw_insert(db_session: Session) -> None:
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = _open(world)  # face A [t0, None) (open)
    repo.save(episode)
    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO booking_contacts "
                "(id, episode_id, provider_id, effective_from, effective_to, change_reason) "
                "VALUES (gen_random_uuid(), :episode_id, :provider_id, :ts, NULL, 'overlap')"
            ),
            {"episode_id": episode.id, "provider_id": world.provider_b, "ts": _T1},
        )
    assert "booking_contacts_no_overlap" in str(exc_info.value.orig)


def test_period_check_rejects_zero_length_raw_insert(db_session: Session) -> None:
    # AM1: a zero-length [t, t) range is EMPTY (the EXCLUDE ignores it), so the
    # ``period`` CHECK is what forbids it. Representative on episode_memberships.
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = _open(world)
    repo.save(episode)
    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO episode_memberships "
                "(id, episode_id, provider_id, role, effective_from, effective_to, change_reason) "
                "VALUES (gen_random_uuid(), :episode_id, :provider_id, 'physician', "
                ":ts, :ts, 'zero')"
            ),
            {"episode_id": episode.id, "provider_id": world.provider_b, "ts": _T1},
        )
    assert "ck_episode_memberships_period" in str(exc_info.value.orig)


def test_episodes_status_check_rejects_bad_value_raw_insert(db_session: Session) -> None:
    world = _world(db_session)
    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO episodes (id, client_id, reason, status, managing_org_id, opened_at) "
                "VALUES (gen_random_uuid(), :client_id, 'r', 'paused', :org_id, now())"
            ),
            {"client_id": world.client_id, "org_id": world.org_id},
        )
    assert "ck_episodes_status" in str(exc_info.value.orig)


def test_membership_role_check_rejects_bad_value_raw_insert(db_session: Session) -> None:
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = _open(world)
    repo.save(episode)
    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO episode_memberships "
                "(id, episode_id, provider_id, role, effective_from, effective_to, change_reason) "
                "VALUES (gen_random_uuid(), :episode_id, :provider_id, 'wizard', :ts, NULL, 'bad')"
            ),
            {"episode_id": episode.id, "provider_id": world.provider_b, "ts": _T0},
        )
    assert "ck_episode_memberships_role" in str(exc_info.value.orig)


def test_episode_fk_violation_missing_client_raises(db_session: Session) -> None:
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    # A real provider/org but a client id that is not an identity.
    episode = Episode.open(
        id=uuid4(),
        client_id=uuid4(),
        reason="r",
        managing_org_id=world.org_id,
        now=_T0,
        responsible_provider_id=world.provider_a,
        responsible_role=Role.PHYSICIAN,
        change_reason="opened",
    )
    with pytest.raises(IntegrityError) as exc_info:
        repo.save(episode)
    assert "fk_episodes_client_id_identities" in str(exc_info.value.orig)


def test_episode_fk_violation_missing_managing_org_raises(db_session: Session) -> None:
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    episode = Episode.open(
        id=uuid4(),
        client_id=world.client_id,
        reason="r",
        managing_org_id=uuid4(),
        now=_T0,
        responsible_provider_id=world.provider_a,
        responsible_role=Role.PHYSICIAN,
        change_reason="opened",
    )
    with pytest.raises(IntegrityError) as exc_info:
        repo.save(episode)
    assert "fk_episodes_managing_org_id_organizations" in str(exc_info.value.orig)


def test_child_fk_violation_missing_provider_raises(db_session: Session) -> None:
    world = _world(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    # Real client/org so the root inserts; the responsible provider is not an
    # identity, so a child INSERT (phase B) violates a provider FK.
    episode = Episode.open(
        id=uuid4(),
        client_id=world.client_id,
        reason="r",
        managing_org_id=world.org_id,
        now=_T0,
        responsible_provider_id=uuid4(),
        responsible_role=Role.PHYSICIAN,
        change_reason="opened",
    )
    with pytest.raises(IntegrityError) as exc_info:
        repo.save(episode)
    assert "provider_id_identities" in str(exc_info.value.orig)


def test_child_fk_violation_missing_episode_raises(db_session: Session) -> None:
    world = _world(db_session)
    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO episode_memberships "
                "(id, episode_id, provider_id, role, effective_from, effective_to, change_reason) "
                "VALUES (gen_random_uuid(), :episode_id, :provider_id, 'physician', :ts, NULL, 'x')"
            ),
            {"episode_id": uuid4(), "provider_id": world.provider_a, "ts": _T0},
        )
    assert "fk_episode_memberships_episode_id_episodes" in str(exc_info.value.orig)
