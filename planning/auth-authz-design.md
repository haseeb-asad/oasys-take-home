# Authentication & Authorization — Design Spec

**Project:** Kinetic (Oasys)
**Stack:** FastAPI · Pydantic · PostgreSQL · SQLAlchemy 2.0 (sync)
**Owner:** Haseeb Asad
**Status:** Design locked; PDP is **actor-context-scoped** (per-request surface; resolver + thin gate); membership lookup is resolved against the `Episode` aggregate.
**Captured:** 2026-06-18

> **Cross-refs (single source of truth, no duplication):** `decision-log.md` A11/A12 hold the locked auth choices and link here for the full design. The **[`care-team-design.md`](care-team-design.md)** owns the *membership aggregate* (the `Episode`) this PDP reads; this doc owns the *capability mapping + decision*.

---

## Summary

Two-layer authorization:
- **Layer 1 — Coarse RBAC (profile-type gating):** gates whole API surfaces by the **profile-types the caller's Identity holds** (client / provider / org_staff), and **fixes the request's actor surface**. Kept simple.
- **Layer 2 — Contextual capability check (ReBAC-flavored):** the core. **For the request's actor surface**, access flows from server-side relationships to the resource: provider episode membership, client ownership, responsible-provider authority, or managing-org staff authority.

A **single PDP** (Policy Decision Point) makes every access decision from a resolved **actor context** (identity + surface). **Hand-rolled**, not a library. JWT for authentication.

Rationale for two layers: both pure global models (scopes-on-token, or global roles) fail the brief because access here is *relationship-scoped and temporal* — a provider may see a client's record only while on that client's team, in a role that grants it. A global `clinical:read` scope would expose every client. Industry consensus is hybrid: coarse RBAC for broad buckets + relationship/attribute layer for fine-grained access.

---

## Authentication (identity)

- **JWT access token**, OAuth2 password flow (`OAuth2PasswordBearer`).
- **Password hashing:** `passlib[bcrypt]`.
- **`get_current_user` dependency** resolves the authenticated Identity from the verified token. Nothing beyond the signed token is trusted; **all authorization is derived server-side**.
- **`Identity` table (the login credential):** `identities(id UUID PK, email citext UNIQUE NOT NULL, display_name, password_hash, created_at)`. **Email is the login identifier** (the OAuth2 password-flow "username"), promoted from "optional" (decision-log A13) to required; `display_name` rides here too for readable seeds/demos. Each `Profile` references `identity_id` and is **only the typed surface** (`profile_type`) authz gates on — **persona-specific data (provider credentials/specialty, client intake) is cut for v1** (typed `provider_profiles`/`client_profiles` are Next).
- **Identity + Profiles:** one Person is a single `Identity` (the authenticating subject) that owns **one or more typed `Profile`s**, one per persona it acts as (e.g. a client profile AND a provider profile). The multi-hat case (client AND provider) is **two Profiles under one Identity — never a `system_roles` set on the Identity.** Profiles are the typed surface that authorization gates on, and the natural home for **profile-specific data** (provider credentials/specialty; client demographics/intake) **once it is modeled — deferred to Next; v1 stores only `profile_type` + `discarded_at`**.
  - *Rejected — flat roles-set only:* a set of roles with no profiles has nowhere to hang persona-specific data (provider credentials vs client intake) and blurs the client-vs-provider personas the no-self-treatment invariant turns on.
  - *Rejected — generic Party / PartyRelationship engine:* a fully abstract party-and-relationship metamodel is the classic over-build; it buys flexibility this brief never asks for and hides the domain the exercise grades. `Identity → Profile` is the minimal shape that is the typed surface authorization gates on (and the natural home for persona data when modeled).
  - **Self-treatment is barred at the membership boundary, not here.** A provider profile may not join a care-team episode whose `client_id` is the same Identity — the **no-self-treatment invariant**, whose canonical home is the `Episode` aggregate (see [`care-team-design.md`](care-team-design.md), Q1 invariants). This section carries only the cross-ref.
