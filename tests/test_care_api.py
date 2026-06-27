"""Scenario tests for the /v1 care API on real Postgres (per-test rollback).

Each test drives the REAL app through the ``client`` fixture (its session / clock /
JWT-secret are overridden onto the per-test transaction); ``get_current_user`` is
NOT overridden, so the whole authentication + two-layer authorization path runs
end to end. The ``_world`` builder persists a rich, scenario-mapped fixture via the
REAL repositories/services into the SAME per-test ``db_session`` the app reads, so
every row is visible to the requests and rolled back at teardown (A19).

Coverage windows are set RELATIVE to the fixed ``clock`` so "current at now" flips
without changing the clock. Tokens are minted with ``mint_token(str(identity_id))``.
Stub password hashes + ``@example.com`` emails only (no realistic secrets).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
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
from app.identity.domain.value_objects import ProfileType
from app.organization.domain.value_objects import OrgType
from tests._world import (
    auth_header as _auth,
)
from tests._world import (
    make_admin_membership as _admin_membership,
)
from tests._world import (
    make_identity as _identity,
)
from tests._world import (
    make_org as _org,
)
from tests._world import (
    make_profile as _profile,
)

_EPISODES = "/v1/episodes"


@dataclass(frozen=True)
class _World:
    client: UUID
    other_client: UUID
    physician: UUID
    physiotherapist: UUID
    personal_trainer: UUID
    massage_therapist: UUID
    coverage_physio: UUID
    expired_physio: UUID
    khan_provider: UUID
    org_admin: UUID
    fitgym: UUID
    khan: UUID
    general: UUID
    shoulder: UUID
    closed: UUID
    seed_clinical_body: str
    seed_rehab_body: str


def _world(session: Session, clock: datetime) -> _World:
    """Persist the full scenario world; coverage windows are relative to ``clock``."""
    suffix = uuid4().hex[:8]
    opened = clock - timedelta(weeks=8)

    def person(label: str, profile_type: ProfileType) -> UUID:
        identity_id = _identity(session, f"{label}-{suffix}@example.com")
        _profile(session, identity_id, profile_type)
        return identity_id

    client = person("client", ProfileType.CLIENT)
    other_client = person("other-client", ProfileType.CLIENT)
    physician = person("physician", ProfileType.PROVIDER)
    physiotherapist = person("physio", ProfileType.PROVIDER)
    personal_trainer = person("trainer", ProfileType.PROVIDER)
    massage_therapist = person("massage", ProfileType.PROVIDER)
    coverage_physio = person("coverage", ProfileType.PROVIDER)
    expired_physio = person("expired", ProfileType.PROVIDER)
    khan_provider = person("khan-prov", ProfileType.PROVIDER)
    org_admin = person("admin", ProfileType.ORG_STAFF)

    fitgym = _org(session, "FitGym", OrgType.GYM, opened)
    khan = _org(session, "Khan Solo Practice", OrgType.SOLO_PRACTICE, opened)
    _admin_membership(session, org_admin, fitgym, opened)

    episode_repo = SqlAlchemyEpisodeRepository(session)

    # General Training (FitGym): physiotherapist responsible+face; a role mix of members.
    general = open_episode(
        episode_repo,
        client_id=client,
        reason="general training",
        managing_org_id=fitgym,
        responsible_provider_id=physiotherapist,
        responsible_role=Role.PHYSIOTHERAPIST,
        change_reason="opened",
        now=opened,
        new_id=uuid4(),
    )
    add_member(
        episode_repo,
        general,
        provider_id=physician,
        role=Role.PHYSICIAN,
        change_reason="add",
        now=opened,
    )
    add_member(
        episode_repo,
        general,
        provider_id=personal_trainer,
        role=Role.PERSONAL_TRAINER,
        change_reason="add",
        now=opened,
    )
    add_member(
        episode_repo,
        general,
        provider_id=massage_therapist,
        role=Role.MASSAGE_THERAPIST,
        change_reason="add",
        now=opened,
    )
    # Active coverage window (spans now) -> current member at the clock.
    add_member(
        episode_repo,
        general,
        provider_id=coverage_physio,
        role=Role.PHYSIOTHERAPIST,
        change_reason="cover",
        now=opened,
        effective_from=clock - timedelta(weeks=1),
        effective_to=clock + timedelta(weeks=1),
    )
    # Expired coverage window (ended before now) -> NOT a current member at the clock.
    add_member(
        episode_repo,
        general,
        provider_id=expired_physio,
        role=Role.PHYSIOTHERAPIST,
        change_reason="cover",
        now=opened,
        effective_from=clock - timedelta(weeks=4),
        effective_to=clock - timedelta(weeks=2),
    )

    seed_clinical_body = "seeded clinical note"
    seed_rehab_body = "seeded rehab assessment"
    add_clinical_record(
        SqlAlchemyClinicalRecordRepository(session),
        episode_id=general.id,
        author_provider_id=physician,
        body=seed_clinical_body,
        now=opened + timedelta(weeks=1),
        new_id=uuid4(),
    )
    SqlAlchemyRehabAssessmentRepository(session).add(
        RehabAssessment(
            id=uuid4(),
            episode_id=general.id,
            author_provider_id=physician,
            body=seed_rehab_body,
            created_at=opened + timedelta(weeks=1),
        )
    )

    # Independent Shoulder Rehab (Khan, staffless org): khan_provider responsible.
    shoulder = open_episode(
        episode_repo,
        client_id=client,
        reason="shoulder rehab",
        managing_org_id=khan,
        responsible_provider_id=khan_provider,
        responsible_role=Role.PHYSIOTHERAPIST,
        change_reason="opened",
        now=opened,
        new_id=uuid4(),
    )

    # A CLOSED episode (FitGym) with a seeded clinical record, for closed-state tests.
    closed = open_episode(
        episode_repo,
        client_id=client,
        reason="old episode",
        managing_org_id=fitgym,
        responsible_provider_id=physiotherapist,
        responsible_role=Role.PHYSIOTHERAPIST,
        change_reason="opened",
        now=opened,
        new_id=uuid4(),
    )
    add_member(
        episode_repo,
        closed,
        provider_id=physician,
        role=Role.PHYSICIAN,
        change_reason="add",
        now=opened,
    )
    add_clinical_record(
        SqlAlchemyClinicalRecordRepository(session),
        episode_id=closed.id,
        author_provider_id=physician,
        body="closed clinical note",
        now=opened + timedelta(weeks=1),
        new_id=uuid4(),
    )
    close_episode(episode_repo, closed, now=clock - timedelta(weeks=4))

    return _World(
        client=client,
        other_client=other_client,
        physician=physician,
        physiotherapist=physiotherapist,
        personal_trainer=personal_trainer,
        massage_therapist=massage_therapist,
        coverage_physio=coverage_physio,
        expired_physio=expired_physio,
        khan_provider=khan_provider,
        org_admin=org_admin,
        fitgym=fitgym,
        khan=khan,
        general=general.id,
        shoulder=shoulder.id,
        closed=closed.id,
        seed_clinical_body=seed_clinical_body,
        seed_rehab_body=seed_rehab_body,
    )


# --- auth / coarse-surface ---------------------------------------------------


def test_unauthenticated_create_returns_401(client: TestClient) -> None:
    resp = client.post(_EPISODES, json={"client_id": str(uuid4())})
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_client_surface_cannot_create_episode_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    body = {
        "client_id": str(world.other_client),
        "reason": "x",
        "managing_org_id": str(world.fitgym),
        "responsible_role": "physician",
        "change_reason": "opened",
    }
    # The caller holds only a client profile; create is provider-only (Layer 1).
    resp = client.post(_EPISODES, headers=_auth(mint_token(str(world.client))), json=body)
    assert resp.status_code == 403


# --- AM1: explicit acting surface on multi-surface routes --------------------


def test_multi_surface_route_without_acting_as_returns_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    # GET episode serves provider|client|org_staff: omitting acting_as is rejected.
    resp = client.get(
        f"{_EPISODES}/{world.general}", headers=_auth(mint_token(str(world.physician)))
    )
    assert resp.status_code == 403


def test_multi_surface_route_with_not_allowed_acting_as_returns_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    # Team management serves provider|org_staff only; client is not an allowed surface.
    resp = client.post(
        f"{_EPISODES}/{world.general}/members",
        headers=_auth(mint_token(str(world.physiotherapist))),
        params={"acting_as": "client"},
        json={"provider_id": str(world.khan_provider), "role": "physician", "change_reason": "x"},
    )
    assert resp.status_code == 403


def test_multi_surface_route_with_not_held_acting_as_returns_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    # The physician does not hold an org_staff profile, so acting_as=org_staff is 403.
    resp = client.get(
        f"{_EPISODES}/{world.general}",
        headers=_auth(mint_token(str(world.physician))),
        params={"acting_as": "org_staff"},
    )
    assert resp.status_code == 403


# --- S1: bootstrap create (creator == responsible, server-owned) -------------


def test_provider_creates_episode_creator_is_responsible_201(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    body = {
        "client_id": str(world.client),
        "reason": "new knee episode",
        "managing_org_id": str(world.fitgym),
        "responsible_role": "physician",
        "change_reason": "opened",
    }
    resp = client.post(_EPISODES, headers=_auth(mint_token(str(world.physician))), json=body)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "active"
    assert data["responsible_provider_id"] == str(world.physician)
    assert data["face_provider_id"] == str(world.physician)


def test_create_ignores_decoy_responsible_provider_id(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    body = {
        "client_id": str(world.client),
        "reason": "knee episode",
        "managing_org_id": str(world.fitgym),
        "responsible_role": "physician",
        "change_reason": "opened",
        "responsible_provider_id": str(world.khan_provider),  # decoy: must be ignored
    }
    resp = client.post(_EPISODES, headers=_auth(mint_token(str(world.physician))), json=body)
    assert resp.status_code == 201
    data = resp.json()
    # The server value (the authenticated caller) wins; the decoy is dropped.
    assert data["responsible_provider_id"] == str(world.physician)
    assert data["responsible_provider_id"] != str(world.khan_provider)


# --- unknown client-supplied FK -> 422 (a dangling reference is not a 500) ----


def test_create_episode_unknown_client_returns_422(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    # A well-formed but non-existent client_id: the episodes.client_id FK has no parent
    # row, so the flush trips a Postgres FK violation. That is bad client input and must
    # surface as a 422, never a raw IntegrityError mapped to a 500.
    body = {
        "client_id": str(uuid4()),
        "reason": "new knee episode",
        "managing_org_id": str(world.fitgym),
        "responsible_role": "physician",
        "change_reason": "opened",
    }
    resp = client.post(_EPISODES, headers=_auth(mint_token(str(world.physician))), json=body)
    assert resp.status_code == 422


def test_create_episode_unknown_managing_org_returns_422(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    # A well-formed but non-existent managing_org_id trips the episodes.managing_org_id FK.
    body = {
        "client_id": str(world.client),
        "reason": "new knee episode",
        "managing_org_id": str(uuid4()),
        "responsible_role": "physician",
        "change_reason": "opened",
    }
    resp = client.post(_EPISODES, headers=_auth(mint_token(str(world.physician))), json=body)
    assert resp.status_code == 422


def test_add_member_unknown_provider_returns_422(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    # The physiotherapist is responsible on general (holds MANAGE_TEAM), so authz passes;
    # the provider_id is well-formed but is no identity, so the new membership row trips the
    # episode_memberships.provider_id FK. The dangling reference must be a 422, not a 500.
    resp = client.post(
        f"{_EPISODES}/{world.general}/members",
        headers=_auth(mint_token(str(world.physiotherapist))),
        params={"acting_as": "provider"},
        json={"provider_id": str(uuid4()), "role": "physician", "change_reason": "add"},
    )
    assert resp.status_code == 422


# --- team management: both MANAGE_TEAM paths, self-treatment, roster ----------


def test_responsible_provider_adds_member_201_path_b(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.post(
        f"{_EPISODES}/{world.general}/members",
        headers=_auth(mint_token(str(world.physiotherapist))),
        params={"acting_as": "provider"},
        json={"provider_id": str(world.khan_provider), "role": "physician", "change_reason": "add"},
    )
    assert resp.status_code == 201
    assert str(world.khan_provider) in {m["provider_id"] for m in resp.json()["members"]}


def test_org_admin_adds_member_201_path_a(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.post(
        f"{_EPISODES}/{world.general}/members",
        headers=_auth(mint_token(str(world.org_admin))),
        params={"acting_as": "org_staff"},
        json={"provider_id": str(world.khan_provider), "role": "physician", "change_reason": "add"},
    )
    assert resp.status_code == 201


def test_non_authorized_member_cannot_manage_team_returns_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    # The personal trainer is a CURRENT member of general but is NEITHER the responsible
    # provider NOR an org_admin, and the personal_trainer role grid lacks MANAGE_TEAM, so
    # acting_as=provider cannot add a member (the PDP denies MANAGE_TEAM): the only
    # difference from the 201 add-member path is the actor's authority.
    resp = client.post(
        f"{_EPISODES}/{world.general}/members",
        headers=_auth(mint_token(str(world.personal_trainer))),
        params={"acting_as": "provider"},
        json={"provider_id": str(world.khan_provider), "role": "physician", "change_reason": "add"},
    )
    assert resp.status_code == 403


def test_add_member_self_treatment_returns_422(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    # Adding the episode's own client as a provider member is self-treatment.
    resp = client.post(
        f"{_EPISODES}/{world.general}/members",
        headers=_auth(mint_token(str(world.physiotherapist))),
        params={"acting_as": "provider"},
        json={"provider_id": str(world.client), "role": "physician", "change_reason": "add"},
    )
    assert resp.status_code == 422


def test_get_episode_returns_roster_200(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.get(
        f"{_EPISODES}/{world.general}",
        headers=_auth(mint_token(str(world.physician))),
        params={"acting_as": "provider"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["responsible_provider_id"] == str(world.physiotherapist)
    member_ids = {m["provider_id"] for m in data["members"]}
    assert {str(world.physician), str(world.physiotherapist)} <= member_ids


def test_end_member_closes_membership_200(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    # The trainer is neither responsible nor the face, so ending is a plain close.
    end_at = (clock + timedelta(weeks=1)).isoformat()
    resp = client.post(
        f"{_EPISODES}/{world.general}/members/{world.personal_trainer}/end",
        headers=_auth(mint_token(str(world.physiotherapist))),
        params={"acting_as": "provider"},
        json={"effective_to": end_at, "change_reason": "trainer leaves"},
    )
    assert resp.status_code == 200
    trainer_rows = [
        m for m in resp.json()["members"] if m["provider_id"] == str(world.personal_trainer)
    ]
    assert len(trainer_rows) == 1 and trainer_rows[0]["effective_to"] is not None


# --- S2/S7: reassignment, divergent face, non-member guard -------------------


def test_reassign_responsibility_creates_two_rows_200(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.put(
        f"{_EPISODES}/{world.general}/responsibility",
        headers=_auth(mint_token(str(world.physiotherapist))),
        params={"acting_as": "provider"},
        json={"provider_id": str(world.physician), "change_reason": "handoff"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["responsible_provider_id"] == str(world.physician)
    physio_rows = [
        r for r in data["responsibility"] if r["provider_id"] == str(world.physiotherapist)
    ]
    physician_rows = [r for r in data["responsibility"] if r["provider_id"] == str(world.physician)]
    assert len(physio_rows) == 1 and physio_rows[0]["effective_to"] is not None  # closed
    assert len(physician_rows) == 1 and physician_rows[0]["effective_to"] is None  # open


def test_set_divergent_face_200(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.put(
        f"{_EPISODES}/{world.general}/face",
        headers=_auth(mint_token(str(world.physiotherapist))),
        params={"acting_as": "provider"},
        json={"provider_id": str(world.physician), "change_reason": "face moves"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["face_provider_id"] == str(world.physician)
    assert data["responsible_provider_id"] == str(world.physiotherapist)  # unchanged


def test_reassign_to_non_member_returns_422(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    # khan_provider is a member of shoulder, NOT of general.
    resp = client.put(
        f"{_EPISODES}/{world.general}/responsibility",
        headers=_auth(mint_token(str(world.physiotherapist))),
        params={"acting_as": "provider"},
        json={"provider_id": str(world.khan_provider), "change_reason": "bad"},
    )
    assert resp.status_code == 422


# --- S3: coverage window (active vs expired at the same clock) ----------------


def test_active_coverage_member_reads_clinical_200(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.get(
        f"{_EPISODES}/{world.general}/clinical-records",
        headers=_auth(mint_token(str(world.coverage_physio))),
    )
    assert resp.status_code == 200
    assert world.seed_clinical_body in {r["body"] for r in resp.json()}


def test_expired_coverage_member_denied_clinical_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.get(
        f"{_EPISODES}/{world.general}/clinical-records",
        headers=_auth(mint_token(str(world.expired_physio))),
    )
    assert resp.status_code == 403


# --- S4: closed-episode lifecycle (read survives, act suppressed) ------------


def test_close_episode_200(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.post(
        f"{_EPISODES}/{world.general}/close",
        headers=_auth(mint_token(str(world.physiotherapist))),
        params={"acting_as": "provider"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"


def test_closed_episode_read_shows_history_200(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.get(
        f"{_EPISODES}/{world.closed}",
        headers=_auth(mint_token(str(world.physician))),
        params={"acting_as": "provider"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "closed"
    assert len(data["members"]) >= 2  # append-only history survives the close


def test_closed_episode_clinical_read_200(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.get(
        f"{_EPISODES}/{world.closed}/clinical-records",
        headers=_auth(mint_token(str(world.physician))),
    )
    assert resp.status_code == 200
    assert "closed clinical note" in {r["body"] for r in resp.json()}


def test_closed_episode_clinical_write_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.post(
        f"{_EPISODES}/{world.closed}/clinical-records",
        headers=_auth(mint_token(str(world.physician))),
        json={"body": "late note"},
    )
    assert resp.status_code == 403


def test_closed_episode_manage_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.post(
        f"{_EPISODES}/{world.closed}/members",
        headers=_auth(mint_token(str(world.physiotherapist))),
        params={"acting_as": "provider"},
        json={
            "provider_id": str(world.khan_provider),
            "role": "physician",
            "change_reason": "late",
        },
    )
    assert resp.status_code == 403


# --- S5: role-split (physician full clinical; trainer/massage denied) ---------


def test_physician_reads_clinical_and_rehab_200(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    token = mint_token(str(world.physician))
    clinical = client.get(f"{_EPISODES}/{world.general}/clinical-records", headers=_auth(token))
    rehab = client.get(f"{_EPISODES}/{world.general}/rehab-assessments", headers=_auth(token))
    assert clinical.status_code == 200
    assert rehab.status_code == 200
    assert world.seed_clinical_body in {r["body"] for r in clinical.json()}
    assert world.seed_rehab_body in {r["body"] for r in rehab.json()}


def test_physician_writes_clinical_201_and_reads_back(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    token = mint_token(str(world.physician))
    write = client.post(
        f"{_EPISODES}/{world.general}/clinical-records",
        headers=_auth(token),
        json={"body": "fresh physician note"},
    )
    assert write.status_code == 201
    assert write.json()["author_provider_id"] == str(world.physician)
    read = client.get(f"{_EPISODES}/{world.general}/clinical-records", headers=_auth(token))
    assert "fresh physician note" in {r["body"] for r in read.json()}


def test_massage_member_denied_clinical_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.get(
        f"{_EPISODES}/{world.general}/clinical-records",
        headers=_auth(mint_token(str(world.massage_therapist))),
    )
    assert resp.status_code == 403


def test_trainer_denied_rehab_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.get(
        f"{_EPISODES}/{world.general}/rehab-assessments",
        headers=_auth(mint_token(str(world.personal_trainer))),
    )
    assert resp.status_code == 403


def test_trainer_denied_clinical_write_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.post(
        f"{_EPISODES}/{world.general}/clinical-records",
        headers=_auth(mint_token(str(world.personal_trainer))),
        json={"body": "trainer note"},
    )
    assert resp.status_code == 403


# --- S6: cross-episode / cross-org isolation ---------------------------------


def test_non_member_provider_denied_clinical_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    # khan_provider is a member of shoulder only, never of general.
    resp = client.get(
        f"{_EPISODES}/{world.general}/clinical-records",
        headers=_auth(mint_token(str(world.khan_provider))),
    )
    assert resp.status_code == 403


def test_non_member_provider_cannot_write_clinical_returns_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    # khan_provider is a member of shoulder only, never of general: with a valid token
    # and an active provider profile, the PDP's provider branch still finds no current
    # membership, so WRITE_CLINICAL is not granted (the write-side IDOR denial path, the
    # POST counterpart to the GET deny above on the same episode physician writes to).
    resp = client.post(
        f"{_EPISODES}/{world.general}/clinical-records",
        headers=_auth(mint_token(str(world.khan_provider))),
        json={"body": "outsider note"},
    )
    assert resp.status_code == 403


def test_org_admin_cannot_manage_other_org_episode_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    # FitGym admin has no authority over the Khan-managed shoulder episode.
    resp = client.post(
        f"{_EPISODES}/{world.shoulder}/members",
        headers=_auth(mint_token(str(world.org_admin))),
        params={"acting_as": "org_staff"},
        json={"provider_id": str(world.physician), "role": "physician", "change_reason": "x"},
    )
    assert resp.status_code == 403


# --- S7: existence (404) -----------------------------------------------------


def test_missing_episode_returns_404(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    # Correct held surface (provider), but the episode does not exist -> 404 (after Layer 1).
    resp = client.get(
        f"{_EPISODES}/{uuid4()}/clinical-records",
        headers=_auth(mint_token(str(world.physician))),
    )
    assert resp.status_code == 404


# --- client self-access surface ----------------------------------------------


def test_client_reads_own_episode_200(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.get(
        f"{_EPISODES}/{world.general}",
        headers=_auth(mint_token(str(world.client))),
        params={"acting_as": "client"},
    )
    assert resp.status_code == 200
    assert resp.json()["client_id"] == str(world.client)


def test_other_client_denied_episode_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    resp = client.get(
        f"{_EPISODES}/{world.general}",
        headers=_auth(mint_token(str(world.other_client))),
        params={"acting_as": "client"},
    )
    assert resp.status_code == 403


def test_client_denied_clinical_403(
    client: TestClient, db_session: Session, clock: datetime, mint_token: Callable[..., str]
) -> None:
    world = _world(db_session, clock)
    # The clinical read route is provider-only; a client never reaches the PDP.
    resp = client.get(
        f"{_EPISODES}/{world.general}/clinical-records",
        headers=_auth(mint_token(str(world.client))),
    )
    assert resp.status_code == 403
