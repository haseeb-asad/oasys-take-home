"""Unit tests for the care use cases: open_episode, get_episode (no DB)."""

from __future__ import annotations

from unittest import mock
from uuid import UUID

from app.care.domain.clinical import ClinicalRecord, RehabAssessment
from app.care.domain.episode import Episode, EpisodeStatus
from app.care.domain.value_objects import Role
from app.care.service import (
    add_clinical_record,
    add_member,
    close_episode,
    end_member,
    get_episode,
    list_clinical_records,
    list_rehab_assessments,
    open_episode,
    reassign_responsible,
    set_face,
)

from .conftest import (
    CLIENT,
    EPISODE_ID,
    ORG_ID,
    PROVIDER_A,
    PROVIDER_B,
    FakeClinicalRecordRepository,
    FakeEpisodeRepository,
    FakeRehabAssessmentRepository,
    at,
)


def _open(repo: FakeEpisodeRepository) -> Episode:
    """Open + persist an episode (A responsible + face + member at t0)."""
    return open_episode(
        repo,
        client_id=CLIENT,
        reason="shoulder_rehab",
        managing_org_id=ORG_ID,
        responsible_provider_id=PROVIDER_A,
        responsible_role=Role.PHYSIOTHERAPIST,
        change_reason="opened",
        now=at(0),
        new_id=EPISODE_ID,
    )


# --- open_episode -----------------------------------------------------------


def test_open_episode_builds_and_saves() -> None:
    repo = FakeEpisodeRepository()
    episode = open_episode(
        repo,
        client_id=CLIENT,
        reason="shoulder_rehab",
        managing_org_id=ORG_ID,
        responsible_provider_id=PROVIDER_A,
        responsible_role=Role.PHYSIOTHERAPIST,
        change_reason="opened",
        now=at(0),
        new_id=EPISODE_ID,
    )
    # Built with the injected id/now, and persisted via the port.
    assert episode.id == EPISODE_ID
    assert episode.client_id == CLIENT
    assert episode.managing_org_id == ORG_ID
    assert episode.opened_at == at(0)
    assert episode.status is EpisodeStatus.ACTIVE
    assert repo.get(EPISODE_ID) is episode
    # The responsible provider is a member + responsible + the face.
    responsibility = episode.current_responsibility(at(0))
    face = episode.current_face(at(0))
    assert responsibility is not None and responsibility.provider_id == PROVIDER_A
    assert face is not None and face.provider_id == PROVIDER_A
    assert episode.is_current_member(PROVIDER_A, at(0)) is True


def test_open_episode_divergent_face() -> None:
    repo = FakeEpisodeRepository()
    episode = open_episode(
        repo,
        client_id=CLIENT,
        reason="shoulder_rehab",
        managing_org_id=ORG_ID,
        responsible_provider_id=PROVIDER_A,
        responsible_role=Role.PHYSIOTHERAPIST,
        change_reason="opened",
        now=at(0),
        new_id=EPISODE_ID,
        face_provider_id=PROVIDER_B,
        face_role=Role.PHYSICIAN,
    )
    responsibility = episode.current_responsibility(at(0))
    face = episode.current_face(at(0))
    assert responsibility is not None and responsibility.provider_id == PROVIDER_A
    assert face is not None and face.provider_id == PROVIDER_B
    # Both the responsible and the divergent face are members from t0.
    assert episode.is_current_member(PROVIDER_A, at(0)) is True
    assert episode.is_current_member(PROVIDER_B, at(0)) is True
    assert repo.get(EPISODE_ID) is episode


# --- get_episode ------------------------------------------------------------


def test_get_episode_returns_persisted() -> None:
    repo = FakeEpisodeRepository()
    episode = open_episode(
        repo,
        client_id=CLIENT,
        reason="shoulder_rehab",
        managing_org_id=ORG_ID,
        responsible_provider_id=PROVIDER_A,
        responsible_role=Role.PHYSIOTHERAPIST,
        change_reason="opened",
        now=at(0),
        new_id=EPISODE_ID,
    )
    assert get_episode(repo, EPISODE_ID) is episode


def test_get_episode_missing_returns_none() -> None:
    repo = FakeEpisodeRepository()
    assert get_episode(repo, EPISODE_ID) is None


# --- mutation orchestrators (receive the loaded aggregate, mutate, save) -----


def test_add_member_appends_and_saves() -> None:
    repo = FakeEpisodeRepository()
    episode = _open(repo)
    returned = add_member(
        repo,
        episode,
        provider_id=PROVIDER_B,
        role=Role.PHYSICIAN,
        change_reason="add b",
        now=at(1),
    )
    assert returned is episode
    assert episode.is_current_member(PROVIDER_B, at(1)) is True
    assert repo.get(EPISODE_ID) is episode  # persisted via the port


def test_add_member_with_coverage_window() -> None:
    repo = FakeEpisodeRepository()
    episode = _open(repo)
    add_member(
        repo,
        episode,
        provider_id=PROVIDER_B,
        role=Role.MASSAGE_THERAPIST,
        change_reason="covering",
        now=at(0),
        effective_from=at(1),
        effective_to=at(3),
    )
    # The bounded window expires on its own: current inside, not current after.
    assert episode.is_current_member(PROVIDER_B, at(2)) is True
    assert episode.is_current_member(PROVIDER_B, at(3)) is False