- **Onboarding / invitations (stub):** an `Invitation` record + token; **no email send**. Client self-signup allowed. Provider/org invites a client → pending `Invitation` → on accept, an **`Identity` (+ client `Profile`)** is created; any care-team membership is added **separately** via `Episode.add_member` (membership is an entity of the Episode aggregate, not created by the invite itself). **Bootstrap policy:** an active provider Profile may create/invite into an episode where that provider becomes the initial responsible member; an org_staff Profile with active admin membership in the target org may create/invite for that org. After the episode exists, team changes require `MANAGE_TEAM`.
- **Per-request actor surface, not a token persona switch.** The token authenticates the **Identity** only (`sub`); it carries **no `active_profile` claim** and there is **no `/auth/switch` endpoint**. Instead, every request acts under exactly **one surface** — the profile-type the *route* serves (`client` / `provider` / `org_staff`), fixed by Layer 1's `require_profile` and carried into Layer 2 as an **`ActorContext(identity_id, profile_type)`**. The PDP evaluates **only the branches valid for that surface** (client → ownership self-access; provider → episode-membership + responsible-provider; org_staff → managing-org admin), not the union of every hat at once. The surface is **route-derived, server-side** — not a client claim, not a UI toggle — so it needs no token change. A multi-hat person (client *and* provider) is therefore gated *and* evaluated on the surface they are actually using: a client endpoint yields only client self-access; a provider endpoint is subject to the no-self-treatment invariant (enforced at the membership boundary). **Why surface-scoped, not union:** per-surface least privilege; the audit log can attribute an action to the hat in use; a mixed route can't allow via the wrong hat; and client-self vs provider access stays unambiguous when one person holds both. This intentionally narrows cross-hat outcomes (a client-surface request cannot borrow provider powers) while preserving the identity-based invariants: self-treatment and own-clinical-notes denial hold on *every* surface. (Within a single surface the relevant relationships still combine — on the provider surface a member's role grant and the responsible-provider grant union.) **Next (its own feature):** a *token-level* active hat — an `active_profile` claim in the token, a `/auth/switch` endpoint, plus a UI affordance — adopted when the acting hat must live in the token / persist across a session rather than being derived per request.
- **Profile discard is soft, and that is deliberate.** Discarding a Profile sets `discarded_at` rather than deleting it (history per the append-only model). Because held profiles and memberships are read **server-side on every request**, a discard (or a removed membership) takes effect on the **next request** — no token re-issue needed for an authorization change. A provider membership grants only if the provider Identity still has an active provider Profile; an org-admin grant requires an active org_staff Profile. The token only authenticates the Identity; **true (immediate) token revocation / logout-all is a production concern — explicitly NOT built here.**

### Cut from auth
No social login, SSO, refresh-token rotation, **token revocation/denylist (soft-discard only)**, **token-level persona-switch / `active_profile` claim (cut — the acting surface is derived per request from the route, not baked into the token)**, MFA, rate limiting, email delivery, production hardening, **persona-specific profile data (v1 Profile is the typed surface only; typed provider/client profile tables are Next)**. (All explicitly granted as out-of-scope by the brief; persona-switch cut as un-asked breadth.)

---

## Layer 1 — Coarse RBAC (surface gating)

- A caller acts under one or more **Profiles** (profile-types `∈ {client, provider, org_staff}`); a person may hold several, one Profile per type.
- **Purpose:** gate entire API families ("can this caller touch org-admin endpoints at all").
- **Mechanism:** `require_profile(...)` FastAPI dependency on routers gates a surface on **which profile-types the caller's Identity holds** (resolved server-side from the Identity's Profiles) — e.g. an endpoint only a *provider* may touch is refused for an Identity with no provider Profile, before Layer 2 runs. No "acting persona" is consulted; holding the required profile-type is sufficient at the surface, and the per-resource PDP (Layer 2) still gates the actual data. The profile-type a router requires also **fixes the request's actor surface** (`ActorContext.profile_type`) handed to Layer 2, so the PDP evaluates only that surface's branches.
- **Mixed-surface route rule:** avoid PDP-protected routes that accept several surfaces at once. If a shared endpoint is unavoidable, it must choose exactly one server-owned `ActorContext` before calling the PDP (for example via separate dependencies or an explicit `actor_surface=` parameter in code). Never pass a set of profile-types into the PDP and never "try another hat" after one surface denies.
- **Do NOT** build full OAuth2 scope machinery — the caller's set of Profiles + the `require_profile` dependency is enough.

