"""The ``Episode`` aggregate root and its internal entities.

Pure domain layer (project std 1): plain Python + stdlib only — no FastAPI,
SQLAlchemy, or Pydantic. All business rules live IN the aggregate; the only way
to mutate an episode is through the root's methods, each of which takes an
injectable ``now: datetime`` (no hidden clock).

Core design move (see ``planning/care-team-design.md``): membership, clinical
responsibility, and the booking "face" are EFFECTIVE-DATED, APPEND-ONLY rows.
"Current X" = the row effective at ``now``. Reassignment never overwrites a row;
it closes the old one and opens a new one, contiguously (no gap, no overlap).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from app.care.domain.exceptions import (
    EpisodeClosed,
    NotACurrentMember,
    OverlappingPeriod,
    SelfTreatment,
)
from app.care.domain.value_objects import EffectivePeriod, Role


class EpisodeStatus(StrEnum):
    """Lifecycle state of an episode."""

    ACTIVE = "active"
    CLOSED = "closed"


@dataclass(eq=False)
class _EffectiveDatedRow:
    """Shared base for the three append-only, effective-dated entities.

    Identity is by ``id`` (a row never changes identity), so ``eq=False`` keeps
    Python identity semantics rather than value equality.
    """

    provider_id: UUID
    period: EffectivePeriod
    change_reason: str
    id: UUID = field(default_factory=uuid4)

    @property
    def is_open(self) -> bool:
        return self.period.is_open

    def is_effective_at(self, now: datetime) -> bool:
        return self.period.is_effective_at(now)

    def _close_at(self, at: datetime, episode_id: UUID) -> None:
        """End this open row at ``at`` (append-only handoff helper).

        A degenerate close (``at <= effective_from``, e.g. handing off at the
        very instant the holder began) surfaces as a typed ``OverlappingPeriod``
        rather than a leaked ``ValueError``.
        """
        try:
            self.period = self.period.closed_at(at)
        except ValueError as exc:
            raise OverlappingPeriod(episode_id) from exc


@dataclass(eq=False)
class Membership(_EffectiveDatedRow):
    """A provider's effective-dated membership of an episode, with its role."""

    role: Role = Role.PHYSICIAN  # overridden at construction; default for dataclass ordering


@dataclass(eq=False)
class Responsibility(_EffectiveDatedRow):
    """The clinically-responsible provider over an effective period."""


@dataclass(eq=False)
class BookingContact(_EffectiveDatedRow):
    """The booking "face" of an episode over an effective period."""


