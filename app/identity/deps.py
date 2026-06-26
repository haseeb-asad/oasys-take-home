"""Identity request-scoped dependencies: OAuth2 scheme, id factory, current user.

``get_current_user`` is the single authenticated-subject dependency (A11): it
decodes the bearer token against the injected ``now`` + settings secret, resolves
the subject to an ``Identity`` via the repository, and raises the uniform
``NotAuthenticated`` (-> 401) on EVERY failure mode (missing/malformed header, bad
signature, expired, non-uuid subject, unknown identity) so the API exposes no
oracle. ``auto_error=False`` funnels a missing/malformed Authorization header
through that same uniform 401 (instead of OAuth2PasswordBearer's own 401, which
would bypass the central handler and its challenge header).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.deps import get_now, get_session
from app.core.exceptions import NotAuthenticated
from app.core.security import decode_access_token
from app.identity import service
from app.identity.domain.entities import Identity
from app.identity.repository import SqlAlchemyIdentityRepository

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/token", auto_error=False)


def get_new_id() -> UUID:
    """Provide a fresh identity id (overridable in tests for determinism)."""
    return uuid4()


def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    session: Annotated[Session, Depends(get_session)],
    now: Annotated[datetime, Depends(get_now)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Identity:
    """Resolve the bearer token to the authenticated ``Identity`` or raise 401."""
    if token is None:  # missing or malformed Authorization header (auto_error=False)
        raise NotAuthenticated()
    subject = decode_access_token(
        token,
        secret=settings.jwt_secret_key.get_secret_value(),
        now=now,
        algorithm=settings.jwt_algorithm,
    )
    try:
        identity_id = UUID(subject)
    except ValueError as exc:  # subject is not a uuid
        raise NotAuthenticated() from exc
    identity = service.get_identity(SqlAlchemyIdentityRepository(session), identity_id)
    if identity is None:  # token valid but the identity no longer exists
        raise NotAuthenticated()
    return identity
