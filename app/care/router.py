"""The ``/v1/episodes`` router: episode lifecycle, team management, clinical resources.

Thin handlers (A6): the request-scoped gate in ``app/care/deps.py`` does ALL of
authenticate + Layer-1 surface + load + Layer-2 ``Pdp`` and hands back the
AUTHORIZED ``Episode`` (or, for create, the ``ActorContext``). Each handler then
makes ONE service call, commits the unit of work on writes only, and returns an
``XOut`` via ``EpisodeOut.from_episode`` / ``model_validate``. No SQL, policy, or
business rule lives here; every route declares a ``response_model`` (A9) and uses
the ``Annotated[T, Depends()]`` idiom.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.authz.capabilities import Capability
from app.authz.context import ActorContext, ProfileType
from app.care import service
from app.care.deps import require_episode_capability, require_profile
from app.care.domain.episode import Episode
from app.care.repository import (
    SqlAlchemyClinicalRecordRepository,
    SqlAlchemyEpisodeRepository,
    SqlAlchemyRehabAssessmentRepository,
)
from app.care.schemas import (
    ClinicalRecordCreate,
    ClinicalRecordOut,
    EpisodeCreate,
    EpisodeOut,
    FaceSet,
    MemberCreate,
    MemberEnd,
    RehabAssessmentOut,
    ResponsibilityReassign,
)
from app.core.deps import get_new_id, get_now, get_session
from app.identity.deps import get_current_user
from app.identity.domain.entities import Identity

router = APIRouter(prefix="/v1", tags=["care"])

# Multi-surface route allow-lists (AM1: acting_as required + validated on these).
_TEAM = (ProfileType.PROVIDER, ProfileType.ORG_STAFF)
_READ = (ProfileType.PROVIDER, ProfileType.CLIENT, ProfileType.ORG_STAFF)

# Capability gates, parametrized once (the dependency objects are created at import).
_ManageTeam = Annotated[
    Episode, Depends(require_episode_capability(Capability.MANAGE_TEAM, *_TEAM, for_update=True))
]
_ReadEpisode = Annotated[
    Episode, Depends(require_episode_capability(Capability.VIEW_BASIC_PROFILE, *_READ))
]
_WriteClinical = Annotated[
    Episode, Depends(require_episode_capability(Capability.WRITE_CLINICAL, ProfileType.PROVIDER))
]
_ReadClinical = Annotated[
    Episode, Depends(require_episode_capability(Capability.VIEW_CLINICAL, ProfileType.PROVIDER))
]
_ReadRehab = Annotated[
    Episode,
    Depends(require_episode_capability(Capability.VIEW_REHAB_ASSESSMENT, ProfileType.PROVIDER)),
]
_ProviderActor = Annotated[ActorContext, Depends(require_profile(ProfileType.PROVIDER))]
_Session = Annotated[Session, Depends(get_session)]
_Now = Annotated[datetime, Depends(get_now)]
_NewId = Annotated[UUID, Depends(get_new_id)]


# --- episode lifecycle ------------------------------------------------------- #


@router.post("/episodes", response_model=EpisodeOut, status_code=status.HTTP_201_CREATED)
def create_episode(
    payload: EpisodeCreate, actor: _ProviderActor, session: _Session, now: _Now, new_id: _NewId
) -> EpisodeOut:
    """Open an episode; the authenticated provider becomes the responsible provider."""
    episode = service.open_episode(
        SqlAlchemyEpisodeRepository(session),
        client_id=payload.client_id,
        reason=payload.reason,
        managing_org_id=payload.managing_org_id,
        responsible_provider_id=actor.identity_id,  # server-owned (AM2)
        responsible_role=payload.responsible_role,
        change_reason=payload.change_reason,
        now=now,
        new_id=new_id,
        face_provider_id=payload.face_provider_id,
        face_role=payload.face_role,
    )
    session.commit()
    return EpisodeOut.from_episode(episode, now)


@router.get("/episodes/{episode_id}", response_model=EpisodeOut)
def read_episode(episode: _ReadEpisode, now: _Now) -> EpisodeOut:
    """Read an episode (roster + history + derived current state)."""
    return EpisodeOut.from_episode(episode, now)


@router.post(
    "/episodes/{episode_id}/members", response_model=EpisodeOut, status_code=status.HTTP_201_CREATED
)
def add_member(
    payload: MemberCreate, episode: _ManageTeam, session: _Session, now: _Now
) -> EpisodeOut:
    """Add a team member (optionally a bounded coverage window)."""
    service.add_member(
        SqlAlchemyEpisodeRepository(session),
        episode,
        provider_id=payload.provider_id,
        role=payload.role,
        change_reason=payload.change_reason,
        now=now,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
    )
    session.commit()
    return EpisodeOut.from_episode(episode, now)


@router.post("/episodes/{episode_id}/members/{provider_id}/end", response_model=EpisodeOut)
def end_member(
    provider_id: UUID, payload: MemberEnd, episode: _ManageTeam, session: _Session, now: _Now
) -> EpisodeOut:
    """End a member's current membership (append-only close, with face handoff if needed)."""
    service.end_member(
        SqlAlchemyEpisodeRepository(session),
        episode,
        provider_id=provider_id,
        effective_to=payload.effective_to,
        change_reason=payload.change_reason,
        now=now,
        successor_face_id=payload.successor_face_id,
    )
    session.commit()
    return EpisodeOut.from_episode(episode, now)


