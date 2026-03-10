"""Worker pool — concurrent task execution with git worktree isolation,
DB persistence, heartbeat tracking, FSM-guarded state transitions,
watchdog supervision, and blocked-state detection.

Concurrency model:
    Each task with full/local autonomy gets its own git worktree — an isolated
    checkout directory that shares the repo's .git database.  This allows
    multiple agents to work on the same repo simultaneously without blocking.

    The per-repo lock is only held briefly for merge operations (in the
    pipeline hook) when a pipeline completes and branches are merged back
    into the default branch.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime

from . import git_ops
from .config import settings
from .context import ResolvedContext, resolve_context
from .database import (
    TaskRow,
    async_session,
    check_dependencies_met,
    find_stale_active_tasks,
    load_queued_tasks,
    update_task,
)
from .exceptions import (
    ContextResolveError,
    ExecutorError,
    FSMTransitionError,
    TaskResolutionError,
)
from .executor import build_prompt, run_opencode
from .fsm import State, TaskFSM, fsm_from_row
from .models import Autonomy, ContextItem, TaskCreate
from .resolvers import resolve

# Backward-compatible aliases used in this module
TransitionError = FSMTransitionError
ResolveError = TaskResolutionError

log = logging.getLogger(__name__)

# How often (seconds) workers update the heartbeat timestamp in the DB.
HEARTBEAT_INTERVAL = 15

# How often (seconds) the watchdog scans for stuck tasks.
WATCHDOG_INTERVAL = 30

# Maximum size for per-task log subscriber queues.  When a queue is full
# (e.g. because the SSE client fell behind), new messages are dropped so that
# a slow or disconnected consumer can never cause unbounded memory growth.
# Now configurable via WORKBENCH_LOG_BUFFER_MAXSIZE (see config.py).

# Regex to detect BLOCKED: marker in opencode output.
# Convention: output line starting with "BLOCKED:" followed by the question.
BLOCKED_PATTERN = re.compile(r"^BLOCKED:\s*(.+)", re.MULTILINE)


class WorkerPool:
    """Async worker pool that processes tasks from a queue.

    Tasks are persisted in Postgres. The in-memory queue only holds task IDs
    for dispatch; all state mutations go through the FSM + database.
    """

    def __init__(self, max_workers: int | None = None) -> None:
        self.max_workers = max_workers or settings.max_workers
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()  # task IDs (None = sentinel)
        self._repo_locks: dict[str, asyncio.Lock] = {}
        self._workers: list[asyncio.Task] = []  # type: ignore[type-arg]
        self._running = False
        # Per-task FSM instances (in-memory, loaded from DB row on pickup)
        self._fsms: dict[str, TaskFSM] = {}
        # Log streaming: per-task async queues for SSE consumers
        self._log_buffers: dict[str, asyncio.Queue] = {}  # type: ignore[type-arg]
        # Watchdog background task
        self._watchdog_task: asyncio.Task | None = None  # type: ignore[type-arg]
        # Track active subprocesses so watchdog can kill stuck ones
        self._active_processes: dict[str, asyncio.subprocess.Process] = {}

    # --- Lifecycle ---

    async def start(self) -> None:
        """Start the worker pool, watchdog, and re-enqueue incomplete tasks from DB."""
        if self._running:
            return
        self._running = True

        # Re-enqueue tasks that were in-flight when the service last stopped
        async with async_session() as session:
            stale = await load_queued_tasks(session)
            for row in stale:
                log.info("Re-enqueuing incomplete task %s (status=%s)", row.id, row.status)
                # Reset to queued so workers pick them up cleanly
                await update_task(session, row.id, status="queued", started_at=None, phase=None)
                self._queue.put_nowait(row.id)

        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker_loop(i), name=f"worker-{i}")
            self._workers.append(worker)

        # Start watchdog supervisor
        self._watchdog_task = asyncio.create_task(self._watchdog_loop(), name="watchdog")

        log.info(
            "Worker pool started with %d workers, watchdog active (%d tasks re-enqueued)",
            self.max_workers,
            len(stale),
        )

    async def stop(self) -> None:
        """Gracefully stop all workers and the watchdog."""
        self._running = False

        # Stop watchdog
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass

        # Stop workers
        for _ in self._workers:
            await self._queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

        # Purge all remaining log buffers to release memory
        self._log_buffers.clear()
        self._fsms.clear()
        self._active_processes.clear()

        log.info("Worker pool stopped")

    # --- Task management ---

    def enqueue(self, task_id: str) -> None:
        """Add a task ID to the processing queue (task must already exist in DB)."""
        self._queue.put_nowait(task_id)

    async def cancel_task(self, task_id: str) -> None:
        """Kill the subprocess for a cancelled task and clean up.

        Called by the cancel endpoint after the DB status is set to cancelled.
        The running worker will detect the process exit and handle cleanup.
        """
        proc = self._active_processes.get(task_id)
        if proc is None or proc.returncode is not None:
            return

        log.info("Cancelling task %s: sending SIGTERM to pid %d", task_id, proc.pid)
        try:
            proc.terminate()
        except ProcessLookupError:
            return

        # Give the process a grace period to exit, then force-kill
        try:
            async with asyncio.timeout(5):
                await proc.wait()
                log.info("Task %s: process %d terminated gracefully", task_id, proc.pid)
        except TimeoutError:
            log.warning(
                "Task %s: process %d didn't exit after SIGTERM, sending SIGKILL", task_id, proc.pid
            )
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass

    def subscribe_logs(self, task_id: str) -> asyncio.Queue:
        """Get or create a log buffer for SSE streaming.

        Returns an asyncio.Queue that receives tuples:
          ("log", text)    — stdout/stderr output chunk
          ("phase", name)  — phase transition
          ("done", status)  — task completed
          ("error", msg)   — task failed

        The queue has a bounded maxsize (LOG_BUFFER_MAXSIZE) to prevent
        unbounded memory growth if the SSE client falls behind.
        """
        if task_id not in self._log_buffers:
            self._log_buffers[task_id] = asyncio.Queue(maxsize=settings.log_buffer_maxsize)
        return self._log_buffers[task_id]

    def unsubscribe_logs(self, task_id: str) -> None:
        """Remove the log buffer for a task (called when SSE client disconnects).

        Only removes the buffer if the task is in a terminal state (completed,
        failed, cancelled).  If the task is still active, the buffer is kept so
        that a subsequent SSE subscriber can pick up future events — including
        the final "done" event.  Buffers for active tasks are cleaned up when
        the task finishes (via _cleanup_task).
        """
        fsm = self._fsms.get(task_id)
        if fsm is not None and not fsm.is_terminal:
            # Task still running — keep the buffer alive so the next
            # subscriber doesn't miss the done event.
            return
        self._log_buffers.pop(task_id, None)

    def _emit_log(self, task_id: str, text: str) -> None:
        """Publish a log chunk to the task's SSE buffer (if subscribed).

        If the queue is full (slow/disconnected consumer), the oldest message
        is dropped to make room, preventing unbounded memory growth.
        """
        buf = self._log_buffers.get(task_id)
        if buf:
            try:
                buf.put_nowait(("log", text))
            except asyncio.QueueFull:
                # Drop the oldest item to make room
                try:
                    buf.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    buf.put_nowait(("log", text))
                except asyncio.QueueFull:
                    pass  # Queue is contended; skip this message

    def _emit_done(self, task_id: str, status: str, error: str | None = None) -> None:
        """Publish a completion event to the task's SSE buffer.

        Uses _safe_put to handle full queues gracefully.
        """
        buf = self._log_buffers.get(task_id)
        if buf:
            if error:
                self._safe_put(buf, ("error", error))
            self._safe_put(buf, ("done", status))

    @staticmethod
    def _safe_put(buf: asyncio.Queue, item: tuple) -> None:
        """Put an item into a bounded queue, dropping the oldest entry if full."""
        try:
            buf.put_nowait(item)
        except asyncio.QueueFull:
            try:
                buf.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                buf.put_nowait(item)
            except asyncio.QueueFull:
                pass

    # --- FSM-guarded state transition ---

    async def _transition(
        self,
        task_id: str,
        target: State,
        *,
        reason: str = "",
        extra_db_fields: dict | None = None,
    ) -> State:
        """Transition a task's state through the FSM and persist to DB.

        This is the ONLY way worker code should change task status.
        Raises TransitionError if the transition is invalid.
        """
        fsm = self._fsms.get(task_id)
        if fsm is None:
            raise TransitionError("unknown", target, f"no FSM for task {task_id}")

        new_state = fsm.transition(target, reason=reason)

        # Build DB update fields
        fields: dict = {"status": new_state.value}
        if target == State.BLOCKED:
            fields["blocked_reason"] = fsm.blocked_reason
        if target == State.QUEUED and fsm.retry_count > 0:
            fields["retry_count"] = fsm.retry_count
            fields["started_at"] = None
            fields["phase"] = None
        if extra_db_fields:
            fields.update(extra_db_fields)

        async with async_session() as session:
            await update_task(session, task_id, **fields)

        # Emit phase event for SSE
        buf = self._log_buffers.get(task_id)
        if buf:
            self._safe_put(buf, ("phase", f"status:{new_state.value}"))

        return new_state

    # --- Internal ---

    def _get_repo_lock(self, repo_name: str) -> asyncio.Lock:
        if repo_name not in self._repo_locks:
            self._repo_locks[repo_name] = asyncio.Lock()
        return self._repo_locks[repo_name]

    async def _worker_loop(self, worker_id: int) -> None:
        log.debug("Worker %d started", worker_id)
        while self._running:
            try:
                task_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except TimeoutError:
                continue

            if task_id is None:
                break

            await self._process_task(worker_id, task_id)

        log.debug("Worker %d stopped", worker_id)

    async def _db_update(self, task_id: str, **fields) -> None:
        """Convenience: update a task in a fresh session (for non-status fields)."""
        async with async_session() as session:
            await update_task(session, task_id, **fields)

    async def _set_phase(self, task_id: str, phase: str) -> None:
        """Update phase and heartbeat atomically (does NOT change status)."""
        now = datetime.now(UTC)
        await self._db_update(task_id, phase=phase, last_heartbeat=now)
        buf = self._log_buffers.get(task_id)
        if buf:
            self._safe_put(buf, ("phase", phase))

    async def _heartbeat_loop(self, task_id: str, stop_event: asyncio.Event) -> None:
        """Periodically update last_heartbeat while a task is being processed."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_INTERVAL)
                break  # Event was set, stop
            except TimeoutError:
                pass  # Interval elapsed, send heartbeat
            try:
                await self._db_update(task_id, last_heartbeat=datetime.now(UTC))
            except Exception:
                log.warning("Heartbeat update failed for task %s", task_id, exc_info=True)

    async def _process_task(self, worker_id: int, task_id: str) -> None:
        """Execute a single task end-to-end with FSM transitions and heartbeat."""
        log.info("[worker-%d] Processing task %s", worker_id, task_id)

        # Load task from DB and create FSM
        async with async_session() as session:
            row = await session.get(TaskRow, task_id)

        if row is None:
            log.error("[worker-%d] Task %s not found in DB, skipping", worker_id, task_id)
            return

        if row.status == "cancelled":
            log.info("[worker-%d] Task %s already cancelled, skipping", worker_id, task_id)
            return

        # --- Dependency check ---
        # If this task has depends_on, verify all dependencies are met before proceeding.
        if row.depends_on_json:
            async with async_session() as session:
                deps_met, deps_reason = await check_dependencies_met(session, task_id)
            if not deps_met:
                if deps_reason:
                    # A dependency failed/cancelled — fail this task
                    log.warning(
                        "[worker-%d] Task %s dependency not met: %s",
                        worker_id,
                        task_id,
                        deps_reason,
                    )
                    await self._db_update(
                        task_id,
                        status="failed",
                        error=deps_reason,
                        phase="failed",
                        completed_at=datetime.now(UTC),
                    )
                    self._emit_done(task_id, "failed", deps_reason)
                    return
                else:
                    # Dependencies not yet complete — re-enqueue with delay
                    log.info(
                        "[worker-%d] Task %s dependencies not ready, re-enqueuing in 5s",
                        worker_id,
                        task_id,
                    )
                    await asyncio.sleep(5)
                    self._queue.put_nowait(task_id)
                    return

        # Create in-memory FSM from DB row
        fsm = fsm_from_row(row)
        self._fsms[task_id] = fsm

        now = datetime.now(UTC)
        await self._db_update(task_id, started_at=now, last_heartbeat=now)

        # Start heartbeat background task
        stop_heartbeat = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(task_id, stop_heartbeat),
            name=f"heartbeat-{task_id}",
        )

        repo_name: str | None = row.repo
        branch_name = f"{settings.branch_prefix}/{task_id}"

        # Compute effective timeout: explicit per-task > role-based default > global default
        effective_timeout: int | None = getattr(row, "timeout", None)
        if effective_timeout is None:
            role = getattr(row, "role", "worker") or "worker"
            if role == "orchestrator":
                effective_timeout = settings.orchestrator_timeout
            else:
                effective_timeout = settings.task_timeout

        try:
            # --- Step 1: Resolve input ---
            await self._transition(task_id, State.RESOLVING)
            await self._set_phase(task_id, "resolving_input")

            task_input = TaskCreate(
                type=row.input_type,
                source=row.source,
                repo=row.repo,
                autonomy=row.autonomy,
                model=row.model,
                extra_instructions=row.extra_instructions,
                file_path=row.file_path,
                file_content=row.file_content,
                file_format=row.file_format,
            )
            resolved_text, inferred_repo = await resolve(task_input)
            await self._db_update(task_id, resolved_prompt=resolved_text)

            # Determine repo
            repo_name = repo_name or inferred_repo
            repo_path = settings.resolve_repo_path(repo_name) if repo_name else None

            if repo_path is None and row.autonomy in ("full", "local"):
                raise ResolveError(
                    f"Cannot determine target repository. "
                    f"Provided: {row.repo!r}, inferred: {inferred_repo!r}. "
                    f"Please specify a repo explicitly."
                )

            # --- Step 1b: Resolve context ---
            context_blocks: list[ResolvedContext] = []
            if row.context_json or row.parent_task_id:
                await self._set_phase(task_id, "resolving_context")
                import json as _json

                context_items: list[ContextItem] = []
                if row.context_json:
                    try:
                        context_items = [
                            ContextItem(**item) for item in _json.loads(row.context_json)
                        ]
                    except (ValueError, TypeError) as e:
                        log.warning(
                            "Task %s: invalid context_json, skipping: %s",
                            task_id,
                            e,
                        )
                try:
                    context_blocks = await resolve_context(
                        context_items,
                        parent_task_id=row.parent_task_id,
                    )
                    log.info(
                        "Task %s: resolved %d context blocks",
                        task_id,
                        len(context_blocks),
                    )
                except ContextResolveError as e:
                    log.warning(
                        "Task %s: context resolution failed: %s",
                        task_id,
                        e,
                    )

            # --- Step 2: Execute ---
            if repo_path and row.autonomy in ("full", "local"):
                await self._execute_with_git(
                    task_id,
                    row,
                    repo_path,
                    branch_name,
                    resolved_text,
                    context_blocks=context_blocks,
                    timeout=effective_timeout,
                )
            else:
                await self._execute_readonly(
                    task_id,
                    row,
                    resolved_text,
                    repo_path,
                    context_blocks=context_blocks,
                    timeout=effective_timeout,
                )

        except ResolveError as e:
            await self._fail_task(task_id, f"Resolution failed: {e}", worker_id)
        except ExecutorError as e:
            await self._fail_task(task_id, f"Execution failed: {e}", worker_id)
        except TransitionError as e:
            # FSM violation — this is a bug, log loudly
            log.exception("[worker-%d] Task %s FSM violation: %s", worker_id, task_id, e)
            error_msg = f"Internal error: invalid state transition: {e}"
            await self._fail_task(task_id, error_msg, worker_id)
        except Exception as e:
            await self._fail_task(task_id, f"Unexpected error: {e}", worker_id)
        finally:
            stop_heartbeat.set()
            await heartbeat_task
            await self._db_update(task_id, completed_at=datetime.now(UTC))
            # Generate and store a compact summary for downstream context chaining
            await self._generate_summary(task_id)
            # Pipeline hook: runs under repo lock so that merge operations
            # on pipeline completion don't race with each other.  With
            # worktrees, this is the ONLY place the repo lock is held —
            # task execution itself is lock-free.
            # The hook must run for ALL outcomes (success, failure, cancel)
            # because on_task_failed also needs to be called.
            if repo_name:
                repo_lock = self._get_repo_lock(repo_name)
                async with repo_lock:
                    await self._pipeline_hook(task_id)
            else:
                await self._pipeline_hook(task_id)
            # Clean up in-memory state for terminal tasks
            self._cleanup_task(task_id)
            log.info("[worker-%d] Task %s finished", worker_id, task_id)

    def _cleanup_task(self, task_id: str) -> None:
        """Clean up in-memory state for a task if it has reached a terminal state.

        Only removes FSM, active process, and log buffer entries when the task
        is terminal (completed, failed, cancelled).  Non-terminal tasks (e.g.
        blocked or re-queued after stuck) keep their entries so that SSE
        subscribers can still receive events and the FSM remains available.

        Called from both _process_task (finally) and _handle_stuck_task (finally)
        to ensure consistent cleanup behavior.
        """
        fsm = self._fsms.get(task_id)
        if fsm is not None and not fsm.is_terminal:
            # Task is not terminal (e.g. blocked, re-queued) — keep state alive
            return
        self._fsms.pop(task_id, None)
        self._active_processes.pop(task_id, None)
        self._log_buffers.pop(task_id, None)

    async def _fail_task(self, task_id: str, error_msg: str, worker_id: int) -> None:
        """Transition task to failed state with error message.

        If the task was already cancelled (e.g. by the cancel endpoint while
        the subprocess was running), preserve the cancelled state instead of
        overwriting it with failed.
        """
        # Check if already cancelled — don't overwrite with failed
        async with async_session() as session:
            row = await session.get(TaskRow, task_id)
        if row and row.status == "cancelled":
            log.info("[worker-%d] Task %s was cancelled, not marking as failed", worker_id, task_id)
            self._emit_done(task_id, "cancelled", "cancelled by user")
            return

        try:
            await self._transition(
                task_id,
                State.FAILED,
                reason=error_msg,
                extra_db_fields={"error": error_msg, "phase": "failed"},
            )
        except TransitionError:
            # Fallback: force-update DB even if FSM rejects (e.g. already terminal)
            log.warning("FSM rejected failed transition for %s, forcing DB update", task_id)
            await self._db_update(task_id, status="failed", error=error_msg, phase="failed")
        log.error("[worker-%d] Task %s failed: %s", worker_id, task_id, error_msg)
        self._emit_done(task_id, "failed", error_msg)

    async def _generate_summary(self, task_id: str) -> None:
        """Generate a compact summary of the task's output for downstream chaining.

        Extracts the first ~20 meaningful lines from the output as a simple
        heuristic summary. This avoids needing an LLM call for summarisation
        while still providing useful context to downstream tasks.
        """
        try:
            async with async_session() as session:
                row = await session.get(TaskRow, task_id)
            if row is None or not row.output:
                return

            # Simple heuristic: take the last non-empty lines (often the conclusion)
            # and first lines (often a summary header), capped at ~30 lines total
            lines = [ln for ln in row.output.splitlines() if ln.strip()]
            if len(lines) <= 30:
                summary = "\n".join(lines)
            else:
                # First 15 lines + last 15 lines
                summary_lines = lines[:15] + ["", "...", ""] + lines[-15:]
                summary = "\n".join(summary_lines)

            await self._db_update(task_id, summary=summary)
            log.debug("Task %s: summary generated (%d chars)", task_id, len(summary))
        except Exception:
            log.warning("Task %s: summary generation failed", task_id, exc_info=True)

    async def _pipeline_hook(self, task_id: str) -> None:
        """If this task belongs to a pipeline, notify the pipeline manager.

        Checks the task's final status and calls the appropriate hook:
        - completed → on_task_completed (may advance, loop, or complete the pipeline)
        - failed → on_task_failed (fails the pipeline)
        - cancelled/blocked — ignored (pipeline stays in current state)
        """
        try:
            async with async_session() as session:
                row = await session.get(TaskRow, task_id)
            if row is None or row.pipeline_id is None:
                return

            from .pipeline import on_task_completed, on_task_failed

            if row.status == "completed":
                await on_task_completed(task_id, self.enqueue)
            elif row.status == "failed":
                await on_task_failed(task_id)
            else:
                log.debug(
                    "Task %s (pipeline %s) ended with status=%s, no pipeline action",
                    task_id,
                    row.pipeline_id,
                    row.status,
                )
        except Exception:
            log.exception("Pipeline hook failed for task %s", task_id)

    async def _execute_with_git(
        self,
        task_id: str,
        row: TaskRow,
        repo_path,
        branch_name: str,
        resolved_text: str,
        *,
        context_blocks: list[ResolvedContext] | None = None,
        timeout: int | None = None,
    ) -> None:
        """Full/local autonomy: worktree, run opencode, commit, optionally push+PR.

        Uses git worktrees for isolation — each task gets its own checkout
        directory, so multiple tasks targeting the same repo run concurrently
        without blocking each other.  The repo lock is only held briefly for
        the final merge (local autonomy) or not at all (full autonomy pushes
        from the worktree).
        """
        from pathlib import Path as _Path

        worktree_path = _Path(settings.worktree_base_dir) / task_id

        # --- Create worktree (no lock needed — git handles concurrent adds) ---
        await self._set_phase(task_id, "creating_worktree")
        await git_ops.create_worktree(repo_path, worktree_path, branch_name)
        await self._transition(task_id, State.RUNNING, extra_db_fields={"branch": branch_name})

        try:
            # --- Execute opencode in the worktree ---
            await self._set_phase(task_id, "executing_opencode")
            prompt = build_prompt(
                resolved_text,
                Autonomy(row.autonomy),
                row.repo,
                row.extra_instructions,
                unblock_response=getattr(row, "unblock_response", None),
                context_blocks=context_blocks,
                worktree_path=str(worktree_path),
            )

            # Accumulate output for blocked-state detection
            output_lines: list[str] = []

            def log_callback(text: str) -> None:
                output_lines.append(text)
                self._emit_log(task_id, text)

            def process_callback(proc: asyncio.subprocess.Process) -> None:
                self._active_processes[task_id] = proc

            output = await run_opencode(
                prompt=prompt,
                work_dir=settings.workspace_root,
                model=row.model,
                timeout=timeout,
                log_callback=log_callback,
                process_callback=process_callback,
            )
            await self._db_update(task_id, output=output)

            # Check for BLOCKED marker in output
            full_output = "".join(output_lines)
            blocked_match = BLOCKED_PATTERN.search(full_output)
            if blocked_match:
                blocked_reason = blocked_match.group(1).strip()
                await self._transition(
                    task_id,
                    State.BLOCKED,
                    reason=blocked_reason,
                    extra_db_fields={"phase": "blocked"},
                )
                self._emit_done(task_id, "blocked", blocked_reason)
                return  # Don't proceed — wait for unblock

            # --- Commit changes in the worktree ---
            await self._set_phase(task_id, "checking_changes")
            if await git_ops.has_changes(worktree_path):
                await self._set_phase(task_id, "committing")
                await git_ops.add_and_commit(
                    worktree_path,
                    f"agent({task_id}): automated changes\n\nSource: {row.input_type}:{row.source}",
                )

            # --- Local autonomy: done (merge handled by pipeline hook) ---
            if row.autonomy == "local":
                await self._transition(
                    task_id,
                    State.COMPLETED,
                    extra_db_fields={"phase": "completed"},
                )
                self._emit_done(task_id, "completed")
                return

            # --- Full autonomy: push from worktree and create PR ---
            await self._set_phase(task_id, "pushing_branch")
            await self._transition(task_id, State.CREATING_PR)
            await git_ops.push_branch(worktree_path, branch_name)

            await self._set_phase(task_id, "creating_pr")
            pr_title = f"[WIP] agent/{task_id}: {self._make_pr_title(row)}"
            pr_body = self._make_pr_body(task_id, row, resolved_text)
            pr_url = await git_ops.create_draft_pr(repo_path, branch_name, pr_title, pr_body)
            await self._transition(
                task_id,
                State.COMPLETED,
                extra_db_fields={"pr_url": pr_url, "phase": "completed"},
            )
            self._emit_done(task_id, "completed")

        finally:
            # Always clean up the worktree
            await self._set_phase(task_id, "cleaning_worktree")
            await git_ops.remove_worktree(repo_path, worktree_path)

    async def _execute_readonly(
        self,
        task_id: str,
        row: TaskRow,
        resolved_text: str,
        repo_path=None,
        *,
        context_blocks: list[ResolvedContext] | None = None,
        timeout: int | None = None,
    ) -> None:
        """Research or plan-only mode — no git operations.

        Post-execution enforcement: if the agent modified files despite being
        in research/plan_only mode, the changes are discarded and a warning
        is logged. This prevents research tasks from producing code changes
        that conflict with implementation branches.
        """
        await self._transition(task_id, State.RUNNING)
        await self._set_phase(task_id, "executing_opencode")

        work_dir = repo_path or settings.workspace_root

        # Snapshot git status before execution for enforcement check
        pre_status: str | None = None
        if work_dir:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "status",
                    "--porcelain",
                    cwd=str(work_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                pre_status = stdout.decode(errors="replace")
            except Exception:
                pre_status = None

        prompt = build_prompt(
            resolved_text,
            Autonomy(row.autonomy),
            row.repo,
            row.extra_instructions,
            unblock_response=getattr(row, "unblock_response", None),
            context_blocks=context_blocks,
        )

        # Accumulate output for blocked-state detection
        output_lines: list[str] = []

        def log_callback(text: str) -> None:
            output_lines.append(text)
            self._emit_log(task_id, text)

        def process_callback(proc: asyncio.subprocess.Process) -> None:
            self._active_processes[task_id] = proc

        output = await run_opencode(
            prompt=prompt,
            work_dir=work_dir,
            model=row.model,
            timeout=timeout,
            log_callback=log_callback,
            process_callback=process_callback,
        )
        await self._db_update(task_id, output=output)

        # --- Autonomy enforcement: discard any file changes ---
        # Research/plan_only tasks should not modify files. If the agent
        # ignored the prompt guardrails, revert the changes to prevent
        # conflicts with implementation branches.
        if work_dir and pre_status is not None:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "status",
                    "--porcelain",
                    cwd=str(work_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                post_status = stdout.decode(errors="replace")
                if post_status != pre_status:
                    new_changes = set(post_status.strip().splitlines()) - set(
                        pre_status.strip().splitlines()
                    )
                    if new_changes:
                        log.warning(
                            "Task %s (%s mode) modified files — discarding changes: %s",
                            task_id,
                            row.autonomy,
                            [line.strip() for line in new_changes],
                        )
                        # Discard tracked file changes
                        await asyncio.create_subprocess_exec(
                            "git",
                            "checkout",
                            "--",
                            ".",
                            cwd=str(work_dir),
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        # Remove untracked files that weren't there before
                        for line in new_changes:
                            if line.startswith("??"):
                                untracked = line[3:].strip()
                                from pathlib import Path as _Path

                                try:
                                    _Path(str(work_dir)).joinpath(untracked).unlink(missing_ok=True)
                                except Exception:
                                    pass
            except Exception as e:
                log.warning(
                    "Task %s: post-execution autonomy check failed: %s",
                    task_id,
                    e,
                )

        # Check for BLOCKED marker
        full_output = "".join(output_lines)
        blocked_match = BLOCKED_PATTERN.search(full_output)
        if blocked_match:
            blocked_reason = blocked_match.group(1).strip()
            await self._transition(
                task_id,
                State.BLOCKED,
                reason=blocked_reason,
                extra_db_fields={"phase": "blocked"},
            )
            self._emit_done(task_id, "blocked", blocked_reason)
            return

        await self._transition(
            task_id,
            State.COMPLETED,
            extra_db_fields={"phase": "completed"},
        )
        self._emit_done(task_id, "completed")

    # --- Watchdog supervisor ---

    async def _watchdog_loop(self) -> None:
        """Background loop that scans for stuck tasks and handles them.

        Runs every WATCHDOG_INTERVAL seconds. For each stale task:
        1. Load FSM from DB row
        2. Transition to STUCK
        3. Kill subprocess if alive
        4. Retry (-> QUEUED) or fail (max retries exceeded)
        """
        log.info("Watchdog started (interval=%ds)", WATCHDOG_INTERVAL)
        while self._running:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL)
            except asyncio.CancelledError:
                break

            try:
                async with async_session() as session:
                    stale_rows = await find_stale_active_tasks(session)

                for row in stale_rows:
                    await self._handle_stuck_task(row)

            except Exception:
                log.exception("Watchdog scan failed")

    async def _handle_stuck_task(self, row: TaskRow) -> None:
        """Process a single stuck task: mark stuck, kill process, retry or fail."""
        task_id = row.id
        log.warning(
            "Watchdog: task %s appears stuck (status=%s, last_heartbeat=%s)",
            task_id,
            row.status,
            row.last_heartbeat,
        )

        # Create or update FSM
        fsm = self._fsms.get(task_id)
        if fsm is None:
            fsm = fsm_from_row(row)
            self._fsms[task_id] = fsm

        try:
            # Transition to STUCK
            fsm.mark_stuck()
            await self._db_update(task_id, status="stuck", phase="stuck")

            # Kill subprocess if we have a reference
            proc = self._active_processes.get(task_id)
            if proc and proc.returncode is None:
                log.info("Watchdog: killing subprocess for task %s (pid=%d)", task_id, proc.pid)
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

            # Emit event
            self._emit_done(task_id, "stuck", "watchdog: stale heartbeat detected")

            # Decide: retry or fail
            result = fsm.retry_or_fail()
            if result == State.QUEUED:
                await self._db_update(
                    task_id,
                    status="queued",
                    retry_count=fsm.retry_count,
                    started_at=None,
                    phase=None,
                )
                self._queue.put_nowait(task_id)
                log.info(
                    "Watchdog: task %s re-enqueued (retry %d/%d)",
                    task_id,
                    fsm.retry_count,
                    fsm.max_retries,
                )
            else:
                # Failed
                error_msg = f"Max retries exceeded ({fsm.retry_count}/{fsm.max_retries})"
                await self._db_update(
                    task_id,
                    status="failed",
                    error=error_msg,
                    phase="failed",
                    retry_count=fsm.retry_count,
                    completed_at=datetime.now(UTC),
                )
                self._emit_done(task_id, "failed", error_msg)
                log.error("Watchdog: task %s failed permanently: %s", task_id, error_msg)

        except TransitionError as e:
            log.warning("Watchdog: FSM rejected transition for task %s: %s", task_id, e)
        finally:
            self._cleanup_task(task_id)

    # --- Unblock support ---

    async def unblock_task(self, task_id: str, response: str) -> None:
        """Unblock a task that is waiting for human input.

        Called by the /unblock API endpoint. Stores the response in the DB
        and re-enqueues the task for processing.
        """
        async with async_session() as session:
            row = await session.get(TaskRow, task_id)

        if row is None:
            raise ValueError(f"Task {task_id} not found")

        if row.status != "blocked":
            raise ValueError(f"Task {task_id} is not blocked (status={row.status})")

        # Load or create FSM
        fsm = self._fsms.get(task_id)
        if fsm is None:
            fsm = fsm_from_row(row)
            self._fsms[task_id] = fsm

        # Transition: blocked -> running (FSM validates)
        fsm.unblock()

        # Store response and reset to queued for re-processing
        # (The worker will pick it up and see the unblock_response)
        await self._db_update(
            task_id,
            status="queued",
            unblock_response=response,
            blocked_reason=None,
            phase=None,
            started_at=None,
        )

        # Override FSM state to queued (we go blocked -> running -> queued conceptually,
        # but in practice we re-enqueue from scratch with the additional context)
        fsm.state = State.QUEUED

        self._queue.put_nowait(task_id)
        log.info("Task %s unblocked with response, re-enqueued", task_id)

    # --- PR helpers ---

    def _make_pr_title(self, row: TaskRow) -> str:
        source = row.source
        if row.input_type == "jira":
            return source.upper()
        if row.input_type == "github_issue":
            return source.split("/")[-1] if "/" in source else source
        return source[:80] + ("..." if len(source) > 80 else "")

    def _make_pr_body(self, task_id: str, row: TaskRow, resolved_prompt: str) -> str:
        lines = [
            "## Automated by workbench",
            "",
            f"**Task ID:** `{task_id}`",
            f"**Input type:** {row.input_type}",
            f"**Source:** {row.source}",
            f"**Autonomy:** {row.autonomy}",
            "",
            "> This PR was created automatically by workbench. "
            "It is in **draft** mode and marked **[WIP]**. "
            "Please review carefully before merging.",
        ]
        if resolved_prompt:
            lines.extend(
                [
                    "",
                    "<details>",
                    "<summary>Resolved prompt</summary>",
                    "",
                    resolved_prompt,
                    "",
                    "</details>",
                ]
            )
        return "\n".join(lines)
