"""SQLAlchemy models for the care tables + domain <-> model mappers.

Infrastructure/edge layer: the persistence shape, kept separate from the pure
``Episode`` aggregate (``app/care/domain/episode.py``). One root table
(``episodes``) plus three append-only, effective-dated child tables
(``episode_memberships`` / ``responsibility_assignments`` / ``booking_contacts``),
which share their columns via the ``_CareChildModel`` abstract base. The
module-level mappers convert across the boundary so the repository never leaks
SQLAlchemy into the domain; ``status`` / ``role`` are stored as ``VARCHAR`` (A18)
and mapped string <-> ``StrEnum`` here. All timestamps are TIMESTAMPTZ
(``DateTime(timezone=True)``): a naive read-back would make the pure value objects
reject it.

The aggregate's child entities carry no ``episode_id`` (the root owns the
boundary), so the to_model mappers inject it. The per-episode no-overlap
guarantee on responsibility / booking-face is enforced by a Postgres
``EXCLUDE USING gist`` constraint that lives in migration ``0005`` ONLY (it is not
expressible in this ORM metadata); the ``CHECK`` constraints below are mirrored in
that migration so the two agree.

``IdentityModel`` and ``OrganizationModel`` are imported (and re-exported under
``noqa: F401``) so the ``episodes -> identities`` / ``episodes -> organizations``
and ``child -> identities`` foreign keys resolve on ``Base.metadata`` even under
isolated test runs that import only this module. The reverse dependency (domain
-> other contexts) does not exist: the domain layer imports nothing from them.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.care.domain.clinical import ClinicalRecord, RehabAssessment
from app.care.domain.episode import (
    BookingContact,
    Episode,
    EpisodeStatus,
    Membership,
    Responsibility,
)
from app.care.domain.value_objects import EffectivePeriod, Role
from app.core.database import Base
from app.identity.orm import IdentityModel  # noqa: F401  (resolves person FKs)
from app.organization.orm import OrganizationModel  # noqa: F401  (resolves managing-org FK)

_ROLE_CHECK = (
    "role IN ('physician', 'physiotherapist', 'personal_trainer', "
    "'massage_therapist', 'nutrition_coach')"
)
# Mirrors EffectivePeriod's positive-length rule at the DB: the EXCLUDE rejects
# OVERLAP, but treats a zero-length [t, t) range as EMPTY (so it would ignore it);
# this CHECK forbids that degenerate/inverted period directly.
_PERIOD_CHECK = "effective_to IS NULL OR effective_from < effective_to"


class EpisodeModel(Base):
    """The ``episodes`` table: the aggregate root, one row per course of care."""

    __tablename__ = "episodes"
    __table_args__ = (CheckConstraint("status IN ('active', 'closed')", name="status"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    client_id: Mapped[UUID] = mapped_column(ForeignKey("identities.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text(), nullable=False)
    status: Mapped[str] = mapped_column(String(), nullable=False)
    managing_org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class _CareChildModel(Base):
    """Shared columns for the three append-only, effective-dated care child tables.

    Abstract (no table of its own): each concrete subclass inherits ``id`` (PK),
    the ``episode_id`` / ``provider_id`` foreign keys, the half-open
    ``effective_from`` / ``effective_to`` window, and ``change_reason``. There is
    no ``created_at`` - the row carries only business time. The naming convention
    resolves every PK/FK/CHECK name per concrete table.
    """

    __abstract__ = True

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    episode_id: Mapped[UUID] = mapped_column(ForeignKey("episodes.id"), nullable=False)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("identities.id"), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    change_reason: Mapped[str] = mapped_column(Text(), nullable=False)


class EpisodeMembershipModel(_CareChildModel):
    """The ``episode_memberships`` table: a provider's effective-dated membership."""

    __tablename__ = "episode_memberships"
    __table_args__ = (
        CheckConstraint(_ROLE_CHECK, name="role"),
        CheckConstraint(_PERIOD_CHECK, name="period"),
    )

    role: Mapped[str] = mapped_column(String(), nullable=False)


class ResponsibilityAssignmentModel(_CareChildModel):
    """The ``responsibility_assignments`` table: the responsible provider over time.

    A Postgres ``EXCLUDE USING gist`` no-overlap constraint (migration ``0005``)
    guarantees at most one responsible provider per episode at any instant.
    """

    __tablename__ = "responsibility_assignments"
    __table_args__ = (CheckConstraint(_PERIOD_CHECK, name="period"),)


class BookingContactModel(_CareChildModel):
    """The ``booking_contacts`` table: the booking "face" of an episode over time.

    Carries the same ``EXCLUDE USING gist`` no-overlap constraint as
    responsibility (one face per episode at any instant).
    """

    __tablename__ = "booking_contacts"
    __table_args__ = (CheckConstraint(_PERIOD_CHECK, name="period"),)


