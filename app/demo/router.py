"""The top-level ``/demo`` route: a self-contained live access-scenario page.

Edge layer (web). The handler depends ONLY on ``get_session`` and on the seed's
DB-free ``world_ids()`` to inject the Sara world's DETERMINISTIC uuid5 ids (plus
the seed login password) into a single static HTML page. The page's own
JavaScript then replays the care-team access scenarios against the SAME-ORIGIN
live ``/v1`` API: all are non-mutating (reads plus denied writes) except a final
short coverage write whose membership expires within ~30s, so the demo requires
no manual cleanup.

Why deterministic ids and not a by-business-key lookup: ``reason`` (episode) and
``name`` (org) are NOT unique columns - the public API lets anyone open an episode
with the same ``reason`` - so a by-reason / by-name / by-email query could resolve
the WRONG row. ``world_ids()`` returns the exact uuid5 ids ``scripts.seed`` writes
(``test_world_ids_matches_seed`` pins the two together), so the page always targets
the real seed rows.

``seeded`` is set True only when the KEY seed rows actually EXIST in the database by
id (a deterministic id that has not been seeded yet still yields the run-the-seed
notice). When the world is absent the route still returns 200, in a "seed-missing"
mode whose page shows a clear run-the-seed notice instead of running the scenarios.
No business rule, policy, or SQL beyond the presence check lives here; the
placeholder ``__SEED_JSON__`` in ``demo.html`` is replaced with the ``json.dumps``
of the lookup result.

The ``scripts.seed`` import below is an INTENTIONAL demo-only coupling to the seed
world: this near-read-only page (the lone write self-expires) exists to showcase
that one committed world, so it is allowed to know its deterministic ids directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.care.orm import EpisodeMembershipModel, EpisodeModel
from app.core.deps import get_session
from app.identity.orm import IdentityModel
from scripts.seed import SaraWorld, world_ids  # intentional demo-only coupling to the seed world

router = APIRouter(tags=["demo"])

_TEMPLATE = Path(__file__).parent / "demo.html"
_SEED_PLACEHOLDER = "__SEED_JSON__"

# The seed's deterministic non-secret login password (mirrors scripts/seed.py).
_SEED_PASSWORD = "seed-not-a-secret"


def _world_present(session: Session, ids: SaraWorld) -> bool:
    """True iff the KEY seed rows exist in the database, by their deterministic id.

    Presence is a primary-key ``get`` on a representative subset (both episodes and
    a couple of identities), NOT merely "world_ids() returned an id": an id that has
    not been seeded yet is correctly reported absent, so the page falls back to the
    run-the-seed notice rather than firing scenarios against rows that do not exist.
    """
    return (
        session.get(EpisodeModel, ids.general) is not None
        and session.get(EpisodeModel, ids.shoulder) is not None
        and session.get(EpisodeModel, ids.closed) is not None
        and session.get(IdentityModel, ids.khan) is not None
        and session.get(IdentityModel, ids.org_admin) is not None
    )


def _lee_coverage(session: Session, ids: SaraWorld) -> tuple[str | None, str | None]:
    """Resolve Lee's BOUNDED Shoulder coverage window as ISO-8601 strings.

    The seed creates exactly one bounded Lee membership on the Shoulder episode (a
    half-open ``[effective_from, effective_to)`` window), so a single
    ``provider_id == lee AND episode_id == shoulder AND effective_to IS NOT NULL``
    row is the coverage window. Returns ``(from, to)`` as ``isoformat`` strings, or
    ``(None, None)`` when that row is absent (e.g. an unseeded world), so the page
    can degrade gracefully rather than assert a window that is not there.
    """
    membership = session.scalars(
        select(EpisodeMembershipModel).where(
            EpisodeMembershipModel.provider_id == ids.lee,
            EpisodeMembershipModel.episode_id == ids.shoulder,
            EpisodeMembershipModel.effective_to.is_not(None),
        )
    ).first()
    if membership is None or membership.effective_to is None:
        return None, None
    return membership.effective_from.isoformat(), membership.effective_to.isoformat()


def _build_seed(session: Session) -> dict[str, object]:
    """Resolve the seed's deterministic ids into the page's injected ``SEED`` dict.

    Ids are all strings (the page's JS only ever interpolates them into URLs). The
    page-facing handle ``olivia`` maps to the FitGym org admin (``admin@example.com``,
    ``SaraWorld.org_admin``). ``coverage_from`` / ``coverage_to`` carry Lee's bounded
    Shoulder coverage window (ISO-8601, or ``None`` when absent) so the page can
    evaluate the time-aware S3 scenario against the current clock. ``seeded`` is True
    only when the key rows are present in the DB; the page replays the scenarios only
    then, otherwise it renders the run-the-seed notice.
    """
    ids = world_ids()
    coverage_from, coverage_to = _lee_coverage(session, ids)
    return {
        "password": _SEED_PASSWORD,
        "sara": str(ids.sara),
        "mike": str(ids.mike),
        "khan": str(ids.khan),
        "patel": str(ids.patel),
        "lee": str(ids.lee),
        "olivia": str(ids.org_admin),
        "fitgym": str(ids.fitgym),
        "khan_solo": str(ids.khan_solo),
        "general": str(ids.general),
        "shoulder": str(ids.shoulder),
        "closed": str(ids.closed),
        "coverage_from": coverage_from,
        "coverage_to": coverage_to,
        "seeded": _world_present(session, ids),
    }


@router.get("/demo", response_class=HTMLResponse)
def demo(session: Annotated[Session, Depends(get_session)]) -> HTMLResponse:
    """Render the self-contained live-scenario page with the seed ids injected."""
    seed = _build_seed(session)
    template = _TEMPLATE.read_text(encoding="utf-8")
    page = template.replace(_SEED_PLACEHOLDER, json.dumps(seed))
    return HTMLResponse(content=page)
