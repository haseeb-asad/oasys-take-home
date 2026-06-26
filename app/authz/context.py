"""Actor- and resource-context value objects for the policy decision point.

Pure authorization layer (project std 1): plain Python only — frozen
dataclasses + stdlib ``enum`` / ``datetime`` / ``uuid``. No FastAPI / SQLAlchemy
/ Pydantic. The only domain import is the ``Episode`` aggregate, which the PDP
*inspects* (never mutates) to resolve membership / responsibility at ``now``.

Authorization is **actor-context-scoped**: an identity that holds several
profiles presents exactly one ``ProfileType`` per decision, and only that
surface's branches are evaluated (no union-of-hats).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.care.domain.episode import Episode


class ProfileType(StrEnum):
    """The surface an actor acts under for a single decision.

    Values are ``snake_case`` (project naming): one per persona the route may
    serve. The PDP dispatches on this and evaluates only that surface.
    """

    CLIENT = "client"
    PROVIDER = "provider"
    ORG_STAFF = "org_staff"


@dataclass(frozen=True, slots=True)
class ActorContext:
    """Who is acting, and in which capacity, for one authorization decision.

    ``identity_id`` is an ``identities.id`` (the same id space as an episode's
    ``client_id`` / a provider's membership id), so identity-to-identity checks
    such as client ownership are plain equality.
    """

    identity_id: UUID
    profile_type: ProfileType


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """The target of an action: exactly one of an episode OR a bare client id.

    Episode-scoped decisions carry the ``Episode`` aggregate (the PDP reads its
    membership / responsibility / status, threading ``now``); client-scoped
    decisions carry a ``client_id``. Exactly one must be set — the ``__post_init__``
    guard rejects neither/both so ``owner_client_id`` is always well-defined.
    """

    episode: Episode | None = None
    client_id: UUID | None = None

    def __post_init__(self) -> None:
        if (self.episode is None) == (self.client_id is None):
            raise ValueError("ResourceRef requires exactly one of episode or client_id.")

    @classmethod
    def for_episode(cls, episode: Episode) -> ResourceRef:
        """An episode-scoped reference."""
        return cls(episode=episode)

    @classmethod
    def for_client(cls, client_id: UUID) -> ResourceRef:
        """A client-scoped reference (e.g. a basic-profile / schedule resource)."""
        return cls(client_id=client_id)

    @property
    def owner_client_id(self) -> UUID:
        """The client that owns this resource (the episode's client, or ``client_id``)."""
        if self.episode is not None:
            return self.episode.client_id
        assert self.client_id is not None  # the exactly-one guard makes this total
        return self.client_id

    @property
    def is_episode_scoped(self) -> bool:
        """True iff this reference targets an episode rather than a bare client."""
        return self.episode is not None
