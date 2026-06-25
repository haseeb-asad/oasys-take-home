# Kinetic — Data Model (v1 implementation guide)

Concise ERD for what we actually build. **Not tables:** the 9 capabilities + the
role→capability grid live in `app/authz/` code; `BasicProfile`/`Schedule` are
stubbed client-scoped resources. **11 tables.**

```mermaid
erDiagram
  IDENTITIES ||--o{ PROFILES : "owns"
  IDENTITIES ||--o{ EPISODES : "client_id"
  IDENTITIES ||--o{ EPISODE_MEMBERSHIPS : "provider_id"
  IDENTITIES ||--o{ RESPONSIBILITY_ASSIGNMENTS : "provider_id"
  IDENTITIES ||--o{ BOOKING_CONTACTS : "provider_id (face)"
  IDENTITIES ||--o{ ORG_STAFF_MEMBERSHIPS : "identity_id"
  IDENTITIES ||--o{ CLINICAL_RECORDS : "author"
  IDENTITIES ||--o{ REHAB_ASSESSMENTS : "author"
  IDENTITIES ||--o{ INVITATIONS : "accepted_identity"
  ORGANIZATIONS ||--o{ EPISODES : "managing_org"
  ORGANIZATIONS ||--o{ ORG_STAFF_MEMBERSHIPS : "org_id"
  ORGANIZATIONS ||--o{ INVITATIONS : "from_org"
  EPISODES ||--o{ EPISODE_MEMBERSHIPS : "has"
  EPISODES ||--o{ RESPONSIBILITY_ASSIGNMENTS : "has"
  EPISODES ||--o{ BOOKING_CONTACTS : "has"
  EPISODES ||--o{ CLINICAL_RECORDS : "contains"
  EPISODES ||--o{ REHAB_ASSESSMENTS : "contains"

  IDENTITIES {
    uuid id PK
    citext email UK
    text display_name
    text password_hash
    timestamptz created_at
  }
  PROFILES {
    uuid id PK
    uuid identity_id FK
    text profile_type "client | provider | org_staff"
    timestamptz discarded_at "nullable (soft discard)"
  }
  ORGANIZATIONS {
    uuid id PK
    text name
    text type "gym | clinic | solo_practice"
    timestamptz created_at
  }
  ORG_STAFF_MEMBERSHIPS {
    uuid id PK
    uuid identity_id FK
    uuid org_id FK
    text role
    timestamptz effective_from
    timestamptz effective_to "nullable"
  }
  EPISODES {
    uuid id PK
    uuid client_id FK
    text reason "free-text"
    text status "active | closed"
    uuid managing_org_id FK
    timestamptz opened_at
    timestamptz closed_at "nullable"
  }
  EPISODE_MEMBERSHIPS {
    uuid id PK
    uuid episode_id FK
    uuid provider_id FK
    text role "5 provider roles"
    timestamptz effective_from
    timestamptz effective_to "nullable"
    text change_reason
  }
  RESPONSIBILITY_ASSIGNMENTS {
    uuid id PK
    uuid episode_id FK
    uuid provider_id FK
    timestamptz effective_from
    timestamptz effective_to "nullable"
    text change_reason
  }
  BOOKING_CONTACTS {
    uuid id PK
    uuid episode_id FK
    uuid provider_id FK
    timestamptz effective_from
    timestamptz effective_to "nullable"
    text change_reason
  }
  CLINICAL_RECORDS {
    uuid id PK
    uuid episode_id FK
    uuid author_provider_id FK
    text body
    timestamptz created_at
  }
  REHAB_ASSESSMENTS {
    uuid id PK
    uuid episode_id FK
    uuid author_provider_id FK
    text body
    timestamptz created_at
  }
  INVITATIONS {
    uuid id PK
    text email
    text intended_role
    uuid org_id FK "nullable"
    text token
    text status "pending | accepted | expired"
    uuid accepted_identity_id FK "nullable"
    timestamptz created_at
  }
```

All `*_id` columns that point at a person reference `IDENTITIES.id` (not `PROFILES`);
`provider_id` carries its role via `EPISODE_MEMBERSHIPS.role`. The four effective-dated,
append-only tables (`EPISODE_MEMBERSHIPS`, `RESPONSIBILITY_ASSIGNMENTS`,
`BOOKING_CONTACTS`, `ORG_STAFF_MEMBERSHIPS`) never overwrite — "current" = the row
effective at `now()`. `RESPONSIBILITY_ASSIGNMENTS` and `BOOKING_CONTACTS` (both per episode) carry a Postgres
`EXCLUDE … gist` no-overlap constraint (exactly one holder at any instant).

## Notes

- **Episode membership is the cross-org access boundary.** A provider can see/act on a
  client's episode only by being a current member of that episode — regardless of which
  org they belong to. There is no provider→org employment table in v1.
- **Managing org grants management only through `ORG_STAFF_MEMBERSHIPS`.** An episode's
  `managing_org_id` does not, by itself, give anyone access. `org_admin` capabilities
  (e.g. `MANAGE_TEAM`) come from holding an active `org_staff` membership (admin role) in
  that managing org.
- **Invitations are intentionally stubbed.** The brief asks us to decide how much
  onboarding to build, so `INVITATIONS` only covers token + status + creating the accepted
  `Identity`. It does **not** auto-create episode membership — that remains an explicit
  `Episode.add_member` step.
