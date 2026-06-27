"""Unit tests for the care Pydantic v2 edge schemas (no DB, no app)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.care.domain.clinical import ClinicalRecord, RehabAssessment
from app.care.domain.episode import Episode
from app.care.domain.value_objects import Role
from app.care.schemas import (
    ClinicalRecordCreate,
    ClinicalRecordOut,
    EpisodeCreate,
    EpisodeOut,
    MemberCreate,
    RehabAssessmentOut,
)

from .conftest import CLIENT, EPISODE_ID, ORG_ID, PROVIDER_A, PROVIDER_B, at


def _valid_create() -> dict[str, object]:
    return {
        "client_id": str(CLIENT),
        "reason": "shoulder rehab",
        "managing_org_id": str(ORG_ID),
        "responsible_role": "physiotherapist",
        "change_reason": "opened",
    }


# --- EpisodeCreate: server-owned responsible (AM2) + constraints -------------


def test_episode_create_has_no_responsible_provider_field() -> None:
    # responsible_provider_id is SERVER-OWNED (the authenticated provider); the
    # request schema must not declare it.
    assert "responsible_provider_id" not in EpisodeCreate.model_fields


def test_episode_create_silently_ignores_decoy_responsible_provider_id() -> None:
    payload = _valid_create()
    payload["responsible_provider_id"] = str(uuid4())  # decoy
    model = EpisodeCreate.model_validate(payload)
    assert not hasattr(model, "responsible_provider_id")  # dropped, never bound
    assert model.client_id == CLIENT


def test_episode_create_rejects_empty_reason() -> None:
    payload = _valid_create()
    payload["reason"] = "   "  # whitespace -> stripped to empty -> under min_length
    with pytest.raises(ValidationError):
        EpisodeCreate.model_validate(payload)


def test_episode_create_rejects_unknown_role() -> None:
    payload = _valid_create()
    payload["responsible_role"] = "wizard"
    with pytest.raises(ValidationError):
        EpisodeCreate.model_validate(payload)


def test_member_create_rejects_unknown_role() -> None:
    with pytest.raises(ValidationError):
        MemberCreate.model_validate(
            {"provider_id": str(uuid4()), "role": "wizard", "change_reason": "x"}
        )


def test_clinical_record_create_rejects_empty_body() -> None:
    with pytest.raises(ValidationError):
        ClinicalRecordCreate.model_validate({"body": ""})


# --- EpisodeOut.from_episode: current derivation + append-only history --------


def _episode_with_handoff() -> Episode:
    episode = Episode.open(
        id=EPISODE_ID,
        client_id=CLIENT,
        reason="shoulder rehab",
        managing_org_id=ORG_ID,
        now=at(0),
        responsible_provider_id=PROVIDER_A,
        responsible_role=Role.PHYSIOTHERAPIST,
        change_reason="opened",
    )
    episode.add_member(
        provider_id=PROVIDER_B, role=Role.PHYSICIAN, now=at(1), change_reason="add b"
    )
    episode.assign_responsible(provider_id=PROVIDER_B, now=at(2), change_reason="handoff")
    return episode


def test_episode_out_maps_current_responsible_and_face_at_now() -> None:
    out = EpisodeOut.from_episode(_episode_with_handoff(), at(2))
    assert out.id == EPISODE_ID
    assert out.client_id == CLIENT
    assert out.managing_org_id == ORG_ID
    assert out.status == "active"
    assert out.closed_at is None
    # Current (at now): responsibility handed to B; face never moved (still A).
    assert out.responsible_provider_id == PROVIDER_B
    assert out.face_provider_id == PROVIDER_A


def test_episode_out_exposes_append_only_history() -> None:
    out = EpisodeOut.from_episode(_episode_with_handoff(), at(2))
    assert {m.provider_id for m in out.members} == {PROVIDER_A, PROVIDER_B}
    # Two responsibility rows (A closed at t2, B open): full audit history (S7).
    assert len(out.responsibility) == 2
    assert len(out.faces) == 1
    a_resp = next(r for r in out.responsibility if r.provider_id == PROVIDER_A)
    b_resp = next(r for r in out.responsibility if r.provider_id == PROVIDER_B)
    assert a_resp.effective_to == at(2)
    assert b_resp.effective_to is None
    # Membership rows carry their role.
    b_member = next(m for m in out.members if m.provider_id == PROVIDER_B)
    assert b_member.role == Role.PHYSICIAN


def test_episode_out_closed_episode_reports_status_and_closed_at() -> None:
    episode = _episode_with_handoff()
    episode.close(now=at(3))
    out = EpisodeOut.from_episode(episode, at(3))
    assert out.status == "closed"
    assert out.closed_at == at(3)
    # close() does not end-date the rows, so current responsible is still derivable.
    assert out.responsible_provider_id == PROVIDER_B


def test_episode_out_no_current_responsible_or_face_when_before_open() -> None:
    # Queried BEFORE the episode opened: no row is effective, so the derived
    # "current" responsible / face are None (the else branch of from_episode),
    # while the append-only history is still exposed.
    out = EpisodeOut.from_episode(_episode_with_handoff(), at(-1))
    assert out.responsible_provider_id is None
    assert out.face_provider_id is None
    assert len(out.members) == 2  # history still present


# --- Clinical / rehab Out via from_attributes --------------------------------


def test_clinical_record_out_from_attributes() -> None:
    record = ClinicalRecord(
        id=UUID(int=5),
        episode_id=EPISODE_ID,
        author_provider_id=PROVIDER_A,
        body="note body",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    out = ClinicalRecordOut.model_validate(record)
    assert out.id == record.id
    assert out.episode_id == EPISODE_ID
    assert out.author_provider_id == PROVIDER_A
    assert out.body == "note body"
    assert out.created_at == record.created_at


def test_rehab_assessment_out_from_attributes() -> None:
    assessment = RehabAssessment(
        id=UUID(int=6),
        episode_id=EPISODE_ID,
        author_provider_id=PROVIDER_A,
        body="rehab body",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    out = RehabAssessmentOut.model_validate(assessment)
    assert out.body == "rehab body"
    assert out.author_provider_id == PROVIDER_A
