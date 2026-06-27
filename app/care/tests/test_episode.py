"""Exhaustive unit tests for the Episode aggregate (pure, no DB).

Covers every invariant + branch: happy paths, each rejection, and the
temporal/effective-dated cases (contiguous handoff has no gap and no overlap at
the boundary instant; coverage becomes effective in-window then expires).
"""

from __future__ import annotations

import pytest

from app.care.domain.episode import (
    BookingContact,
    Episode,
    EpisodeStatus,
    Membership,
    Responsibility,
)
from app.care.domain.exceptions import (
    EpisodeClosed,
    NotACurrentMember,
    OverlappingPeriod,
    SelfTreatment,
)
from app.care.domain.value_objects import EffectivePeriod, Role

from .conftest import (
    CLIENT,
    EPISODE_ID,
    ORG_ID,
    PROVIDER_A,
    PROVIDER_B,
    PROVIDER_C,
    at,
)


def _no_overlap(rows: tuple[Responsibility, ...] | tuple[BookingContact, ...]) -> bool:
    """True iff no two rows' periods overlap (the one-at-a-time invariant)."""
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if rows[i].period.overlaps(rows[j].period):
                return False
    return True


# --------------------------------------------------------------------------- #
# Episode.open
# --------------------------------------------------------------------------- #
class TestOpen:
    def test_opens_active(self, episode: Episode) -> None:
        assert episode.status is EpisodeStatus.ACTIVE
        assert episode.is_active is True
        assert episode.opened_at == at(0)
        assert episode.closed_at is None

    def test_has_one_current_responsibility(self, episode: Episode) -> None:
        r = episode.current_responsibility(at(0))
        assert r is not None
        assert r.provider_id == PROVIDER_A
        assert len(episode.responsibility) == 1

    def test_has_one_current_face(self, episode: Episode) -> None:
        f = episode.current_face(at(0))
        assert f is not None
        assert f.provider_id == PROVIDER_A
        assert len(episode.faces) == 1

    def test_has_one_membership(self, episode: Episode) -> None:
        assert episode.is_current_member(PROVIDER_A, at(0)) is True
        assert len(episode.memberships) == 1

    def test_invariants_hold_from_t0(self, episode: Episode) -> None:
        assert _no_overlap(episode.responsibility)
        assert _no_overlap(episode.faces)

    def test_self_treatment_at_open(self) -> None:
        with pytest.raises(SelfTreatment):
            Episode.open(
                id=EPISODE_ID,
                client_id=CLIENT,
                reason="r",
                managing_org_id=ORG_ID,
                now=at(0),
                responsible_provider_id=CLIENT,  # provider == client
                responsible_role=Role.PHYSICIAN,
                change_reason="open",
            )

    def test_divergent_initial_face(self) -> None:
        ep = Episode.open(
            id=EPISODE_ID,
            client_id=CLIENT,
            reason="r",
            managing_org_id=ORG_ID,
            now=at(0),
            responsible_provider_id=PROVIDER_A,
            responsible_role=Role.PHYSICIAN,
            change_reason="open",
            face_provider_id=PROVIDER_B,
            face_role=Role.PERSONAL_TRAINER,
        )
        resp = ep.current_responsibility(at(0))
        face = ep.current_face(at(0))
        assert resp is not None and resp.provider_id == PROVIDER_A
        assert face is not None and face.provider_id == PROVIDER_B
        # Both bootstrapped as members, no transient/zero-length rows.
        assert len(ep.memberships) == 2
        assert len(ep.responsibility) == 1
        assert len(ep.faces) == 1
        assert _no_overlap(ep.faces)

    def test_divergent_face_requires_role(self) -> None:
        with pytest.raises(ValueError, match="face_role is required"):
            Episode.open(
                id=EPISODE_ID,
                client_id=CLIENT,
                reason="r",
                managing_org_id=ORG_ID,
                now=at(0),
                responsible_provider_id=PROVIDER_A,
                responsible_role=Role.PHYSICIAN,
                change_reason="open",
                face_provider_id=PROVIDER_B,
                face_role=None,
            )

    def test_face_equal_responsible_explicit_is_single_member(self) -> None:
        ep = Episode.open(
            id=EPISODE_ID,
            client_id=CLIENT,
            reason="r",
            managing_org_id=ORG_ID,
            now=at(0),
            responsible_provider_id=PROVIDER_A,
            responsible_role=Role.PHYSICIAN,
            change_reason="open",
            face_provider_id=PROVIDER_A,  # explicitly same
            face_role=Role.PHYSICIAN,
        )
        assert len(ep.memberships) == 1
        face = ep.current_face(at(0))
        assert face is not None and face.provider_id == PROVIDER_A