---

## Layer 2 — Contextual capability check (THE CORE)

**Decision question:** *"Can this SUBJECT perform this CAPABILITY on this RESOURCE?"*

Answered by explicit PDP branches **selected by the actor surface**; callers do not choose or re-derive them:
1. **Client ownership branch** *(client surface)*: client self-access for client-facing `VIEW_*` capabilities.
2. **Provider episode-membership branch** *(provider surface)*: active provider Profile + effective episode membership + role capability.
3. **Responsible-provider branch** *(provider surface)*: current clinically-responsible provider grants `MANAGE_TEAM` while the episode is active.
4. **Managing-org admin branch** *(org_staff surface)*: active org_staff Profile + active admin membership in the episode's `managing_org` grants the org_admin capability set.

The provider surface combines branches 2 and 3 (a member's role grant and any responsible-provider grant union *within* that surface); the client and org_staff surfaces each evaluate their single branch. No branch from another surface is consulted.

**Client self-access (resolved).** A logged-in **client** is never a care-team *member* (members are the 5 provider roles), so the three questions above would deny a client *all* access — including to their own data. Layer 2 therefore has an explicit **ownership short-circuit**: when the subject's Identity owns a **client `Profile`** and that **Identity's id `== resource.client_id`** (recall `client_id` is an `identities.id`, so this is the same identity-to-identity equality as the self-treatment check), the PDP grants the client-facing `VIEW_*` set over their **own** client-scoped data (`VIEW_BASIC_PROFILE`, `VIEW_SCHEDULE`). Whether a client may also read their **own provider-authored clinical / rehab notes** (`VIEW_CLINICAL` / `VIEW_REHAB_ASSESSMENT`) is a policy the brief is silent on — **defaulted to NOT granted** by self-access, and raised as an open question (`decision-log.md`). Clients never get act capabilities via self-access. (Booking *through* the face is a scheduling action, out of scope here — not a PDP read capability.)

### Capabilities — small FLAT named set (extend as needed)
**The model is role-on-episode → capability.** A member's `role` *on a specific episode* maps to a set of capabilities; the decision is "does this member's role-on-this-episode grant the demanded capability."

The **9 capabilities** are a **flat, un-scoped** vocabulary:
`VIEW_CLINICAL`, `WRITE_CLINICAL`, `VIEW_REHAB_ASSESSMENT`, `RUN_SESSION`,
`MESSAGE_CLIENT`, `BILL`, `VIEW_SCHEDULE`, `VIEW_BASIC_PROFILE`, `MANAGE_TEAM`

**Flat, not scoped — and that is the design.** A capability is a bare verb (`VIEW_CLINICAL`), **not** a scoped grant (`VIEW_CLINICAL@episode-123`). **Episode membership *is* the scope:** the PDP already constrains every check to the resource's own episode, so baking scope into the capability name would duplicate that boundary and reintroduce the N×M explosion the design avoids. There is therefore **no per-role sub-scope** (e.g. no "rehab scope" on a `WRITE_*`).
- *Rejected — scoped capabilities:* attaching an episode/resource scope to each capability (e.g. `WRITE_CLINICAL{rehab}`) makes the vocabulary multiply with the data and pushes relationship logic into permission strings. Keep capabilities flat and let **membership-in-the-episode** supply the scope.

See-vs-act split (a brief requirement): `VIEW_*` = *see*; `WRITE_*` / `RUN_*` / `MESSAGE_*` / `BILL` / `MANAGE_*` = *act*.

### Care-team role → capability grid (authoritative — the single home, lives in ONE place in code)
Capabilities are **flat** (no per-role sub-scope, see above); the **episode membership is the scope**.

| Capability | physician | physiotherapist | personal_trainer | massage_therapist | nutrition_coach | org_admin |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| VIEW_BASIC_PROFILE | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| VIEW_SCHEDULE | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| RUN_SESSION | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| MESSAGE_CLIENT | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| VIEW_REHAB_ASSESSMENT | ✓ | ✓ | — | — | — | — |
| VIEW_CLINICAL | ✓ | ✓ | — | — | — | — |
| WRITE_CLINICAL | ✓ | ✓ | — | — | — | — |
| BILL | ✓ | ✓ | — | — | — | ✓ |
| MANAGE_TEAM | — | — | — | — | — | ✓ |

> **`MANAGE_TEAM` is the tunable knob.** Who may add/remove members and reassign responsibility is the policy choice most likely to vary by deployment. **Baseline (resolved):** `MANAGE_TEAM` is held by **(a)** an `org_admin` affiliated to the episode's `managing_org`, **and (b)** the episode's **clinically-responsible provider**, regardless of that provider's role. (b) is required so an **independent / cross-org episode** (e.g. Khan Solo Practice's *Shoulder Rehab*, which has no org_staff admin in the take-home seed) still has a principal who can add members and hand off responsibility — without it, the brief's "Khan adds Patel" (S1) and the rehab→training step-back (S4) would be unmanageable. (b) is an episode-relationship grant, not a role→capability cell, so it stays out of the grid (the grid's `MANAGE_TEAM` column remains the org_admin path). **Both paths are subject to the closed-episode suppression** — `MANAGE_TEAM` is denied once `episode.status == closed`, so path (b) is live only while the episode is active. Isolated to one capability so retuning team-management policy is a small change.
> For `org_admin`, "within own org" is checked through the Organization context's effective-dated `org_staff_memberships(identity_id, org_id, role, effective_from, effective_to)`. **`org_admin` is not an episode `Membership.role`** (the five provider roles are the only membership roles) — it denotes an active `org_staff` profile whose Identity is an active admin of the episode's `managing_org`, and its single grid column is keyed on that org-staff membership, not on care-team membership.

