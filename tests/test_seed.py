"""Integration tests for the idempotent Sara-world seed (real Postgres, A19).

Each test runs the ``seed()`` composition into the SAME per-test ``db_session``
(savepoint-joined, rolled back at teardown), so the clean slate means every row
count reflects ONLY the seed. The seed builds the world purely through the
existing application services; these tests assert the end state, the controlled
vocabulary, the bounded coverage window, and - centrally - idempotency: a second
run in the same session adds no rows, raises nothing, and returns the same ids.

Currency / boundary checks use the real ``Episode`` aggregate query methods and
the half-open ``EffectivePeriod`` semantics. The commit test spies on the
session's ``commit`` directly, NOT relying on the rollback harness as proof.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.care.orm import (
    BookingContactModel,
    EpisodeMembershipModel,
    EpisodeModel,
    ResponsibilityAssignmentModel,
    _CareChildModel,
)
from app.care.repository import SqlAlchemyEpisodeRepository
from app.care.service import get_episode
from app.core.database import Base
from app.identity.domain.value_objects import ProfileType
from app.identity.orm import IdentityModel, ProfileModel
from app.identity.repository import SqlAlchemyProfileRepository
from app.identity.service import has_active_profile
from app.organization.orm import OrganizationModel, OrgStaffMembershipModel
from app.organization.repository import SqlAlchemyOrgStaffMembershipRepository
from app.organization.service import has_active_admin_membership
from scripts.seed import SEED_EPOCH, SaraWorld, seed

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_EIGHT_WEEKS = timedelta(weeks=8)
_TEN_WEEKS = timedelta(weeks=10)


# --- query helpers -----------------------------------------------------------


def _count(session: Session, model: type[Base]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _count_child(session: Session, model: type[_CareChildModel], episode_id: UUID) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(model).where(model.episode_id == episode_id)
        )
        or 0
    )


def _snapshot_counts(session: Session) -> dict[str, int]:
    """Row counts for every table the seed touches (for idempotency diffing)."""
    return {
        "identities": _count(session, IdentityModel),
        "profiles": _count(session, ProfileModel),
        "organizations": _count(session, OrganizationModel),
        "org_staff_memberships": _count(session, OrgStaffMembershipModel),
        "episodes": _count(session, EpisodeModel),
        "episode_memberships": _count(session, EpisodeMembershipModel),
        "responsibility_assignments": _count(session, ResponsibilityAssignmentModel),
        "booking_contacts": _count(session, BookingContactModel),
    }


# --- shape & cardinality -----------------------------------------------------


def test_seed_returns_sara_world_with_all_ids(db_session: Session) -> None:
    world = seed(db_session)
    ids = [
        world.sara,
        world.mike,
        world.khan,
        world.patel,
        world.lee,
        world.org_admin,
        world.fitgym,
        world.khan_solo,
        world.general,
        world.shoulder,
    ]
    assert all(isinstance(i, UUID) for i in ids)
    assert len(set(ids)) == 10


def test_seed_creates_expected_identity_and_profile_counts(db_session: Session) -> None:
    seed(db_session)
    assert _count(db_session, IdentityModel) == 6
    assert _count(db_session, ProfileModel) == 6


def test_seed_creates_two_organizations(db_session: Session) -> None:
    seed(db_session)
    assert _count(db_session, OrganizationModel) == 2


def test_seed_creates_two_episodes(db_session: Session) -> None:
    seed(db_session)
    assert _count(db_session, EpisodeModel) == 2


# --- org-staff membership ----------------------------------------------------


def test_seed_org_admin_membership_has_role_admin(db_session: Session) -> None:
    world = seed(db_session)
    repo = SqlAlchemyOrgStaffMembershipRepository(db_session)
    assert has_active_admin_membership(repo, world.org_admin, world.fitgym, SEED_EPOCH) is True
    rows = db_session.scalars(
        select(OrgStaffMembershipModel).where(OrgStaffMembershipModel.org_id == world.fitgym)
    ).all()
    assert len(rows) == 1
    assert rows[0].role == "admin"


def test_exactly_one_fitgym_admin_row_full_tuple(db_session: Session) -> None:
    world = seed(db_session)
    rows = db_session.scalars(
        select(OrgStaffMembershipModel).where(
            OrgStaffMembershipModel.identity_id == world.org_admin,
            OrgStaffMembershipModel.org_id == world.fitgym,
            OrgStaffMembershipModel.role == "admin",
            OrgStaffMembershipModel.effective_from == SEED_EPOCH,
            OrgStaffMembershipModel.effective_to.is_(None),
        )
    ).all()
    assert len(rows) == 1


def test_khan_solo_practice_is_staffless(db_session: Session) -> None:
    world = seed(db_session)
    rows = db_session.scalars(
        select(OrgStaffMembershipModel).where(OrgStaffMembershipModel.org_id == world.khan_solo)
    ).all()
    assert len(rows) == 0


def test_exactly_one_active_profile_per_identity_and_type(db_session: Session) -> None:
    world = seed(db_session)
    expected = {
        (world.sara, "client"),
        (world.mike, "provider"),
        (world.khan, "provider"),
        (world.patel, "provider"),
        (world.lee, "provider"),
        (world.org_admin, "org_staff"),
    }
    for identity_id, profile_type in expected:
        rows = db_session.scalars(
            select(ProfileModel).where(
                ProfileModel.identity_id == identity_id,
                ProfileModel.profile_type == profile_type,
                ProfileModel.discarded_at.is_(None),
            )
        ).all()
        assert len(rows) == 1


# --- episodes: management, cardinality, roster -------------------------------


def test_episodes_managed_by_correct_orgs(db_session: Session) -> None:
    world = seed(db_session)
    repo = SqlAlchemyEpisodeRepository(db_session)
    general = get_episode(repo, world.general)
    shoulder = get_episode(repo, world.shoulder)
    assert general is not None and general.managing_org_id == world.fitgym
    assert shoulder is not None and shoulder.managing_org_id == world.khan_solo


def test_general_training_child_row_cardinality(db_session: Session) -> None:
    world = seed(db_session)
    assert _count_child(db_session, EpisodeMembershipModel, world.general) == 1
    assert _count_child(db_session, ResponsibilityAssignmentModel, world.general) == 1
    assert _count_child(db_session, BookingContactModel, world.general) == 1


def test_shoulder_rehab_child_row_cardinality(db_session: Session) -> None:
    world = seed(db_session)
    assert _count_child(db_session, EpisodeMembershipModel, world.shoulder) == 3
    assert _count_child(db_session, ResponsibilityAssignmentModel, world.shoulder) == 1
    assert _count_child(db_session, BookingContactModel, world.shoulder) == 1


def test_general_training_responsible_and_face_are_mike(db_session: Session) -> None:
    world = seed(db_session)
    general = get_episode(SqlAlchemyEpisodeRepository(db_session), world.general)
    assert general is not None
    responsible = general.current_responsibility(SEED_EPOCH)
    face = general.current_face(SEED_EPOCH)
    assert responsible is not None and responsible.provider_id == world.mike
    assert face is not None and face.provider_id == world.mike
    assert general.is_current_member(world.mike, SEED_EPOCH) is True


def test_shoulder_rehab_responsible_and_face_are_khan(db_session: Session) -> None:
    world = seed(db_session)
    shoulder = get_episode(SqlAlchemyEpisodeRepository(db_session), world.shoulder)
    assert shoulder is not None
    responsible = shoulder.current_responsibility(SEED_EPOCH)
    face = shoulder.current_face(SEED_EPOCH)
    assert responsible is not None and responsible.provider_id == world.khan
    assert face is not None and face.provider_id == world.khan
    assert shoulder.is_current_member(world.khan, SEED_EPOCH) is True


def test_shoulder_rehab_members_include_khan_patel_lee(db_session: Session) -> None:
    world = seed(db_session)
    shoulder = get_episode(SqlAlchemyEpisodeRepository(db_session), world.shoulder)
    assert shoulder is not None
    provider_ids = {m.provider_id for m in shoulder.memberships}
    assert provider_ids == {world.khan, world.patel, world.lee}
    roles = sorted(m.role.value for m in shoulder.memberships)
    assert roles == sorted(["physiotherapist", "physician", "physiotherapist"])


# --- bounded coverage window (Lee) -------------------------------------------


def test_lee_coverage_window_boundaries(db_session: Session) -> None:
    world = seed(db_session)
    shoulder = get_episode(SqlAlchemyEpisodeRepository(db_session), world.shoulder)
    assert shoulder is not None
    # Half-open [now+8w, now+10w): NOT current before the window, current inside,
    # NOT current at the exclusive upper bound.
    assert shoulder.is_current_member(world.lee, SEED_EPOCH) is False
    assert shoulder.is_current_member(world.lee, SEED_EPOCH + timedelta(weeks=9)) is True
    assert shoulder.is_current_member(world.lee, SEED_EPOCH + _TEN_WEEKS) is False


def test_lee_membership_bounded_window_values(db_session: Session) -> None:
    world = seed(db_session)
    shoulder = get_episode(SqlAlchemyEpisodeRepository(db_session), world.shoulder)
    assert shoulder is not None
    lee_rows = [m for m in shoulder.memberships if m.provider_id == world.lee]
    assert len(lee_rows) == 1
    assert lee_rows[0].period.effective_from == SEED_EPOCH + _EIGHT_WEEKS
    assert lee_rows[0].period.effective_to == SEED_EPOCH + _TEN_WEEKS


# --- profiles active & vocabulary --------------------------------------------


def test_provider_profiles_are_active(db_session: Session) -> None:
    world = seed(db_session)
    repo = SqlAlchemyProfileRepository(db_session)
    for provider_id in (world.mike, world.khan, world.patel, world.lee):
        assert has_active_profile(repo, provider_id, ProfileType.PROVIDER) is True
    assert has_active_profile(repo, world.sara, ProfileType.CLIENT) is True
    assert has_active_profile(repo, world.org_admin, ProfileType.ORG_STAFF) is True


def test_seed_uses_correct_vocabulary(db_session: Session) -> None:
    world = seed(db_session)
    fitgym = db_session.get(OrganizationModel, world.fitgym)
    khan_solo = db_session.get(OrganizationModel, world.khan_solo)
    assert fitgym is not None and fitgym.type == "gym"
    assert khan_solo is not None and khan_solo.type == "solo_practice"
    general = db_session.get(EpisodeModel, world.general)
    shoulder = db_session.get(EpisodeModel, world.shoulder)
    assert general is not None and general.reason == "general_training"
    assert shoulder is not None and shoulder.reason == "shoulder_rehab"
    admin_row = db_session.scalars(
        select(OrgStaffMembershipModel).where(OrgStaffMembershipModel.org_id == world.fitgym)
    ).one()
    assert admin_row.role == "admin"


# --- idempotency -------------------------------------------------------------


def test_seed_is_idempotent_no_exception(db_session: Session) -> None:
    seed(db_session)
    seed(db_session)  # second convergent run must not raise


def test_seed_idempotent_counts_unchanged(db_session: Session) -> None:
    seed(db_session)
    before = _snapshot_counts(db_session)
    seed(db_session)
    after = _snapshot_counts(db_session)
    assert before == after


def test_lee_rerun_single_bounded_membership(db_session: Session) -> None:
    world = seed(db_session)
    seed(db_session)
    shoulder = get_episode(SqlAlchemyEpisodeRepository(db_session), world.shoulder)
    assert shoulder is not None
    lee_rows = [m for m in shoulder.memberships if m.provider_id == world.lee]
    # Provider-id dedup (not is_current_member): exactly one Lee row, same window.
    assert len(lee_rows) == 1
    assert lee_rows[0].period.effective_from == SEED_EPOCH + _EIGHT_WEEKS
    assert lee_rows[0].period.effective_to == SEED_EPOCH + _TEN_WEEKS


def test_seed_idempotent_returns_same_ids(db_session: Session) -> None:
    first = seed(db_session)
    second = seed(db_session)
    assert isinstance(first, SaraWorld)
    assert first == second


def test_seed_does_not_commit(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Mock(wraps=db_session.commit)
    monkeypatch.setattr(db_session, "commit", spy)
    seed(db_session)
    spy.assert_not_called()


# --- setup wiring guard ------------------------------------------------------


def test_setup_invokes_scripts_seed() -> None:
    setup = (_PROJECT_ROOT / "setup.sh").read_text()
    assert "scripts.seed" in setup
    assert "app.seed" not in setup
    assert not (_PROJECT_ROOT / "app" / "seed.py").exists()
