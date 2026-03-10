"""Unit tests for workbench.fsm — finite state machine for task lifecycle."""

from __future__ import annotations

import pytest

from workbench.fsm import (
    ACTIVE_STATES,
    DEFAULT_MAX_RETRIES,
    NON_TERMINAL_STATES,
    RECOVERABLE_STATES,
    TERMINAL_STATES,
    TRANSITIONS,
    State,
    TaskFSM,
    TransitionError,
    fsm_from_row,
)
from workbench.exceptions import FSMTransitionError


# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------

class TestState:
    def test_all_states_present(self):
        expected = {
            "queued", "resolving", "running", "creating_pr",
            "blocked", "stuck", "completed", "failed", "cancelled",
        }
        assert {s.value for s in State} == expected

    def test_string_values(self):
        """State is a StrEnum — string comparisons should work."""
        assert State.QUEUED == "queued"
        assert State.RUNNING == "running"
        assert State.COMPLETED == "completed"

    def test_state_from_string(self):
        assert State("queued") is State.QUEUED
        assert State("failed") is State.FAILED


# ---------------------------------------------------------------------------
# State groupings
# ---------------------------------------------------------------------------

class TestStateGroupings:
    def test_active_states(self):
        assert ACTIVE_STATES == frozenset({
            State.RESOLVING, State.RUNNING, State.CREATING_PR,
        })

    def test_terminal_states(self):
        assert TERMINAL_STATES == frozenset({
            State.COMPLETED, State.FAILED, State.CANCELLED,
        })

    def test_non_terminal_states(self):
        assert NON_TERMINAL_STATES == frozenset(State) - TERMINAL_STATES
        for s in TERMINAL_STATES:
            assert s not in NON_TERMINAL_STATES
        for s in NON_TERMINAL_STATES:
            assert s not in TERMINAL_STATES

    def test_recoverable_states(self):
        assert RECOVERABLE_STATES == frozenset({
            State.QUEUED, State.RESOLVING, State.RUNNING, State.STUCK,
        })


# ---------------------------------------------------------------------------
# TRANSITIONS table
# ---------------------------------------------------------------------------

class TestTransitions:
    def test_every_state_has_entry(self):
        """Every state must appear as a key in TRANSITIONS."""
        for state in State:
            assert state in TRANSITIONS, f"{state} missing from TRANSITIONS"

    def test_terminal_states_have_no_transitions(self):
        for state in TERMINAL_STATES:
            assert TRANSITIONS[state] == frozenset(), (
                f"Terminal state {state} should have no outgoing transitions"
            )

    def test_queued_valid_transitions(self):
        assert TRANSITIONS[State.QUEUED] == frozenset({
            State.RESOLVING, State.CANCELLED, State.FAILED,
        })

    def test_resolving_valid_transitions(self):
        assert TRANSITIONS[State.RESOLVING] == frozenset({
            State.RUNNING, State.FAILED, State.STUCK, State.CANCELLED,
        })

    def test_running_valid_transitions(self):
        assert TRANSITIONS[State.RUNNING] == frozenset({
            State.CREATING_PR, State.COMPLETED, State.FAILED,
            State.BLOCKED, State.STUCK, State.CANCELLED,
        })

    def test_creating_pr_valid_transitions(self):
        assert TRANSITIONS[State.CREATING_PR] == frozenset({
            State.COMPLETED, State.FAILED, State.STUCK, State.CANCELLED,
        })

    def test_blocked_valid_transitions(self):
        assert TRANSITIONS[State.BLOCKED] == frozenset({
            State.RUNNING, State.CANCELLED, State.FAILED, State.STUCK,
        })

    def test_stuck_valid_transitions(self):
        assert TRANSITIONS[State.STUCK] == frozenset({
            State.QUEUED, State.FAILED, State.CANCELLED,
        })

    def test_all_active_states_can_go_to_stuck(self):
        """All active states should be able to transition to stuck (watchdog)."""
        for state in ACTIVE_STATES:
            assert State.STUCK in TRANSITIONS[state], (
                f"Active state {state} should allow transition to stuck"
            )

    def test_non_terminal_states_can_be_cancelled(self):
        """All non-terminal states should allow cancellation."""
        for state in NON_TERMINAL_STATES:
            assert State.CANCELLED in TRANSITIONS[state], (
                f"Non-terminal state {state} should allow transition to cancelled"
            )


