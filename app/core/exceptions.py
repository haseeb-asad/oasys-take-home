"""Shared-kernel domain exception base — PURE (no infra imports).

Every bounded context defines its OWN domain exceptions (e.g.
``app/care/domain/exceptions.py``) that subclass ``DomainError`` from here, so the
central exception handler (``app/core/errors.py``) can map any domain-rule breach
by catching this one base. The exception -> HTTP mapping lives in that handler
(web layer), never here.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for every domain-rule violation across all bounded contexts."""
