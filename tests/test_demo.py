"""Route tests for the live ``/demo`` access-scenario page.

These cover the ROUTE only: that it renders HTML with the seed ids injected when
the Sara world is present, and that it degrades to a clear run-the-seed notice
(still 200, never a 500) when the seed is absent. The scenario LOGIC the page
replays (S1..S7 plus the MANAGE_TEAM paths) is asserted end to end against the
real authorization stack in ``tests/test_scenarios.py``; this page is read-only
and mirrors a non-mutating subset of those recipes in the browser.

Both tests share the per-test ``db_session`` with the ``client`` (the
``get_session`` override yields it), so a seed flushed into the transaction is
visible to the route and rolled back at teardown (A19).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from scripts.seed import seed


def test_demo_renders_with_seeded_world(client: TestClient, db_session: Session) -> None:
    """With the Sara world seeded, ``/demo`` returns HTML carrying the injected ids."""
    world = seed(db_session)

    resp = client.get("/demo")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    # The shoulder episode id is injected via the SEED placeholder replacement.
    assert str(world.shoulder) in body
    # At least one scenario title from the page is present (static template text).
    assert "Khan reads the Shoulder episode" in body


def test_demo_without_seed_shows_notice(client: TestClient) -> None:
    """Without the seed, ``/demo`` still returns 200 and shows the run-the-seed notice."""
    resp = client.get("/demo")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # The notice tells the operator how to provision the world (never a 500).
    assert "python -m scripts.seed" in resp.text
