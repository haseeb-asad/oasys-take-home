"""Typed domain exceptions for the Identity bounded context.

Pure domain layer (project std 1): imports zero infrastructure (no FastAPI /
SQLAlchemy / Pydantic). The exception -> HTTP status mapping lives centrally in
the web layer (``app/core/errors.py``), never here.
"""

from __future__ import annotations

from app.core.exceptions import DomainError


class EmailAlreadyRegistered(DomainError):
    """An identity with the given email already exists.

    Raised by the repository when an insert hits the email unique constraint
    (race-free: no pre-check, the database is the arbiter). The address is kept
    as an attribute for structured logging but deliberately omitted from the
    human-facing message, so the central 409 response body never echoes which
    address is taken. Mapped centrally to HTTP 409.
    """

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__("An identity with this email already exists.")