class _ClinicalEventModel(Base):
    """Shared columns for the two write-once, episode-scoped clinical event tables.

    Abstract (no table of its own): each concrete subclass is its own tiny
    aggregate root (``clinical_records`` / ``rehab_assessments``). Unlike the
    effective-dated care child tables these carry a ``created_at`` (TIMESTAMPTZ
    authoring time, the row IS the event) and no effective window / role / CHECK.
    The naming convention resolves every PK/FK name per concrete table.
    """

    __abstract__ = True

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    episode_id: Mapped[UUID] = mapped_column(ForeignKey("episodes.id"), nullable=False)
    author_provider_id: Mapped[UUID] = mapped_column(ForeignKey("identities.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ClinicalRecordModel(_ClinicalEventModel):
    """The ``clinical_records`` table: a write-once clinical note on an episode."""

    __tablename__ = "clinical_records"


class RehabAssessmentModel(_ClinicalEventModel):
    """The ``rehab_assessments`` table: a write-once rehab assessment on an episode."""

    __tablename__ = "rehab_assessments"


# --- Root mappers -----------------------------------------------------------


def _episode_to_model(episode: Episode) -> EpisodeModel:
    """Map the aggregate root to a new ``episodes`` row (every column explicit)."""
    return EpisodeModel(
        id=episode.id,
        client_id=episode.client_id,
        reason=episode.reason,
        status=episode.status.value,
        managing_org_id=episode.managing_org_id,
        opened_at=episode.opened_at,
        closed_at=episode.closed_at,
    )


# --- Child mappers (episode_id injected by the root) ------------------------


def _membership_to_model(membership: Membership, episode_id: UUID) -> EpisodeMembershipModel:
    return EpisodeMembershipModel(
        id=membership.id,
        episode_id=episode_id,
        provider_id=membership.provider_id,
        role=membership.role.value,
        effective_from=membership.period.effective_from,
        effective_to=membership.period.effective_to,
        change_reason=membership.change_reason,
    )


def _membership_to_domain(model: EpisodeMembershipModel) -> Membership:
    return Membership(
        id=model.id,
        provider_id=model.provider_id,
        period=EffectivePeriod(model.effective_from, model.effective_to),
        change_reason=model.change_reason,
        role=Role(model.role),
    )


def _responsibility_to_model(
    responsibility: Responsibility, episode_id: UUID
) -> ResponsibilityAssignmentModel:
    return ResponsibilityAssignmentModel(
        id=responsibility.id,
        episode_id=episode_id,
        provider_id=responsibility.provider_id,
        effective_from=responsibility.period.effective_from,
        effective_to=responsibility.period.effective_to,
        change_reason=responsibility.change_reason,
    )


def _responsibility_to_domain(model: ResponsibilityAssignmentModel) -> Responsibility:
    return Responsibility(
        id=model.id,
        provider_id=model.provider_id,
        period=EffectivePeriod(model.effective_from, model.effective_to),
        change_reason=model.change_reason,
    )


def _booking_contact_to_model(face: BookingContact, episode_id: UUID) -> BookingContactModel:
    return BookingContactModel(
        id=face.id,
        episode_id=episode_id,
        provider_id=face.provider_id,
        effective_from=face.period.effective_from,
        effective_to=face.period.effective_to,
        change_reason=face.change_reason,
    )


def _booking_contact_to_domain(model: BookingContactModel) -> BookingContact:
    return BookingContact(
        id=model.id,
        provider_id=model.provider_id,
        period=EffectivePeriod(model.effective_from, model.effective_to),
        change_reason=model.change_reason,
    )


def _episode_to_domain(
    root: EpisodeModel,
    membership_models: list[EpisodeMembershipModel],
    responsibility_models: list[ResponsibilityAssignmentModel],
    face_models: list[BookingContactModel],
) -> Episode:
    """Assemble the root row + its child rows into the pure aggregate.

    Rebuilds the three child collections then calls ``Episode.reconstitute`` (which
    assigns them directly, running no mutator invariants), so historical
    closed/reassigned rows load faithfully.
    """
    return Episode.reconstitute(
        id=root.id,
        client_id=root.client_id,
        reason=root.reason,
        managing_org_id=root.managing_org_id,
        opened_at=root.opened_at,
        status=EpisodeStatus(root.status),
        closed_at=root.closed_at,
        memberships=[_membership_to_domain(m) for m in membership_models],
        responsibility=[_responsibility_to_domain(r) for r in responsibility_models],
        faces=[_booking_contact_to_domain(f) for f in face_models],
    )


# --- Clinical event mappers (own aggregates; no episode injection needed) ----


def _clinical_record_to_model(record: ClinicalRecord) -> ClinicalRecordModel:
    return ClinicalRecordModel(
        id=record.id,
        episode_id=record.episode_id,
        author_provider_id=record.author_provider_id,
        body=record.body,
        created_at=record.created_at,
    )


def _clinical_record_to_domain(model: ClinicalRecordModel) -> ClinicalRecord:
    return ClinicalRecord(
        id=model.id,
        episode_id=model.episode_id,
        author_provider_id=model.author_provider_id,
        body=model.body,
        created_at=model.created_at,
    )


def _rehab_assessment_to_model(assessment: RehabAssessment) -> RehabAssessmentModel:
    return RehabAssessmentModel(
        id=assessment.id,
        episode_id=assessment.episode_id,
        author_provider_id=assessment.author_provider_id,
        body=assessment.body,
        created_at=assessment.created_at,
    )


def _rehab_assessment_to_domain(model: RehabAssessmentModel) -> RehabAssessment:
    return RehabAssessment(
        id=model.id,
        episode_id=model.episode_id,
        author_provider_id=model.author_provider_id,
        body=model.body,
        created_at=model.created_at,
    )
