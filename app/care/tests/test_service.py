"""Unit tests for the care use cases: open_episode, get_episode (no DB)."""

from __future__ import annotations

from app.care.domain.episode import EpisodeStatus
from app.care.domain.value_objects import Role
from app.care.service import get_episode, open_episode

from .conftest import CLIENT, EPISODE_ID, ORG_ID, PROVIDER_A, PROVIDER_B, FakeEpisodeRepository, at

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
