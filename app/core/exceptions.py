"""Shared-kernel domain exception base — PURE (no infra imports).

Every bounded context defines its OWN domain exceptions (e.g.
``app/care/domain/exceptions.py``) that subclass ``DomainError`` from here, so the
central exception handler (``app/core/errors.py``) can map any domain-rule breach
by catching this one base. The exception -> HTTP mapping lives in that handler
(web layer), never here.

It also holds the cross-cutting ``NotAuthenticated``: authentication is owned by
no single bounded context (its raiser, ``decode_access_token``, is a core
primitive), so the shared kernel is its natural home.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for every domain-rule violation across all bounded contexts."""


class NotAuthenticated(DomainError):
    """Authentication failed: missing, invalid, or expired credentials/token.

    Mapped centrally to HTTP 401. Message is deliberately GENERIC — it never
    reveals which check failed (unknown email vs wrong password vs bad/expired
    token), to avoid a user-enumeration / oracle. Carries no identifying fields.
    """

    def __init__(self, detail: str = "Could not validate credentials.") -> None:
        super().__init__(detail)


class NotFound(DomainError):
    """A requested resource does not exist (mapped centrally to HTTP 404).

    Cross-cutting like ``NotAuthenticated``: existence is not owned by a single
    bounded context, so the shared kernel is its home. The message is deliberately
    GENERIC (it names no id) so a 404 reveals nothing beyond "absent".
    """

    def __init__(self, detail: str = "The requested resource was not found.") -> None:
        super().__init__(detail)


class UnknownReference(DomainError):
    """A write named a foreign key whose target row does not exist.

    Raised by a repository when a client-supplied foreign key (e.g. an episode's
    ``client_id`` / ``managing_org_id``, or a member's ``provider_id``) names a row
    absent from the database, so the INSERT/UPDATE trips a Postgres foreign-key
    violation (SQLSTATE 23503). Cross-cutting like ``NotFound``: a dangling
    reference is not owned by a single bounded context, so the shared kernel is its
    home. Mapped centrally to HTTP 422 (bad client input, not a server fault).

    The violated ``constraint_name`` is kept as an attribute for structured logging
    but deliberately omitted from the generic message, so the response body never
    echoes the submitted id (no PII / enumeration oracle), mirroring
    ``EmailAlreadyRegistered``.
    """

    def __init__(self, constraint_name: str | None = None) -> None:
        self.constraint_name = constraint_name
        super().__init__("A referenced resource does not exist.")
