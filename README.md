# Kinetic Backend

Backend take-home for the Oasys prompt. This is a small FastAPI and PostgreSQL
implementation of the Kinetic care-team core: identity, typed profiles, episode
membership, responsibility handoff, booking face handoff, cross-org access, and
role-based clinical access.

The scope is intentionally narrow. The care-team model is the center of the
submission; scheduling, billing, documents, wearables, email delivery, and a full
onboarding workflow are not built.

## Quick Start

Prerequisites:

- Docker Desktop or Docker Engine
- Python 3.14, or `uv` with Python installation support

One command handles local setup:

```bash
./setup.sh
```

The script does all of this:

- starts Postgres with Docker Compose
- installs Python dependencies, preferring `uv` and falling back to `.venv`
- creates `.env` from `.env.example` if needed
- runs `alembic upgrade head`
- runs the idempotent seed script

Start the API after setup:

```bash
uv run uvicorn app.main:app --reload
```

If setup used the `.venv` fallback:

```bash
./.venv/bin/uvicorn app.main:app --reload
```

Then open:

- API docs: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## Seed Data

The seed creates the scenario world used by the design:

| Person | Email | Password | Profile |
|---|---|---|---|
| Sara Client | `sara@example.com` | `seed-not-a-secret` | client |
| Mike Trainer | `mike@example.com` | `seed-not-a-secret` | provider |
| Dr Khan | `khan@example.com` | `seed-not-a-secret` | provider |
| Dr Patel | `patel@example.com` | `seed-not-a-secret` | provider |
| Dr Lee | `lee@example.com` | `seed-not-a-secret` | provider |
| Olivia Admin | `admin@example.com` | `seed-not-a-secret` | org_staff |

The seed also creates:

- `FitGym`, a `gym` organization with Olivia as org admin
- `Khan Solo Practice`, a `solo_practice` organization with no org admin
- Sara's `general_training` episode managed by FitGym, with Mike responsible and as the booking face
- Sara's `shoulder_rehab` episode managed by Khan Solo Practice, with Khan responsible and as the booking face
- Patel as physician on `shoulder_rehab`
- Lee as temporary coverage on `shoulder_rehab`

Re-run the seed at any time:

```bash
uv run python -m scripts.seed
```

It is idempotent. It creates missing rows and leaves existing seeded rows alone.

To print the deterministic seed IDs:

```bash
uv run python - <<'PY'
import dataclasses

from app.core.database import get_sessionmaker
from scripts.seed import seed

with get_sessionmaker()() as session:
    world = seed(session)
    session.commit()
    for key, value in dataclasses.asdict(world).items():
        print(f"{key}: {value}")
PY
```

Use `./.venv/bin/python` instead of `uv run python` if you are not using `uv`.

## API Demo

The easiest manual path is to use Swagger at `/docs`.

1. Call `POST /v1/auth/token`.
2. Use a seeded email as `username` and `seed-not-a-secret` as `password`.
3. Click `Authorize` in Swagger and paste the bearer token.
4. Use the seeded IDs printed by the command above for episode and person IDs.

Important route rule:

- Some episode routes can be reached by more than one profile surface.
- For those routes, pass `acting_as=provider`, `acting_as=client`, or `acting_as=org_staff`.
- This is not a permission claim. The server verifies that the authenticated identity actually holds that profile, then the PDP evaluates only that actor surface.

Useful flows to try:

- Login as Mike and read Sara's `general_training` episode as `acting_as=provider`.
- Login as Sara and read her own episode as `acting_as=client`.
- Login as Olivia and add a provider to the FitGym-managed episode as `acting_as=org_staff`.
- Login as Khan and add Patel to the Khan-managed rehab episode as `acting_as=provider`.
- Try Olivia against the Khan-managed episode. It should be denied because FitGym admin authority does not cross into Khan Solo Practice.
- Try a trainer or massage therapist against clinical or rehab endpoints. They should be denied even when they are team members.

Core endpoints:

- `POST /v1/auth/register`
- `POST /v1/auth/token`
- `GET /v1/auth/me`
- `POST /v1/episodes`
- `GET /v1/episodes/{episode_id}`
- `POST /v1/episodes/{episode_id}/members`
- `POST /v1/episodes/{episode_id}/members/{provider_id}/end`
- `PUT /v1/episodes/{episode_id}/responsibility`
- `PUT /v1/episodes/{episode_id}/face`
- `POST /v1/episodes/{episode_id}/close`
- `POST /v1/episodes/{episode_id}/clinical-records`
- `GET /v1/episodes/{episode_id}/clinical-records`
- `GET /v1/episodes/{episode_id}/rehab-assessments`

## Tests As Demonstration

The tests are the best executable tour of the system. The suite is 601 tests, and
`ruff`, `mypy --strict`, and `pytest` run in CI on every pull request.