class Episode:
    """Aggregate root: a bounded course of care for one client, one reason.

    Protects (see the design spec): exactly one clinically-responsible provider
    and exactly one face at every instant while active; responsible/face must be
    current members; no self-treatment; closed episodes are immutable; all
    history is append-only.
    """

    def __init__(
        self,
        *,
        id: UUID,
        client_id: UUID,
        reason: str,
        managing_org_id: UUID,
        opened_at: datetime,
        status: EpisodeStatus = EpisodeStatus.ACTIVE,
        closed_at: datetime | None = None,
    ) -> None:
        self.id = id
        self.client_id = client_id
        self.reason = reason
        self.managing_org_id = managing_org_id
        self.opened_at = opened_at
        self.status = status
        self.closed_at = closed_at
        self._memberships: list[Membership] = []
        self._responsibility: list[Responsibility] = []
        self._faces: list[BookingContact] = []

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def open(
        cls,
        *,
        id: UUID,
        client_id: UUID,
        reason: str,
        managing_org_id: UUID,
        now: datetime,
        responsible_provider_id: UUID,
        responsible_role: Role,
        change_reason: str,
        face_provider_id: UUID | None = None,
        face_role: Role | None = None,
    ) -> Episode:
        """Open an ACTIVE episode that already satisfies its invariants at ``t0``.

        Bootstraps in one step: adds the responsible provider as a member, makes
        them responsible, and sets the face. The face may DIVERGE from the
        responsible provider from ``t0`` by passing ``face_provider_id`` (its
        ``face_role`` is then required) — that provider is added as a member too,
        so there is never a transient/zero-length handoff.
        """
        episode = cls(
            id=id,
            client_id=client_id,
            reason=reason,
            managing_org_id=managing_org_id,
            opened_at=now,
        )
        episode.add_member(
            provider_id=responsible_provider_id,
            role=responsible_role,
            now=now,
            change_reason=change_reason,
        )
        episode.assign_responsible(
            provider_id=responsible_provider_id, now=now, change_reason=change_reason
        )

        if face_provider_id is None or face_provider_id == responsible_provider_id:
            face_id = responsible_provider_id
        else:
            if face_role is None:
                raise ValueError("face_role is required when face_provider_id differs.")
            episode.add_member(
                provider_id=face_provider_id,
                role=face_role,
                now=now,
                change_reason=change_reason,
            )
            face_id = face_provider_id
        episode.set_face(provider_id=face_id, now=now, change_reason=change_reason)
        return episode

    # ------------------------------------------------------------------ #
    # Read-only snapshots (encapsulation: no mutable list leaks)
    # ------------------------------------------------------------------ #
    @property
    def memberships(self) -> tuple[Membership, ...]:
        return tuple(self._memberships)

    @property
    def responsibility(self) -> tuple[Responsibility, ...]:
        return tuple(self._responsibility)

    @property
    def faces(self) -> tuple[BookingContact, ...]:
        return tuple(self._faces)

    @property
    def is_active(self) -> bool:
        return self.status is EpisodeStatus.ACTIVE

    # ------------------------------------------------------------------ #
    # "Current" derivations (the design move: derive, never store)
    # ------------------------------------------------------------------ #
    def current_membership(self, provider_id: UUID, now: datetime) -> Membership | None:
        for m in self._memberships:
            if m.provider_id == provider_id and m.is_effective_at(now):
                return m
        return None

    def is_current_member(self, provider_id: UUID, now: datetime) -> bool:
        return self.current_membership(provider_id, now) is not None

    def current_responsibility(self, now: datetime) -> Responsibility | None:
        for r in self._responsibility:
            if r.is_effective_at(now):
                return r
        return None

    def current_face(self, now: datetime) -> BookingContact | None:
        for f in self._faces:
            if f.is_effective_at(now):
                return f
        return None

    # ------------------------------------------------------------------ #
    # Guards
    # ------------------------------------------------------------------ #
    def _guard_open(self) -> None:
        if self.status is EpisodeStatus.CLOSED:
            raise EpisodeClosed(self.id)

    def _guard_not_self(self, provider_id: UUID) -> None:
        if provider_id == self.client_id:
            raise SelfTreatment(provider_id)

    # ------------------------------------------------------------------ #
    # Mutators
    # ------------------------------------------------------------------ #
    def add_member(
        self,
        *,
        provider_id: UUID,
        role: Role,
        now: datetime,
        change_reason: str,
        effective_from: datetime | None = None,
        effective_to: datetime | None = None,
    ) -> Membership:
        """Append a membership row.

        ``effective_from`` defaults to ``now``. Plain membership may be
        future-dated and may have gaps (unlike responsibility/face), so an
        explicit period is allowed. Self-treatment is barred here so it holds for
        every membership.
        """
        self._guard_open()
        self._guard_not_self(provider_id)
        period = EffectivePeriod(effective_from or now, effective_to)
        membership = Membership(
            provider_id=provider_id, period=period, change_reason=change_reason, role=role
        )
        self._memberships.append(membership)
        return membership

    def start_coverage(
        self,
        *,
        provider_id: UUID,
        role: Role,
        effective_from: datetime,
        effective_to: datetime,
        now: datetime,
        change_reason: str,
    ) -> Membership:
        """A bounded ``add_member`` naming the intent (e.g. "covering for X").

        Coverage is a membership with a hard end date (so access expires
        automatically); it does NOT touch responsibility or the face — the cover
        does not become responsible. The window may be future-dated.
        """
        self._guard_open()
        self._guard_not_self(provider_id)
        period = EffectivePeriod(effective_from, effective_to)
        membership = Membership(
            provider_id=provider_id, period=period, change_reason=change_reason, role=role
        )
        self._memberships.append(membership)
        return membership

    def assign_responsible(
        self, *, provider_id: UUID, now: datetime, change_reason: str
    ) -> Responsibility:
        """Make ``provider_id`` the clinically-responsible provider as of ``now``.

        Contiguous, append-only handoff: the current row (if any) is closed at
        ``now`` and a new open row ``[now, None)`` is opened, so exactly one row
        is effective at every instant. Reassigning the SAME current provider is a
        no-op (avoids audit noise and a zero-length row).
        """
        self._guard_open()
        self._guard_not_self(provider_id)
        if not self.is_current_member(provider_id, now):
            raise NotACurrentMember(provider_id)

        current = self.current_responsibility(now)
        if current is not None:
            if current.provider_id == provider_id:
                return current
            current._close_at(now, self.id)

        new_row = Responsibility(
            provider_id=provider_id,
            period=EffectivePeriod(now, None),
            change_reason=change_reason,
        )
        self._responsibility.append(new_row)
        self._assert_no_overlap(self._responsibility)
        return new_row

    def set_face(self, *, provider_id: UUID, now: datetime, change_reason: str) -> BookingContact:
        """Set the booking face to ``provider_id`` as of ``now``.

        Identical contiguous-handoff pattern to ``assign_responsible``; the face
        must be a current member. Independent of responsibility (they may
        coincide or diverge). Same-provider call is a no-op.
        """
        self._guard_open()
        if not self.is_current_member(provider_id, now):
            raise NotACurrentMember(provider_id)

        current = self.current_face(now)
        if current is not None:
            if current.provider_id == provider_id:
                return current
            current._close_at(now, self.id)

        new_row = BookingContact(
            provider_id=provider_id,
            period=EffectivePeriod(now, None),
            change_reason=change_reason,
        )
        self._faces.append(new_row)
        self._assert_no_overlap(self._faces)
        return new_row

    def end_member(
        self,
        *,
        provider_id: UUID,
        effective_to: datetime,
        now: datetime,
        change_reason: str,
        successor_face_id: UUID | None = None,
    ) -> Membership:
        """End ``provider_id``'s current membership at ``effective_to``.

        Guards:
        - the provider must currently be a member (else ``NotACurrentMember``);
        - you may NOT end the membership of the currently-responsible provider
          while the episode is active — reassign responsibility first (keeps
          invariant 2: responsible must be a current member);
        - if the provider is the current FACE while active, the face must be
          handed off in the same call: ``successor_face_id`` is required, must be
          a current member, must not be the leaver, and (because face handoffs
          are never back-/future-dated) ``effective_to`` must equal ``now``.
        """
        self._guard_open()

        membership = self.current_membership(provider_id, now)
        if membership is None:
            raise NotACurrentMember(provider_id)

        responsible = self.current_responsibility(now)
        if responsible is not None and responsible.provider_id == provider_id:
            # Cannot strand responsibility on a non-member; reassign first.
            raise NotACurrentMember(provider_id)

        face = self.current_face(now)
        if face is not None and face.provider_id == provider_id:
            if effective_to != now:
                # Face handoffs happen at `now`, never back/future-dated.
                raise OverlappingPeriod(self.id)
            if successor_face_id is None or successor_face_id == provider_id:
                raise NotACurrentMember(
                    provider_id if successor_face_id is None else successor_face_id
                )
            if not self.is_current_member(successor_face_id, now):
                raise NotACurrentMember(successor_face_id)
            self.set_face(provider_id=successor_face_id, now=now, change_reason=change_reason)

        membership.change_reason = change_reason
        membership._close_at(effective_to, self.id)
        return membership

    def close(self, *, now: datetime) -> None:
        """Close the episode (immutable thereafter).

        Does NOT end-date memberships / responsibility / face — they persist as
        history (former members keep role-limited view access per the PDP).
        """
        self._guard_open()
        self.status = EpisodeStatus.CLOSED
        self.closed_at = now

    # ------------------------------------------------------------------ #
    # Internal invariant check
    # ------------------------------------------------------------------ #
    def _assert_no_overlap(self, rows: list[Responsibility] | list[BookingContact]) -> None:
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                if rows[i].period.overlaps(rows[j].period):
                    raise OverlappingPeriod(self.id)
