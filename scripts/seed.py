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

Idempotency. ``seed()`` is convergent and never raises on a re-run: each entity is
gated before it is created (identity by email; profile by ``has_active_profile``;
org by its uuid5 id via ``get_by_id``; admin membership by
``has_active_admin_membership``; episode by its uuid5 id via ``get_episode``;
episode member by provider-id over ALL memberships, so the future-dated coverage
row is deduped too). Because the gates short-circuit on existing entities,
re-running with a DIFFERENT ``now`` does NOT rewrite an already-seeded window: the
first run's effective dates stand.

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
from app.care.service import add_member, get_episode, open_episode
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
from app.organization.service import (
    add_staff_membership,
    create_organization,
    has_active_admin_membership,
)

# Deterministic defaults (A16): a fixed clock and a fixed uuid5 namespace, so every
# run produces byte-identical ids/timestamps regardless of wall-clock or machine.
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


# --- per-entity idempotent helpers (each gates, then creates only if absent) ---


def _ensure_identity(
    repo: SqlAlchemyIdentityRepository,
    *,
    email: str,
    display_name: str,
    now: datetime,
) -> UUID:
    """Return the identity id for ``email``, registering it once if absent.

    Gate: ``get_by_email`` (the email carries the only unique business key). The
    deterministic ``new_id`` is used only on the create path.
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
        new_id=_seed_id(f"identity:{email}"),
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
) -> UUID:
    """Ensure an identity and its single active profile; return the identity id."""
    identity_id = _ensure_identity(identity_repo, email=email, display_name=display_name, now=now)
    _ensure_profile(profile_repo, identity_id=identity_id, profile_type=profile_type)
    return identity_id


def _ensure_org(
    repo: SqlAlchemyOrganizationRepository,
    *,
    slug: str,
    name: str,
    org_type: OrgType,
    now: datetime,
) -> UUID:
    """Return the org id for ``slug``, creating it once if absent.

    Gate: ``get_by_id`` on the deterministic uuid5 id (there is no get-by-name).
    """
    org_id = _seed_id(f"org:{slug}")
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
    """Grant an open-ended ADMIN membership from ``now`` if none is active.

    Gate: ``has_active_admin_membership`` (the table has NO unique constraint, so
    the gate is load-bearing against duplicate admin rows on a re-run).
    """
    if has_active_admin_membership(repo, identity_id, org_id, now):
        return
    add_staff_membership(
        repo,
        identity_id=identity_id,
        org_id=org_id,
        role=OrgRole.ADMIN,
        effective_from=now,
        new_id=_seed_id(f"membership:{identity_id}:{org_id}:admin"),
    )


def _ensure_episode(
    repo: SqlAlchemyEpisodeRepository,
    *,
    slug: str,
    client_id: UUID,
    reason: str,
    managing_org_id: UUID,
    responsible_provider_id: UUID,
    responsible_role: Role,
    now: datetime,
) -> Episode:
    """Return the episode for ``slug``, opening it once if absent.

    Gate: ``get_episode`` on the deterministic uuid5 id - which reconstitutes ALL
    child rows (incl. future-dated memberships), so the returned aggregate feeds
    the member gates correctly on a re-run.
    """
    episode_id = _seed_id(f"episode:{slug}")
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
) -> Episode:
    """Add ``provider_id`` as a member if absent; return the (updated) aggregate.

    Gate: existence-by-provider over ALL memberships (time-independent), so a
    future-dated coverage row is deduped on a re-run too. The returned aggregate
    is threaded through successive calls so each gate sees prior additions.
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

    # 1-6: people (identity + single active profile each).
    sara = _ensure_person(
        identity_repo,
        profile_repo,
        email="sara@example.com",
        display_name="Sara Client",
        profile_type=ProfileType.CLIENT,
        now=now,
    )
    mike = _ensure_person(
        identity_repo,
        profile_repo,
        email="mike@example.com",
        display_name="Mike Trainer",
        profile_type=ProfileType.PROVIDER,
        now=now,
    )
    khan = _ensure_person(
        identity_repo,
        profile_repo,
        email="khan@example.com",
        display_name="Dr Khan",
        profile_type=ProfileType.PROVIDER,
        now=now,
    )
    patel = _ensure_person(
        identity_repo,
        profile_repo,
        email="patel@example.com",
        display_name="Dr Patel",
        profile_type=ProfileType.PROVIDER,
        now=now,
    )
    lee = _ensure_person(
        identity_repo,
        profile_repo,
        email="lee@example.com",
        display_name="Dr Lee",
        profile_type=ProfileType.PROVIDER,
        now=now,
    )
    org_admin = _ensure_person(
        identity_repo,
        profile_repo,
        email="admin@example.com",
        display_name="Olivia Admin",
        profile_type=ProfileType.ORG_STAFF,
        now=now,
    )

    # 7-8: organizations (Khan Solo Practice is deliberately staffless).
    fitgym = _ensure_org(org_repo, slug="fitgym", name="FitGym", org_type=OrgType.GYM, now=now)
    khan_solo = _ensure_org(
        org_repo,
        slug="khan_solo",
        name="Khan Solo Practice",
        org_type=OrgType.SOLO_PRACTICE,
        now=now,
    )

    # 9: the first org admin (FitGym only) - the whole point of the seed.
    _ensure_admin_membership(membership_repo, identity_id=org_admin, org_id=fitgym, now=now)

    # 10: General Training (FitGym), Mike responsible -> member+responsible+face.
    general = _ensure_episode(
        episode_repo,
        slug="general_training",
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
        slug="shoulder_rehab",
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
    )

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