# --------------------------------------------------------------------------- #
# add_member
# --------------------------------------------------------------------------- #
class TestAddMember:
    def test_happy(self, episode: Episode) -> None:
        m = episode.add_member(
            provider_id=PROVIDER_B,
            role=Role.PHYSICIAN,
            now=at(4),
            change_reason="khan adds patel",
        )
        assert isinstance(m, Membership)
        assert m.role is Role.PHYSICIAN
        assert episode.is_current_member(PROVIDER_B, at(4)) is True
        assert len(episode.memberships) == 2

    def test_default_effective_from_is_now(self, episode: Episode) -> None:
        m = episode.add_member(
            provider_id=PROVIDER_B, role=Role.PHYSICIAN, now=at(4), change_reason="x"
        )
        assert m.period.effective_from == at(4)
        assert m.period.is_open is True

    def test_future_dated_member_not_effective_yet(self, episode: Episode) -> None:
        episode.add_member(
            provider_id=PROVIDER_B,
            role=Role.PHYSICIAN,
            now=at(4),
            change_reason="x",
            effective_from=at(6),
        )
        assert episode.is_current_member(PROVIDER_B, at(4)) is False
        assert episode.is_current_member(PROVIDER_B, at(6)) is True

    def test_closed_episode_rejected(self, episode: Episode) -> None:
        episode.close(now=at(16))
        with pytest.raises(EpisodeClosed):
            episode.add_member(
                provider_id=PROVIDER_B, role=Role.PHYSICIAN, now=at(17), change_reason="x"
            )

    def test_self_treatment_rejected(self, episode: Episode) -> None:
        with pytest.raises(SelfTreatment):
            episode.add_member(
                provider_id=CLIENT, role=Role.PHYSICIAN, now=at(1), change_reason="x"
            )


# --------------------------------------------------------------------------- #
# start_coverage
# --------------------------------------------------------------------------- #
class TestStartCoverage:
    def test_effective_window(self, episode: Episode) -> None:
        episode.start_coverage(
            provider_id=PROVIDER_B,
            role=Role.PHYSIOTHERAPIST,
            effective_from=at(8),
            effective_to=at(10),
            now=at(8),
            change_reason="covering for khan",
        )
        assert episode.is_current_member(PROVIDER_B, at(7)) is False  # before
        assert episode.is_current_member(PROVIDER_B, at(9)) is True  # in-window
        assert episode.is_current_member(PROVIDER_B, at(10)) is False  # at-to (half-open)
        assert episode.is_current_member(PROVIDER_B, at(11)) is False  # after

    def test_coverage_does_not_change_responsibility(self, episode: Episode) -> None:
        episode.start_coverage(
            provider_id=PROVIDER_B,
            role=Role.PHYSIOTHERAPIST,
            effective_from=at(8),
            effective_to=at(10),
            now=at(8),
            change_reason="cover",
        )
        r = episode.current_responsibility(at(9))
        assert r is not None and r.provider_id == PROVIDER_A  # original still responsible
        assert len(episode.responsibility) == 1

    def test_coverage_does_not_change_face(self, episode: Episode) -> None:
        episode.start_coverage(
            provider_id=PROVIDER_B,
            role=Role.PHYSIOTHERAPIST,
            effective_from=at(8),
            effective_to=at(10),
            now=at(8),
            change_reason="cover",
        )
        f = episode.current_face(at(9))
        assert f is not None and f.provider_id == PROVIDER_A
        assert len(episode.faces) == 1

    def test_future_dated_coverage_allowed(self, episode: Episode) -> None:
        m = episode.start_coverage(
            provider_id=PROVIDER_B,
            role=Role.PHYSIOTHERAPIST,
            effective_from=at(20),
            effective_to=at(22),
            now=at(8),  # now is well before the window
            change_reason="planned cover",
        )
        assert m.period.effective_from == at(20)

    def test_closed_rejected(self, episode: Episode) -> None:
        episode.close(now=at(16))
        with pytest.raises(EpisodeClosed):
            episode.start_coverage(
                provider_id=PROVIDER_B,
                role=Role.PHYSIOTHERAPIST,
                effective_from=at(17),
                effective_to=at(18),
                now=at(17),
                change_reason="x",
            )

    def test_self_treatment_rejected(self, episode: Episode) -> None:
        with pytest.raises(SelfTreatment):
            episode.start_coverage(
                provider_id=CLIENT,
                role=Role.PHYSIOTHERAPIST,
                effective_from=at(8),
                effective_to=at(10),
                now=at(8),
                change_reason="x",
            )

    def test_invalid_window_rejected(self, episode: Episode) -> None:
        with pytest.raises(ValueError):
            episode.start_coverage(
                provider_id=PROVIDER_B,
                role=Role.PHYSIOTHERAPIST,
                effective_from=at(10),
                effective_to=at(8),  # inverted
                now=at(8),
                change_reason="x",
            )


