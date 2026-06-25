# Care-Team Domain — Design Spec

**Project:** Kinetic (Oasys)
**Stack:** FastAPI · Pydantic · PostgreSQL · SQLAlchemy 2.0 (sync)
**Owner:** Haseeb Asad
**Status:** Locked — `Episode of Care` is the aggregate root. Closes the membership seam the PDP depends on.
**Captured:** 2026-06-19
**Companion:** [`auth-authz-design.md`](auth-authz-design.md) (auth & the PDP this design feeds)

> **Cross-refs (single home per fact, no duplication):** capabilities, the role→capability map, and the PDP live in `auth-authz-design.md`. This doc owns the **domain** — the `Episode` aggregate, membership, responsibility, the booking face, lifecycle, and bounded contexts. `decision-log.md` Part B is the index entry, and the **master open-questions list** lives there.

---

## Decision

**The care team is of an *Episode of Care* — a bounded course of care for one reason. `Episode` is the aggregate root.**

Why episode and not the alternatives:
- **Not the whole client** (one CareTeam per client): too coarse to say who is responsible *for the rehab specifically* when a client has several concurrent reasons for care; you end up smuggling "episode" back in as a text field.
- **Not a single org-owned case:** the brief's cross-org scenario kills it — *"the client belongs to a gym but also sees an independent physiotherapist who is not the gym's staff... the team spans organizational boundaries."* Org-ownership belongs on the *episode* (per-episode managing org), not the team.
- **Episode** localizes every hard scenario (responsibility per condition, coverage, lifecycle, cross-org, episode-scoped access) to one clean aggregate.

Hybrid (a separate `CareTeam` aggregate + `Episode`) is the documented "where I'd take it next" — see *Cut & Next*.

---

## Running example (used throughout)

**Sara** trains with **Mike** at **FitGym**, rehabs her shoulder with **Dr. Khan** (independent physio with a solo-practice Organization), and sees **Dr. Patel** (sports physician) for the shoulder.
→ Two episodes under one client: **"General Training"** (FitGym, member Mike) and **"Shoulder Rehab"** (managing org = Khan Solo Practice, members Khan + Patel).

---

## The four positions the brief asks us to take

### Q1 — What is the team of? What is the aggregate, what invariants?
The team is of an **Episode of Care**. **Aggregate root = `Episode`.** Invariants it protects:
1. An active episode has **exactly one** clinically-responsible provider at any instant.
2. The responsible provider is a **current member** (membership effective now).
3. A **closed** episode is immutable — no new members, no reassignment; PDP honors only `VIEW_*` for still-effective members.
4. Membership & responsibility are **effective-dated and append-only, never overwritten** (brief: *"information the business keeps, not overwrites"*).
5. **No self-treatment.** A provider may not be added to an episode whose `client_id` is that provider's own Identity — a person cannot be on their own care team. Enforced on `add_member` (and `assign_responsible`), so it holds for every mutation. The Identity/Profile model that makes "same person" detectable lives in [`auth-authz-design.md`](auth-authz-design.md); the invariant is enforced **here**, at the membership boundary. *(Alternative: allow-and-log; defaulted to barred.)* **Identity model (resolved):** both `episode_memberships.provider_id` and `episodes.client_id` reference **`identities.id`** (not a Profile, not a `providers` table); the service resolves the acting provider and the client to their `identity_id` and passes both in, so the pure aggregate checks self-treatment by simple **`provider_identity_id == client_identity_id`** equality — no cross-context lookup inside the aggregate. A member's provider *type* = `Membership.role`; credentials/specialty would live on a typed provider profile (deferred to Next — not stored in v1).

### Q2 — Roles, the reason someone is on a team, see vs. do — without a permissions matrix
- **"Reason someone is on the team"** = the episode's `reason` + the member's `role` + their `effective_from/to` + membership `change_reason`.
- **What each may see/do** = **capabilities** (full detail in `auth-authz-design.md`): role → capability set; resource → required capability; decision = does this member's role-on-this-episode grant it.
- **See vs. act** = `VIEW_*` vs `WRITE_* / RUN_* / MESSAGE_* / BILL / MANAGE_TEAM` (matches the canonical split in `auth-authz-design.md`).
- No N×M role×resource grid: add a provider type → define its capability set once; add a resource → tag its required capability.

