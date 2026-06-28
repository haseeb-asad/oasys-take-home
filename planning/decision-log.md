# Kinetic Backend — Decision Log

**Project:** Kinetic — a physical-wellness care-team platform (Oasys).
**Stack (fixed by brief):** Python · FastAPI · Pydantic · PostgreSQL
**Owner:** Haseeb Asad
**Status:** Part A locked · Part B locked (Episode aggregate) · living document
**Captured:** 2026-06-18 · **Updated:** 2026-06-25

> This file is the **source of truth** for locked decisions. `CLAUDE.md` engineering
> guidance defers to it where they differ.
>
> **Detailed designs:** [`auth-authz-design.md`](auth-authz-design.md) (auth & authz) · [`care-team-design.md`](care-team-design.md) (care-team / Episode aggregate).

---

## Part A — Infrastructure & Scaffolding (LOCKED)

| # | Decision | Choice | Justification |
|---|----------|--------|---------------|
| A1 | ORM vs raw SQL | **SQLAlchemy 2.0 (sync)** for CRUD; raw SQL only for any gnarly reporting query | ORM gives migrations, relationships, pooling. Sync because this is a zero-load app — async buys nothing and adds friction in sessions/repos/tests. |
| A2 | Migrations | **Alembic — hand-written, per-context, extensions first** | Versioned, git-tracked schema. Migrations are **hand-authored** (autogenerate is a draft only — it silently misses `CREATE EXTENSION`, `EXCLUDE`, `citext`, `CHECK`); split **per bounded context in FK order** (`0001_extensions` → identity → organization → care-team) so each ships with its context's commit and the migration history mirrors the incremental build. Alternatives (yoyo, dbmate, raw .sql) add nothing here. |
| A3 | Module structure | **Feature/context modules**, each owning `router · schemas · models · service · repository · domain` | Maps to DDD bounded contexts. Beats layer-first folders that scatter one feature across four dirs. |
| A4 | Layering | **router → service → repository** (Spring-style) | Idiomatic in FastAPI, well-supported. Clean separation, testable domain without HTTP. |
| A5 | Repository pattern | **Yes — one per aggregate root, not per table** | Core DDD building block; keeps domain layer free of SQLAlchemy imports. Justified *because* the brief grades DDD made visible (earlier "skip it" reversed after reading brief). |
| A6 | Thin routers | **Yes** — endpoint only: validate input → resolve identity (`Depends`) → call service → return | Anti-pattern this exercise tests against is fat handlers with inline SQL/policy. Same service callable from seed/CLI. |
| A7 | Schema vs Model naming | **model** = SQLAlchemy (table) · **schema** = Pydantic (DTO) | "Schema" is FastAPI's word for DTO (it emits JSON Schema). Mentally = Spring's `@Entity` vs `DTO`. A *third* exists: the **domain model** (plain Python, holds invariants) — the real point of this exercise. |
| A8 | Request/response models | **Separate `XCreate` / `XUpdate` / `XOut`** (not one shared class) | Input ≠ output ≠ storage. `Create` has password/no id; `Out` has id/timestamps/no password; `Update` all-optional. Prevents nullable hacks + hash leaks. = Spring request/response DTOs. |
| A9 | API style | **REST, always `response_model`** | Explicit output contract; never leak DB fields. |
| A10 | API versioning | **`/v1/*`** | Free now, painful to retrofit. (`/api/v1` not required.) |
| A11 | Auth | **JWT access token, OAuth2 password flow, `passlib[bcrypt]`, `get_current_user` dependency** | FastAPI is built around this; least friction. Authz is **two-layer** (coarse `require_profile` + contextual PDP), not scattered checks. The PDP is **actor-context-scoped** — each request acts on exactly one surface (`client`/`provider`/`org_staff`) fixed by `require_profile`, and an `allowed_capabilities()` resolver evaluates only that surface's branches (replacing the earlier union-of-all-hats model); full design in `auth-authz-design.md`. |
| A12 | Auth — cut | No social login, no SSO, **no refresh-token rotation**, invites **stubbed** as token/record (no email), **no true/immediate revocation — soft-discard only** | Brief explicitly grants skipping these; soft-discard keeps history per the append-only model (rationale in `auth-authz-design.md`). |
| A13 | Postgres extensions | **pgcrypto** (`gen_random_uuid()` for UUID PKs); **btree_gist** for temporal EXCLUDE constraints; **citext** for email (optional, nice) | UUID PKs avoid leaking row counts/sequential IDs. `btree_gist` enforces no overlapping effective periods. citext = case-insensitive email without `LOWER()` everywhere. |
| A14 | ASGI server | **uvicorn** (`uvicorn app.main:app --reload`) | FastAPI is ASGI; uvicorn is the actual server. gunicorn is a process manager (prod hardening, out of scope). |
| A15 | Run / DB | **Docker Compose for Postgres**; app run locally via uvicorn | Fast reload in dev; DB containerized for reproducibility. |
| A16 | Seeding | **Idempotent `scripts/seed.py`** (upsert), separate from migrations | Migrations = schema, seeds = data. Conflating them bites later. |
| A17 | Setup automation | **Bash script** to bring up Docker + Python deps | One-command setup; documented in README. |
| A18 | Column / type conventions | Enum-like vocab (`role`, `profile_type`, episode `status`) → **`VARCHAR` + `CHECK`**, vocabulary owned by the value object in code; **capabilities are code-only** (no DB column); **all timestamps `TIMESTAMPTZ`**, `created_at`/`opened_at` server-default `now()`; **`reason` is free-text `VARCHAR`**, not an enum. | Keeps each vocabulary single-homed in the domain VO (no native-PG-enum migration churn); `TIMESTAMPTZ` matches the `tstzrange` EXCLUDE constraints; nothing keys on `reason`'s value, so it needs no closed set. |
| A19 | Testing strategy | **Two buckets: (1) pure domain/PDP unit tests — no DB, ≥95% branch coverage; (2) scenario tests through the `/v1` API on real Postgres, each building its own world via `conftest` factories, rolled back per test (serial pytest, no xdist). Injectable `now` (`get_now` FastAPI dep, overridden in tests) threaded into PDP + services. Seed script = manual demo only (idempotent, commits to dev DB), decoupled from tests.** | Domain is infra-free (std 1) → its tests need no DB; `EXCLUDE`/`citext`/temporal `[from,to)` are Postgres-only → scenario proof must hit real PG; per-test rollback keeps the shared DB order-independent without truncate/reseed; injectable clock proves coverage-expiry without time-travel; seed decoupled so each test reads as its own world and demo data can evolve freely. |