### Resource sensitivity
Each resource type declares the capability it demands:
- `ClinicalRecord` → VIEW_CLINICAL / WRITE_CLINICAL
- `RehabAssessment` → VIEW_REHAB_ASSESSMENT
- `Schedule` → VIEW_SCHEDULE
- `BasicProfile` → VIEW_BASIC_PROFILE

**Maintainability:** add a resource → tag its required capability. Add a provider type → define its capability set once. **No N×M role×resource matrix.**

> **Scope of resources built (resolved).** Only the **episode-scoped** resources are built/seeded — `ClinicalRecord`, `RehabAssessment` — since those are what S5 exercises and the PDP resolves them cleanly (`resource.episode_id → episode.client_id`). `BasicProfile` and `Schedule` are **client-scoped** (no `episode_id`); their grid rows stay, but the tables are **stubbed**, and their resolution rule is **documented, not built**: a client-scoped resource resolves via its `client_id`, and a provider satisfies it by being a **current member of *any* of that client's active episodes** (a client reaching their own uses the self-access short-circuit instead). Building that second PDP path is in *Cut & Next*. (Consequently the `org_admin` `VIEW_BASIC_PROFILE` / `VIEW_SCHEDULE` grid cells resolve only via this documented-not-built path, so they are not exercised by any seeded resource — expected, not a dead row.)

---

## The PDP (single decision point)

- **Module:** `authz` (own bounded context / shared kernel).
- **Actor context:** `ActorContext(identity_id, profile_type)` — the subject identity plus the **surface** it acts under (`client` / `provider` / `org_staff`), fixed by Layer 1's `require_profile` on the route. The PDP evaluates only the branches for that surface.
- **Interface (resolver + thin gate):**
  - `allowed_capabilities(actor, resource_ref) -> frozenset[Capability]` — resolves the final set the actor may exercise on the resource: its surface's branch(es), temporal membership, relationship grants, and the closed-episode overlay. **The single place policy is computed**; `now` is threaded in for the temporal checks.
  - `can(actor, capability, resource_ref) -> bool`  ≡  `capability in allowed_capabilities(...)`
  - `require(actor, capability, resource_ref) -> None`  ≡  raises `Forbidden` unless `can(...)`
