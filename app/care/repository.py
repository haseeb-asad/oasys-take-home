"""SQLAlchemy adapter implementing the ``EpisodeRepository`` port.

Infrastructure layer: maps the care tables to/from the pure ``Episode`` aggregate
via the mappers in ``app/care/orm.py``. ``get`` loads the root plus its three
child collections (ordered by ``effective_from``) and reconstitutes the aggregate
without re-running invariants. ``save`` upserts the whole aggregate.

THE TWO-PHASE FLUSH (load-bearing). The per-episode ``EXCLUDE USING gist`` on
``responsibility_assignments`` / ``booking_contacts`` is NON-deferrable, so it is
checked after every statement, not at COMMIT. A contiguous handoff closes an open
row ``[t0, None) -> [t0, t5)`` and opens a new one ``[t5, None)``; a single naive
flush could emit the INSERT of the new open row while the old row is still open,
producing a transient overlap the constraint rejects. ``save`` therefore runs two
explicit phases:

* Phase A - upsert the root and apply ALL closures (UPDATE ``effective_to`` /
  ``change_reason`` on existing child rows matched by id), then ``flush()``;
* Phase B - INSERT every NEW child row (aggregate child ids absent from the DB),
  then ``flush()``.

After Phase A the old row ends at ``t5``; the new row inserted in Phase B starts at
``t5`` and (half-open ``[)``) shares no instant with it, so the per-statement check
passes. Children are diffed by their stable ``id``: present in the DB -> closure /
update; absent -> insert. Rows are never deleted (history is append-only).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.care.domain.episode import Episode, _EffectiveDatedRow
from app.care.orm import (
    BookingContactModel,
    EpisodeMembershipModel,
    EpisodeModel,
    ResponsibilityAssignmentModel,
    _booking_contact_to_model,
    _CareChildModel,
    _episode_to_domain,
    _episode_to_model,
    _membership_to_model,
    _responsibility_to_model,
)

_Row = TypeVar("_Row", bound=_EffectiveDatedRow)
_Model = TypeVar("_Model", bound=_CareChildModel)


class SqlAlchemyEpisodeRepository:
    """Reads and stores the Episode aggregate against the care tables."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, episode_id: UUID) -> Episode | None:
        root = self._session.get(EpisodeModel, episode_id)
        if root is None:
            return None
        membership_models = self._session.scalars(
            select(EpisodeMembershipModel)
            .where(EpisodeMembershipModel.episode_id == episode_id)
            .order_by(EpisodeMembershipModel.effective_from)
        ).all()
        responsibility_models = self._session.scalars(
            select(ResponsibilityAssignmentModel)
            .where(ResponsibilityAssignmentModel.episode_id == episode_id)
            .order_by(ResponsibilityAssignmentModel.effective_from)
        ).all()
        face_models = self._session.scalars(
            select(BookingContactModel)
            .where(BookingContactModel.episode_id == episode_id)
            .order_by(BookingContactModel.effective_from)
        ).all()
        return _episode_to_domain(
            root, list(membership_models), list(responsibility_models), list(face_models)
        )

    def save(self, episode: Episode) -> None:
        """Upsert the aggregate via the two-phase flush (see module docstring)."""
        root = self._session.get(EpisodeModel, episode.id)
        if root is None:
            self._session.add(_episode_to_model(episode))
        else:
            root.status = episode.status.value
            root.closed_at = episode.closed_at

        # Phase A: apply closures onto existing child rows; collect the new rows.
        new_rows: list[_CareChildModel] = []
        new_rows += self._diff(
            episode.memberships,
            self._existing(EpisodeMembershipModel, episode.id),
            _membership_to_model,
            episode.id,
        )
        new_rows += self._diff(
            episode.responsibility,
            self._existing(ResponsibilityAssignmentModel, episode.id),
            _responsibility_to_model,
            episode.id,
        )
        new_rows += self._diff(
            episode.faces,
            self._existing(BookingContactModel, episode.id),
            _booking_contact_to_model,
            episode.id,
        )
        self._session.flush()  # Phase A: root upsert + every closure land first.

        # Phase B: only now insert the newly-opened rows, so no transient overlap
        # is ever visible to the non-deferrable per-statement EXCLUDE.
        for row in new_rows:
            self._session.add(row)
        self._session.flush()

    def _existing(self, model_cls: type[_Model], episode_id: UUID) -> dict[UUID, _Model]:
        """The persisted child rows of ``model_cls`` for an episode, keyed by id."""
        models = self._session.scalars(
            select(model_cls).where(model_cls.episode_id == episode_id)
        ).all()
        return {model.id: model for model in models}

    @staticmethod
    def _diff(
        rows: Iterable[_Row],
        existing: dict[UUID, _Model],
        to_model: Callable[[_Row, UUID], _Model],
        episode_id: UUID,
    ) -> list[_Model]:
        """Diff aggregate child ``rows`` against ``existing`` DB rows by stable id.

        Matched id -> sync the (possibly newly-closed) ``effective_to`` /
        ``change_reason`` onto the existing model (a closure / update, applied in
        Phase A). Absent id -> a freshly-opened row returned for Phase B insertion.
        Nothing is ever deleted: history is append-only.
        """
        new_models: list[_Model] = []
        for row in rows:
            model = existing.get(row.id)
            if model is None:
                new_models.append(to_model(row, episode_id))
            else:
                model.effective_to = row.period.effective_to
                model.change_reason = row.change_reason
        return new_models