# ---------------------------------------------------------------------------
# TaskFSM — basic construction
# ---------------------------------------------------------------------------

class TestTaskFSMConstruction:
    def test_default_values(self):
        fsm = TaskFSM(task_id="t1", state=State.QUEUED)
        assert fsm.task_id == "t1"
        assert fsm.state == State.QUEUED
        assert fsm.retry_count == 0
        assert fsm.max_retries == DEFAULT_MAX_RETRIES
        assert fsm.blocked_reason is None

    def test_custom_values(self):
        fsm = TaskFSM(
            task_id="t2",
            state=State.BLOCKED,
            retry_count=2,
            max_retries=5,
            blocked_reason="need API key",
        )
        assert fsm.retry_count == 2
        assert fsm.max_retries == 5
        assert fsm.blocked_reason == "need API key"


# ---------------------------------------------------------------------------
# TaskFSM.is_terminal / is_active
# ---------------------------------------------------------------------------

class TestTaskFSMProperties:
    @pytest.mark.parametrize("state", list(TERMINAL_STATES))
    def test_is_terminal_true(self, state: State):
        fsm = TaskFSM(task_id="t", state=state)
        assert fsm.is_terminal is True

    @pytest.mark.parametrize("state", list(NON_TERMINAL_STATES))
    def test_is_terminal_false(self, state: State):
        fsm = TaskFSM(task_id="t", state=state)
        assert fsm.is_terminal is False

    @pytest.mark.parametrize("state", list(ACTIVE_STATES))
    def test_is_active_true(self, state: State):
        fsm = TaskFSM(task_id="t", state=state)
        assert fsm.is_active is True

    @pytest.mark.parametrize("state", list(frozenset(State) - ACTIVE_STATES))
    def test_is_active_false(self, state: State):
        fsm = TaskFSM(task_id="t", state=state)
        assert fsm.is_active is False


# ---------------------------------------------------------------------------
# TaskFSM.can_transition()
# ---------------------------------------------------------------------------

class TestCanTransition:
    def test_valid_transitions(self):
        for source, targets in TRANSITIONS.items():
            fsm = TaskFSM(task_id="t", state=source)
            for target in targets:
                assert fsm.can_transition(target), (
                    f"{source} -> {target} should be allowed"
                )

    def test_invalid_transitions(self):
        for source, valid_targets in TRANSITIONS.items():
            invalid_targets = frozenset(State) - valid_targets
            fsm = TaskFSM(task_id="t", state=source)
            for target in invalid_targets:
                assert not fsm.can_transition(target), (
                    f"{source} -> {target} should NOT be allowed"
                )

    def test_self_transition_not_allowed(self):
        """No state should be able to transition to itself."""
        for state in State:
            fsm = TaskFSM(task_id="t", state=state)
            assert not fsm.can_transition(state), (
                f"{state} -> {state} (self-transition) should not be allowed"
            )


# ---------------------------------------------------------------------------
# TaskFSM.transition() — valid transitions
# ---------------------------------------------------------------------------