# --------------------------------------------------------------------------- #
# assign_responsible
# --------------------------------------------------------------------------- #
class TestAssignResponsible:
    def _add_b(self, episode: Episode, week: int = 1) -> None:
        episode.add_member(
            provider_id=PROVIDER_B, role=Role.PHYSICIAN, now=at(week), change_reason="add b"
        )

    def test_reassign_to_another_member(self, episode: Episode) -> None:
        self._add_b(episode)
        new = episode.assign_responsible(provider_id=PROVIDER_B, now=at(5), change_reason="handoff")
        assert new.provider_id == PROVIDER_B
        assert len(episode.responsibility) == 2

    def test_contiguous_handoff_no_gap_no_overlap(self, episode: Episode) -> None:
        self._add_b(episode)
        episode.assign_responsible(provider_id=PROVIDER_B, now=at(5), change_reason="h")
        # No overlap across all rows.
        assert _no_overlap(episode.responsibility)
        # Just before the boundary -> old holder A.
        before = episode.current_responsibility(at(5) - (at(5) - at(4)) / 10000)
        assert before is not None and before.provider_id == PROVIDER_A
        # At the boundary instant -> exactly one effective, the NEW holder B.
        effective_at_boundary = [r for r in episode.responsibility if r.is_effective_at(at(5))]
        assert len(effective_at_boundary) == 1
        assert effective_at_boundary[0].provider_id == PROVIDER_B
        # The old row is now closed exactly at the boundary -> [t0, 5).
        old = next(r for r in episode.responsibility if r.provider_id == PROVIDER_A)
        assert old.period.effective_to == at(5)

    def test_non_member_rejected(self, episode: Episode) -> None:
        with pytest.raises(NotACurrentMember):
            episode.assign_responsible(provider_id=PROVIDER_C, now=at(5), change_reason="x")

    def test_future_member_rejected(self, episode: Episode) -> None:
        # B becomes a member only at week 6.
        episode.add_member(
            provider_id=PROVIDER_B,
            role=Role.PHYSICIAN,
            now=at(1),
            change_reason="x",
            effective_from=at(6),
        )
        with pytest.raises(NotACurrentMember):
            episode.assign_responsible(provider_id=PROVIDER_B, now=at(5), change_reason="too early")

    def test_expired_member_rejected_at_exact_to(self, episode: Episode) -> None:
        # B covers [4, 8); at exactly 8 the membership is no longer effective.
        episode.start_coverage(
            provider_id=PROVIDER_B,
            role=Role.PHYSIOTHERAPIST,
            effective_from=at(4),
            effective_to=at(8),
            now=at(4),
            change_reason="cover",
        )
        with pytest.raises(NotACurrentMember):
            episode.assign_responsible(provider_id=PROVIDER_B, now=at(8), change_reason="expired")

    def test_member_starting_exactly_now_passes(self, episode: Episode) -> None:
        episode.add_member(
            provider_id=PROVIDER_B,
            role=Role.PHYSICIAN,
            now=at(1),
            change_reason="x",
            effective_from=at(5),
        )
        new = episode.assign_responsible(
            provider_id=PROVIDER_B, now=at(5), change_reason="exactly at from"
        )
        assert new.provider_id == PROVIDER_B

    def test_self_treatment_rejected(self, episode: Episode) -> None:
        with pytest.raises(SelfTreatment):
            episode.assign_responsible(provider_id=CLIENT, now=at(5), change_reason="x")

    def test_closed_rejected(self, episode: Episode) -> None:
        episode.close(now=at(16))
        with pytest.raises(EpisodeClosed):
            episode.assign_responsible(provider_id=PROVIDER_A, now=at(17), change_reason="x")

    def test_reassign_same_provider_is_noop(self, episode: Episode) -> None:
        current = episode.current_responsibility(at(0))
        result = episode.assign_responsible(
            provider_id=PROVIDER_A, now=at(5), change_reason="redundant"
        )
        assert result is current  # same row, no new row
        assert len(episode.responsibility) == 1

    def test_reassign_at_open_instant_to_different_provider_rejected(
        self, episode: Episode
    ) -> None:
        # B is a member from t0 too (add at t0), then try to reassign at t0:
        # closing A's [t0, None) at t0 would be a zero-length [t0, t0) -> reject.
        episode.add_member(
            provider_id=PROVIDER_B,
            role=Role.PHYSICIAN,
            now=at(0),
            change_reason="add b at t0",
        )
        with pytest.raises(OverlappingPeriod):
            episode.assign_responsible(
                provider_id=PROVIDER_B, now=at(0), change_reason="same instant"
            )


