"""Named ``/v1`` access-scenario suite (item 12): S1..S7 + both MANAGE_TEAM paths.

Drives the REAL app through the ``client`` fixture against the per-test, rolled-back
Postgres transaction (A19). ``get_current_user`` is NOT overridden, so the full
authentication + two-layer authorization path runs end to end for every request.
The world is the named ``build_sara_world`` (Sara actors, Shoulder-Rehab topology),
with coverage windows anchored to the fixed ``clock`` so "current at now" flips by
time travel rather than by reshaping the world.

Auth as an actor: ``headers=auth_header(mint_token(str(sara_world.<actor>)))``.
``acting_as`` is a REQUIRED query param on the multi-surface routes (``GET
/episodes/{id}`` and every team mutation); it is omitted on the provider-only
``clinical-records`` / ``rehab-assessments`` routes.

S3 / S7 time travel (see ``app/core/security.py`` + ``app/identity/deps.py``): the
injected ``get_now`` is reassigned on the live app AND the actor token is RE-MINTED
at that same instant, otherwise ``decode_access_token`` (which checks ``iat`` / ``exp``
against the injected ``now``) returns a spurious 401 that masks the intended status.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.deps import get_now
from tests._world import SaraTestWorld, auth_header, build_sara_world

_EPISODES = "/v1/episodes"


@pytest.fixture
def sara_world(db_session: Session, clock: datetime) -> SaraTestWorld:
    return build_sara_world(db_session, clock)


def _fixed_now(instant: datetime) -> Callable[[], datetime]:
    """A zero-arg ``get_now`` override pinned to ``instant`` (no late binding)."""

    def _override() -> datetime:
        return instant

    return _override


def _set_now(client: TestClient, instant: datetime) -> None:
    """Reassign the live app's ``get_now`` override to ``instant`` (S3 / S7 recipe)."""
    cast(FastAPI, client.app).dependency_overrides[get_now] = _fixed_now(instant)


# --- harness smoke ----------------------------------------------------------- #


def test_sara_world_smoke(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str]
) -> None:
    """The builder stands up the named Shoulder topology (RED before the builder exists)."""
    resp = client.get(
        f"{_EPISODES}/{sara_world.shoulder}",
        headers=auth_header(mint_token(str(sara_world.khan))),
        params={"acting_as": "provider"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"
    assert data["responsible_provider_id"] == str(sara_world.khan)
    assert data["face_provider_id"] == str(sara_world.khan)
    member_ids = {m["provider_id"] for m in data["members"]}
    assert {str(sara_world.khan), str(sara_world.patel), str(sara_world.marco)} <= member_ids


# --- MANAGE_TEAM path (a): org-admin over the managing-org episode ------------ #


def test_manage_team_path_a_org_admin_manages_managing_org_episode(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str]
) -> None:
    """Olivia (FitGym admin) manages the FitGym-managed General episode (policy branch 4)."""
    resp = client.post(
        f"{_EPISODES}/{sara_world.general}/members",
        headers=auth_header(mint_token(str(sara_world.olivia))),
        params={"acting_as": "org_staff"},
        json={
            "provider_id": str(sara_world.extra_provider),
            "role": "physician",
            "change_reason": "add",
        },
    )
    assert resp.status_code == 201
    assert str(sara_world.extra_provider) in {m["provider_id"] for m in resp.json()["members"]}


# --- MANAGE_TEAM path (b): responsible provider, despite the role grid -------- #


def test_manage_team_path_b_responsible_provider_grant_despite_grid(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str]
) -> None:
    """Khan (physiotherapist, grid lacks MANAGE_TEAM) manages Shoulder as responsible provider.

    The Shoulder episode is managed by the staffless Khan-Solo org, so the
    responsible-provider relationship grant is the ONLY reason a non-org-staff
    principal can manage this team.
    """
    resp = client.post(
        f"{_EPISODES}/{sara_world.shoulder}/members",
        headers=auth_header(mint_token(str(sara_world.khan))),
        params={"acting_as": "provider"},
        json={
            "provider_id": str(sara_world.extra_provider),
            "role": "physician",
            "change_reason": "add",
        },
    )
    assert resp.status_code == 201


# --- S1: multi-role team management ------------------------------------------ #