class TestTransitionValid:
    def test_queued_to_resolving(self):
        fsm = TaskFSM(task_id="t", state=State.QUEUED)
        result = fsm.transition(State.RESOLVING)
        assert result == State.RESOLVING
        assert fsm.state == State.RESOLVING

    def test_resolving_to_running(self):
        fsm = TaskFSM(task_id="t", state=State.RESOLVING)
        result = fsm.transition(State.RUNNING)
        assert result == State.RUNNING
        assert fsm.state == State.RUNNING

    def test_running_to_creating_pr(self):
        fsm = TaskFSM(task_id="t", state=State.RUNNING)
        result = fsm.transition(State.CREATING_PR)
        assert result == State.CREATING_PR
        assert fsm.state == State.CREATING_PR

    def test_creating_pr_to_completed(self):
        fsm = TaskFSM(task_id="t", state=State.CREATING_PR)
        result = fsm.transition(State.COMPLETED)
        assert result == State.COMPLETED
        assert fsm.state == State.COMPLETED

    def test_running_to_completed_direct(self):
        """Plan-only tasks skip creating_pr."""
        fsm = TaskFSM(task_id="t", state=State.RUNNING)
        result = fsm.transition(State.COMPLETED)
        assert result == State.COMPLETED

    def test_full_happy_path(self):
        """Walk through the entire successful lifecycle."""
        fsm = TaskFSM(task_id="t", state=State.QUEUED)
        fsm.transition(State.RESOLVING)
        fsm.transition(State.RUNNING)
        fsm.transition(State.CREATING_PR)
        result = fsm.transition(State.COMPLETED)
        assert result == State.COMPLETED
        assert fsm.is_terminal

    def test_queued_to_failed(self):
        """Validation failure before work starts."""
        fsm = TaskFSM(task_id="t", state=State.QUEUED)
        result = fsm.transition(State.FAILED)
        assert result == State.FAILED

    def test_any_active_to_cancelled(self):
        for state in ACTIVE_STATES:
            fsm = TaskFSM(task_id="t", state=state)
            result = fsm.transition(State.CANCELLED)
            assert result == State.CANCELLED


# ---------------------------------------------------------------------------
# TaskFSM.transition() — invalid transitions raise TransitionError
# ---------------------------------------------------------------------------

class TestTransitionInvalid:
    def test_completed_cannot_transition(self):
        fsm = TaskFSM(task_id="t", state=State.COMPLETED)
        with pytest.raises(TransitionError) as exc_info:
            fsm.transition(State.RUNNING)
        assert exc_info.value.current == State.COMPLETED
        assert exc_info.value.target == State.RUNNING

    def test_failed_cannot_transition(self):
        fsm = TaskFSM(task_id="t", state=State.FAILED)
        with pytest.raises(TransitionError):
            fsm.transition(State.QUEUED)

    def test_cancelled_cannot_transition(self):
        fsm = TaskFSM(task_id="t", state=State.CANCELLED)
        with pytest.raises(TransitionError):
            fsm.transition(State.RUNNING)

    def test_queued_cannot_go_to_completed(self):
        fsm = TaskFSM(task_id="t", state=State.QUEUED)
        with pytest.raises(TransitionError):
            fsm.transition(State.COMPLETED)

    def test_queued_cannot_go_to_running(self):
        """Must go through resolving first."""
        fsm = TaskFSM(task_id="t", state=State.QUEUED)
        with pytest.raises(TransitionError):
            fsm.transition(State.RUNNING)

    def test_self_transition_raises(self):
        fsm = TaskFSM(task_id="t", state=State.RUNNING)
        with pytest.raises(TransitionError):
            fsm.transition(State.RUNNING)

    def test_transition_error_is_fsm_transition_error(self):
        """TransitionError is a backward-compatible alias for FSMTransitionError."""
        assert TransitionError is FSMTransitionError

    @pytest.mark.parametrize("state", list(TERMINAL_STATES))
    def test_all_terminal_states_reject_all_transitions(self, state: State):
        for target in State:
            fsm = TaskFSM(task_id="t", state=state)
            with pytest.raises(TransitionError):
                fsm.transition(target)

    def test_state_unchanged_on_invalid_transition(self):
        fsm = TaskFSM(task_id="t", state=State.QUEUED)
        with pytest.raises(TransitionError):
            fsm.transition(State.COMPLETED)
        assert fsm.state == State.QUEUED


# ---------------------------------------------------------------------------
# TaskFSM.transition() — stuck -> queued guard (retry logic)
# ---------------------------------------------------------------------------