# --------------------------------------------------------------------------- #
# set_face
# --------------------------------------------------------------------------- #
class TestSetFace:
    def _add_b(self, episode: Episode) -> None:
        episode.add_member(
            provider_id=PROVIDER_B, role=Role.PHYSICIAN, now=at(1), change_reason="add b"
        )

    def test_reassign_face_contiguous(self, episode: Episode) -> None:
        self._add_b(episode)
        new = episode.set_face(provider_id=PROVIDER_B, now=at(5), change_reason="handoff")
        assert isinstance(new, BookingContact)
        assert new.provider_id == PROVIDER_B
        assert len(episode.faces) == 2
        assert _no_overlap(episode.faces)
        effective_at_boundary = [f for f in episode.faces if f.is_effective_at(at(5))]
        assert len(effective_at_boundary) == 1
        assert effective_at_boundary[0].provider_id == PROVIDER_B
        old = next(f for f in episode.faces if f.provider_id == PROVIDER_A)
        assert old.period.effective_to == at(5)

    def test_non_member_rejected(self, episode: Episode) -> None:
        with pytest.raises(NotACurrentMember):
            episode.set_face(provider_id=PROVIDER_C, now=at(5), change_reason="x")

    def test_future_member_rejected(self, episode: Episode) -> None:
        episode.add_member(
            provider_id=PROVIDER_B,
            role=Role.PHYSICIAN,
            now=at(1),
            change_reason="x",
            effective_from=at(6),
        )
        with pytest.raises(NotACurrentMember):
            episode.set_face(provider_id=PROVIDER_B, now=at(5), change_reason="early")

    def test_expired_member_rejected_at_exact_to(self, episode: Episode) -> None:
        episode.start_coverage(
            provider_id=PROVIDER_B,
            role=Role.PHYSIOTHERAPIST,
            effective_from=at(4),
            effective_to=at(8),
            now=at(4),
            change_reason="cover",
        )
        with pytest.raises(NotACurrentMember):
            episode.set_face(provider_id=PROVIDER_B, now=at(8), change_reason="expired")

    def test_closed_rejected(self, episode: Episode) -> None:
        episode.close(now=at(16))
        with pytest.raises(EpisodeClosed):
            episode.set_face(provider_id=PROVIDER_A, now=at(17), change_reason="x")

    def test_same_provider_is_noop(self, episode: Episode) -> None:
        current = episode.current_face(at(0))
        result = episode.set_face(provider_id=PROVIDER_A, now=at(5), change_reason="redundant")
        assert result is current
        assert len(episode.faces) == 1

    def test_face_independent_of_responsibility(self, episode: Episode) -> None:
        self._add_b(episode)
        episode.set_face(provider_id=PROVIDER_B, now=at(5), change_reason="face only")
        # Responsibility unchanged.
        r = episode.current_responsibility(at(5))
        assert r is not None and r.provider_id == PROVIDER_A
        # Face changed.
        f = episode.current_face(at(5))
        assert f is not None and f.provider_id == PROVIDER_B

    def test_responsibility_independent_of_face(self, episode: Episode) -> None:
        self._add_b(episode)
        episode.assign_responsible(provider_id=PROVIDER_B, now=at(5), change_reason="resp only")
        f = episode.current_face(at(5))
        assert f is not None and f.provider_id == PROVIDER_A  # face unchanged