### Q3 — Where is the access decision made?
The single **PDP** (`can(subject, capability, resource)` / `require(...)`) from `auth-authz-design.md`. It derives every decision from the documented server-side branches: client ownership, episode membership + role + time, responsible-provider authority, or managing-org staff authority. Every surface (router dependency, service, seed script) calls it; nothing re-derives policy.

### Q4 — Migration off one-provider-per-client (bonus, kept short)
Expand/contract, no big-bang:
1. Introduce `Episode` + membership tables alongside the existing one-provider-per-client link.
2. **Backfill:** one single-member Episode per existing pairing — that provider is the episode's sole member, responsible, **and** booking face. Legacy rows carry no clinical reason, so the backfilled episode gets a reserved free-text `reason = "general_care"`. Because the **face is per-episode**, each backfilled episode just writes one `booking_contacts` row for its sole provider — uniform, no special-casing.
3. Route all reads through the PDP, which already handles single-member (old-shaped) and multi-member (new) episodes identically.
4. Once traffic is on the new path, retire the legacy link column.

---

## The Episode aggregate (object shape)

All changes go through the root's methods, so it enforces its rules on every change.

```
Episode  (aggregate root)
├─ id
├─ client_id           → whose episode (Sara)
├─ reason              → "shoulder_rehab"
├─ status              → active | closed
├─ managing_org_id     → FitGym, or Khan Solo Practice (can differ per episode)
├─ opened_at / closed_at
├─ members[]           → Membership(provider_id, role, effective_from, effective_to, change_reason)
├─ responsibility[]    → Responsibility(provider_id, effective_from, effective_to, change_reason)
└─ faces[]             → BookingContact(provider_id, effective_from, effective_to, change_reason)

methods (the ONLY way to mutate it):
  add_member(provider, role, from, change_reason)
  assign_responsible(provider, change_reason)   # rejects if provider is not a current member
  set_face(provider, change_reason, at=now())   # the booking face; rejects non-member; contiguous handoff
  start_coverage(covering_provider, role, from, to, change_reason)   # bounded add_member alias (names the intent); "covering for X" → change_reason
  end_member(provider, effective_to, change_reason)   # if provider is the current face & episode active: name a successor face or reject
  close()                                # rejects if already closed
```

**Value objects / entities inside the aggregate:**
- `Membership` (entity) — provider + role + effective period + change_reason.
- `Responsibility` (entity) — provider + effective period + change_reason.
- `BookingContact` (entity) — the **face**: provider + effective period + change_reason; per-episode, identical shape to `Responsibility`.
- `EffectivePeriod` (value object) — `(from, to|None)`, with overlap logic.
- `Role` — value object (a controlled vocabulary; its 5 values are the single home consumed by the role→capability grid).
- `Reason` — a **free-text** label on the episode (e.g. `"shoulder_rehab"`); part of the ubiquitous language but **not** a closed vocabulary — nothing keys on its value, so it is a plain string, not an enum.

---

## The core design move — derive "current" from effective-dated rows

**Do not store mutable "current value" columns.** "Current responsible provider" is *the responsibility row effective at `now()`*, not a column you overwrite. Reassigning = close the old row (`effective_to = now`) + open a new one.

This makes the invariants trivial and history free:
- Invariant 1 ("exactly one responsible at any instant") = **no two responsibility rows for an episode overlap in time.**
- Enforced in both places: aggregate methods plus a Postgres `EXCLUDE USING gist` (`btree_gist` required) — on **both** `responsibility_assignments` and `booking_contacts`, the identical shape `(episode_id WITH =, tstzrange(effective_from, effective_to) WITH &&)`. (Both partition by `episode_id` now that the face is per-episode.)
- History (every past holder + why it changed) is just the older rows — nothing is overwritten.
- Auto-expiry (coverage) = a row whose `effective_to` has passed simply stops being "effective now."
- **Gap-free for the one-at-a-time roles.** While an episode is active, responsibility holds for **exactly one** provider *at every instant* (brief: "unambiguous at every instant"); likewise the face. A reassignment is therefore a **contiguous, same-transaction** close-old + open-new (`new.effective_from = old.effective_to = now()`) — never a gap, never an overlap — applied at `now()` (responsibility and the face are never back- or future-dated). Episodes open **with** an initial responsible provider and an initial face, so the invariant holds from t0. Plain membership rows — **including coverage** — may be future-dated and may have gaps; only responsibility and the face are gap-free.

### Tables

