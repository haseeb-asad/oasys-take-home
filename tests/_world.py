"""Shared test world primitives + the named Sara scenario world.

Hosts the five persistence helpers reused by ``test_care_api.py`` (moved here,
same signatures/bodies) plus ``SaraTestWorld`` and ``build_sara_world``: a rich,
Sara-named, Shoulder-Rehab topology persisted via the REAL repositories/services
into the per-test ``db_session`` (A19), with coverage windows anchored to the
fixed ``clock`` so "current at now" flips by time travel, not by reshaping.

``build_sara_world`` mints UNIQUE emails per call (an 8-char uuid suffix) and
``uuid4`` entity ids, so it never collides with the dev DB's committed seed rows
(``sara@example.com`` etc.) and stays order-independent under per-test rollback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.care.domain.clinical import RehabAssessment
from app.care.domain.value_objects import Role
from app.care.repository import (
    SqlAlchemyClinicalRecordRepository,
    SqlAlchemyEpisodeRepository,
    SqlAlchemyRehabAssessmentRepository,
)
from app.care.service import (
    add_clinical_record,
    add_member,
    close_episode,
    open_episode,
)
from app.identity.domain.entities import Identity
from app.identity.domain.value_objects import ProfileType
from app.identity.repository import SqlAlchemyIdentityRepository, SqlAlchemyProfileRepository
from app.identity.service import create_profile
from app.organization.domain.entities import Organization
from app.organization.domain.value_objects import OrgRole, OrgType
from app.organization.repository import (
    SqlAlchemyOrganizationRepository,
    SqlAlchemyOrgStaffMembershipRepository,
)
from app.organization.service import add_staff_membership

# --- persistence helpers (real repos/services, flushed into the txn) --------- #


def make_identity(session: Session, email: str) -> UUID:
    identity = Identity(
        id=uuid4(),
        email=email,
        display_name="Person",
        password_hash="stub-hash",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    SqlAlchemyIdentityRepository(session).add(identity)
    return identity.id


def make_profile(session: Session, identity_id: UUID, profile_type: ProfileType) -> None:
    create_profile(
        SqlAlchemyProfileRepository(session),
        identity_id=identity_id,
        profile_type=profile_type,
        new_id=uuid4(),
    )


def make_org(session: Session, name: str, org_type: OrgType, created_at: datetime) -> UUID:
    org = Organization(id=uuid4(), name=name, type=org_type, created_at=created_at)
    SqlAlchemyOrganizationRepository(session).add(org)
    return org.id


def make_admin_membership(
    session: Session, identity_id: UUID, org_id: UUID, effective_from: datetime
) -> None:
    add_staff_membership(
        SqlAlchemyOrgStaffMembershipRepository(session),
        identity_id=identity_id,
        org_id=org_id,
        role=OrgRole.ADMIN,
        effective_from=effective_from,
        new_id=uuid4(),
    )


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- the named Sara world ---------------------------------------------------- #


@dataclass(frozen=True)
class SaraTestWorld:
    """The named Sara world handles (people, orgs, episodes, seeded bodies)."""

    sara: UUID
    mike: UUID
    khan: UUID
    patel: UUID
    lee: UUID
    marco: UUID
    extra_provider: UUID
    olivia: UUID
    fitgym: UUID
    khan_solo: UUID
    general: UUID
    shoulder: UUID
    closed: UUID
    shoulder_clinical_body: str
    shoulder_rehab_body: str
    closed_clinical_body: str


def build_sara_world(session: Session, clock: datetime) -> SaraTestWorld:
    """Persist the named Sara world; coverage windows are RELATIVE to ``clock`` (A19)."""
    suffix = uuid4().hex[:8]
    opened = clock - timedelta(weeks=12)

    def person(label: str, profile_type: ProfileType) -> UUID:
        identity_id = make_identity(session, f"{label}-{suffix}@example.com")
        make_profile(session, identity_id, profile_type)
        return identity_id

    sara = person("sara", ProfileType.CLIENT)
    mike = person("mike", ProfileType.PROVIDER)
    khan = person("khan", ProfileType.PROVIDER)
    patel = person("patel", ProfileType.PROVIDER)
    lee = person("lee", ProfileType.PROVIDER)
    marco = person("marco", ProfileType.PROVIDER)
    extra_provider = person("extra", ProfileType.PROVIDER)
    olivia = person("olivia", ProfileType.ORG_STAFF)

    fitgym = make_org(session, "FitGym", OrgType.GYM, opened)
    khan_solo = make_org(session, "Khan Solo Practice", OrgType.SOLO_PRACTICE, opened)
    make_admin_membership(session, olivia, fitgym, opened)

    episode_repo = SqlAlchemyEpisodeRepository(session)

    # General Training (FitGym, Mike responsible+face): the path-(a) / S6-positive world.
    general = open_episode(
        episode_repo,
        client_id=sara,
        reason="general training",
        managing_org_id=fitgym,
        responsible_provider_id=mike,
        responsible_role=Role.PERSONAL_TRAINER,
        change_reason="opened",
        now=opened,
        new_id=uuid4(),
    )

    # Shoulder Rehab (staffless cross-org Khan-Solo, Khan responsible+face): the
    # multi-role clinical team the responsible-provider grant (path b) manages.
    shoulder = open_episode(
        episode_repo,
        client_id=sara,
        reason="shoulder rehab",
        managing_org_id=khan_solo,
        responsible_provider_id=khan,
        responsible_role=Role.PHYSIOTHERAPIST,
        change_reason="opened",
        now=opened,
        new_id=uuid4(),
    )
    add_member(
        episode_repo,
        shoulder,
        provider_id=patel,
        role=Role.PHYSICIAN,
        change_reason="add",
        now=opened,
    )
    add_member(
        episode_repo,
        shoulder,
        provider_id=marco,
        role=Role.MASSAGE_THERAPIST,
        change_reason="add",
        now=opened,
    )
    # Lee's FUTURE half-open coverage window [clock+8w, clock+10w): the S3 time-flip.
    add_member(
        episode_repo,
        shoulder,
        provider_id=lee,
        role=Role.PHYSIOTHERAPIST,
        change_reason="covering for Khan",
        now=opened,
        effective_from=clock + timedelta(weeks=8),
        effective_to=clock + timedelta(weeks=10),
    )

    shoulder_clinical_body = "shoulder clinical note"
    shoulder_rehab_body = "shoulder rehab assessment"
    add_clinical_record(
        SqlAlchemyClinicalRecordRepository(session),
        episode_id=shoulder.id,
        author_provider_id=khan,
        body=shoulder_clinical_body,
        now=opened + timedelta(weeks=1),
        new_id=uuid4(),
    )
    SqlAlchemyRehabAssessmentRepository(session).add(
        RehabAssessment(
            id=uuid4(),
            episode_id=shoulder.id,
            author_provider_id=khan,
            body=shoulder_rehab_body,
            created_at=opened + timedelta(weeks=1),
        )
    )

    # A CLOSED episode (Khan-Solo, Khan responsible) for the S4 read-survives tests.
    closed_clinical_body = "closed clinical note"
    closed = open_episode(
        episode_repo,
        client_id=sara,
        reason="old shoulder episode",
        managing_org_id=khan_solo,
        responsible_provider_id=khan,
        responsible_role=Role.PHYSIOTHERAPIST,
        change_reason="opened",
        now=opened,
        new_id=uuid4(),
    )
    add_member(
        episode_repo,
        closed,
        provider_id=patel,
        role=Role.PHYSICIAN,
        change_reason="add",
        now=opened,
    )
    add_clinical_record(
        SqlAlchemyClinicalRecordRepository(session),
        episode_id=closed.id,
        author_provider_id=khan,
        body=closed_clinical_body,
        now=opened + timedelta(weeks=1),
        new_id=uuid4(),
    )
    close_episode(episode_repo, closed, now=clock - timedelta(weeks=1))

    return SaraTestWorld(
        sara=sara,
        mike=mike,
        khan=khan,
        patel=patel,
        lee=lee,
        marco=marco,
        extra_provider=extra_provider,
        olivia=olivia,
        fitgym=fitgym,
        khan_solo=khan_solo,
        general=general.id,
        shoulder=shoulder.id,
        closed=closed.id,
        shoulder_clinical_body=shoulder_clinical_body,
        shoulder_rehab_body=shoulder_rehab_body,
        closed_clinical_body=closed_clinical_body,
    )
