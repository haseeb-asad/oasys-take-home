# Care-Team Scenario Answers

This file is the lightweight implementation anchor for the project. It answers
the PDF prompts directly and intentionally avoids production-grade edge policy.

## Core Position

The care team is the team of an **Episode of Care**.

An episode is a bounded course of care for one client and one reason, such as
`general_training` or `shoulder_rehab`. The aggregate root is `Episode`.

The aggregate protects these invariants:

- An active episode has exactly one clinically responsible provider at a time.
- Episode members are effective-dated, so current access is derived from time.
- Responsibility changes are effective-dated and leave history behind.
- A closed episode cannot be mutated.
- History is append-only: who was involved, in what role, when, and why is kept.

This is lighter than modeling one giant client team and more flexible than an
organization-owned case. It handles concurrent reasons for care and cross-org
collaboration without making the organization the access boundary.

## Scenario Answers

### 1. Client has trainer, physiotherapist, and physician at once

Model this as multiple active episodes for the same client.

Example:

- Sara has a `General Training` episode managed by FitGym.
- Mike is a member of that episode as `personal_trainer`.
- Sara also has a `Shoulder Rehab` episode managed by Khan Solo Practice.
- Dr. Khan is a member of that episode as `physiotherapist`.
- Dr. Patel is added to `Shoulder Rehab` as `physician`.

This lets the same client be served by multiple providers at once without making
every provider part of every care relationship.

### 2. Booking face differs from clinically responsible provider

Use two separate effective-dated concepts, both **episode-scoped** and owned by the
`Episode` aggregate:

- `responsibility_assignments`: who is clinically responsible for one episode.
- `booking_contacts`: the provider face the client books through for that episode.

They can be the same provider, but do not have to be. A handoff is a same-transaction
close-old/open-new update using effective timestamps, so there is no ambiguous
moment with two current values or no current value while the episode is active.

### 3. Temporary coverage

Coverage is just a temporary episode membership.

Example:

- Dr. Khan is away for two weeks.
- Dr. Lee is added to the `Shoulder Rehab` episode as `physiotherapist`.
- Lee's membership has `effective_from` and `effective_to`.
- During that window, the PDP treats Lee as a current member and grants the role's
  capabilities.
- After `effective_to`, Lee is no longer current, so access stops automatically.

No cleanup job and no special revocation flow are needed.

### 4. Rehab finishes and provider steps back

When shoulder rehab finishes, close the `Shoulder Rehab` episode.

Closing the episode does not delete memberships or responsibility history. Khan,
Patel, and Lee remain visible in history with their roles and effective periods.

The PDP suppresses act capabilities on closed episodes, but can still allow
role-limited reads of historical records. That makes the physiotherapist "step
back" without disappearing from the business record.

General training continues in its own episode, so Mike and FitGym can keep working
with Sara without being affected by rehab closure.

### 5. Access is not all-or-nothing

Use flat capabilities and map each episode role to capabilities once.

Example capabilities:

- `VIEW_BASIC_PROFILE`
- `VIEW_SCHEDULE`
- `VIEW_REHAB_ASSESSMENT`
- `VIEW_CLINICAL`
- `WRITE_CLINICAL`
- `RUN_SESSION`
- `MESSAGE_CLIENT`
- `BILL`
- `MANAGE_TEAM`

Each resource declares the capability it requires. Each episode role grants a
small capability set.

Example:

- Physician can view clinical records and rehab assessments.
- Physiotherapist can view/write rehab-relevant clinical material.
- Personal trainer can run sessions and message the client, but not view clinical
  records for the shoulder rehab episode.
- Massage therapist can run sessions if they are on an episode, but should not
  see medical history or rehab assessments by default.

The important point is that access depends on both:

- whether the provider is a current member of the resource's episode
- whether the provider's role grants the resource's required capability

### 6. Cross-organization team

Organizations do not own the whole client relationship. An episode has a
`managing_org_id`, but episode membership can include providers across org
boundaries.

Example:

- Sara belongs to FitGym for general training.
- Khan is an independent physiotherapist, represented by a simple solo-practice
  organization.
- Sara's `Shoulder Rehab` episode is managed by Khan Solo Practice.
- Mike at FitGym does not automatically access `Shoulder Rehab`.
- FitGym admins do not automatically administer Khan's episode.
- Patel can be added to Khan's episode even if he is not FitGym staff.

This respects cross-org collaboration while keeping access scoped to the episode.

### 7. History matters

Use effective-dated append-only rows for:

- episode membership
- clinical responsibility
- booking face

Rows include `effective_from`, optional `effective_to`, and `change_reason`.

Current state is derived from "which row is effective now." Past state remains in
the database. Reassignment and handoff close the old row and open a new row
instead of overwriting the old value.

## Required Positions

### What is the team of?

The team is of an `Episode of Care`.

Rejected alternatives:

- Whole-client team: too broad when the same client has unrelated concurrent
  care reasons.
- Organization-owned case: breaks cross-org care because the team can span
  multiple organizations.

### What is the aggregate?

`Episode` is the aggregate root.

It owns:

- reason for care
- status
- managing org
- episode memberships
- responsibility assignments
- booking contacts (the face)

The booking face is **episode-scoped** and uses the same effective-dated handoff
pattern as responsibility.

### How are roles, reason, and permissions represented?

- Episode `reason`: free text, such as `shoulder_rehab`.
- Member `role`: controlled vocabulary, such as `physician`,
  `physiotherapist`, `personal_trainer`, `massage_therapist`, or
  `nutrition_coach`.
- Member `change_reason`: free text explaining why the membership changed.
- Permissions: flat capability names, mapped from role to capability set.

This avoids a large role-by-resource-by-client matrix. The episode membership is
the scope, and the capability is the action.

### Where is access decided?

One PDP decides access for every surface.

The PDP checks:

- authenticated identity
- active profile type
- resource's episode
- current episode membership
- member role capability
- clinically responsible provider grant for team management
- managing-org admin grant for team management
- episode status

Routers and services call the PDP. They do not re-derive policy.

### Bonus migration path

Use expand/contract:

1. Add `episodes`, `episode_memberships`, `responsibility_assignments`, and
   `booking_contacts` beside the legacy one-provider-per-client link.
2. Backfill one default episode per existing client-provider relationship.
3. Make the legacy provider the initial member, responsible provider, and booking face.
4. Route reads and access checks through the new PDP.
5. Once behavior is stable, stop reading the old link and remove it later.

This avoids a big-bang migration and lets old and new shapes coexist during the
transition.

## Implementation North Star

Build only what demonstrates the model:

- seed Sara, Mike, Khan, Patel, Lee, FitGym (with an org_admin), and Khan Solo Practice (deliberately staffless)
- seed `General Training` and `Shoulder Rehab`
- prove access through tests or a thin API
- prove coverage expires by time
- prove closed rehab keeps history but blocks acts
- prove FitGym access does not leak into Khan's independent rehab episode
- prove both `MANAGE_TEAM` paths: FitGym org_admin manages General Training (path-a); Khan manages Shoulder Rehab as responsible provider with no org_admin present (path-b)

Do not build a full scheduling system, billing system, document system, org
governance engine, or policy DSL for the first implementation.
