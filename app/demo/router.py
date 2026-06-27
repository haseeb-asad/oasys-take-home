"""The top-level ``/demo`` route: a self-contained live access-scenario page.

Edge layer (web). The handler depends ONLY on ``get_session`` to look up the
seeded "Sara world" by its stable business keys (identities by email,
organizations by name, episodes by reason) and inject their ids plus the seed
password into a single static HTML page. The page's own JavaScript then replays
the NON-MUTATING care-team access scenarios against the SAME-ORIGIN live ``/v1``
API (reads plus denied writes), so the demo is repeatable and never degrades the
seed.

There is no org-create / profile-create HTTP surface, so the page cannot
bootstrap a fresh world: it drives the COMMITTED seed (``python -m scripts.seed``).
When the core seed entities are absent the route still returns 200, in a
"seed-missing" mode whose page shows a clear run-the-seed notice instead of
running the scenarios. No business rule, policy, or SQL beyond the lookup lives
here; the placeholder ``__SEED_JSON__`` in ``demo.html`` is replaced with the
``json.dumps`` of the lookup result.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.care.orm import EpisodeModel
from app.core.deps import get_session
from app.identity.orm import IdentityModel
from app.organization.orm import OrganizationModel

router = APIRouter(tags=["demo"])

_TEMPLATE = Path(__file__).parent / "demo.html"
_SEED_PLACEHOLDER = "__SEED_JSON__"

# The seed's deterministic non-secret login password (mirrors scripts/seed.py).
_SEED_PASSWORD = "seed-not-a-secret"

# The seed's stable business keys, by the page-facing handle they map to. These
# mirror scripts/seed.py exactly (identity email, org name, episode reason).
_IDENTITY_EMAILS: dict[str, str] = {
    "sara": "sara@example.com",
    "mike": "mike@example.com",
    "khan": "khan@example.com",
    "patel": "patel@example.com",
    "lee": "lee@example.com",
    "olivia": "admin@example.com",
}
_ORG_NAMES: dict[str, str] = {
    "fitgym": "FitGym",
    "khan_solo": "Khan Solo Practice",
}
_EPISODE_REASONS: dict[str, str] = {
    "general": "general_training",
    "shoulder": "shoulder_rehab",
}
# Every handle whose id the page needs before it can replay the live scenarios.
_REQUIRED_IDS: tuple[str, ...] = (*_IDENTITY_EMAILS, *_ORG_NAMES, *_EPISODE_REASONS)


def _identity_id(session: Session, email: str) -> str | None:
    """The id of the identity with ``email`` as a string (None if absent)."""
    model = session.scalars(select(IdentityModel).where(IdentityModel.email == email)).first()
    return str(model.id) if model is not None else None


def _org_id(session: Session, name: str) -> str | None:
    """The id of the organization named ``name`` as a string (None if absent)."""
    model = session.scalars(select(OrganizationModel).where(OrganizationModel.name == name)).first()
    return str(model.id) if model is not None else None


def _episode_id(session: Session, reason: str) -> str | None:
    """The id of the episode whose ``reason`` matches as a string (None if absent)."""
    model = session.scalars(select(EpisodeModel).where(EpisodeModel.reason == reason)).first()
    return str(model.id) if model is not None else None


def _build_seed(session: Session) -> dict[str, object]:
    """Resolve the seed business keys into the page's injected ``SEED`` dict.

    Values are all strings (or None when a key is unresolved). ``seeded`` is True
    only when EVERY required id resolved; the page replays the scenarios only
    then, otherwise it renders the run-the-seed notice.
    """
    seed: dict[str, object] = {"password": _SEED_PASSWORD}
    for handle, email in _IDENTITY_EMAILS.items():
        seed[handle] = _identity_id(session, email)
    for handle, name in _ORG_NAMES.items():
        seed[handle] = _org_id(session, name)
    for handle, reason in _EPISODE_REASONS.items():
        seed[handle] = _episode_id(session, reason)
    seed["seeded"] = all(seed.get(handle) is not None for handle in _REQUIRED_IDS)
    return seed


@router.get("/demo", response_class=HTMLResponse)
def demo(session: Annotated[Session, Depends(get_session)]) -> HTMLResponse:
    """Render the self-contained live-scenario page with the seed ids injected."""
    seed = _build_seed(session)
    template = _TEMPLATE.read_text(encoding="utf-8")
    page = template.replace(_SEED_PLACEHOLDER, json.dumps(seed))
    return HTMLResponse(content=page)
