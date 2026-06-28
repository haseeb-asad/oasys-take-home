"""Idempotent "Sara world" seed (A16): the by-design first-org-admin bootstrap.

There is no admin-assignment API, so this script is the deterministic, re-runnable
way the initial world is provisioned. It is a thin composition at the edge: it
constructs the concrete SQLAlchemy repositories from one ``Session`` and drives the
EXISTING application services only - it introduces NO new domain logic and writes
no SQL of its own.

Determinism. Every entity id is ``uuid5(_SEED_NS, key)`` over a canonical stable
slug (e.g. ``org:fitgym``, ``episode:shoulder_rehab``,
``identity:sara@example.com``), and a single ``now`` (default ``SEED_EPOCH``) is
threaded into every service call. The seed path itself never calls
``datetime.now()`` or ``uuid4()`` (the only ``uuid4`` is the domain's own internal
child-row id default inside ``Episode.open`` / ``add_member``, which is never
re-reached once an episode exists).

Idempotency. ``seed()`` is convergent and never raises on a re-run, for ANY
``now``: each entity is gated before it is created (identity by email; profile by
``has_active_profile``; org by its uuid5 id via ``get_by_id``; admin membership by
its deterministic id; episode by its uuid5 id via ``get_episode``; episode member
by provider-id over ALL memberships, so the future-dated coverage row is deduped
too). Every gate is ``now``-independent, so re-running with a DIFFERENT ``now``
does NOT rewrite an already-seeded window: the first run's effective dates stand.

Transactions. ``seed()`` does NOT commit - it composes inside the caller's unit of
work (and the savepoint-joined test harness). ``main()`` is the ONLY place that
opens an engine-backed session, calls ``seed()``, and commits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy.orm import Session

from app.care.domain.episode import Episode
from app.care.domain.value_objects import Role
from app.care.repository import SqlAlchemyEpisodeRepository
from app.care.service import add_member, close_episode, get_episode, open_episode
from app.core.database import get_sessionmaker
from app.identity.domain.value_objects import ProfileType
from app.identity.repository import (
    SqlAlchemyIdentityRepository,
    SqlAlchemyProfileRepository,
)
from app.identity.service import create_profile, has_active_profile, register
from app.organization.domain.value_objects import OrgRole, OrgType
from app.organization.repository import (
    SqlAlchemyOrganizationRepository,
    SqlAlchemyOrgStaffMembershipRepository,
)
from app.organization.service import add_staff_membership, create_organization

# Deterministic defaults (A16): a fixed clock and a fixed uuid5 namespace, so the
# ids the seed assigns (identities, profiles, orgs, memberships, episodes) and all
# timestamps are byte-identical across runs and machines. The care aggregate's own
# child rows (membership/responsibility/face) keep their domain-assigned uuid4 ids,
# which are NOT stable across fresh databases; that is by design (the seed adds no
# domain logic) and affects neither idempotency nor the seeded world's shape.
SEED_EPOCH: datetime = datetime(2026, 1, 1, tzinfo=UTC)
_SEED_NS: UUID = UUID("5eed5eed-5eed-5eed-5eed-5eed5eed5eed")

# Obvious non-secret (GitGuardian-safe): the seed creates login rows, but no real
# credential is ever embedded.
_SEED_PASSWORD = "seed-not-a-secret"

# Lee's bounded coverage window on the Shoulder Rehab episode, relative to ``now``.
_COVERAGE_FROM = timedelta(weeks=8)
_COVERAGE_TO = timedelta(weeks=10)


def _seed_id(key: str) -> UUID:
    """Deterministic id for a canonical stable slug ``key`` (uuid5 over the seed NS)."""
    return uuid5(_SEED_NS, key)


@dataclass(frozen=True)
class SaraWorld:
    """The ids of every entity the seed converges to (returned by ``seed``)."""

    sara: UUID
    mike: UUID
    khan: UUID
    patel: UUID
    lee: UUID
    org_admin: UUID
    fitgym: UUID
    khan_solo: UUID
    general: UUID
    shoulder: UUID
    closed: UUID


def world_ids() -> SaraWorld:
    """The seed's deterministic top-level ids, computed WITHOUT a database (A16).

    Pure: every id is ``uuid5(_SEED_NS, key)`` over the SAME canonical slugs
    ``seed()`` uses (``identity:<email>``, ``org:<slug>``, ``episode:<slug>``), so a
    caller that only needs the ids (e.g. the read-only ``/demo`` route) can resolve
    the Sara world without a DB round-trip or re-running the seed. ``seed()`` threads
    these very ids in as its ``new_id``s, and ``test_world_ids_matches_seed`` asserts
    ``world_ids() == seed(db)``, so the precomputed ids and the persisted ids can
    never drift.
    """
    return SaraWorld(
        sara=_seed_id("identity:sara@example.com"),
        mike=_seed_id("identity:mike@example.com"),
        khan=_seed_id("identity:khan@example.com"),
        patel=_seed_id("identity:patel@example.com"),
        lee=_seed_id("identity:lee@example.com"),
        org_admin=_seed_id("identity:admin@example.com"),
        fitgym=_seed_id("org:fitgym"),
        khan_solo=_seed_id("org:khan_solo"),
        general=_seed_id("episode:general_training"),
        shoulder=_seed_id("episode:shoulder_rehab"),
        closed=_seed_id("episode:prior_rehab"),
    )


# --- per-entity idempotent helpers (each gates, then creates only if absent) ---


def _ensure_identity(
    repo: SqlAlchemyIdentityRepository,
    *,
    email: str,
    display_name: str,
    now: datetime,
    new_id: UUID,
) -> UUID:
    """Return the identity id for ``email``, registering it once if absent.

    Gate: ``get_by_email`` (the email carries the only unique business key). The
    deterministic ``new_id`` (supplied by ``world_ids()``) is used only on the
    create path.
    """
    existing = repo.get_by_email(email)
    if existing is not None:
        return existing.id
    identity = register(
        repo,
        email,
        display_name,
        _SEED_PASSWORD,
        now=now,
        new_id=new_id,
    )
    return identity.id


def _ensure_profile(
    repo: SqlAlchemyProfileRepository,
    *,
    identity_id: UUID,
    profile_type: ProfileType,
) -> None:
    """Create one active profile of ``profile_type`` for ``identity_id`` if absent.

    Gate: ``has_active_profile`` (profiles have NO unique constraint, so the gate
    is load-bearing - without it a re-run would append a duplicate active row).
    """
    if has_active_profile(repo, identity_id, profile_type):
        return
    create_profile(
        repo,
        identity_id=identity_id,
        profile_type=profile_type,
        new_id=_seed_id(f"profile:{identity_id}:{profile_type.value}"),
    )


def _ensure_person(
    identity_repo: SqlAlchemyIdentityRepository,
    profile_repo: SqlAlchemyProfileRepository,
    *,
    email: str,
    display_name: str,
    profile_type: ProfileType,
    now: datetime,
    new_id: UUID,
) -> UUID:
    """Ensure an identity and its single active profile; return the identity id."""
    identity_id = _ensure_identity(
        identity_repo, email=email, display_name=display_name, now=now, new_id=new_id
    )
    _ensure_profile(profile_repo, identity_id=identity_id, profile_type=profile_type)
    return identity_id


def _ensure_org(
    repo: SqlAlchemyOrganizationRepository,
    *,
    org_id: UUID,
    name: str,
    org_type: OrgType,
    now: datetime,
) -> UUID:
    """Return the org id, creating it once if absent.

    Gate: ``get_by_id`` on the deterministic uuid5 id (supplied by ``world_ids()``;
    there is no get-by-name).
    """
    if repo.get_by_id(org_id) is not None:
        return org_id
    create_organization(repo, name, org_type, now=now, new_id=org_id)
    return org_id


def _ensure_admin_membership(
    repo: SqlAlchemyOrgStaffMembershipRepository,
    *,
    identity_id: UUID,
    org_id: UUID,
    now: datetime,
) -> None:
    """Grant an open-ended ADMIN membership from ``now`` if not already seeded.

    Gate: the deterministic membership id over ALL rows for the pair (``list_for``
    applies no time filter), exactly like the org/episode gates. A time-based gate
    (``has_active_admin_membership(now)``) would be WRONG here: a re-run with a
    ``now`` BEFORE the seeded ``effective_from`` would not see the row as active,
    retry the insert with the same deterministic id, and collide on the primary
    key. Gating by id is ``now``-independent, so the seed converges and never raises
    for any ``now``; the table's lack of a unique constraint makes it load-bearing.
    """
    membership_id = _seed_id(f"membership:{identity_id}:{org_id}:admin")
    if any(membership.id == membership_id for membership in repo.list_for(identity_id, org_id)):
        return
    add_staff_membership(
        repo,
        identity_id=identity_id,
        org_id=org_id,
        role=OrgRole.ADMIN,
        effective_from=now,
        new_id=membership_id,
    )


def _ensure_episode(
    repo: SqlAlchemyEpisodeRepository,
    *,
    episode_id: UUID,
    client_id: UUID,
    reason: str,
    managing_org_id: UUID,
    responsible_provider_id: UUID,
    responsible_role: Role,
    now: datetime,
) -> Episode:
    """Return the episode for the deterministic ``episode_id``, opening it once if absent.

    Gate: ``get_episode`` on the deterministic uuid5 id (supplied by ``world_ids()``)
    - which reconstitutes ALL child rows (incl. future-dated memberships), so the
    returned aggregate feeds the member gates correctly on a re-run.
    """
    existing = get_episode(repo, episode_id)
    if existing is not None:
        return existing
    return open_episode(
        repo,
        client_id=client_id,
        reason=reason,
        managing_org_id=managing_org_id,
        responsible_provider_id=responsible_provider_id,
        responsible_role=responsible_role,
        change_reason="opened",
        now=now,
        new_id=episode_id,
    )


def _ensure_member(
    repo: SqlAlchemyEpisodeRepository,
    episode: Episode,
    *,
    provider_id: UUID,
    role: Role,
    change_reason: str,
    now: datetime,
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
    covering_for: UUID | None = None,
) -> Episode:
    """Add ``provider_id`` as a member if absent; return the (updated) aggregate.

    Gate: existence-by-provider over ALL memberships (time-independent), so a
    future-dated coverage row is deduped on a re-run too. The returned aggregate
    is threaded through successive calls so each gate sees prior additions.
    ``covering_for`` routes the creation through ``add_coverage`` when set (same
    semantics as the API path; see ``service.add_member``).
    """
    if any(m.provider_id == provider_id for m in episode.memberships):
        return episode
    return add_member(
        repo,
        episode,
        provider_id=provider_id,
        role=role,
        change_reason=change_reason,
        now=now,
        effective_from=effective_from,
        effective_to=effective_to,
        covering_for=covering_for,
    )


def seed(session: Session, *, now: datetime = SEED_EPOCH) -> SaraWorld:
    """Build the Sara world idempotently via the application services; NEVER commits.

    Convergent and re-runnable (A16): every step gates before it creates, so a
    second run in the same session adds no rows and raises nothing. Re-running with
    a different ``now`` does not rewrite already-seeded windows (the gates
    short-circuit on existing entities). Persistence is left to the caller's unit
    of work; ``seed`` itself issues no ``commit`` (so it composes inside the
    savepoint test harness).
    """
    identity_repo = SqlAlchemyIdentityRepository(session)
    profile_repo = SqlAlchemyProfileRepository(session)
    org_repo = SqlAlchemyOrganizationRepository(session)
    membership_repo = SqlAlchemyOrgStaffMembershipRepository(session)
    episode_repo = SqlAlchemyEpisodeRepository(session)

    # The deterministic top-level ids, computed DB-free. seed() threads them in as
    # its new_ids, so what it persists is exactly what world_ids() returns (the
    # drift test asserts the equality).
    ids = world_ids()

    # 1-6: people (identity + single active profile each).
    sara = _ensure_person(
        identity_repo,
        profile_repo,
        email="sara@example.com",
        display_name="Sara Client",
        profile_type=ProfileType.CLIENT,
        now=now,
        new_id=ids.sara,
    )
    mike = _ensure_person(
        identity_repo,
        profile_repo,
        email="mike@example.com",
        display_name="Mike Trainer",
        profile_type=ProfileType.PROVIDER,
        now=now,
        new_id=ids.mike,
    )
    khan = _ensure_person(
        identity_repo,
        profile_repo,
        email="khan@example.com",
        display_name="Dr Khan",
        profile_type=ProfileType.PROVIDER,
        now=now,
        new_id=ids.khan,
    )
    patel = _ensure_person(
        identity_repo,
        profile_repo,
        email="patel@example.com",
        display_name="Dr Patel",
        profile_type=ProfileType.PROVIDER,
        now=now,
        new_id=ids.patel,
    )
    lee = _ensure_person(
        identity_repo,
        profile_repo,
        email="lee@example.com",
        display_name="Dr Lee",
        profile_type=ProfileType.PROVIDER,
        now=now,
        new_id=ids.lee,
    )
    org_admin = _ensure_person(
        identity_repo,
        profile_repo,
        email="admin@example.com",
        display_name="Olivia Admin",
        profile_type=ProfileType.ORG_STAFF,
        now=now,
        new_id=ids.org_admin,
    )

    # 7-8: organizations (Khan Solo Practice is deliberately staffless).
    fitgym = _ensure_org(org_repo, org_id=ids.fitgym, name="FitGym", org_type=OrgType.GYM, now=now)
    khan_solo = _ensure_org(
        org_repo,
        org_id=ids.khan_solo,
        name="Khan Solo Practice",
        org_type=OrgType.SOLO_PRACTICE,
        now=now,
    )

    # 9: the first org admin (FitGym only) - the whole point of the seed.
    _ensure_admin_membership(membership_repo, identity_id=org_admin, org_id=fitgym, now=now)

    # 10: General Training (FitGym), Mike responsible -> member+responsible+face.
    general = _ensure_episode(
        episode_repo,
        episode_id=ids.general,
        client_id=sara,
        reason="general_training",
        managing_org_id=fitgym,
        responsible_provider_id=mike,
        responsible_role=Role.PERSONAL_TRAINER,
        now=now,
    )

    # 11: Shoulder Rehab (Khan Solo Practice), Khan responsible; Patel open, Lee bounded.
    shoulder = _ensure_episode(
        episode_repo,
        episode_id=ids.shoulder,
        client_id=sara,
        reason="shoulder_rehab",
        managing_org_id=khan_solo,
        responsible_provider_id=khan,
        responsible_role=Role.PHYSIOTHERAPIST,
        now=now,
    )
    shoulder = _ensure_member(
        episode_repo,
        shoulder,
        provider_id=patel,
        role=Role.PHYSICIAN,
        change_reason="added physician",
        now=now,
    )
    shoulder = _ensure_member(
        episode_repo,
        shoulder,
        provider_id=lee,
        role=Role.PHYSIOTHERAPIST,
        change_reason="covering for Khan",
        now=now,
        effective_from=now + _COVERAGE_FROM,
        effective_to=now + _COVERAGE_TO,
        covering_for=khan,
    )

    # 12: Prior Rehab (Khan Solo Practice), Khan responsible + face, opened then CLOSED.
    # A previous, now-discharged rehab: it lets the /demo page replay scenario S4 (a
    # closed episode still serves reads to its team, but the PDP suppresses every act).
    # Idempotent in two steps: _ensure_episode gates the OPEN on the deterministic id,
    # and the close is gated on is_active so a re-run (already closed) closes nothing
    # and writes no row. Closing at ``now`` (== opened_at) is valid: Episode.close only
    # guards that the episode is still open, never that closed_at is strictly later, and
    # it leaves the append-only roster rows open as surviving history.
    closed = _ensure_episode(
        episode_repo,
        episode_id=ids.closed,
        client_id=sara,
        reason="prior_rehab",
        managing_org_id=khan_solo,
        responsible_provider_id=khan,
        responsible_role=Role.PHYSIOTHERAPIST,
        now=now,
    )
    if closed.is_active:
        closed = close_episode(episode_repo, closed, now=now)

    return SaraWorld(
        sara=sara,
        mike=mike,
        khan=khan,
        patel=patel,
        lee=lee,
        org_admin=org_admin,
        fitgym=fitgym,
        khan_solo=khan_solo,
        general=general.id,
        shoulder=shoulder.id,
        closed=closed.id,
    )


def main() -> None:
    """Open an engine-backed session, run the idempotent seed, and COMMIT.

    The only place a real commit happens (``seed`` itself never commits). Safe to
    re-run: the per-entity gates make a second invocation a no-op.
    """
    session_factory = get_sessionmaker()
    with session_factory() as session:
        seed(session)
        session.commit()


if __name__ == "__main__":
    main()