Run the full suite after `./setup.sh`:

```bash
uv run pytest
```

If you are using the `.venv` fallback:

```bash
./.venv/bin/python -m pytest
```

Useful focused suites:

```bash
uv run pytest tests/test_care_api.py -q -rs
uv run pytest app/care/tests/test_episode.py -q
uv run pytest app/authz/tests/test_policy.py -q
uv run pytest tests/test_seed.py -q -rs
uv run pytest tests/test_backfill_episodes.py -q -rs
```

What these prove:

- `tests/test_care_api.py` exercises the real FastAPI app, real auth, real PDP gates, and real Postgres persistence for the care-team scenarios.
- `app/care/tests/test_episode.py` proves the `Episode` aggregate invariants without infrastructure.
- `app/authz/tests/test_policy.py` proves actor-surface-scoped authorization, see-vs-act capabilities, coverage expiry, closed episode behavior, and cross-org denial.
- `tests/test_seed.py` proves the Sara world seed is deterministic and idempotent.
- `tests/test_backfill_episodes.py` covers the bonus expand/contract migration sketch.

DB-backed tests skip locally if Postgres is not reachable. In CI they fail closed
instead of silently passing. Run `./setup.sh` first if you want the full
Postgres-backed proof locally.

Quality checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## Architecture

The code is organized by bounded context rather than by technical layer:

- `app/identity`: login identity and typed profiles
- `app/authz`: capability vocabulary, actor context, PDP, and profile-directory port
- `app/care`: Episode aggregate, care-team API, clinical records, rehab assessments
- `app/organization`: organizations and effective-dated org-staff memberships
- `app/core`: settings, database, security primitives, shared exceptions, error handling

Within each context, the shape is:

- domain entities and value objects are plain Python
- services orchestrate use cases
- repositories adapt SQLAlchemy to domain ports
- routers are thin FastAPI edges
- schemas are Pydantic request and response DTOs

The main DDD boundary is the `Episode` aggregate. It owns:

- one client
- one reason for care
- one managing org
- effective-dated episode memberships
- effective-dated clinical responsibility
- effective-dated booking face
- active or closed lifecycle

The aggregate protects these invariants:

- a provider cannot be added to their own episode as a care-team member
- responsibility and booking face must point at current episode members
- responsibility handoff closes the old row and opens the new row at the same instant
- booking face handoff follows the same no-gap, no-overlap pattern
- closed episodes cannot be mutated
- history is kept as effective-dated rows with a change reason; membership rows also carry the role

Postgres reinforces the one-at-a-time rules with `EXCLUDE USING gist` constraints
on responsibility and booking-face periods.

The full design rationale ships in `planning/`: the data model and ERD
(`data-model.md`), the care-team design (`care-team-design.md`), the authorization
design (`auth-authz-design.md`), the locked decision log (`decision-log.md`), and
the per-scenario answers to the prompt (`scenario-answers.md`).

## Access Model

Authentication identifies an `Identity`. Authorization is derived server-side.

One identity can hold multiple typed `Profile` rows:

- `client`
- `provider`
- `org_staff`

Each request is evaluated under exactly one actor surface:

```text
ActorContext(identity_id, profile_type)
```

The PDP then evaluates only the branches for that surface:

- client surface: self-access to client-facing views
- provider surface: active provider profile plus current episode membership plus role capability
- provider surface: current clinically responsible provider gets `MANAGE_TEAM`
- org_staff surface: active org-staff profile plus active admin membership in the episode's managing org

Capabilities are deliberately flat. Episode membership supplies the scope.

Examples:

- Physician and physiotherapist can view clinical and rehab material.
- Personal trainer and massage therapist can run sessions and message clients, but do not see clinical or rehab records by default.
- A responsible provider can manage the team for their active episode.
- A FitGym admin can manage FitGym-managed episodes, but not Khan Solo Practice episodes.
- A closed episode keeps role-limited views but drops act capabilities.

## Care-Team Scenario Mapping

The PDF scenarios map as follows:

- Concurrent trainer, physiotherapist, and physician: multiple active episodes for the same client, with different members per episode.
- Booking face differs from clinical responsibility: `booking_contacts` and `responsibility_assignments` are separate effective-dated rows.
- Coverage: a temporary membership with `effective_from` and `effective_to`.
- Rehab closes but history remains: closing an episode blocks mutation, but rows remain for historical reads.
- See vs act: resources require capabilities, and roles grant different capability sets.
- Cross-org access: episode membership is the access boundary, not provider employment or org membership.
- History: membership, responsibility, and face changes keep their time window and `change_reason`, and membership rows also keep the role.

## Key Decisions And Trade-offs

1. Team of an Episode of Care

   A whole-client team is too broad when one client has unrelated concurrent care
   reasons. An org-owned case breaks cross-org rehab. `Episode` keeps the hard
   invariants local and still supports multiple concurrent care relationships.

