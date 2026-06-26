"""SQLAlchemy adapter implementing the ``IdentityRepository`` port.

Infrastructure layer: maps the ``identities`` table to/from the pure ``Identity``
domain entity via ``_to_domain`` / ``_to_model`` (``app/identity/orm.py``). Email
lookups are case-insensitive at the database (the column is CITEXT), so no
``LOWER()`` is applied here. ``add`` translates the email unique-violation to a
typed domain error without a pre-check (race-free) and without poisoning the
session (the insert runs inside a SAVEPOINT).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.identity.domain.entities import Identity
from app.identity.domain.exceptions import EmailAlreadyRegistered
from app.identity.orm import IdentityModel, _to_domain, _to_model

_UNIQUE_VIOLATION = "23505"
_EMAIL_UNIQUE_CONSTRAINT = "uq_identities_email"


def _is_email_unique_violation(exc: IntegrityError) -> bool:
    """True iff ``exc`` is the Postgres unique violation on the email constraint.

    Defensive: psycopg exposes ``sqlstate`` and ``diag.constraint_name`` on the
    wrapped cause, but either may be absent. If the email constraint cannot be
    positively identified, return False so the caller re-raises (an unrelated
    IntegrityError is never mis-translated into a duplicate-email error).
    """
    orig = exc.orig
    sqlstate: object = getattr(orig, "sqlstate", None)
    if sqlstate != _UNIQUE_VIOLATION:
        return False
    diag = getattr(orig, "diag", None)
    constraint_name: object = getattr(diag, "constraint_name", None)
    return constraint_name == _EMAIL_UNIQUE_CONSTRAINT


class SqlAlchemyIdentityRepository:
    """Reads and stores Identity records against the ``identities`` table."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_email(self, email: str) -> Identity | None:
        model = self._session.scalars(
            select(IdentityModel).where(IdentityModel.email == email)
        ).one_or_none()
        return _to_domain(model) if model is not None else None

    def get_by_id(self, identity_id: UUID) -> Identity | None:
        model = self._session.get(IdentityModel, identity_id)
        return _to_domain(model) if model is not None else None

    def add(self, identity: Identity) -> None:
        """Insert a new identity; translate a duplicate-email to a typed error.

        The insert + flush run inside a SAVEPOINT (``begin_nested``): on a unique
        violation the ``with`` block rolls back only the SAVEPOINT before the
        ``except`` runs, leaving the surrounding session usable. Only the email
        unique violation is translated; any other IntegrityError is re-raised.
        """
        try:
            with self._session.begin_nested():
                self._session.add(_to_model(identity))
                self._session.flush()
        except IntegrityError as exc:
            if _is_email_unique_violation(exc):
                raise EmailAlreadyRegistered(identity.email) from exc
            raise