def test_scenario_s1_responsible_provider_adds_provider_multi_role(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str]
) -> None:
    """Khan adds a fourth provider; the resulting team spans genuinely multiple roles."""
    resp = client.post(
        f"{_EPISODES}/{sara_world.shoulder}/members",
        headers=auth_header(mint_token(str(sara_world.khan))),
        params={"acting_as": "provider"},
        json={
            "provider_id": str(sara_world.extra_provider),
            "role": "physician",
            "change_reason": "add",
        },
    )
    assert resp.status_code == 201
    roles_by_provider = {m["provider_id"]: m["role"] for m in resp.json()["members"]}
    assert roles_by_provider[str(sara_world.khan)] == "physiotherapist"
    assert roles_by_provider[str(sara_world.patel)] == "physician"
    assert roles_by_provider[str(sara_world.marco)] == "massage_therapist"
    assert roles_by_provider[str(sara_world.extra_provider)] == "physician"


def test_scenario_s1_non_responsible_member_cannot_manage_team_403(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str]
) -> None:
    """Patel is a current Shoulder member but neither responsible nor org-admin: 403.

    Membership alone never grants MANAGE_TEAM (the physician grid lacks it).
    """
    resp = client.post(
        f"{_EPISODES}/{sara_world.shoulder}/members",
        headers=auth_header(mint_token(str(sara_world.patel))),
        params={"acting_as": "provider"},
        json={
            "provider_id": str(sara_world.extra_provider),
            "role": "physician",
            "change_reason": "add",
        },
    )
    assert resp.status_code == 403


# --- S2: booking face diverges from the responsible provider ----------------- #


def test_scenario_s2_booking_face_differs_from_responsible(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str]
) -> None:
    """Setting the face to Patel leaves Khan responsible: two independent current holders."""
    resp = client.put(
        f"{_EPISODES}/{sara_world.shoulder}/face",
        headers=auth_header(mint_token(str(sara_world.khan))),
        params={"acting_as": "provider"},
        json={"provider_id": str(sara_world.patel), "change_reason": "face moves"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["face_provider_id"] == str(sara_world.patel)
    assert data["responsible_provider_id"] == str(sara_world.khan)


# --- S3: coverage expires by time (the headline single-member time-flip) ----- #


@pytest.mark.parametrize(
    ("weeks", "expected"),
    [
        (0, 403),  # before the window (clock < clock+8w)
        (8, 200),  # inclusive lower bound
        (9, 200),  # inside the window
        (10, 403),  # exclusive upper bound
        (11, 403),  # after the window
    ],
)
def test_scenario_s3_coverage_window_half_open_across_time(
    client: TestClient,
    sara_world: SaraTestWorld,
    mint_token: Callable[..., str],
    clock: datetime,
    weeks: int,
    expected: int,
) -> None:
    """Lee's clinical read flips 403->200->200->403->403 across her ``[clock+8w, clock+10w)``."""
    instant = clock + timedelta(weeks=weeks)
    _set_now(client, instant)
    token = mint_token(str(sara_world.lee), now=instant)
    resp = client.get(
        f"{_EPISODES}/{sara_world.shoulder}/clinical-records",
        headers=auth_header(token),
    )
    assert resp.status_code == expected
    if expected == 200:
        assert sara_world.shoulder_clinical_body in {r["body"] for r in resp.json()}


def test_scenario_s3_coverage_member_never_gets_manage_team_403(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str], clock: datetime
) -> None:
    """In-window, Lee is only a member: coverage never confers the responsible-provider grant."""
    instant = clock + timedelta(weeks=9)
    _set_now(client, instant)
    token = mint_token(str(sara_world.lee), now=instant)
    resp = client.post(
        f"{_EPISODES}/{sara_world.shoulder}/members",
        headers=auth_header(token),
        params={"acting_as": "provider"},
        json={
            "provider_id": str(sara_world.extra_provider),
            "role": "physician",
            "change_reason": "add",
        },
    )
    assert resp.status_code == 403


# --- S4: closed episode (reads survive, acts suppressed by the PDP overlay) --- #


def test_scenario_s4_closed_episode_read_survives_act_suppressed(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str]
) -> None:
    """On the closed episode, Khan reads (200) but every act is 403 (NOT 409 via /v1).

    The closed overlay strips the act capabilities BEFORE the service runs, so the
    PDP suppression fires ahead of the domain ``EpisodeClosed`` guard.
    """
    token = mint_token(str(sara_world.khan))

    read = client.get(
        f"{_EPISODES}/{sara_world.closed}/clinical-records", headers=auth_header(token)
    )
    assert read.status_code == 200

    write = client.post(
        f"{_EPISODES}/{sara_world.closed}/clinical-records",
        headers=auth_header(token),
        json={"body": "late note"},
    )
    assert write.status_code == 403

    manage = client.post(
        f"{_EPISODES}/{sara_world.closed}/members",
        headers=auth_header(token),
        params={"acting_as": "provider"},
        json={
            "provider_id": str(sara_world.extra_provider),
            "role": "physician",
            "change_reason": "late",
        },
    )
    assert manage.status_code == 403

    roster = client.get(
        f"{_EPISODES}/{sara_world.closed}",
        headers=auth_header(token),
        params={"acting_as": "provider"},
    )
    assert roster.status_code == 200
    data = roster.json()
    assert data["status"] == "closed"
    assert len(data["members"]) >= 2  # append-only history survives the close


