"""Central exception handling: map domain exceptions to HTTP responses.

Lives in the web/infra layer (imports FastAPI), not the domain. Every bounded
context raises pure exceptions (subclasses of ``app.core.exceptions.DomainError``,
defined per context); this module maps any of them to an RFC 7807
``application/problem+json`` response with the right status code. Register it once
on the app in ``app/main.py`` via ``register_exception_handlers(app)``.

The exception -> HTTP status table is the single home for that mapping; it lives
here (web layer), never on the pure domain exception (project std 5).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.authz.exceptions import Forbidden
from app.care.domain.exceptions import (
    EpisodeClosed,
    NotACurrentMember,
    OverlappingPeriod,
    SelfTreatment,
)
from app.core.exceptions import DomainError, NotAuthenticated
from app.identity.domain.exceptions import EmailAlreadyRegistered

logger = logging.getLogger("kinetic")

# {domain exception -> HTTP status}: the auth-design table, single home.
_DOMAIN_STATUS: dict[type[Exception], int] = {
    NotAuthenticated: 401,
    EmailAlreadyRegistered: 409,
    Forbidden: 403,
    SelfTreatment: 422,
    NotACurrentMember: 422,
    EpisodeClosed: 409,
    OverlappingPeriod: 409,
}
_DEFAULT_DOMAIN_STATUS = 422  # any future DomainError without an explicit mapping


def _problem(status: int, title: str, detail: str) -> JSONResponse:
    """Build an RFC 7807 ``application/problem+json`` response.

    A 401 additionally carries ``WWW-Authenticate: Bearer`` (RFC 7235 requires a
    challenge on every 401); this is its single home, additive to the body.
    """
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(
        status_code=status,
        content={"type": "about:blank", "title": title, "status": status, "detail": detail},
        media_type="application/problem+json",
        headers=headers,
    )


def _handle_domain_error(request: Request, exc: Exception) -> JSONResponse:
    """Map any ``DomainError`` subclass to its HTTP status (catches the hierarchy)."""
    status = _DOMAIN_STATUS.get(type(exc), _DEFAULT_DOMAIN_STATUS)
    return _problem(status, type(exc).__name__, str(exc))


def _handle_value_error(request: Request, exc: Exception) -> JSONResponse:
    """Value-object / precondition validation (naive datetime, bad period, ...) -> 422."""
    return _problem(422, "ValidationError", str(exc))


def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort net so nothing leaks as an unstructured 500; logged with context."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return _problem(500, "InternalServerError", "An unexpected error occurred.")


def register_exception_handlers(app: FastAPI) -> None:
    """Register the exception handlers on the app (call once from ``app/main.py``)."""
    app.add_exception_handler(DomainError, _handle_domain_error)  # catches every subclass
    app.add_exception_handler(ValueError, _handle_value_error)
    app.add_exception_handler(Exception, _handle_unexpected)