class TestStuckToQueuedRetry:
    def test_stuck_to_queued_increments_retry(self):
        fsm = TaskFSM(task_id="t", state=State.STUCK, retry_count=0, max_retries=3)
        result = fsm.transition(State.QUEUED)
        assert result == State.QUEUED
        assert fsm.retry_count == 1

    def test_stuck_to_queued_multiple_retries(self):
        fsm = TaskFSM(task_id="t", state=State.STUCK, retry_count=0, max_retries=3)
        fsm.transition(State.QUEUED)
        assert fsm.retry_count == 1
        assert fsm.state == State.QUEUED

        # Simulate going through stuck again
        fsm.state = State.STUCK
        fsm.transition(State.QUEUED)
        assert fsm.retry_count == 2

        fsm.state = State.STUCK
        fsm.transition(State.QUEUED)
        assert fsm.retry_count == 3

    def test_stuck_to_queued_at_max_retries_raises(self):
        """When retry_count == max_retries, the guard blocks stuck -> queued."""
        fsm = TaskFSM(task_id="t", state=State.STUCK, retry_count=3, max_retries=3)
        with pytest.raises(TransitionError, match="max retries exceeded"):
            fsm.transition(State.QUEUED)
        # State should not change
        assert fsm.state == State.STUCK
        assert fsm.retry_count == 3

    def test_stuck_to_queued_over_max_retries_raises(self):
        fsm = TaskFSM(task_id="t", state=State.STUCK, retry_count=5, max_retries=3)
        with pytest.raises(TransitionError, match="max retries exceeded"):
            fsm.transition(State.QUEUED)

    def test_stuck_to_queued_with_max_retries_zero(self):
        """max_retries=0 means no retries allowed."""
        fsm = TaskFSM(task_id="t", state=State.STUCK, retry_count=0, max_retries=0)
        with pytest.raises(TransitionError, match="max retries exceeded"):
            fsm.transition(State.QUEUED)

    def test_stuck_to_failed_always_allowed(self):
        """Even when retries are exhausted, stuck -> failed is allowed."""
        fsm = TaskFSM(task_id="t", state=State.STUCK, retry_count=3, max_retries=3)
        result = fsm.transition(State.FAILED, reason="giving up")
        assert result == State.FAILED


# ---------------------------------------------------------------------------
# TaskFSM.transition() — blocked guard (requires reason)
# ---------------------------------------------------------------------------

class TestBlockedGuard:
    def test_entering_blocked_requires_reason(self):
        fsm = TaskFSM(task_id="t", state=State.RUNNING)
        with pytest.raises(TransitionError, match="blocked_reason is required"):
            fsm.transition(State.BLOCKED)

    def test_entering_blocked_empty_reason_raises(self):
        fsm = TaskFSM(task_id="t", state=State.RUNNING)
        with pytest.raises(TransitionError, match="blocked_reason is required"):
            fsm.transition(State.BLOCKED, reason="")

    def test_entering_blocked_with_reason_succeeds(self):
        fsm = TaskFSM(task_id="t", state=State.RUNNING)
        result = fsm.transition(State.BLOCKED, reason="need user input")
        assert result == State.BLOCKED
        assert fsm.blocked_reason == "need user input"

    def test_state_unchanged_when_blocked_guard_fails(self):
        fsm = TaskFSM(task_id="t", state=State.RUNNING)
        with pytest.raises(TransitionError):
            fsm.transition(State.BLOCKED)
        assert fsm.state == State.RUNNING
        assert fsm.blocked_reason is None


# ---------------------------------------------------------------------------
# TaskFSM.mark_stuck()
# ---------------------------------------------------------------------------

class TestMarkStuck:
    def test_mark_stuck_from_active_state(self):
        for state in ACTIVE_STATES:
            fsm = TaskFSM(task_id="t", state=state)
            result = fsm.mark_stuck()
            assert result == State.STUCK

    def test_mark_stuck_from_blocked(self):
        fsm = TaskFSM(task_id="t", state=State.BLOCKED)
        result = fsm.mark_stuck()
        assert result == State.STUCK

    def test_mark_stuck_from_terminal_raises(self):
        for state in TERMINAL_STATES:
            fsm = TaskFSM(task_id="t", state=state)
            with pytest.raises(TransitionError):
                fsm.mark_stuck()

    def test_mark_stuck_from_queued_raises(self):
        """Queued is not in the TRANSITIONS table for stuck."""
        fsm = TaskFSM(task_id="t", state=State.QUEUED)
        with pytest.raises(TransitionError):
            fsm.mark_stuck()

    def test_mark_stuck_from_stuck_raises(self):
        """Already stuck — can't go to stuck again."""
        fsm = TaskFSM(task_id="t", state=State.STUCK)
        with pytest.raises(TransitionError):
            fsm.mark_stuck()


# ---------------------------------------------------------------------------
# TaskFSM.retry_or_fail()
# ---------------------------------------------------------------------------

