"""The ``/v1/auth`` router: register, OAuth2 password token, and current user.

Thin handlers (A6): each validates input via a schema, calls the identity service
or a security primitive, and returns a ``response_model`` (A9) that never leaks
``password_hash``. Registration commits the unit of work at the edge (the only
write on this slice); ``/token`` and ``/me`` are read-only. No business rule, SQL,
or policy lives here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.deps import get_now, get_session
from app.core.exceptions import NotAuthenticated
from app.core.security import create_access_token
from app.identity import service
from app.identity.deps import get_current_user, get_new_id
from app.identity.domain.entities import Identity
from app.identity.repository import SqlAlchemyIdentityRepository
from app.identity.schemas import IdentityCreate, IdentityOut, Token

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/register", response_model=IdentityOut, status_code=status.HTTP_201_CREATED)
def register(
    payload: IdentityCreate,
    session: Annotated[Session, Depends(get_session)],
    now: Annotated[datetime, Depends(get_now)],
    new_id: Annotated[UUID, Depends(get_new_id)],
) -> Identity:
    """Register a new identity and return it (201); duplicate email -> 409."""
    repo = SqlAlchemyIdentityRepository(session)
    identity = service.register(
        repo,
        email=payload.email,
        display_name=payload.display_name,
        password=payload.password.get_secret_value(),
        now=now,
        new_id=new_id,
    )
    session.commit()
    return identity


@router.post("/token", response_model=Token)
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: Annotated[Session, Depends(get_session)],
    now: Annotated[datetime, Depends(get_now)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Token:
    """OAuth2 password grant: authenticate by email + password, issue a JWT.

    Login intentionally does NOT re-apply the registration password policy; it
    authenticates against the stored hash and returns a generic 401 on any failure
    (unknown email and wrong password are indistinguishable).
    """
    repo = SqlAlchemyIdentityRepository(session)
    identity = service.authenticate(repo, form.username, form.password)
    if identity is None:
        raise NotAuthenticated()
    access_token = create_access_token(
        subject=str(identity.id),
        secret=settings.jwt_secret_key.get_secret_value(),
        now=now,
        expires_minutes=settings.access_token_expire_minutes,
        algorithm=settings.jwt_algorithm,
    )
    return Token(access_token=access_token)


@router.get("/me", response_model=IdentityOut)
def me(current_user: Annotated[Identity, Depends(get_current_user)]) -> Identity:
    """Return the authenticated identity."""
    return current_user
