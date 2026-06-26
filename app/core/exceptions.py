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