# --------------------------------------------------------------------------- #
# end_member
# --------------------------------------------------------------------------- #
class TestEndMember:
    def _add_b(self, episode: Episode, week: int = 1) -> None:
        episode.add_member(
            provider_id=PROVIDER_B, role=Role.PHYSICIAN, now=at(week), change_reason="add b"
        )

    def test_end_plain_member(self, episode: Episode) -> None:
        self._add_b(episode)
        m = episode.end_member(
            provider_id=PROVIDER_B, effective_to=at(10), now=at(8), change_reason="left"
        )
        assert m.period.effective_to == at(10)
        assert episode.is_current_member(PROVIDER_B, at(9)) is True  # still in [1,10)
        assert episode.is_current_member(PROVIDER_B, at(10)) is False  # ended

    def test_end_records_change_reason(self, episode: Episode) -> None:
        self._add_b(episode)
        m = episode.end_member(
            provider_id=PROVIDER_B, effective_to=at(10), now=at(8), change_reason="resigned"
        )
        assert m.change_reason == "resigned"

    def test_end_non_member_rejected(self, episode: Episode) -> None:
        with pytest.raises(NotACurrentMember):
            episode.end_member(
                provider_id=PROVIDER_C, effective_to=at(10), now=at(8), change_reason="x"
            )

    def test_end_current_responsible_rejected(self, episode: Episode) -> None:
        # A is the responsible provider; cannot end A while active.
        with pytest.raises(NotACurrentMember):
            episode.end_member(
                provider_id=PROVIDER_A, effective_to=at(10), now=at(8), change_reason="x"
            )

    def test_end_responsible_after_reassign_ok(self, episode: Episode) -> None:
        self._add_b(episode)
        # Hand off BOTH responsibility and the face away from A first, so A is a
        # plain member who can be future-/back-dated end-dated freely.
        episode.assign_responsible(provider_id=PROVIDER_B, now=at(5), change_reason="resp->b")
        episode.set_face(provider_id=PROVIDER_B, now=at(5), change_reason="face->b")
        m = episode.end_member(
            provider_id=PROVIDER_A, effective_to=at(10), now=at(8), change_reason="step back"
        )
        assert m.provider_id == PROVIDER_A
        assert m.period.effective_to == at(10)

    def test_end_current_face_requires_successor(self, episode: Episode) -> None:
        self._add_b(episode)
        # A is the face; ending A without naming a successor must reject.
        with pytest.raises(NotACurrentMember):
            episode.end_member(
                provider_id=PROVIDER_A,
                effective_to=at(8),
                now=at(8),
                change_reason="x",
                successor_face_id=None,
            )

    def test_end_current_face_with_successor(self, episode: Episode) -> None:
        # Make B the responsible provider so A is only the face (not responsible),
        # then end A as the face handing off to B.
        self._add_b(episode)
        episode.assign_responsible(provider_id=PROVIDER_B, now=at(5), change_reason="resp->b")
        m = episode.end_member(
            provider_id=PROVIDER_A,
            effective_to=at(8),
            now=at(8),
            change_reason="face handoff",
            successor_face_id=PROVIDER_B,
        )
        assert m.period.effective_to == at(8)
        # Face handed to B at the same instant; no gap/overlap.
        f = episode.current_face(at(8))
        assert f is not None and f.provider_id == PROVIDER_B
        assert _no_overlap(episode.faces)

    def test_end_current_face_successor_must_be_member(self, episode: Episode) -> None:
        self._add_b(episode)
        episode.assign_responsible(provider_id=PROVIDER_B, now=at(5), change_reason="resp->b")
        with pytest.raises(NotACurrentMember):
            episode.end_member(
                provider_id=PROVIDER_A,
                effective_to=at(8),
                now=at(8),
                change_reason="x",
                successor_face_id=PROVIDER_C,  # not a member
            )

    def test_end_current_face_successor_cannot_be_leaver(self, episode: Episode) -> None:
        self._add_b(episode)
        episode.assign_responsible(provider_id=PROVIDER_B, now=at(5), change_reason="resp->b")
        with pytest.raises(NotACurrentMember):
            episode.end_member(
                provider_id=PROVIDER_A,
                effective_to=at(8),
                now=at(8),
                change_reason="x",
                successor_face_id=PROVIDER_A,  # the leaver themselves
            )

    def test_end_current_face_effective_to_must_equal_now(self, episode: Episode) -> None:
        self._add_b(episode)
        episode.assign_responsible(provider_id=PROVIDER_B, now=at(5), change_reason="resp->b")
        with pytest.raises(OverlappingPeriod):
            episode.end_member(
                provider_id=PROVIDER_A,
                effective_to=at(10),  # future-dated face handoff not allowed
                now=at(8),
                change_reason="x",
                successor_face_id=PROVIDER_B,
            )

    def test_closed_rejected(self, episode: Episode) -> None:
        self._add_b(episode)
        episode.close(now=at(16))
        with pytest.raises(EpisodeClosed):
            episode.end_member(
                provider_id=PROVIDER_B, effective_to=at(17), now=at(17), change_reason="x"
            )

    def test_end_member_degenerate_effective_to_rejected(self, episode: Episode) -> None:
        # Ending a plain member at their own start instant -> zero-length -> reject.
        self._add_b(episode, week=4)
        with pytest.raises(OverlappingPeriod):
            episode.end_member(
                provider_id=PROVIDER_B,
                effective_to=at(4),  # == effective_from
                now=at(4),
                change_reason="x",
            )


