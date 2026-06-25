"""Tests for the central exception handlers (``app/core/errors.py``).

Builds a throwaway FastAPI app, registers the handlers, and asserts each domain
exception maps to the right HTTP status + RFC 7807 body — the cross-context
guarantee a real router relies on.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.care.domain.exceptions import (
    EpisodeClosed,
    NotACurrentMember,
    OverlappingPeriod,
    SelfTreatment,
)
from app.core.errors import register_exception_handlers

_ID = UUID(int=7)


def _client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/self-treatment")
    def _st() -> None:
        raise SelfTreatment(_ID)

    @app.get("/not-member")
    def _nm() -> None:
        raise NotACurrentMember(_ID)

    @app.get("/closed")
    def _closed() -> None:
        raise EpisodeClosed(_ID)

    @app.get("/overlap")
    def _ov() -> None:
        raise OverlappingPeriod(_ID)

    @app.get("/value-error")
    def _ve() -> None:
        raise ValueError("bad input")

    @app.get("/boom")
    def _boom() -> None:
        raise RuntimeError("kaboom")

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/self-treatment", 422),
        ("/not-member", 422),
        ("/closed", 409),
        ("/overlap", 409),
        ("/value-error", 422),
    ],
)
def test_exception_maps_to_status_and_problem_json(path: str, status: int) -> None:
    resp = _client().get(path)
    assert resp.status_code == status
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["status"] == status
    assert body["title"]
    assert body["detail"]


def test_unhandled_exception_becomes_structured_500() -> None:
    resp = _client().get("/boom")
    assert resp.status_code == 500
    assert resp.json()["title"] == "InternalServerError"
