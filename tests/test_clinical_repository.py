"""Integration tests for the clinical/rehab repositories (real Postgres, rolled back).

Each test runs inside the per-test transaction (``db_session``) and is rolled back
at teardown, so the shared database stays order-independent (A19). They prove the
Postgres-only behaviour the pure record tests cannot: the write-once ``add`` +
``list_for_episode`` round trip (TIMESTAMPTZ ``created_at``, episode-scoped,
ordered by ``created_at``, NO policy filter) and the foreign keys to
``episodes`` / ``identities``. FK parents (a client + provider Identity, a
managing Organization, an open Episode) are persisted first via their repositories;
every raw-``IntegrityError`` test makes that violation its terminal DB action (the
per-test rollback then recovers, AM3-style).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.care.domain.clinical import ClinicalRecord, RehabAssessment
from app.care.domain.episode import Episode
from app.care.domain.value_objects import Role
from app.care.repository import (
    SqlAlchemyClinicalRecordRepository,
    SqlAlchemyEpisodeRepository,
    SqlAlchemyRehabAssessmentRepository,
)
from app.identity.domain.entities import Identity
from app.identity.repository import SqlAlchemyIdentityRepository
from app.organization.domain.entities import Organization
from app.organization.domain.value_objects import OrgType
from app.organization.repository import SqlAlchemyOrganizationRepository


def _t(weeks: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(weeks=weeks)


def _persist_identity(session: Session, email: str) -> UUID:
    identity = Identity(
        id=uuid4(), email=email, display_name="Person", password_hash="stub-hash", created_at=_t(0)
    )
    SqlAlchemyIdentityRepository(session).add(identity)
    return identity.id


def _open_episode(session: Session) -> tuple[UUID, UUID]:
    """Persist FK parents + an open episode; return (episode_id, provider_id)."""
    suffix = uuid4().hex[:8]
    client_id = _persist_identity(session, f"client-{suffix}@example.com")
    provider_id = _persist_identity(session, f"prov-{suffix}@example.com")
    org = Organization(id=uuid4(), name="Acme Clinic", type=OrgType.CLINIC, created_at=_t(0))
    SqlAlchemyOrganizationRepository(session).add(org)
    episode = Episode.open(
        id=uuid4(),
        client_id=client_id,
        reason="shoulder_rehab",
        managing_org_id=org.id,
        now=_t(0),
        responsible_provider_id=provider_id,
        responsible_role=Role.PHYSIOTHERAPIST,
        change_reason="opened",
    )
    SqlAlchemyEpisodeRepository(session).save(episode)
    return episode.id, provider_id


# --- clinical records --------------------------------------------------------


def test_clinical_add_then_list_round_trip(db_session: Session) -> None:
    episode_id, provider_id = _open_episode(db_session)
    repo = SqlAlchemyClinicalRecordRepository(db_session)
    record = ClinicalRecord(
        id=uuid4(),
        episode_id=episode_id,
        author_provider_id=provider_id,
        body="initial assessment",
        created_at=_t(1),
    )
    repo.add(record)
    db_session.expunge_all()
    records = repo.list_for_episode(episode_id)
    assert len(records) == 1
    assert records[0].id == record.id
    assert records[0].episode_id == episode_id
    assert records[0].author_provider_id == provider_id
    assert records[0].body == "initial assessment"
    assert records[0].created_at == _t(1)
    assert records[0].created_at.tzinfo is not None  # TIMESTAMPTZ survives


def test_clinical_list_ordered_by_created_at(db_session: Session) -> None:
    episode_id, provider_id = _open_episode(db_session)
    repo = SqlAlchemyClinicalRecordRepository(db_session)
    later = ClinicalRecord(
        id=uuid4(),
        episode_id=episode_id,
        author_provider_id=provider_id,
        body="2nd",
        created_at=_t(3),
    )
    earlier = ClinicalRecord(
        id=uuid4(),
        episode_id=episode_id,
        author_provider_id=provider_id,
        body="1st",
        created_at=_t(1),
    )
    repo.add(later)
    repo.add(earlier)
    db_session.expunge_all()
    records = repo.list_for_episode(episode_id)
    assert [r.body for r in records] == ["1st", "2nd"]


def test_clinical_list_is_episode_scoped(db_session: Session) -> None:
    episode_id, provider_id = _open_episode(db_session)
    other_episode_id, _ = _open_episode(db_session)
    repo = SqlAlchemyClinicalRecordRepository(db_session)
    repo.add(
        ClinicalRecord(
            id=uuid4(),
            episode_id=episode_id,
            author_provider_id=provider_id,
            body="mine",
            created_at=_t(1),
        )
    )
    db_session.expunge_all()
    assert repo.list_for_episode(other_episode_id) == []


def test_clinical_list_empty_for_unknown_episode(db_session: Session) -> None:
    repo = SqlAlchemyClinicalRecordRepository(db_session)
    assert repo.list_for_episode(uuid4()) == []


def test_clinical_fk_violation_missing_episode_raises(db_session: Session) -> None:
    _episode_id, provider_id = _open_episode(db_session)
    repo = SqlAlchemyClinicalRecordRepository(db_session)
    record = ClinicalRecord(
        id=uuid4(),
        episode_id=uuid4(),  # not a real episode
        author_provider_id=provider_id,
        body="orphan",
        created_at=_t(1),
    )
    with pytest.raises(IntegrityError) as exc_info:
        repo.add(record)
    assert "fk_clinical_records_episode_id_episodes" in str(exc_info.value.orig)


def test_clinical_fk_violation_missing_author_raises(db_session: Session) -> None:
    episode_id, _provider_id = _open_episode(db_session)
    repo = SqlAlchemyClinicalRecordRepository(db_session)
    record = ClinicalRecord(
        id=uuid4(),
        episode_id=episode_id,
        author_provider_id=uuid4(),  # not a real identity
        body="ghost author",
        created_at=_t(1),
    )
    with pytest.raises(IntegrityError) as exc_info:
        repo.add(record)
    assert "fk_clinical_records_author_provider_id_identities" in str(exc_info.value.orig)


# --- rehab assessments -------------------------------------------------------


def test_rehab_add_then_list_round_trip(db_session: Session) -> None:
    episode_id, provider_id = _open_episode(db_session)
    repo = SqlAlchemyRehabAssessmentRepository(db_session)
    assessment = RehabAssessment(
        id=uuid4(),
        episode_id=episode_id,
        author_provider_id=provider_id,
        body="rehab plan",
        created_at=_t(2),
    )
    repo.add(assessment)
    db_session.expunge_all()
    assessments = repo.list_for_episode(episode_id)
    assert len(assessments) == 1
    assert assessments[0].id == assessment.id
    assert assessments[0].body == "rehab plan"
    assert assessments[0].created_at == _t(2)
    assert assessments[0].created_at.tzinfo is not None


def test_rehab_list_ordered_and_scoped(db_session: Session) -> None:
    episode_id, provider_id = _open_episode(db_session)
    other_episode_id, _ = _open_episode(db_session)
    repo = SqlAlchemyRehabAssessmentRepository(db_session)
    repo.add(
        RehabAssessment(
            id=uuid4(),
            episode_id=episode_id,
            author_provider_id=provider_id,
            body="b",
            created_at=_t(3),
        )
    )
    repo.add(
        RehabAssessment(
            id=uuid4(),
            episode_id=episode_id,
            author_provider_id=provider_id,
            body="a",
            created_at=_t(1),
        )
    )
    db_session.expunge_all()
    assert [r.body for r in repo.list_for_episode(episode_id)] == ["a", "b"]
    assert repo.list_for_episode(other_episode_id) == []