# --- S5: clinical access split by role --------------------------------------- #


def test_scenario_s5_physician_full_clinical_and_rehab(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str]
) -> None:
    """Patel (physician) reads both the clinical record and the rehab assessment."""
    token = mint_token(str(sara_world.patel))
    clinical = client.get(
        f"{_EPISODES}/{sara_world.shoulder}/clinical-records", headers=auth_header(token)
    )
    rehab = client.get(
        f"{_EPISODES}/{sara_world.shoulder}/rehab-assessments", headers=auth_header(token)
    )
    assert clinical.status_code == 200
    assert rehab.status_code == 200
    assert sara_world.shoulder_clinical_body in {r["body"] for r in clinical.json()}
    assert sara_world.shoulder_rehab_body in {r["body"] for r in rehab.json()}


def test_scenario_s5_physiotherapist_clinical_read_write_and_rehab(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str]
) -> None:
    """Khan (physiotherapist) reads clinical + rehab and AUTHORS a clinical record (201)."""
    token = mint_token(str(sara_world.khan))
    clinical = client.get(
        f"{_EPISODES}/{sara_world.shoulder}/clinical-records", headers=auth_header(token)
    )
    rehab = client.get(
        f"{_EPISODES}/{sara_world.shoulder}/rehab-assessments", headers=auth_header(token)
    )
    assert clinical.status_code == 200
    assert rehab.status_code == 200
    write = client.post(
        f"{_EPISODES}/{sara_world.shoulder}/clinical-records",
        headers=auth_header(token),
        json={"body": "physio note"},
    )
    assert write.status_code == 201
    assert write.json()["author_provider_id"] == str(sara_world.khan)


def test_scenario_s5_non_clinical_member_denied_clinical_within_team_403(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str]
) -> None:
    """Marco (massage_therapist, a CURRENT Shoulder member) is denied clinical: 403.

    Access = current membership AND role-grants-capability; the massage grid lacks
    VIEW_CLINICAL. Contrast: Patel (physician member) is allowed on the same team.
    """
    denied = client.get(
        f"{_EPISODES}/{sara_world.shoulder}/clinical-records",
        headers=auth_header(mint_token(str(sara_world.marco))),
    )
    assert denied.status_code == 403
    allowed = client.get(
        f"{_EPISODES}/{sara_world.shoulder}/clinical-records",
        headers=auth_header(mint_token(str(sara_world.patel))),
    )
    assert allowed.status_code == 200


# --- S6: cross-org isolation ------------------------------------------------- #


def test_scenario_s6_org_admin_cannot_manage_other_org_episode_403(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str]
) -> None:
    """FitGym admin Olivia has no authority over the Khan-Solo-managed Shoulder episode."""
    resp = client.post(
        f"{_EPISODES}/{sara_world.shoulder}/members",
        headers=auth_header(mint_token(str(sara_world.olivia))),
        params={"acting_as": "org_staff"},
        json={
            "provider_id": str(sara_world.extra_provider),
            "role": "physician",
            "change_reason": "x",
        },
    )
    assert resp.status_code == 403


def test_scenario_s6_org_admin_cannot_read_other_org_episode_403(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str]
) -> None:
    """Org-admin authority is scoped to ``managing_org_id``; a foreign episode read is 403.

    Contrast: Olivia reads her OWN managing-org (General) episode at 200.
    """
    foreign = client.get(
        f"{_EPISODES}/{sara_world.shoulder}",
        headers=auth_header(mint_token(str(sara_world.olivia))),
        params={"acting_as": "org_staff"},
    )
    assert foreign.status_code == 403
    own = client.get(
        f"{_EPISODES}/{sara_world.general}",
        headers=auth_header(mint_token(str(sara_world.olivia))),
        params={"acting_as": "org_staff"},
    )
    assert own.status_code == 200


def test_scenario_s6_non_member_provider_denied_clinical_403(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str]
) -> None:
    """Mike is a General member only, never on Shoulder: the episode-boundary deny is 403."""
    resp = client.get(
        f"{_EPISODES}/{sara_world.shoulder}/clinical-records",
        headers=auth_header(mint_token(str(sara_world.mike))),
    )
    assert resp.status_code == 403