```
episodes
  id · client_id · reason · status · managing_org_id · opened_at · closed_at

episode_memberships            ← append-only
  id · episode_id · provider_id · role · effective_from · effective_to (NULL = ongoing) · change_reason

responsibility_assignments     ← append-only
  id · episode_id · provider_id · effective_from · effective_to · change_reason

booking_contacts               ← the "face", SAME pattern, episode-scoped
  id · episode_id · provider_id · effective_from · effective_to · change_reason

clinical_records               ← stub resource (the access-control target), episode-scoped
  id · episode_id · author_provider_id · body · created_at
rehab_assessments              ← stub resource, episode-scoped
  id · episode_id · author_provider_id · body · created_at
```

---

## The "face" (booking contact) — why it's cheap

The brief: *"one provider is the 'face' the client books through. That is not necessarily the provider clinically responsible for the rehab. Either can change over time, and the hand-off must be clean and unambiguous at every instant."*

Three requirements only: **may differ from** the clinically-responsible provider (they may coincide or diverge) · **unambiguous at every instant** with clean handoffs · **history** of changes. No richer behavior is asked for, so the face is **owned by the `Episode` aggregate** (same as responsibility) and mutated by the root method `set_face(provider, change_reason, at=now())` (contiguous close-old/open-new). **Bootstrap ordering is explicit:** open the episode (`status=active`) → `add_member`(initial provider) → `assign_responsible` → `set_face` referencing the now-existing membership — sequenced steps in one transaction.

It is the **identical shape *and* scope** as clinical responsibility — "exactly one provider holds this role at any instant on this episode, changes timestamped, history kept." So once the effective-dated one-at-a-time pattern exists for responsibility (a required core invariant), the face is nearly free: same pattern, same `episode_id` partition, `booking_contacts` table.

