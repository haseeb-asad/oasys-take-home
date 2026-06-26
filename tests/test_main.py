"""Tests for the application factory and module-level app.

``/health`` is a liveness probe (no DB). The third test proves the central
exception handlers are wired on the real app: a route raising a domain
exception comes back as an RFC 7807 ``application/problem+json`` response, not
an unstructured 500.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import NotAuthenticated
from app.main import app, create_app


def test_health_returns_ok() -> None:
    resp = TestClient(create_app()).get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_module_app_is_fastapi() -> None:
    assert isinstance(app, FastAPI)
    assert app.title == "Kinetic Backend"


def test_real_app_has_exception_handlers_registered() -> None:
    real = create_app()

    @real.get("/raise-auth")
    def _raise_auth() -> None:
        raise NotAuthenticated()

    resp = TestClient(real, raise_server_exceptions=False).get("/raise-auth")
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["status"] == 401