# --------------------------------------------------------------------------- #
# close
# --------------------------------------------------------------------------- #
class TestClose:
    def test_close_sets_status_and_timestamp(self, episode: Episode) -> None:
        episode.close(now=at(16))
        assert episode.status is EpisodeStatus.CLOSED
        assert episode.is_active is False
        assert episode.closed_at == at(16)

    def test_close_does_not_end_date_members(self, episode: Episode) -> None:
        episode.close(now=at(16))
        # Membership still open and still effective AFTER closed_at.
        m = episode.memberships[0]
        assert m.is_open is True
        assert m.is_effective_at(at(20)) is True

    def test_close_does_not_end_date_responsibility(self, episode: Episode) -> None:
        episode.close(now=at(16))
        r = episode.responsibility[0]
        assert r.period.is_open is True
        assert r.is_effective_at(at(20)) is True

    def test_close_does_not_end_date_face(self, episode: Episode) -> None:
        episode.close(now=at(16))
        f = episode.faces[0]
        assert f.period.is_open is True
        assert f.is_effective_at(at(20)) is True

    def test_already_closed_rejected(self, episode: Episode) -> None:
        episode.close(now=at(16))
        with pytest.raises(EpisodeClosed):
            episode.close(now=at(17))


# --------------------------------------------------------------------------- #
# Lifecycle / temporal integration (Sara walkthrough, condensed)
# --------------------------------------------------------------------------- #
class TestLifecycleIntegration:
    def test_coverage_then_expiry_with_stable_responsibility(self, episode: Episode) -> None:
        # Wk4: Khan(A) adds Patel(B).
        episode.add_member(
            provider_id=PROVIDER_B, role=Role.PHYSICIAN, now=at(4), change_reason="add patel"
        )
        # Wk8: Lee(C) covers [8,10).
        episode.start_coverage(
            provider_id=PROVIDER_C,
            role=Role.PHYSIOTHERAPIST,
            effective_from=at(8),
            effective_to=at(10),
            now=at(8),
            change_reason="khan on leave",
        )
        # Wk9: Lee effective; Khan still responsible (cover is membership only).
        assert episode.is_current_member(PROVIDER_C, at(9)) is True
        r9 = episode.current_responsibility(at(9))
        assert r9 is not None and r9.provider_id == PROVIDER_A
        # Wk11: Lee's cover expired automatically (half-open, gone at/after 10).
        assert episode.is_current_member(PROVIDER_C, at(11)) is False
        # Wk16: close; former members keep history.
        episode.close(now=at(16))
        assert episode.status is EpisodeStatus.CLOSED
        assert episode.is_current_member(PROVIDER_A, at(20)) is True  # open membership persists

    def test_handoff_boundary_is_exact(self, episode: Episode) -> None:
        episode.add_member(
            provider_id=PROVIDER_B, role=Role.PHYSICIAN, now=at(1), change_reason="add b"
        )
        episode.assign_responsible(provider_id=PROVIDER_B, now=at(5), change_reason="handoff")
        # Build a microsecond-scale neighborhood around the boundary.
        eps = (at(6) - at(5)) / 1_000_000
        just_before = at(5) - eps
        just_after = at(5) + eps
        rb = episode.current_responsibility(just_before)
        rb_at = episode.current_responsibility(at(5))
        ra = episode.current_responsibility(just_after)
        assert rb is not None and rb.provider_id == PROVIDER_A
        assert rb_at is not None and rb_at.provider_id == PROVIDER_B  # boundary -> new
        assert ra is not None and ra.provider_id == PROVIDER_B


