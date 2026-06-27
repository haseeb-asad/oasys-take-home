"""SQLAlchemy adapters implementing the organization repository ports.

Infrastructure layer: maps the organization tables to/from the pure domain
entities via the mappers in ``app/organization/orm.py``. Each ``add`` does a
plain ``session.add(...) ; session.flush()`` (no SAVEPOINT, no typed-error
translation): an Organization has no unique business key, and a membership has no
duplicate key, so a CHECK or foreign-key violation surfaces as a raw
``IntegrityError`` (the caller / test treats it as terminal, and the per-request
rollback recovers). The ``flush`` forces the INSERT to hit the database inside
``add`` so those violations raise here, even under the harness's
``autoflush=False`` session.

``list_for`` returns ALL rows for the ``(identity_id, org_id)`` pair ordered by
``effective_from`` with NO role/time filter: the activeness decision is the
domain's (``OrgStaffMembership`` + the service), not the SQL's.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.organization.domain.entities import Organization, OrgStaffMembership
from app.organization.orm import (
    OrganizationModel,
    OrgStaffMembershipModel,
    _membership_to_domain,
    _membership_to_model,
    _org_to_domain,
    _org_to_model,
)


class SqlAlchemyOrganizationRepository:
    """Reads and stores Organization records against the ``organizations`` table."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, org_id: UUID) -> Organization | None:
        model = self._session.get(OrganizationModel, org_id)
        return _org_to_domain(model) if model is not None else None

    def add(self, organization: Organization) -> None:
        self._session.add(_org_to_model(organization))
        self._session.flush()


class SqlAlchemyOrgStaffMembershipRepository:
    """Reads and stores org-staff membership rows (append-only)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for(self, identity_id: UUID, org_id: UUID) -> list[OrgStaffMembership]:
        models = self._session.scalars(
            select(OrgStaffMembershipModel)
            .where(
                OrgStaffMembershipModel.identity_id == identity_id,
                OrgStaffMembershipModel.org_id == org_id,
            )
            .order_by(OrgStaffMembershipModel.effective_from)
        ).all()
        return [_membership_to_domain(model) for model in models]

    def add(self, membership: OrgStaffMembership) -> None:
        self._session.add(_membership_to_model(membership))
        self._session.flush()
