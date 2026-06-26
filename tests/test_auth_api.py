"""Scenario tests for the /v1/auth API on real Postgres (per-test rollback).

Each test drives the real app through the ``client`` fixture (its session/clock/
JWT-secret are overridden onto the per-test transaction); ``get_current_user`` is
NOT overridden, so the whole authentication path runs end to end. Covers
registration (incl. the duplicate-email 409 and the through-the-stack poison
path), the OAuth2 password grant, and every ``/me`` failure mode (uniform 401 +
``WWW-Authenticate: Bearer``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

_REGISTER = "/v1/auth/register"
_TOKEN = "/v1/auth/token"
_ME = "/v1/auth/me"


def _payload(email: str = "ada@example.com", password: str = "s3cretpw") -> dict[str, str]:
    return {"email": email, "display_name": "Ada", "password": password}


# --- registration -----------------------------------------------------------


def test_register_returns_201_identity_without_password_hash(client: TestClient) -> None:
    resp = client.post(_REGISTER, json=_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {"id", "email", "display_name", "created_at"}
    assert body["email"] == "ada@example.com"
    assert body["display_name"] == "Ada"
    assert "password_hash" not in body


def test_register_duplicate_email_returns_409_problem_json(client: TestClient) -> None:
    assert client.post(_REGISTER, json=_payload()).status_code == 201
    dup = client.post(_REGISTER, json=_payload())
    assert dup.status_code == 409
    assert dup.headers["content-type"].startswith("application/problem+json")
    body = dup.json()
    assert body["status"] == 409
    # The address is never echoed back in the body (no enumeration oracle).
    assert "ada@example.com" not in body["detail"]


def test_register_duplicate_email_case_insensitive_returns_409(client: TestClient) -> None:
    assert client.post(_REGISTER, json=_payload(email="ada@example.com")).status_code == 201
    dup = client.post(_REGISTER, json=_payload(email="ADA@example.com"))
    assert dup.status_code == 409


def test_register_after_duplicate_still_succeeds_same_client(client: TestClient) -> None:
    assert client.post(_REGISTER, json=_payload(email="ada@example.com")).status_code == 201
    assert client.post(_REGISTER, json=_payload(email="ada@example.com")).status_code == 409
    # A different email still registers on the SAME client/session: the translated
    # IntegrityError did not poison the unit of work through the full stack.
    assert client.post(_REGISTER, json=_payload(email="bob@example.com")).status_code == 201


def test_register_invalid_email_returns_422(client: TestClient) -> None:
    resp = client.post(_REGISTER, json=_payload(email="not-an-email"))
    assert resp.status_code == 422


def test_register_short_password_returns_422(client: TestClient) -> None:
    resp = client.post(_REGISTER, json=_payload(password="short"))
    assert resp.status_code == 422


# --- token (OAuth2 password grant) ------------------------------------------


def test_token_valid_credentials_returns_bearer(client: TestClient) -> None:
    client.post(_REGISTER, json=_payload())
    resp = client.post(_TOKEN, data={"username": "ada@example.com", "password": "s3cretpw"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_token_username_is_case_insensitive(client: TestClient) -> None:
    client.post(_REGISTER, json=_payload())
    resp = client.post(_TOKEN, data={"username": "ADA@Example.com", "password": "s3cretpw"})
    assert resp.status_code == 200


def test_token_wrong_password_returns_401_with_challenge(client: TestClient) -> None:
    client.post(_REGISTER, json=_payload())
    resp = client.post(_TOKEN, data={"username": "ada@example.com", "password": "wrongpass"})
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_token_unknown_email_returns_401(client: TestClient) -> None:
    resp = client.post(_TOKEN, data={"username": "nobody@example.com", "password": "s3cretpw"})
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


# --- /me failure modes (each maps to a uniform 401 + challenge) -------------


def test_me_with_valid_token_returns_identity(client: TestClient) -> None:
    created = client.post(_REGISTER, json=_payload()).json()
    token = client.post(
        _TOKEN, data={"username": "ada@example.com", "password": "s3cretpw"}
    ).json()["access_token"]
    resp = client.get(_ME, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created["id"]
    assert body["email"] == "ada@example.com"
    assert "password_hash" not in body


def test_me_without_token_returns_401_with_challenge(client: TestClient) -> None:
    resp = client.get(_ME)
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_me_malformed_token_returns_401(client: TestClient) -> None:
    resp = client.get(_ME, headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_me_expired_token_returns_401(
    client: TestClient, mint_token: Callable[..., str], clock: datetime
) -> None:
    created = client.post(_REGISTER, json=_payload()).json()
    # The identity exists; expiry is the only failure. Minted in the past so it is
    # already expired at the fixed verification clock.
    expired = mint_token(created["id"], now=clock - timedelta(hours=1), expires_minutes=1)
    resp = client.get(_ME, headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_me_non_uuid_subject_returns_401(
    client: TestClient, mint_token: Callable[..., str]
) -> None:
    token = mint_token("not-a-uuid")
    resp = client.get(_ME, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_me_unknown_identity_returns_401(
    client: TestClient, mint_token: Callable[..., str]
) -> None:
    token = mint_token(str(uuid4()))  # validly signed, but no such identity row
    resp = client.get(_ME, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_me_with_wrong_secret_token_returns_401(
    client: TestClient, mint_token: Callable[..., str]
) -> None:
    created = client.post(_REGISTER, json=_payload()).json()
    forged = mint_token(created["id"], secret="y" * 40)  # signed with a different secret
    resp = client.get(_ME, headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


# --- capstone: full round trip through one client ---------------------------


def test_register_then_token_then_me_round_trip(client: TestClient) -> None:
    created = client.post(_REGISTER, json=_payload()).json()
    identity_id = created["id"]

    token_resp = client.post(_TOKEN, data={"username": "ada@example.com", "password": "s3cretpw"})
    assert token_resp.status_code == 200
    assert token_resp.json()["token_type"] == "bearer"
    access_token = token_resp.json()["access_token"]

    me_resp = client.get(_ME, headers={"Authorization": f"Bearer {access_token}"})
    assert me_resp.status_code == 200
    me_body = me_resp.json()
    assert me_body["id"] == identity_id
    assert me_body["email"] == "ada@example.com"
    assert "password_hash" not in me_body