class TestReconstitute:
    """``Episode.reconstitute`` rebuilds the aggregate from persisted rows.

    It is additive and pure: it must populate the three child collections,
    preserve status/closed_at, let the "current" derivations work, and - crucially
    - NOT re-run mutator invariants, so historical closed/reassigned rows load
    without raising (the repository depends on this on every ``get``).
    """

    def _reassigned_closed_episode(self) -> Episode:
        # A history with a contiguous responsibility handoff A -> B and a closed
        # episode: exactly the shape a real reassign-then-close run persists.
        memberships = [
            Membership(
                provider_id=PROVIDER_A,
                period=EffectivePeriod(at(0), None),
                change_reason="opened",
                role=Role.PHYSIOTHERAPIST,
            ),
            Membership(
                provider_id=PROVIDER_B,
                period=EffectivePeriod(at(1), None),
                change_reason="added",
                role=Role.PHYSICIAN,
            ),
        ]
        responsibility = [
            Responsibility(
                provider_id=PROVIDER_A,
                period=EffectivePeriod(at(0), at(2)),
                change_reason="opened",
            ),
            Responsibility(
                provider_id=PROVIDER_B,
                period=EffectivePeriod(at(2), None),
                change_reason="handoff",
            ),
        ]
        faces = [
            BookingContact(
                provider_id=PROVIDER_A,
                period=EffectivePeriod(at(0), None),
                change_reason="opened",
            ),
        ]
        return Episode.reconstitute(
            id=EPISODE_ID,
            client_id=CLIENT,
            reason="shoulder_rehab",
            managing_org_id=ORG_ID,
            opened_at=at(0),
            status=EpisodeStatus.CLOSED,
            closed_at=at(3),
            memberships=memberships,
            responsibility=responsibility,
            faces=faces,
        )

    def test_populates_collections(self) -> None:
        episode = self._reassigned_closed_episode()
        assert len(episode.memberships) == 2
        assert len(episode.responsibility) == 2
        assert len(episode.faces) == 1

    def test_preserves_root_fields(self) -> None:
        episode = self._reassigned_closed_episode()
        assert episode.id == EPISODE_ID
        assert episode.client_id == CLIENT
        assert episode.reason == "shoulder_rehab"
        assert episode.managing_org_id == ORG_ID
        assert episode.opened_at == at(0)
        assert episode.status is EpisodeStatus.CLOSED
        assert episode.is_active is False
        assert episode.closed_at == at(3)

    def test_current_derivations_work(self) -> None:
        episode = self._reassigned_closed_episode()
        # The handoff boundary at(2) resolves to the new (half-open) holder.
        before = episode.current_responsibility(at(1))
        after = episode.current_responsibility(at(2))
        assert before is not None and before.provider_id == PROVIDER_A
        assert after is not None and after.provider_id == PROVIDER_B
        assert episode.is_current_member(PROVIDER_B, at(2)) is True
        face = episode.current_face(at(1))
        assert face is not None and face.provider_id == PROVIDER_A

    def test_does_not_rerun_invariants(self) -> None:
        # No exception despite a closed episode + a contiguous-but-historical
        # reassignment: reconstitute assigns the private lists directly, running
        # neither the closed-guard nor the no-overlap assertion.
        episode = self._reassigned_closed_episode()
        assert episode.status is EpisodeStatus.CLOSED

    def test_reconstituted_closed_episode_is_immutable(self) -> None:
        episode = self._reassigned_closed_episode()
        with pytest.raises(EpisodeClosed):
            episode.add_member(
                provider_id=PROVIDER_C, role=Role.PHYSICIAN, now=at(4), change_reason="late"
            )

    def test_reconstitute_active_episode_remains_mutable(self) -> None:
        episode = Episode.reconstitute(
            id=EPISODE_ID,
            client_id=CLIENT,
            reason="shoulder_rehab",
            managing_org_id=ORG_ID,
            opened_at=at(0),
            status=EpisodeStatus.ACTIVE,
            closed_at=None,
            memberships=[
                Membership(
                    provider_id=PROVIDER_A,
                    period=EffectivePeriod(at(0), None),
                    change_reason="opened",
                    role=Role.PHYSIOTHERAPIST,
                )
            ],
            responsibility=[
                Responsibility(
                    provider_id=PROVIDER_A,
                    period=EffectivePeriod(at(0), None),
                    change_reason="opened",
                )
            ],
            faces=[
                BookingContact(
                    provider_id=PROVIDER_A,
                    period=EffectivePeriod(at(0), None),
                    change_reason="opened",
                )
            ],
        )
        # A still-active reconstituted episode accepts further mutation.
        episode.add_member(
            provider_id=PROVIDER_B, role=Role.PHYSICIAN, now=at(1), change_reason="b"
        )
        assert episode.is_current_member(PROVIDER_B, at(1)) is True


class TestDefensiveOverlapGuard:
    """Directly exercise the belt-and-braces no-overlap assertion.

    The public contiguous-handoff logic should never produce overlapping
    responsibility/face rows, so this guard is unreachable through normal use.
    We corrupt the internal list to prove the safety net actually fires if an
    overlap ever slips through.
    """

    def test_assign_responsible_overlap_guard_fires(self, episode: Episode) -> None:
        episode.add_member(
            provider_id=PROVIDER_B, role=Role.PHYSICIAN, now=at(1), change_reason="x"
        )
        # Smuggle in an overlapping open responsibility row bypassing the root.
        episode._responsibility.append(
            Responsibility(
                provider_id=PROVIDER_C,
                period=EffectivePeriod(at(2), None),
                change_reason="corrupt",
            )
        )
        with pytest.raises(OverlappingPeriod):
            episode.assign_responsible(
                provider_id=PROVIDER_B, now=at(5), change_reason="trips guard"
            )