def test_reassign_responsible_closes_old_opens_new() -> None:
    repo = FakeEpisodeRepository()
    episode = _open(repo)
    add_member(
        repo, episode, provider_id=PROVIDER_B, role=Role.PHYSICIAN, change_reason="add b", now=at(1)
    )
    reassign_responsible(repo, episode, provider_id=PROVIDER_B, change_reason="handoff", now=at(2))
    rows = sorted(episode.responsibility, key=lambda r: r.period.effective_from)
    assert len(rows) == 2
    assert rows[0].provider_id == PROVIDER_A and rows[0].period.effective_to == at(2)
    assert rows[1].provider_id == PROVIDER_B and rows[1].period.effective_to is None
    current = episode.current_responsibility(at(2))
    assert current is not None and current.provider_id == PROVIDER_B


def test_set_face_handoff() -> None:
    repo = FakeEpisodeRepository()
    episode = _open(repo)
    add_member(
        repo, episode, provider_id=PROVIDER_B, role=Role.PHYSICIAN, change_reason="add b", now=at(1)
    )
    set_face(repo, episode, provider_id=PROVIDER_B, change_reason="face handoff", now=at(2))
    current = episode.current_face(at(2))
    assert current is not None and current.provider_id == PROVIDER_B
    assert len(episode.faces) == 2


def test_end_member_closes_membership() -> None:
    repo = FakeEpisodeRepository()
    episode = _open(repo)
    add_member(
        repo, episode, provider_id=PROVIDER_B, role=Role.PHYSICIAN, change_reason="add b", now=at(1)
    )
    end_member(
        repo, episode, provider_id=PROVIDER_B, effective_to=at(3), change_reason="left", now=at(2)
    )
    assert episode.is_current_member(PROVIDER_B, at(4)) is False


def test_close_episode_marks_closed() -> None:
    repo = FakeEpisodeRepository()
    episode = _open(repo)
    close_episode(repo, episode, now=at(5))
    assert episode.status is EpisodeStatus.CLOSED
    assert episode.closed_at == at(5)


def test_add_clinical_record_uses_injected_now_and_id() -> None:
    repo = FakeClinicalRecordRepository()
    record_id = UUID(int=999)
    record = add_clinical_record(
        repo,
        episode_id=EPISODE_ID,
        author_provider_id=PROVIDER_A,
        body="assessment",
        now=at(1),
        new_id=record_id,
    )
    assert isinstance(record, ClinicalRecord)
    assert record.id == record_id
    assert record.episode_id == EPISODE_ID
    assert record.author_provider_id == PROVIDER_A
    assert record.body == "assessment"
    assert record.created_at == at(1)
    assert repo.list_for_episode(EPISODE_ID) == [record]


def test_list_clinical_records_delegates() -> None:
    repo = FakeClinicalRecordRepository()
    record = ClinicalRecord(
        id=UUID(int=1),
        episode_id=EPISODE_ID,
        author_provider_id=PROVIDER_A,
        body="x",
        created_at=at(1),
    )
    repo.add(record)
    assert list_clinical_records(repo, EPISODE_ID) == [record]


def test_list_rehab_assessments_delegates() -> None:
    repo = FakeRehabAssessmentRepository()
    assessment = RehabAssessment(
        id=UUID(int=2),
        episode_id=EPISODE_ID,
        author_provider_id=PROVIDER_A,
        body="y",
        created_at=at(1),
    )
    repo.add(assessment)
    assert list_rehab_assessments(repo, EPISODE_ID) == [assessment]


# --- covering_for routing: prove add_coverage vs add_member dispatch ----------


def test_covering_for_dispatches_to_add_coverage() -> None:
    """Service routes covering_for to episode.add_coverage.

    If the service branch were reverted to skip add_coverage (e.g. always calling
    add_member directly), spy_coverage would record zero calls and
    spy_coverage.assert_called_once() would fail, pinning the routing decision.
    """
    repo = FakeEpisodeRepository()
    episode = _open(repo)
    with mock.patch.object(episode, "add_coverage", wraps=episode.add_coverage) as spy_coverage:
        add_member(
            repo,
            episode,
            provider_id=PROVIDER_B,
            role=Role.PHYSICIAN,
            change_reason="covering",
            now=at(1),
            effective_to=at(3),
            covering_for=PROVIDER_A,
        )
    spy_coverage.assert_called_once()


def test_plain_add_does_not_dispatch_to_add_coverage() -> None:
    """Service routes a plain add to episode.add_member and does NOT call add_coverage.

    If the service branch were reverted to always call add_coverage, spy_coverage
    would record one call and spy_coverage.assert_not_called() would fail, pinning
    the routing decision.
    """
    repo = FakeEpisodeRepository()
    episode = _open(repo)
    with (
        mock.patch.object(episode, "add_coverage", wraps=episode.add_coverage) as spy_coverage,
        mock.patch.object(episode, "add_member", wraps=episode.add_member) as spy_add,
    ):
        add_member(
            repo,
            episode,
            provider_id=PROVIDER_B,
            role=Role.PHYSICIAN,
            change_reason="add",
            now=at(1),
        )
    spy_coverage.assert_not_called()
    spy_add.assert_called_once()