@router.put("/episodes/{episode_id}/responsibility", response_model=EpisodeOut)
def reassign_responsibility(
    payload: ResponsibilityReassign, episode: _ManageTeam, session: _Session, now: _Now
) -> EpisodeOut:
    """Hand clinical responsibility to a current member (close-old / open-new)."""
    service.reassign_responsible(
        SqlAlchemyEpisodeRepository(session),
        episode,
        provider_id=payload.provider_id,
        change_reason=payload.change_reason,
        now=now,
    )
    session.commit()
    return EpisodeOut.from_episode(episode, now)


@router.put("/episodes/{episode_id}/face", response_model=EpisodeOut)
def set_face(payload: FaceSet, episode: _ManageTeam, session: _Session, now: _Now) -> EpisodeOut:
    """Set the booking face to a current member (close-old / open-new)."""
    service.set_face(
        SqlAlchemyEpisodeRepository(session),
        episode,
        provider_id=payload.provider_id,
        change_reason=payload.change_reason,
        now=now,
    )
    session.commit()
    return EpisodeOut.from_episode(episode, now)


@router.post("/episodes/{episode_id}/close", response_model=EpisodeOut)
def close_episode(episode: _ManageTeam, session: _Session, now: _Now) -> EpisodeOut:
    """Close the episode (immutable thereafter)."""
    service.close_episode(SqlAlchemyEpisodeRepository(session), episode, now=now)
    session.commit()
    return EpisodeOut.from_episode(episode, now)


# --- clinical / rehab resources --------------------------------------------- #


@router.post(
    "/episodes/{episode_id}/clinical-records",
    response_model=ClinicalRecordOut,
    status_code=status.HTTP_201_CREATED,
)
def create_clinical_record(
    payload: ClinicalRecordCreate,
    episode: _WriteClinical,
    current_user: Annotated[Identity, Depends(get_current_user)],
    session: _Session,
    now: _Now,
    new_id: _NewId,
) -> ClinicalRecordOut:
    """Author a clinical record on the episode (provider-only; the author is the caller)."""
    record = service.add_clinical_record(
        SqlAlchemyClinicalRecordRepository(session),
        episode_id=episode.id,
        author_provider_id=current_user.id,
        body=payload.body,
        now=now,
        new_id=new_id,
    )
    session.commit()
    return ClinicalRecordOut.model_validate(record)


@router.get("/episodes/{episode_id}/clinical-records", response_model=list[ClinicalRecordOut])
def list_clinical_records(episode: _ReadClinical, session: _Session) -> list[ClinicalRecordOut]:
    """List the episode's clinical records (provider-only)."""
    records = service.list_clinical_records(SqlAlchemyClinicalRecordRepository(session), episode.id)
    return [ClinicalRecordOut.model_validate(record) for record in records]


@router.get("/episodes/{episode_id}/rehab-assessments", response_model=list[RehabAssessmentOut])
def list_rehab_assessments(episode: _ReadRehab, session: _Session) -> list[RehabAssessmentOut]:
    """List the episode's rehab assessments (provider-only; read-only via the API)."""
    assessments = service.list_rehab_assessments(
        SqlAlchemyRehabAssessmentRepository(session), episode.id
    )
    return [RehabAssessmentOut.model_validate(assessment) for assessment in assessments]