class TestRetryOrFail:
    def test_retry_when_retries_remain(self):
        fsm = TaskFSM(task_id="t", state=State.STUCK, retry_count=0, max_retries=3)
        result = fsm.retry_or_fail()
        assert result == State.QUEUED
        assert fsm.retry_count == 1

    def test_fail_when_retries_exhausted(self):
        fsm = TaskFSM(task_id="t", state=State.STUCK, retry_count=3, max_retries=3)
        result = fsm.retry_or_fail()
        assert result == State.FAILED
        assert fsm.is_terminal

    def test_fail_when_retries_over_max(self):
        fsm = TaskFSM(task_id="t", state=State.STUCK, retry_count=5, max_retries=3)
        result = fsm.retry_or_fail()
        assert result == State.FAILED

    def test_retry_or_fail_with_max_retries_zero(self):
        """max_retries=0: no retries allowed, should fail immediately."""
        fsm = TaskFSM(task_id="t", state=State.STUCK, retry_count=0, max_retries=0)
        result = fsm.retry_or_fail()
        assert result == State.FAILED

    def test_retry_or_fail_at_boundary(self):
        """retry_count == max_retries - 1: last retry available."""
        fsm = TaskFSM(task_id="t", state=State.STUCK, retry_count=2, max_retries=3)
        result = fsm.retry_or_fail()
        assert result == State.QUEUED
        assert fsm.retry_count == 3

    def test_retry_or_fail_from_non_stuck_raises(self):
        """retry_or_fail can only be called from stuck state."""
        for state in State:
            if state == State.STUCK:
                continue
            fsm = TaskFSM(task_id="t", state=state)
            with pytest.raises(TransitionError, match="only be called from stuck"):
                fsm.retry_or_fail()

    def test_retry_preserves_task_id(self):
        fsm = TaskFSM(task_id="my-task-123", state=State.STUCK, retry_count=0, max_retries=3)
        fsm.retry_or_fail()
        assert fsm.task_id == "my-task-123"

    def test_full_retry_cycle(self):
        """Simulate a complete retry cycle: stuck -> queued -> ... -> stuck -> failed."""
        fsm = TaskFSM(task_id="t", state=State.STUCK, retry_count=0, max_retries=2)

        # First retry
        result = fsm.retry_or_fail()
        assert result == State.QUEUED
        assert fsm.retry_count == 1

        # Simulate work and getting stuck again
        fsm.transition(State.RESOLVING)
        fsm.transition(State.RUNNING)
        fsm.mark_stuck()
        assert fsm.state == State.STUCK

        # Second retry
        result = fsm.retry_or_fail()
        assert result == State.QUEUED
        assert fsm.retry_count == 2

        # Stuck again
        fsm.transition(State.RESOLVING)
        fsm.transition(State.RUNNING)
        fsm.mark_stuck()

        # No more retries
        result = fsm.retry_or_fail()
        assert result == State.FAILED
        assert fsm.is_terminal


# ---------------------------------------------------------------------------
# TaskFSM.unblock()
# ---------------------------------------------------------------------------

class TestUnblock:
    def test_unblock_from_blocked(self):
        fsm = TaskFSM(
            task_id="t", state=State.BLOCKED, blocked_reason="waiting for key",
        )
        result = fsm.unblock()
        assert result == State.RUNNING
        assert fsm.blocked_reason is None

    def test_unblock_from_non_blocked_raises(self):
        for state in State:
            if state == State.BLOCKED:
                continue
            fsm = TaskFSM(task_id="t", state=state)
            with pytest.raises(TransitionError, match="not blocked"):
                fsm.unblock()

    def test_unblock_clears_blocked_reason(self):
        fsm = TaskFSM(
            task_id="t", state=State.BLOCKED, blocked_reason="need API key",
        )
        fsm.unblock()
        assert fsm.blocked_reason is None
        assert fsm.state == State.RUNNING


# ---------------------------------------------------------------------------
# fsm_from_row()
# ---------------------------------------------------------------------------

