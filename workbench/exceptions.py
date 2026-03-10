"""Centralised exception hierarchy for workbench.

All domain-specific exceptions live here so that callers can catch at the
right granularity.  Each exception carries structured context (task ID,
repo name, operation) to make error messages actionable in logs and API
responses.
"""

from __future__ import annotations


class WorkbenchError(Exception):
    """Base class for all workbench exceptions.

    Subclasses should pass *context* keyword arguments so that every error
    message includes the task ID, repo, and/or operation that failed.
    """

    def __init__(
        self,
        message: str,
        *,
        task_id: str | None = None,
        repo: str | None = None,
        operation: str | None = None,
    ) -> None:
        self.task_id = task_id
        self.repo = repo
        self.operation = operation

        parts: list[str] = []
        if task_id:
            parts.append(f"task={task_id}")
        if repo:
            parts.append(f"repo={repo}")
        if operation:
            parts.append(f"op={operation}")

        if parts:
            prefix = "[" + ", ".join(parts) + "] "
        else:
            prefix = ""

        self.raw_message = message
        super().__init__(f"{prefix}{message}")


class TaskResolutionError(WorkbenchError):
    """Raised when an input source (Jira, GitHub issue, prompt file, etc.)
    cannot be resolved into a usable prompt."""


class GitOperationError(WorkbenchError):
    """Raised when a git or ``gh`` CLI operation fails."""


class ExecutorError(WorkbenchError):
    """Raised when the ``opencode run`` subprocess fails or times out."""


class FSMTransitionError(WorkbenchError):
    """Raised when an invalid FSM state transition is attempted.

    Carries the *current* and *target* states for diagnostics.
    """

    def __init__(
        self,
        current: str,
        target: str,
        reason: str = "",
        *,
        task_id: str | None = None,
        repo: str | None = None,
        operation: str | None = None,
    ) -> None:
        self.current = current
        self.target = target
        self.reason = reason
        msg = f"Invalid transition: {current} -> {target}"
        if reason:
            msg += f" ({reason})"
        super().__init__(
            msg, task_id=task_id, repo=repo, operation=operation,
        )


class ContextResolveError(WorkbenchError):
    """Raised when a context item cannot be resolved."""


class DatabaseError(WorkbenchError):
    """Raised when a database operation fails (connection, query, etc.)."""
