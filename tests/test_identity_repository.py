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
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.identity.domain.entities import Identity, Profile
from app.identity.domain.exceptions import EmailAlreadyRegistered
from app.identity.domain.value_objects import ProfileType
from app.identity.repository import (
    SqlAlchemyIdentityRepository,
    SqlAlchemyProfileRepository,
    _is_email_unique_violation,
)

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_DISCARDED_AT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def _identity(email: str, *, identity_id: UUID | None = None) -> Identity:
    return Identity(
        id=identity_id or uuid4(),
        email=email,
        display_name="Ada",
        password_hash=hash_password("s3cretpw"),
        created_at=_NOW,
    )


def _persist_identity(session: Session, email: str) -> UUID:
    """Persist an FK-parent Identity with a plain stub hash; return its id."""
    identity = Identity(
        id=uuid4(),
        email=email,
        display_name="Ada",
        password_hash="stub-hash",
        created_at=_NOW,
    )
    SqlAlchemyIdentityRepository(session).add(identity)
    return identity.id


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


# --- Profile repository (real Postgres, rolled back) ------------------------


def test_profile_add_then_list_for_round_trip(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "profile-rt@example.com")
    repo = SqlAlchemyProfileRepository(db_session)
    profile = Profile(id=uuid4(), identity_id=identity_id, profile_type=ProfileType.PROVIDER)
    repo.add(profile)
    # Force a real DB read (not the identity map) so the round trip is genuine.
    db_session.expunge_all()
    rows = repo.list_for(identity_id)
    assert len(rows) == 1
    assert rows[0].id == profile.id
    assert rows[0].identity_id == identity_id
    assert rows[0].profile_type is ProfileType.PROVIDER
    assert rows[0].discarded_at is None
    assert rows[0].is_active is True


def test_profile_discarded_round_trip_preserves_tz(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "discarded-rt@example.com")
    repo = SqlAlchemyProfileRepository(db_session)
    repo.add(
        Profile(
            id=uuid4(),
            identity_id=identity_id,
            profile_type=ProfileType.ORG_STAFF,
            discarded_at=_DISCARDED_AT,
        )
    )
    db_session.expunge_all()
    rows = repo.list_for(identity_id)
    assert len(rows) == 1
    assert rows[0].is_active is False
    assert rows[0].discarded_at == _DISCARDED_AT
    assert rows[0].discarded_at is not None
    assert rows[0].discarded_at.tzinfo is not None
    assert rows[0].discarded_at.utcoffset() is not None


def test_profile_list_for_empty_returns_empty_list(db_session: Session) -> None:
    repo = SqlAlchemyProfileRepository(db_session)
    assert repo.list_for(uuid4()) == []


def test_profile_list_for_isolates_by_identity(db_session: Session) -> None:
    identity_a = _persist_identity(db_session, "p-iso-a@example.com")
    identity_b = _persist_identity(db_session, "p-iso-b@example.com")
    repo = SqlAlchemyProfileRepository(db_session)
    repo.add(Profile(id=uuid4(), identity_id=identity_a, profile_type=ProfileType.CLIENT))
    repo.add(Profile(id=uuid4(), identity_id=identity_b, profile_type=ProfileType.PROVIDER))
    db_session.expunge_all()
    rows = repo.list_for(identity_a)
    assert len(rows) == 1
    assert rows[0].identity_id == identity_a


def test_all_profile_types_accepted_by_check(db_session: Session) -> None:
    identity_id = _persist_identity(db_session, "p-all-types@example.com")
    repo = SqlAlchemyProfileRepository(db_session)
    for profile_type in ProfileType:
        repo.add(Profile(id=uuid4(), identity_id=identity_id, profile_type=profile_type))
    db_session.expunge_all()
    rows = repo.list_for(identity_id)
    assert {row.profile_type for row in rows} == set(ProfileType)


def test_profile_fk_violation_missing_identity_raises(db_session: Session) -> None:
    repo = SqlAlchemyProfileRepository(db_session)
    profile = Profile(id=uuid4(), identity_id=uuid4(), profile_type=ProfileType.PROVIDER)
    # Terminal DB action (AM3): the FK violation poisons the session; per-test
    # rollback recovers it.
    with pytest.raises(IntegrityError) as exc_info:
        repo.add(profile)
    assert "fk_profiles_identity_id_identities" in str(exc_info.value.orig)


def test_profile_type_check_rejects_bad_value_raw_insert(db_session: Session) -> None:
    # FK parent satisfied so the ONLY violation is the profile_type CHECK; the raw
    # insert bypasses the domain (which the ProfileType enum would block).
    identity_id = _persist_identity(db_session, "p-check@example.com")
    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text(
                "INSERT INTO profiles (id, identity_id, profile_type) "
                "VALUES (gen_random_uuid(), :identity_id, 'superuser')"
            ),
            {"identity_id": identity_id},
        )
    assert "ck_profiles_profile_type" in str(exc_info.value.orig)