**Why per-episode (not client-level).** Kinetic is a **cross-org, multi-provider** world: a client trains at a gym *and* rehabs with an independent physio in a different org. A single client-level face would force one provider to front bookings across org boundaries (e.g. a FitGym trainer fronting an independent clinic's rehab) — infeasible and coupling. Scoping the face **per-episode** matches reality (each episode/org is booked through its own contact), keeps the face **inside one aggregate** (no cross-aggregate invariant), and makes `close()` clean: the face dies with its episode as history, nothing to reconcile.

**Face rules (all local to the one Episode now):**
- (1) **No overlap _and_ no gap while the episode is active** — exactly one face at every such instant, via the DB `EXCLUDE` (no overlap) + the contiguous same-transaction handoff (no gap). On `close()` the face simply **stops being required** — it persists as history; `close()` need not touch it.
- (2) **Set-time membership** — the chosen face must be a **current member of this episode** (brief: one provider "among that team") — a trivial local check now that the face lives in the aggregate. If `end_member` would end the current face's membership while the episode is still active, the same method must `set_face(...)` to another current member or reject.

> **Open question to raise:** the brief says "the face the client books through" (client-level wording) but, in the multi-episode + cross-org world we surfaced, this models it **per-episode**. Confirm Kinetic is a multi-org marketplace (per-episode face) and not a single-coordinator **concierge** product (which would want a client-level face).

---

## How an access decision runs (worked, ties to the PDP)

`can(subject, capability, resource)` — the resource carries its `episode_id` and the capability it demands.

- **Patel views Sara's rehab assessment** → resource's episode = Shoulder Rehab, demands `VIEW_REHAB_ASSESSMENT`. Patel is a current member (physician) → physician grants it → **allow.**
- **Mike (trainer) views the same** → Mike is a member of General Training, not Shoulder Rehab → no membership in the record's episode → **deny.** The episode boundary *is* the access boundary; no special-casing.
- **A massage therapist who *is* a member of Shoulder Rehab opens the clinical record** → the episode-boundary check passes (they're in the episode), but `massage_therapist` does **not** grant `VIEW_CLINICAL` → **deny** — while that same member *is* allowed `RUN_SESSION`. This is the *see-vs-act / not-all-or-nothing* split (brief S5) operating **within** a team — a role-capability deny, distinct from the episode-boundary deny above.
- **Coverage — Dr. Lee, weeks 8–10** → her membership row is `effective_from=wk8, effective_to=wk10`. PDP checks "effective at `now()`": week 9 → allow; week 11 → row no longer effective → **deny automatically.** No cleanup job. (Brief: *"has what they need while covering, and not after."*)

---

## Lifecycle walkthrough (Sara, by week)

- **Wk 0 — starts training** → Episode *General Training* (org FitGym, responsible Mike, members [Mike: trainer]). General Training's face = Mike.
- **Wk 3 — shoulder injury, referred to Khan** → second Episode *Shoulder Rehab* (managing org Khan Solo Practice, responsible Khan, members [Khan: physio]). Separate lifecycle, different org, same client.
- **Wk 4 — Khan adds Patel** → Patel added to *Shoulder Rehab* only. Not on General Training.
- **Wk 8 — Khan on leave, Lee covers** → Lee added to *Shoulder Rehab* with explicit role `physiotherapist`, effective **[wk8, wk10)** (half-open). Access is live wk8–9 and gone from the wk10 instant onward, automatically. Khan stays clinically responsible throughout — coverage is membership only; the cover does not become responsible.
- **Wk 16 — shoulder healed** → `shoulder_rehab.close()`: status closed; memberships are not end-dated, so former members keep view-only access to that episode's history per their existing `VIEW_*` tier.
- **Resolved — lighter role:** on closed episodes, the PDP suppresses all act capabilities (`WRITE_*/RUN_*/MESSAGE_*/BILL/MANAGE_TEAM`) regardless of role; read is not mutation, so immutability still holds. (Reassigning responsibility is gated by `MANAGE_TEAM`, so it is suppressed too.)
- **Coverage distinction:** covers are created with `effective_to`, so after handback membership is no longer effective and grants nothing; the hard end date is the difference between expired cover and stepped-back provider.

---

## Bounded contexts & ubiquitous language (DDD made visible)

- **Identity & Access** context — `Identity` (a person; holds one or more typed **Profiles**, one per participation: client/provider/org_staff), authentication, the PDP. Owns words: *Identity, Profile, Capability*.
- **Care Coordination** context (this file) — owns words: *Episode, Membership, Role (values: physician, physiotherapist, personal_trainer, massage_therapist, nutrition_coach), Reason, ClinicallyResponsible, BookingContact (Face), Coverage*. The `Role` vocabulary is owned here and **consumed** by the role→capability grid in `auth-authz-design.md` (the grid's single home — not restated here).
- **Organization** context — `Organization`, staff, the per-episode managing org. Owns words: *Organization, OrgStaff, ManagingOrg, OrgStaffMembership*.
  Minimal entities: `organizations(id, type)` where type includes `gym`, `clinic`, `solo_practice`; `org_staff_memberships(identity_id, org_id, role, effective_from, effective_to)` — the concrete source of `org_admin` authority. **No provider→org employment table in v1** — cross-org provider access flows through episode membership. A solo independent provider is a one-person `solo_practice` Organization, so `managing_org_id` stays a single FK.
- Out-of-world-only (stub or omit, used only to justify boundaries): Scheduling, Billing, Documents, Wearables.

Integration across contexts is by **ID reference + the PDP**, not shared tables — kept decoupled deliberately.

---

## Rejected alternatives

- **Client-centric (one CareTeam):** simplest, but clinical responsibility per condition and episode lifecycle get clumsy (episode smuggled in as a field).
- **Org-owned case:** breaks the cross-org scenario; org-ownership belongs on the episode, not the team.
- **Hybrid (CareTeam + Episode):** richest; gives the face + longitudinal roster their own home — but adds a **cross-aggregate invariant** ("must be on the CareTeam to be put on an Episode") that can't be enforced in one transaction. Deferred (see below).

---

## Cut & Next

**Cut for v1:** scheduling, billing, documents, wearables (stubbed/omitted); refresh-token rotation, email invites (stubbed as token records); a separate `CareTeam` aggregate.

**Where more time goes (the hybrid trajectory):** promote `CareTeam` to a real aggregate when the enduring client–provider relationship and the face carry their *own* data/rules (standing billing relationships across episodes, a "primary provider" with obligations, roster-level consent). Reconciliation strategy for the cross-aggregate invariant would then be stated explicitly (app-service check, or CareTeam as a computed projection over episodes + the face).

---

## Open questions surfaced here

Working assumptions are stated inline in the relevant sections above (face scope, managing org). The consolidated **master running list for Oasys (deliverable #3)** lives in [`decision-log.md`](decision-log.md) — this design contributed: **Q2** booking-face scope (client-level vs per-episode), **Q3** one vs. two managing orgs per episode.
