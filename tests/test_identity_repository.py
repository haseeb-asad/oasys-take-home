"""Integration tests for the SQLAlchemy IdentityRepository (real Postgres, rolled back).

Each test runs inside the per-test transaction (``db_session``) and is rolled
back at teardown, so the shared database stays order-independent (A19). These
prove the Postgres-only behaviour the pure unit tests cannot: CITEXT
case-insensitive email, a tz-aware TIMESTAMPTZ round trip, and the race-free
unique-violation translation that keeps the session usable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.identity.domain.entities import Identity
from app.identity.domain.exceptions import EmailAlreadyRegistered
from app.identity.repository import SqlAlchemyIdentityRepository, _is_email_unique_violation

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _identity(email: str, *, identity_id: UUID | None = None) -> Identity:
    return Identity(
        id=identity_id or uuid4(),
        email=email,
        display_name="Ada",
        password_hash=hash_password("s3cretpw"),
        created_at=_NOW,
    )


# --- IntegrityError narrowing (pure unit; no DB) ----------------------------


class _FakeDiag:
    def __init__(self, constraint_name: str | None) -> None:
        self.constraint_name = constraint_name


class _FakeOrig(Exception):
    """Stand-in for the wrapped psycopg cause (carries sqlstate + diag)."""

    def __init__(self, sqlstate: str | None, constraint_name: str | None) -> None:
        super().__init__("fake psycopg error")
        self.sqlstate = sqlstate
        self.diag = _FakeDiag(constraint_name)


def _integrity_error(orig: BaseException) -> IntegrityError:
    return IntegrityError("INSERT INTO identities ...", None, orig)


@pytest.mark.parametrize(
    ("orig", "expected"),
    [
        (_FakeOrig("23505", "uq_identities_email"), True),  # the email unique violation
        (_FakeOrig("23505", "pk_identities"), False),  # unique, but a different constraint
        (_FakeOrig("23502", None), False),  # not-null, not a unique violation
        (_FakeOrig(None, None), False),  # no sqlstate
        (Exception("no sqlstate/diag attrs"), False),  # defensive: attrs absent
    ],
)
def test_is_email_unique_violation_only_matches_email_constraint(
    orig: BaseException, expected: bool
) -> None:
    assert _is_email_unique_violation(_integrity_error(orig)) is expected


def test_add_then_get_by_id_round_trip(db_session: Session) -> None:
    repo = SqlAlchemyIdentityRepository(db_session)
    identity = _identity("ada@example.com")
    repo.add(identity)
    # Force a real DB read (not the identity map) so the TIMESTAMPTZ round trip
    # is genuinely exercised: a naive read-back would make Identity reject it.
    db_session.expunge_all()
    fetched = repo.get_by_id(identity.id)
    assert fetched is not None
    assert fetched.id == identity.id
    assert fetched.email == "ada@example.com"
    assert fetched.display_name == "Ada"
    assert fetched.password_hash == identity.password_hash
    assert fetched.created_at == _NOW
    assert fetched.created_at.tzinfo is not None
    assert fetched.created_at.utcoffset() is not None


def test_add_then_get_by_email(db_session: Session) -> None:
    repo = SqlAlchemyIdentityRepository(db_session)
    identity = _identity("ada@example.com")
    repo.add(identity)
    fetched = repo.get_by_email("ada@example.com")
    assert fetched is not None
    assert fetched.id == identity.id


def test_get_by_email_is_case_insensitive(db_session: Session) -> None:
    repo = SqlAlchemyIdentityRepository(db_session)
    identity = _identity("ada@example.com")
    repo.add(identity)
    fetched = repo.get_by_email("ADA@Example.com")
    assert fetched is not None
    assert fetched.id == identity.id


def test_get_by_id_missing_returns_none(db_session: Session) -> None:
    repo = SqlAlchemyIdentityRepository(db_session)
    assert repo.get_by_id(uuid4()) is None


def test_get_by_email_missing_returns_none(db_session: Session) -> None:
    repo = SqlAlchemyIdentityRepository(db_session)
    assert repo.get_by_email("nobody@example.com") is None


def test_add_duplicate_case_variant_raises_email_already_registered(db_session: Session) -> None:
    repo = SqlAlchemyIdentityRepository(db_session)
    repo.add(_identity("ada@example.com"))
    with pytest.raises(EmailAlreadyRegistered):
        repo.add(_identity("ADA@example.com"))


def test_add_duplicate_id_reraises_non_email_integrity_error(db_session: Session) -> None:
    repo = SqlAlchemyIdentityRepository(db_session)
    shared_id = uuid4()
    repo.add(_identity("ada@example.com", identity_id=shared_id))
    # Same id, different email: this is the PK unique violation, NOT the email one.
    # It must surface as a plain IntegrityError (re-raised), never mistranslated to
    # EmailAlreadyRegistered (which is not an IntegrityError subclass).
    with pytest.raises(IntegrityError):
        repo.add(_identity("bob@example.com", identity_id=shared_id))


def test_session_usable_after_duplicate(db_session: Session) -> None:
    repo = SqlAlchemyIdentityRepository(db_session)
    repo.add(_identity("ada@example.com"))
    with pytest.raises(EmailAlreadyRegistered):
        repo.add(_identity("ada@example.com"))
    # The SAVEPOINT rollback left the session usable: a different email still adds,
    # and the original row is still readable.
    other = _identity("bob@example.com")
    repo.add(other)
    assert repo.get_by_id(other.id) is not None
    assert repo.get_by_email("ada@example.com") is not None