class TestFsmFromRow:
    def test_basic_row(self):
        class Row:
            id = "task-abc"
            status = "queued"
        fsm = fsm_from_row(Row())
        assert fsm.task_id == "task-abc"
        assert fsm.state == State.QUEUED
        assert fsm.retry_count == 0
        assert fsm.max_retries == DEFAULT_MAX_RETRIES
        assert fsm.blocked_reason is None

    def test_row_with_all_fields(self):
        class Row:
            id = "task-xyz"
            status = "blocked"
            retry_count = 2
            max_retries = 5
            blocked_reason = "needs input"
        fsm = fsm_from_row(Row())
        assert fsm.task_id == "task-xyz"
        assert fsm.state == State.BLOCKED
        assert fsm.retry_count == 2
        assert fsm.max_retries == 5
        assert fsm.blocked_reason == "needs input"

    def test_row_with_missing_optional_fields(self):
        """Duck-typed rows may lack retry_count, max_retries, blocked_reason."""
        class Row:
            id = "task-minimal"
            status = "running"
        fsm = fsm_from_row(Row())
        assert fsm.retry_count == 0
        assert fsm.max_retries == DEFAULT_MAX_RETRIES
        assert fsm.blocked_reason is None

    def test_row_with_none_values(self):
        """None values for optional fields should fall back to defaults."""
        class Row:
            id = "task-none"
            status = "resolving"
            retry_count = None
            max_retries = None
            blocked_reason = None
        fsm = fsm_from_row(Row())
        assert fsm.retry_count == 0
        assert fsm.max_retries == DEFAULT_MAX_RETRIES
        assert fsm.blocked_reason is None

    def test_fsm_from_row_is_functional(self):
        """FSM created from row should be fully functional."""
        class Row:
            id = "task-func"
            status = "queued"
        fsm = fsm_from_row(Row())
        fsm.transition(State.RESOLVING)
        assert fsm.state == State.RESOLVING


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_transition_to_same_state_raises(self):
        """No state allows transitioning to itself."""
        for state in State:
            fsm = TaskFSM(task_id="t", state=state)
            with pytest.raises(TransitionError):
                fsm.transition(state)

    def test_transition_reason_stored_on_error(self):
        """TransitionError should capture current/target states."""
        fsm = TaskFSM(task_id="t", state=State.COMPLETED)
        with pytest.raises(TransitionError) as exc_info:
            fsm.transition(State.RUNNING)
        err = exc_info.value
        assert err.current == State.COMPLETED
        assert err.target == State.RUNNING

    def test_transition_with_reason_kwarg(self):
        """Reason kwarg passes through for valid transitions."""
        fsm = TaskFSM(task_id="t", state=State.QUEUED)
        result = fsm.transition(State.FAILED, reason="validation failed")
        assert result == State.FAILED

    def test_default_max_retries_value(self):
        assert DEFAULT_MAX_RETRIES == 3

    def test_max_retries_one(self):
        """With max_retries=1, exactly one retry is allowed."""
        fsm = TaskFSM(task_id="t", state=State.STUCK, retry_count=0, max_retries=1)
        result = fsm.retry_or_fail()
        assert result == State.QUEUED
        assert fsm.retry_count == 1

        # Stuck again
        fsm.state = State.STUCK
        result = fsm.retry_or_fail()
        assert result == State.FAILED

    def test_blocked_then_stuck_then_retry(self):
        """Complex lifecycle: running -> blocked -> stuck -> queued (retry)."""
        fsm = TaskFSM(task_id="t", state=State.RUNNING, max_retries=3)
        fsm.transition(State.BLOCKED, reason="waiting for user")
        assert fsm.state == State.BLOCKED
        assert fsm.blocked_reason == "waiting for user"

        fsm.mark_stuck()
        assert fsm.state == State.STUCK

        result = fsm.retry_or_fail()
        assert result == State.QUEUED
        assert fsm.retry_count == 1

    def test_multiple_blocked_reasons_overwrite(self):
        """Each block sets a new reason; unblock clears it."""
        fsm = TaskFSM(task_id="t", state=State.RUNNING)
        fsm.transition(State.BLOCKED, reason="reason1")
        assert fsm.blocked_reason == "reason1"

        fsm.unblock()
        assert fsm.blocked_reason is None

        fsm.transition(State.BLOCKED, reason="reason2")
        assert fsm.blocked_reason == "reason2"
