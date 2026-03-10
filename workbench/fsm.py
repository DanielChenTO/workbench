r"""Finite State Machine for task lifecycle management.

Defines all valid states and transitions, enforces guards, tracks retries,
and provides a single entry point for all status changes.

Transition diagram:
    queued -> resolving -> running -> creating_pr -> completed
                  \            \           \
                   \            \           +-> failed
                    \            +-> blocked (needs human)
                     \            \
                      \            +-> failed
                       +-> failed

    Any active state -> stuck (detected by watchdog)
    stuck -> queued (auto-retry, retry_count < max_retries)
    stuck -> failed (retry_count >= max_retries)
    blocked -> running (unblocked by human via /unblock)
    blocked -> cancelled
    Any non-terminal state -> cancelled (user-initiated)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from .exceptions import FSMTransitionError

log = logging.getLogger(__name__)

# Backward-compatible alias
TransitionError = FSMTransitionError

# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

class State(StrEnum):
    """All possible task states."""

    QUEUED = "queued"
    RESOLVING = "resolving"
    RUNNING = "running"
    CREATING_PR = "creating_pr"
    BLOCKED = "blocked"
    STUCK = "stuck"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Convenience sets for grouping
ACTIVE_STATES = frozenset({State.RESOLVING, State.RUNNING, State.CREATING_PR})
TERMINAL_STATES = frozenset({State.COMPLETED, State.FAILED, State.CANCELLED})
NON_TERMINAL_STATES = frozenset(State) - TERMINAL_STATES

# States that should be re-enqueued on startup
RECOVERABLE_STATES = frozenset({State.QUEUED, State.RESOLVING, State.RUNNING, State.STUCK})


# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

# Maps (from_state) -> set of allowed (to_state) values.
# This is the single source of truth for what transitions are legal.
TRANSITIONS: dict[State, frozenset[State]] = {
    State.QUEUED: frozenset({
        State.RESOLVING,
        State.CANCELLED,
        State.FAILED,       # e.g. validation failure before work starts
    }),
    State.RESOLVING: frozenset({
        State.RUNNING,
        State.FAILED,
        State.STUCK,
        State.CANCELLED,
    }),
    State.RUNNING: frozenset({
        State.CREATING_PR,
        State.COMPLETED,     # for read-only/plan-only tasks (no PR step)
        State.FAILED,
        State.BLOCKED,
        State.STUCK,
        State.CANCELLED,
    }),
    State.CREATING_PR: frozenset({
        State.COMPLETED,
        State.FAILED,
        State.STUCK,
        State.CANCELLED,
    }),
    State.BLOCKED: frozenset({
        State.RUNNING,       # unblocked by human
        State.CANCELLED,
        State.FAILED,        # manually failed while blocked
        State.STUCK,         # watchdog timeout on blocked task
    }),
    State.STUCK: frozenset({
        State.QUEUED,        # auto-retry
        State.FAILED,        # max retries exceeded
        State.CANCELLED,
    }),
    # Terminal states: no outgoing transitions
    State.COMPLETED: frozenset(),
    State.FAILED: frozenset(),
    State.CANCELLED: frozenset(),
}


# ---------------------------------------------------------------------------
# FSM context: per-task state + retry tracking
# ---------------------------------------------------------------------------

DEFAULT_MAX_RETRIES = 3


@dataclass
class TaskFSM:
    """Per-task FSM instance that tracks state and retry count.

    This is a lightweight in-memory object. The canonical state lives in the
    database; this object is used for transition validation and retry logic.
    """

    task_id: str
    state: State
    retry_count: int = 0
    max_retries: int = DEFAULT_MAX_RETRIES
    blocked_reason: str | None = None

    def can_transition(self, target: State) -> bool:
        """Check if transitioning to `target` is allowed from current state."""
        allowed = TRANSITIONS.get(self.state, frozenset())
        return target in allowed

    def transition(self, target: State, *, reason: str = "") -> State:
        """Attempt to transition to `target`. Raises TransitionError if invalid.

        Returns the new state on success. Also applies side effects:
        - stuck -> queued: increments retry_count
        - Any -> blocked: requires `reason` (the blocked_reason)
        """
        if not self.can_transition(target):
            raise TransitionError(self.state, target, reason)

        old = self.state

        # --- Guard: stuck -> queued requires retries remaining ---
        if old == State.STUCK and target == State.QUEUED:
            if self.retry_count >= self.max_retries:
                raise TransitionError(
                    old, target,
                    f"max retries exceeded ({self.retry_count}/{self.max_retries})",
                )
            self.retry_count += 1
            log.info(
                "Task %s: retry %d/%d (stuck -> queued)",
                self.task_id, self.retry_count, self.max_retries,
            )

        # --- Guard: entering blocked requires a reason ---
        if target == State.BLOCKED:
            if not reason:
                raise TransitionError(
                    old, target, "blocked_reason is required",
                )
            self.blocked_reason = reason

        # --- Guard: stuck -> failed is forced when max retries exceeded ---
        # (This is an assertion — the watchdog should call this explicitly)

        self.state = target
        log.info(
            "Task %s: %s -> %s%s",
            self.task_id, old, target,
            f" (reason: {reason})" if reason else "",
        )
        return self.state

    def mark_stuck(self) -> State:
        """Convenience: transition to stuck (called by watchdog).

        Returns the resulting state:
        - If retries remain: transitions to stuck (caller should then retry -> queued)
        - If already stuck/terminal: raises TransitionError
        """
        return self.transition(State.STUCK, reason="watchdog: stale heartbeat")

    def retry_or_fail(self) -> State:
        """From stuck state, either retry (-> queued) or fail (-> failed).

        Returns the resulting state.
        """
        if self.state != State.STUCK:
            raise TransitionError(
                self.state, "queued|failed",
                "retry_or_fail can only be called from stuck state",
            )

        if self.retry_count < self.max_retries:
            return self.transition(State.QUEUED)
        else:
            return self.transition(
                State.FAILED,
                reason=f"max retries exceeded ({self.retry_count}/{self.max_retries})",
            )

    def unblock(self) -> State:
        """Resume a blocked task (-> running). Called when human provides answer."""
        if self.state != State.BLOCKED:
            raise TransitionError(
                self.state, State.RUNNING,
                "task is not blocked",
            )
        self.blocked_reason = None
        return self.transition(State.RUNNING, reason="unblocked by human")

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES


# ---------------------------------------------------------------------------
# Factory: load FSM from DB row
# ---------------------------------------------------------------------------

def fsm_from_row(row) -> TaskFSM:
    """Create a TaskFSM from a database TaskRow (duck-typed).

    Expects row to have: id, status, retry_count (optional), max_retries (optional),
    blocked_reason (optional).
    """
    return TaskFSM(
        task_id=row.id,
        state=State(row.status),
        retry_count=getattr(row, "retry_count", 0) or 0,
        max_retries=getattr(row, "max_retries", DEFAULT_MAX_RETRIES) or DEFAULT_MAX_RETRIES,
        blocked_reason=getattr(row, "blocked_reason", None),
    )