def test_scenario_s6_cross_org_member_allowed_by_membership_200(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str]
) -> None:
    """Patel staffs neither managing org, yet his Shoulder MEMBERSHIP grants clinical read."""
    resp = client.get(
        f"{_EPISODES}/{sara_world.shoulder}/clinical-records",
        headers=auth_header(mint_token(str(sara_world.patel))),
    )
    assert resp.status_code == 200
    assert sara_world.shoulder_clinical_body in {r["body"] for r in resp.json()}


# --- S7: effective-dated history is retained and visible --------------------- #


def test_scenario_s7_expired_window_still_visible_in_history(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str], clock: datetime
) -> None:
    """At clock+11w (Lee's window now EXPIRED), Lee's bounded row is STILL in ``members[]``.

    The read is driven at ``now = clock+11w`` (override ``get_now`` + re-mint Khan's
    token) to prove an EXPIRED window persists as append-only history regardless of
    ``now``; Khan's responsibility row remains open.
    """
    instant = clock + timedelta(weeks=11)
    _set_now(client, instant)
    token = mint_token(str(sara_world.khan), now=instant)
    resp = client.get(
        f"{_EPISODES}/{sara_world.shoulder}",
        headers=auth_header(token),
        params={"acting_as": "provider"},
    )
    assert resp.status_code == 200
    data = resp.json()

    lee_rows = [m for m in data["members"] if m["provider_id"] == str(sara_world.lee)]
    assert len(lee_rows) == 1
    lee_row = lee_rows[0]
    assert lee_row["role"] == "physiotherapist"
    assert lee_row["change_reason"] == "covering for Khan"
    assert datetime.fromisoformat(lee_row["effective_from"]) == clock + timedelta(weeks=8)
    assert lee_row["effective_to"] is not None
    assert datetime.fromisoformat(lee_row["effective_to"]) == clock + timedelta(weeks=10)

    khan_resp_rows = [r for r in data["responsibility"] if r["provider_id"] == str(sara_world.khan)]
    assert len(khan_resp_rows) == 1
    assert khan_resp_rows[0]["effective_to"] is None  # still open


def test_scenario_s7_handoff_is_append_only_two_rows(
    client: TestClient, sara_world: SaraTestWorld, mint_token: Callable[..., str]
) -> None:
    """Reassigning responsibility Khan -> Patel is close-old / open-new, never an overwrite."""
    resp = client.put(
        f"{_EPISODES}/{sara_world.shoulder}/responsibility",
        headers=auth_header(mint_token(str(sara_world.khan))),
        params={"acting_as": "provider"},
        json={"provider_id": str(sara_world.patel), "change_reason": "handoff"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["responsible_provider_id"] == str(sara_world.patel)
    khan_rows = [r for r in data["responsibility"] if r["provider_id"] == str(sara_world.khan)]
    patel_rows = [r for r in data["responsibility"] if r["provider_id"] == str(sara_world.patel)]
    assert len(khan_rows) == 1 and khan_rows[0]["effective_to"] is not None  # closed
    assert len(patel_rows) == 1 and patel_rows[0]["effective_to"] is None  # open


# --- covering_for coverage path ---------------------------------------------


def test_scenario_coverage_via_covering_for_marker_creates_bounded_membership(
    client: TestClient,
    sara_world: SaraTestWorld,
    mint_token: Callable[..., str],
    clock: datetime,
) -> None:
    """Khan adds bounded coverage via covering_for; responsibility and face remain his.

    Drives the new add_coverage path through /v1: the cover row is membership-only
    (responsibility / face unchanged) and carries a hard end date.
    """
    effective_to = (clock + timedelta(weeks=4)).isoformat()
    resp = client.post(
        f"{_EPISODES}/{sara_world.shoulder}/members",
        headers=auth_header(mint_token(str(sara_world.khan))),
        params={"acting_as": "provider"},
        json={
            "provider_id": str(sara_world.extra_provider),
            "role": "physiotherapist",
            "change_reason": "covering for Patel",
            "effective_to": effective_to,
            "covering_for": str(sara_world.patel),
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    cover_rows = [m for m in data["members"] if m["provider_id"] == str(sara_world.extra_provider)]
    assert len(cover_rows) == 1
    assert cover_rows[0]["effective_to"] is not None
    assert cover_rows[0]["change_reason"] == "covering for Patel"
    # Responsibility and face must stay with Khan (coverage is membership-only).
    assert data["responsible_provider_id"] == str(sara_world.khan)
    assert data["face_provider_id"] == str(sara_world.khan)
