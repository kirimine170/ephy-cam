"""Small auditable state machine for captured photo retention."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from .contracts import (
    parse_timestamp,
    require_aware_utc,
    validate_photo_acceptance,
)


class LifecycleState(str, Enum):
    EPHEMERAL = "ephemeral"
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    DIARY_ELIGIBLE = "diary-eligible"
    COMMITTED = "committed"
    PURGED = "purged"


class TransitionDenied(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LifecycleEvent:
    previous: LifecycleState | None
    current: LifecycleState
    occurred_at: datetime
    reason: str


TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.EPHEMERAL: frozenset(
        {LifecycleState.CANDIDATE, LifecycleState.PURGED}
    ),
    LifecycleState.CANDIDATE: frozenset(
        {LifecycleState.ACCEPTED, LifecycleState.PURGED}
    ),
    LifecycleState.ACCEPTED: frozenset(
        {LifecycleState.DIARY_ELIGIBLE, LifecycleState.PURGED}
    ),
    LifecycleState.DIARY_ELIGIBLE: frozenset(
        {LifecycleState.COMMITTED, LifecycleState.PURGED}
    ),
    LifecycleState.COMMITTED: frozenset({LifecycleState.PURGED}),
    LifecycleState.PURGED: frozenset(),
}


class MediaLifecycle:
    """Append-only state history for one capture.

    A capture starts as ``ephemeral``. Acceptance is only applicable after an
    explicit transition to ``candidate`` and therefore cannot be implied by
    successful capture.
    """

    def __init__(
        self,
        capture_id: str,
        trace_id: str,
        *,
        captured_at: datetime,
    ) -> None:
        self.capture_id = capture_id
        self.trace_id = trace_id
        initial_time = require_aware_utc(captured_at, "captured_at")
        self._state = LifecycleState.EPHEMERAL
        self._history = [
            LifecycleEvent(None, self._state, initial_time, "captured")
        ]

    @property
    def state(self) -> LifecycleState:
        return self._state

    @property
    def history(self) -> tuple[LifecycleEvent, ...]:
        return tuple(self._history)

    def transition(
        self,
        target: LifecycleState,
        *,
        occurred_at: datetime,
        reason: str,
    ) -> None:
        event_time = require_aware_utc(occurred_at, "occurred_at")
        if not reason.strip():
            raise TransitionDenied("reason_required")
        if event_time < self._history[-1].occurred_at:
            raise TransitionDenied("non_monotonic_transition_time")
        if target not in TRANSITIONS[self._state]:
            raise TransitionDenied(
                f"invalid_transition:{self._state.value}->{target.value}"
            )
        previous = self._state
        self._state = target
        self._history.append(
            LifecycleEvent(previous, target, event_time, reason.strip())
        )

    def mark_candidate(self, *, occurred_at: datetime) -> None:
        self.transition(
            LifecycleState.CANDIDATE,
            occurred_at=occurred_at,
            reason="candidate-proposed",
        )

    def apply_acceptance(self, acceptance: Mapping[str, Any]) -> None:
        validate_photo_acceptance(acceptance)
        if acceptance.get("capture_id") != self.capture_id:
            raise TransitionDenied("acceptance_capture_mismatch")
        if acceptance.get("trace_id") != self.trace_id:
            raise TransitionDenied("acceptance_trace_mismatch")
        if self._state is not LifecycleState.CANDIDATE:
            raise TransitionDenied("acceptance_requires_candidate")
        decided_at = parse_timestamp(acceptance.get("decided_at"), "decided_at")
        decision = acceptance.get("decision")
        if decision == "accepted":
            target = LifecycleState.ACCEPTED
        elif decision == "rejected":
            target = LifecycleState.PURGED
        else:
            raise TransitionDenied("unsupported_acceptance_decision")
        self.transition(
            target,
            occurred_at=decided_at,
            reason=f"photo-{decision}",
        )
