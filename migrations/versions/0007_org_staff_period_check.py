"""enforce the org-staff-membership effective period at the database

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-27

``org_staff_memberships`` is the org-admin AUTHORITY source: the authz layer
reconstitutes admin authority from these effective-dated rows, so a zero-length or
inverted window (``effective_from >= effective_to``) the DB accepts would later make
an authz read fail during reconstitution. Migration 0003 created the table with only
the ``role`` CHECK; this adds the matching ``period`` CHECK so the database and the
``OrgStaffMembership`` domain value object agree (the same rule the care child tables
already enforce in 0005).

The half-open ``[effective_from, effective_to)`` window mirrors ``EffectivePeriod``'s
positive-length rule: an open window (``effective_to IS NULL``) is allowed, otherwise
``effective_from < effective_to`` (strictly positive).

Added via raw ``op.execute`` with the FULL literal constraint name (as 0005 did for
its EXCLUDE constraints), so the landed name is deterministic: it must be exactly
``ck_org_staff_memberships_period`` to match the
``ck_%(table_name)s_%(constraint_name)s`` naming convention and the model.
"""

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: None = None
depends_on: None = None

_CONSTRAINT = "ck_org_staff_memberships_period"
_PERIOD_CHECK = "effective_to IS NULL OR effective_from < effective_to"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE org_staff_memberships ADD CONSTRAINT {_CONSTRAINT} CHECK ({_PERIOD_CHECK})"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE org_staff_memberships DROP CONSTRAINT {_CONSTRAINT}")
