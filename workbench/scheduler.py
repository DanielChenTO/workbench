"""Scheduler — cron-based background loop that dispatches tasks and pipelines.

Runs as an asyncio background task alongside the worker pool. Every 60 seconds,
checks for schedules whose next_run_at has passed and dispatches their payloads.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from croniter import croniter

from .database import (
    ScheduleRow,
    async_session,
    get_due_schedules,
    update_schedule,
)
from .events import emit

log = logging.getLogger(__name__)

# How often the scheduler loop checks for due schedules.
CHECK_INTERVAL_SECONDS = 60


def compute_next_run(cron_expr: str, timezone: str, after: datetime | None = None) -> datetime:
    """Compute the next run time for a cron expression.

    Returns a timezone-aware UTC datetime.
    """
    import zoneinfo

    if after is None:
        after = datetime.now(UTC)

    tz = zoneinfo.ZoneInfo(timezone)
    # croniter works in the schedule's local timezone
    local_now = after.astimezone(tz)
    cron = croniter(cron_expr, local_now)
    local_next = cron.get_next(datetime)

    # Convert back to UTC
    return local_next.astimezone(UTC)


def validate_cron_expr(cron_expr: str) -> bool:
    """Check if a cron expression is valid."""
    return croniter.is_valid(cron_expr)


class Scheduler:
    """Background scheduler that checks for and dispatches due schedules."""

    def __init__(self, dispatch_task_fn, dispatch_pipeline_fn):
        """Initialize the scheduler.

        Args:
            dispatch_task_fn: async callable(payload_dict) -> task_id
                Called to dispatch a task schedule. Receives the stored
                TaskCreate payload as a dict.
            dispatch_pipeline_fn: async callable(payload_dict) -> pipeline_id
                Called to dispatch a pipeline schedule. Receives the stored
                PipelineCreate payload as a dict.
        """
        self._dispatch_task = dispatch_task_fn
        self._dispatch_pipeline = dispatch_pipeline_fn
        self._task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        """Start the scheduler background loop."""
        if self._task is not None:
            log.warning("Scheduler already running")
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="scheduler-loop")
        log.info("Scheduler started (check interval: %ds)", CHECK_INTERVAL_SECONDS)

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("Scheduler stopped")

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _loop(self) -> None:
        """Main scheduler loop — runs until stopped."""
        log.info("Scheduler loop started")
        while not self._stopping:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Scheduler tick failed")

            try:
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break
        log.info("Scheduler loop exited")

    async def _tick(self) -> None:
        """Check for due schedules and dispatch them."""
        now = datetime.now(UTC)
        async with async_session() as session:
            due = await get_due_schedules(session, now)

        if not due:
            return

        log.info("Scheduler: %d schedule(s) due", len(due))
        for schedule in due:
            await self._fire(schedule)

    async def _fire(self, schedule: ScheduleRow) -> None:
        """Dispatch a single schedule's payload and update its state."""
        log.info(
            "Scheduler: firing schedule %s (%s) — type=%s, cron=%s",
            schedule.id, schedule.name, schedule.schedule_type, schedule.cron_expr,
        )

        try:
            payload = json.loads(schedule.payload_json)
        except (json.JSONDecodeError, TypeError) as e:
            log.error("Schedule %s: invalid payload JSON: %s", schedule.id, e)
            async with async_session() as session:
                await update_schedule(session, schedule.id, error=f"Invalid payload: {e}")
            return

        dispatched_id = None
        error_msg = None

        try:
            if schedule.schedule_type == "task":
                dispatched_id = await self._dispatch_task(payload)
            elif schedule.schedule_type == "pipeline":
                dispatched_id = await self._dispatch_pipeline(payload)
            else:
                error_msg = f"Unknown schedule_type: {schedule.schedule_type}"
                log.error("Schedule %s: %s", schedule.id, error_msg)
        except Exception as e:
            error_msg = f"Dispatch failed: {e}"
            log.exception("Schedule %s: dispatch failed", schedule.id)

        # Compute next run time
        next_run = compute_next_run(schedule.cron_expr, schedule.timezone)

        # Update schedule state
        update_fields: dict = {
            "last_run_at": datetime.now(UTC),
            "next_run_at": next_run,
            "run_count": schedule.run_count + 1,
            "error": error_msg,
        }
        if dispatched_id:
            if schedule.schedule_type == "task":
                update_fields["last_task_id"] = dispatched_id
            else:
                update_fields["last_pipeline_id"] = dispatched_id

        async with async_session() as session:
            await update_schedule(session, schedule.id, **update_fields)

        if dispatched_id:
            await emit(
                "schedule_fired",
                schedule_id=schedule.id,
                detail=f"dispatched {schedule.schedule_type} {dispatched_id}",
            )
            log.info(
                "Schedule %s (%s): dispatched %s %s, next run at %s",
                schedule.id, schedule.name, schedule.schedule_type,
                dispatched_id, next_run.isoformat(),
            )
        elif error_msg:
            await emit(
                "schedule_error",
                schedule_id=schedule.id,
                detail=error_msg,
            )

    async def trigger_now(self, schedule_id: str) -> str | None:
        """Manually trigger a schedule immediately, regardless of its cron time.

        Returns the dispatched task/pipeline ID, or None on failure.
        """
        async with async_session() as session:
            from .database import get_schedule
            schedule = await get_schedule(session, schedule_id)

        if schedule is None:
            raise ValueError(f"Schedule {schedule_id} not found")

        await self._fire(schedule)

        # Return the dispatched ID
        async with async_session() as session:
            from .database import get_schedule
            updated = await get_schedule(session, schedule_id)
            if updated and updated.schedule_type == "task":
                return updated.last_task_id
            elif updated:
                return updated.last_pipeline_id
        return None