2. Capabilities in code, not tables

   The DB stores facts: identities, profiles, memberships, org staff rows,
   effective periods. Code owns the product meaning of those facts. This keeps
   the authorization baseline versioned, reviewable, and tested.

3. Flat capabilities, episode-scoped PDP

   A capability like `VIEW_CLINICAL` is not scoped inside the string. The resource
   points to an episode, and the PDP checks membership in that episode. This
   avoids a role by resource by episode permission matrix.

4. Actor surface per request

   A multi-hat person does not get a union of all hats. The route resolves one
   surface, verifies the identity holds it, and passes that `ActorContext` to the
   PDP. This avoids ambiguous client/provider/org-staff behavior.

5. Sync SQLAlchemy and hand-authored Alembic

   Sync SQLAlchemy is enough for this workload and keeps the repository code
   simple. Migrations are hand-authored because this schema uses Postgres details
   such as `citext`, `btree_gist`, checks, and exclusion constraints.

## Accepted V1 Trade-offs

These are deliberate simplifications for the take-home. They keep the runnable
slice focused on identity, episode membership, and authorization.

- Authz denial diagnostics: the PDP carries actor, capability, and resource
  internally for tests and debugging. The v1 API may expose that detail in a 403
  response; a hardened public API should return a generic forbidden message and
  log the structured denial server-side.
- Invitation onboarding: v1 supports registration/login and documents
  invitations as a token/status/accepted-identity stub. Email sending and full
  invitation acceptance are intentionally cut because the care-team core is the
  focus.
- Provider-create bootstrap: providers can create the initial episode in the
  synthetic demo path. A fuller product would add org-admin episode creation and
  stricter onboarding policy.
- One managing organization per episode: this keeps org-admin management
  unambiguous. Multi-org management is left as an open question.
- Episode-scoped resources only: clinical records and rehab assessments are
  built because they exercise the access model. Client-scoped profile/schedule
  resources are documented next steps.
- Enriched backfill staging: the bonus migration assumes staging contains role
  and managing org so the backfill is deterministic. A raw legacy link would
  need policy to synthesize missing structure.

## What Is Cut

Cut from runtime scope:

- email sending
- SSO, social login, MFA, refresh-token rotation, token denylist
- full scheduling and billing
- documents and wearable data
- typed provider credentials and client intake tables
- public organization-management API
- full invitation acceptance API
- client-scoped profile and schedule tables
- production observability, rate limiting, and deployment hardening

Onboarding is intentionally shallow. The runtime API supports identity
registration and login. The seed and tests create the typed profiles and org
staff memberships needed to exercise the care-team core. The planning ERD marks
invitations as the minimal token/status/accepted-identity stub to add next, with
no email sending and no automatic episode membership.

## Bonus Migration Path

The PDF asks, as a bonus, how to migrate from a one-provider-per-client model
without a big-bang cutover.

This repo includes an executable expand/contract-style migration sketch:

- `migrations/versions/0008_legacy_provider_backfill.py`
- `tests/test_backfill_episodes.py`

It uses an enriched legacy staging table, backfills one `general_care` episode
per legacy client-provider pairing, makes the legacy provider the initial member,
responsible provider, and booking face, then records `migrated_episode_id` so the
backfill is idempotent.

The sketch takes the enriched-staging-table path: the legacy table carries the
`role` and `managing_org_id` the episode model requires, so the backfill copies
those fields straight from staging (a policy-free projection). A second path, closer to a literal one-provider-per-client
link, would carry only the client and provider and synthesize the missing
structure during the backfill:

- managing org: create one `solo_practice` organization per legacy provider, with
  a deterministic id keyed on the provider so a provider's clients share one org,
  matching how independent providers are already modeled.
- role: default to a documented least-privilege role such as `personal_trainer`,
  since the literal link records none.

That path demonstrates the migration filling the schema gap, at the cost of
writing organization rows from a data migration and a heavier downgrade (the
synthesized organizations must be reasoned about). The open question on whether
independent providers should always be `solo_practice` organizations is exactly
this trade-off.

This is a bonus demonstration, not required for the v1 runtime API.

## Open Questions

Questions I would ask Oasys before hardening this into product behavior:

1. Can an episode have more than one managing organization, or is one
   `managing_org_id` correct?
2. Should self-sign-up create a client profile immediately, or should profile
   creation always happen through invitation/onboarding?
3. Should independent providers always be represented as `solo_practice`
   organizations, or should there be a separate individual-provider org shape?
4. Should an organization admin be able to open episodes for their organization?
   Today only providers open episodes; an admin can manage an existing
   managing-org episode but cannot create one.
5. Is closing an episode terminal, or should an episode be reopenable if care
   resumes for the same reason? Closing is terminal today, so resumed care starts
   a new episode.