> **A11 deviation: password hashing.** Password hashing uses **bcrypt directly** over a **base64-encoded SHA-256 pre-hash** (so passwords of any length are supported without bcrypt's 72-byte truncation/collision). `passlib` is dropped — unmaintained (last release 2020) and incompatible with bcrypt 5 on Python 3.14 (its `bcrypt.__about__` probe and wrap-bug detection raise; bcrypt ≤4 has no cp314 wheel).

### Explicitly NOT doing (anti-gold-plating)
Domain events · CQRS · full hexagonal ports/adapters · value-object-around-every-primitive · async · rate limiting · prod hardening · invite emails. Add only if finished early. Brief says twice: depth over breadth, don't gold-plate.

---

## Part B — Domain / Care-Team (LOCKED)

**The care team is of an *Episode of Care* — a bounded course of care for one reason; aggregate root = `Episode`.** "Current" responsibility, membership, and the booking "face" are **derived from effective-dated, append-only rows** (never overwritten), which makes the invariants trivial and history free. Cross-org is handled by a **per-episode managing org**, not by team ownership.

Full design — aggregate shape & methods, invariants, the effective-dated core move, lifecycle walkthrough, bounded contexts & ubiquitous language, migration (expand/contract), and rejected alternatives — lives in [`care-team-design.md`](care-team-design.md), the single source of truth for the domain.

---

## Open questions for Oasys (deliverable #3, running list)

> **Ship note:** `planning/` is committed, so this list ships with the repo; still surface the key open questions in the README for the reviewer's convenience.

1. **Brief inconsistency — company vs. product.** "About Oasys" describes a mental-health EHR; the product paragraph and every build target/scenario describe **"Kinetic," a physical-wellness care-team platform** (trainers, physiotherapists, sports physicians, gyms, PT clinics).
   - **Working assumption (adopted):** model per the **product paragraph + scenarios** (Kinetic, physical wellness). The build targets, scenarios, and evaluation criteria are all written in Kinetic's vocabulary; "About Oasys" reads as company background, and the take-home deliberately swaps in an analogous domain to keep it synthetic.
   - **To confirm with Oasys:** is modeling to the Kinetic product paragraph the intended reading, or should the mental-health-EHR framing drive the ubiquitous language?

2. **Booking "face" — client-level or per-episode?** The brief says "the face the client books through" (client-level wording), but in the multi-episode + cross-org world a single client-level face would front bookings across org boundaries (infeasible/coupling). **Resolved: per-episode**, owned by the `Episode` aggregate. **To confirm with Oasys:** is Kinetic a multi-org marketplace (per-episode face), or a single-coordinator concierge product (client-level face)? (Surfaced by `care-team-design.md`.)
3. **Managing org per episode — one or two?** Can a single episode be co-managed by two orgs, or exactly one managing org per episode? **Assumed exactly one.** (Surfaced by `care-team-design.md`.)
4. **Client self-access to own clinical notes?** A client can self-access their own basic profile, schedule, and booking face (ownership short-circuit). Whether a client may also read their **own provider-authored clinical / rehab notes** is unspecified. **Defaulted to NOT granted** (provider-authored content). Consequence: a **multi-hat person (provider who is also a client) can never read their own notes** — self-treatment bars the provider hat, self-access excludes clinical. It also runs counter to patient-record-access norms (US info-blocking / 21st Century Cures), so it is a genuine question for Oasys; granting it is a one-line PDP change. (Surfaced by `auth-authz-design.md` Layer 2.)