- **Inputs it pulls:** the actor (identity + surface); the resource's owning client + demanded capability; care-team membership (current + effective) from the `Episode`; responsible-provider assignment; **active-provider / managing-org-admin state via a port**; **the episode's `status`**.
- **Closed-episode rule:** `allowed_capabilities` resolves the actor surface's branch(es) first; if the resource's episode is `closed`, it then drops every act capability (`WRITE_*/RUN_*/MESSAGE_*/BILL/MANAGE_TEAM`), leaving only allowed `VIEW_*` regardless of role — mirroring `care-team-design.md` invariant 3 (read is not mutation, so immutability holds). Members are **not** end-dated on close, so they keep role-limited view history.
- **Every caller uses it** — router dependency (the PEP), service methods, even seed/verification scripts. **No surface re-derives policy.**

> Dependency note: Layer 2 reads **care-team membership**. **Resolved** — the aggregate is the **`Episode`** ([`care-team-design.md`](care-team-design.md)): membership = `{provider_id, role, effective_from, effective_to}` per episode, and every resource carries its `episode_id` → `episode.client_id`. The PDP **interface is unchanged**; the membership lookup now binds to the Episode aggregate.

### Domain exception → HTTP status (one central handler, per CLAUDE.md std 5)
| Exception | HTTP | Raised when |
|---|---|---|
| `NotAuthenticated` | 401 | missing / invalid / expired token |
| `Forbidden` | 403 | PDP `require(...)` denies (no membership, capability, or not effective) |
| `NotFound` | 404 | resource / episode id does not exist |
| `EpisodeClosed` | 409 | a mutation is attempted on a closed episode |
| `OverlappingPeriod` | 409 | a responsibility / face handoff would overlap (DB `EXCLUDE` surfaced) |
| `NotACurrentMember` | 422 | `assign_responsible` / face-set names a non-member or not-currently-effective provider |
| `SelfTreatment` | 422 | `add_member` / `assign_responsible` names the client's own Identity |

---

## Temporal validity

- Membership carries an **effective period** (`effective_from`, `effective_to` nullable).
- **Coverage** grants are effective-dated and **expire automatically** — "access while covering, not after."
- The PDP checks "effective at `now()`" with the **half-open** predicate `effective_from <= now() AND (effective_to IS NULL OR now() < effective_to)` — matching the `tstzrange(from, to)` `[from, to)` exclusion constraint, so the app-level read and the DB constraint agree exactly at the handoff instant. Past memberships remain in **history** (auditable) but grant nothing.

---

## Library decision — hand-roll the PDP

Rejected alternatives:
- **Casbin / Oso** — embed a policy DSL/CSV; our decision is a *query against our own care-team aggregate* (membership + role + temporal), not a static policy file — a DSL would hide the domain reasoning this exercise grades.
- **Cerbos / OPA** — external *network* PDP; adds infra; overkill here.
- **OpenFGA / SpiceDB** — Zanzibar-style ReBAC graph; where I'd go if the relationship graph gets deep at real scale.

---

## Maps to brief requirements

| Brief requirement | Satisfied by |
|---|---|
| Access flows from identity; trust no client claim | Server-side PDP from verified token |
| See vs. act are distinct | `VIEW_*` vs act capabilities |
| No unmaintainable permissions matrix | Capabilities + contextual check |
| One access decision; no surface re-derives policy | Single `authz` PDP module |
| Coverage has access while covering, not after | Effective-dated membership + temporal check |
| Cross-org team | Access via membership link, not org ownership |
| Multi-role person | One `Identity` holding multiple typed `Profile`s; each request acts on one **surface** (actor context), and the PDP evaluates that surface's branches |
