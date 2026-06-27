"""Unit tests for the care ORM model <-> domain mappers (no DB).

Exercises the four mappers and the ``_episode_to_domain`` assembler in isolation:
the children carry no ``episode_id`` (the root owns the boundary), so the
to_model mappers inject it; row -> entity rebuilds the half-open
``EffectivePeriod`` and the ``Role`` / ``EpisodeStatus`` enums; and a tz-aware
TIMESTAMPTZ round trip (incl. an open ``effective_to is None``) is preserved.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.care.domain.episode import (
    BookingContact,
    Episode,
    EpisodeStatus,
    Membership,
    Responsibility,
)
from app.care.domain.value_objects import EffectivePeriod, Role
from app.care.orm import (
    BookingContactModel,
    EpisodeMembershipModel,
    EpisodeModel,
    ResponsibilityAssignmentModel,
    _booking_contact_to_domain,
    _booking_contact_to_model,
    _episode_to_domain,
    _episode_to_model,
    _membership_to_domain,
    _membership_to_model,
    _responsibility_to_domain,
    _responsibility_to_model,
)

_EPISODE_ID = UUID(int=100)
_CLIENT = UUID(int=1)
_PROVIDER_A = UUID(int=10)
_PROVIDER_B = UUID(int=11)
_ORG_ID = UUID(int=200)
_MEMBERSHIP_ID = UUID(int=300)
_RESP_ID = UUID(int=301)
_FACE_ID = UUID(int=302)


def _t(weeks: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(weeks=weeks)


# --- Membership mappers -----------------------------------------------------


def test_membership_to_model_injects_episode_id_and_unpacks_period() -> None:
    membership = Membership(
        id=_MEMBERSHIP_ID,
        provider_id=_PROVIDER_A,
        period=EffectivePeriod(_t(0), _t(5)),
        change_reason="added",
        role=Role.PHYSIOTHERAPIST,
    )
    model = _membership_to_model(membership, _EPISODE_ID)
    assert model.id == _MEMBERSHIP_ID
    assert model.episode_id == _EPISODE_ID  # the root injects the FK
    assert model.provider_id == _PROVIDER_A
    assert model.role == "physiotherapist"  # stored as the raw enum value
    assert model.effective_from == _t(0)
    assert model.effective_to == _t(5)
    assert model.change_reason == "added"


def test_membership_to_domain_rebuilds_period_and_role() -> None:
    model = EpisodeMembershipModel(
        id=_MEMBERSHIP_ID,
        episode_id=_EPISODE_ID,
        provider_id=_PROVIDER_A,
        role="physician",
        effective_from=_t(0),
        effective_to=None,
        change_reason="opened",
    )
    membership = _membership_to_domain(model)
    assert membership.id == _MEMBERSHIP_ID
    assert membership.provider_id == _PROVIDER_A
    assert membership.role is Role.PHYSICIAN
    assert membership.period == EffectivePeriod(_t(0), None)
    assert membership.change_reason == "opened"


def test_membership_round_trip_open_and_bounded() -> None:
    for period in (EffectivePeriod(_t(0), None), EffectivePeriod(_t(0), _t(5))):
        membership = Membership(
            id=_MEMBERSHIP_ID,
            provider_id=_PROVIDER_A,
            period=period,
            change_reason="x",
            role=Role.NUTRITION_COACH,
        )
        round_tripped = _membership_to_domain(_membership_to_model(membership, _EPISODE_ID))
        assert round_tripped.period == period
        assert round_tripped.role is Role.NUTRITION_COACH
        assert round_tripped.period.effective_from.tzinfo is not None


# --- Responsibility mappers -------------------------------------------------


def test_responsibility_to_model_injects_episode_id() -> None:
    responsibility = Responsibility(
        id=_RESP_ID,
        provider_id=_PROVIDER_A,
        period=EffectivePeriod(_t(0), None),
        change_reason="opened",
    )
    model = _responsibility_to_model(responsibility, _EPISODE_ID)
    assert model.id == _RESP_ID
    assert model.episode_id == _EPISODE_ID
    assert model.provider_id == _PROVIDER_A
    assert model.effective_from == _t(0)
    assert model.effective_to is None
    assert model.change_reason == "opened"


def test_responsibility_round_trip_preserves_open_end() -> None:
    responsibility = Responsibility(
        id=_RESP_ID,
        provider_id=_PROVIDER_A,
        period=EffectivePeriod(_t(0), None),
        change_reason="opened",
    )
    round_tripped = _responsibility_to_domain(_responsibility_to_model(responsibility, _EPISODE_ID))
    assert round_tripped.id == _RESP_ID
    assert round_tripped.period.effective_to is None
    assert round_tripped.period.effective_from == _t(0)


# --- BookingContact mappers -------------------------------------------------


def test_booking_contact_round_trip_bounded() -> None:
    face = BookingContact(
        id=_FACE_ID,
        provider_id=_PROVIDER_B,
        period=EffectivePeriod(_t(0), _t(3)),
        change_reason="handoff",
    )
    model = _booking_contact_to_model(face, _EPISODE_ID)
    assert model.episode_id == _EPISODE_ID
    round_tripped = _booking_contact_to_domain(model)
    assert round_tripped.id == _FACE_ID
    assert round_tripped.provider_id == _PROVIDER_B
    assert round_tripped.period == EffectivePeriod(_t(0), _t(3))


# --- Episode root + assembler -----------------------------------------------


def test_episode_to_model_sets_root_columns() -> None:
    episode = Episode(
        id=_EPISODE_ID,
        client_id=_CLIENT,
        reason="shoulder_rehab",
        managing_org_id=_ORG_ID,
        opened_at=_t(0),
        status=EpisodeStatus.CLOSED,
        closed_at=_t(9),
    )
    model = _episode_to_model(episode)
    assert model.id == _EPISODE_ID
    assert model.client_id == _CLIENT
    assert model.reason == "shoulder_rehab"
    assert model.status == "closed"  # stored as the raw enum value
    assert model.managing_org_id == _ORG_ID
    assert model.opened_at == _t(0)
    assert model.closed_at == _t(9)


def test_episode_to_domain_assembles_root_and_children() -> None:
    root = EpisodeModel(
        id=_EPISODE_ID,
        client_id=_CLIENT,
        reason="shoulder_rehab",
        status="active",
        managing_org_id=_ORG_ID,
        opened_at=_t(0),
        closed_at=None,
    )
    membership_models = [
        EpisodeMembershipModel(
            id=_MEMBERSHIP_ID,
            episode_id=_EPISODE_ID,
            provider_id=_PROVIDER_A,
            role="physiotherapist",
            effective_from=_t(0),
            effective_to=None,
            change_reason="opened",
        )
    ]
    responsibility_models = [
        ResponsibilityAssignmentModel(
            id=_RESP_ID,
            episode_id=_EPISODE_ID,
            provider_id=_PROVIDER_A,
            effective_from=_t(0),
            effective_to=None,
            change_reason="opened",
        )
    ]
    face_models = [
        BookingContactModel(
            id=_FACE_ID,
            episode_id=_EPISODE_ID,
            provider_id=_PROVIDER_A,
            effective_from=_t(0),
            effective_to=None,
            change_reason="opened",
        )
    ]
    episode = _episode_to_domain(root, membership_models, responsibility_models, face_models)
    assert isinstance(episode, Episode)
    assert episode.id == _EPISODE_ID
    assert episode.status is EpisodeStatus.ACTIVE  # string -> enum
    assert episode.is_active is True
    assert len(episode.memberships) == 1
    assert len(episode.responsibility) == 1
    assert len(episode.faces) == 1
    # The assembled aggregate's derivations resolve against the loaded rows.
    current = episode.current_responsibility(_t(1))
    assert current is not None and current.provider_id == _PROVIDER_A
    assert episode.is_current_member(_PROVIDER_A, _t(1)) is True


def test_episode_to_domain_round_trips_closed_status_and_tz() -> None:
    root = EpisodeModel(
        id=_EPISODE_ID,
        client_id=_CLIENT,
        reason="shoulder_rehab",
        status="closed",
        managing_org_id=_ORG_ID,
        opened_at=_t(0),
        closed_at=_t(9),
    )
    episode = _episode_to_domain(root, [], [], [])
    assert episode.status is EpisodeStatus.CLOSED
    assert episode.closed_at == _t(9)
    assert episode.closed_at is not None
    assert episode.closed_at.tzinfo is not None
    assert episode.opened_at.tzinfo is not None
