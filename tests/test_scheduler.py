"""Unit tests for workbench.scheduler — cron-based scheduling."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workbench.scheduler import (
    CHECK_INTERVAL_SECONDS,
    Scheduler,
    compute_next_run,
    validate_cron_expr,
)


# ---------------------------------------------------------------------------
# compute_next_run
# ---------------------------------------------------------------------------

class TestComputeNextRun:
    def test_basic_daily_cron(self):
        """A daily-at-midnight cron should return a future UTC datetime."""
        result = compute_next_run("0 0 * * *", "UTC")
        assert result.tzinfo is not None
        assert result > datetime.now(UTC)

    def test_returns_utc(self):
        """Result should always be in UTC regardless of input timezone."""
        result = compute_next_run("0 22 * * *", "US/Pacific")
        assert result.tzinfo is not None
        # The UTC offset name could be "UTC" or "+00:00" depending on impl
        utc_result = result.astimezone(UTC)
        assert utc_result == result

    def test_respects_timezone(self):
        """Same cron in different timezones should produce different UTC times."""
        utc_result = compute_next_run("0 12 * * *", "UTC")
        pacific_result = compute_next_run("0 12 * * *", "US/Pacific")
        # 12:00 Pacific is 19:00 or 20:00 UTC (depending on DST)
        assert utc_result != pacific_result

    def test_after_parameter(self):
        """Passing an 'after' datetime should compute the next run after that time."""
        base = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        result = compute_next_run("0 12 * * *", "UTC", after=base)
        assert result > base
        assert result.hour == 12
        assert result.day == 15  # same day, since 12:00 is after 10:00

    def test_after_parameter_next_day(self):
        """If 'after' is past the cron time, result should be the next day."""
        base = datetime(2025, 6, 15, 14, 0, 0, tzinfo=UTC)
        result = compute_next_run("0 12 * * *", "UTC", after=base)
        assert result > base
        assert result.day == 16

    def test_every_5_minutes(self):
        """Frequent cron should return a time within 5 minutes."""
        result = compute_next_run("*/5 * * * *", "UTC")
        assert result - datetime.now(UTC) <= timedelta(minutes=5, seconds=5)

    def test_specific_weekday(self):
        """Cron for Monday-only should return a Monday."""
        result = compute_next_run("0 9 * * 1", "UTC")
        assert result.weekday() == 0  # Monday


# ---------------------------------------------------------------------------
# validate_cron_expr
# ---------------------------------------------------------------------------

class TestValidateCronExpr:
    def test_valid_expressions(self):
        assert validate_cron_expr("0 22 * * *") is True
        assert validate_cron_expr("*/5 * * * *") is True
        assert validate_cron_expr("0 22 * * 1-5") is True
        assert validate_cron_expr("30 8 1 * *") is True
        assert validate_cron_expr("0 0 * * 0") is True

    def test_invalid_expressions(self):
        assert validate_cron_expr("not a cron") is False
        assert validate_cron_expr("") is False
        assert validate_cron_expr("60 * * * *") is False  # minute > 59
        assert validate_cron_expr("* 25 * * *") is False  # hour > 23

    def test_six_field_rejected(self):
        """Standard 5-field cron; 6-field (with seconds) might or might not work
        depending on croniter config, but we test a clearly broken one."""
        assert validate_cron_expr("1 2 3 4 5 6 7") is False


# ---------------------------------------------------------------------------
# Scheduler._fire
# ---------------------------------------------------------------------------

class TestSchedulerFire:
    """Test the _fire method dispatches correctly and updates state."""

    def _make_schedule_row(self, **overrides):
        """Build a mock ScheduleRow."""
        defaults = {
            "id": "sched_001",
            "name": "test schedule",
            "cron_expr": "0 22 * * *",
            "timezone": "UTC",
            "schedule_type": "task",
            "payload_json": json.dumps({"type": "prompt", "source": "do stuff"}),
            "enabled": True,
            "run_count": 0,
            "last_run_at": None,
            "next_run_at": None,
            "last_task_id": None,
            "last_pipeline_id": None,
            "error": None,
        }
        defaults.update(overrides)
        row = MagicMock()
        for k, v in defaults.items():
            setattr(row, k, v)
        return row

    @pytest.mark.asyncio
    async def test_fire_task_schedule(self):
        """Firing a task schedule should call dispatch_task_fn."""
        dispatch_task = AsyncMock(return_value="task_abc")
        dispatch_pipeline = AsyncMock()
        sched = Scheduler(dispatch_task, dispatch_pipeline)
        row = self._make_schedule_row()

        with patch("workbench.scheduler.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("workbench.scheduler.update_schedule") as mock_update:
                with patch("workbench.scheduler.emit"):
                    await sched._fire(row)

        dispatch_task.assert_awaited_once()
        dispatch_pipeline.assert_not_awaited()
        # Verify update_schedule was called with run_count incremented
        call_kwargs = mock_update.call_args[1]
        assert call_kwargs["run_count"] == 1
        assert call_kwargs["last_task_id"] == "task_abc"
        assert call_kwargs["error"] is None

    @pytest.mark.asyncio
    async def test_fire_pipeline_schedule(self):
        """Firing a pipeline schedule should call dispatch_pipeline_fn."""
        dispatch_task = AsyncMock()
        dispatch_pipeline = AsyncMock(return_value="pipe_xyz")
        sched = Scheduler(dispatch_task, dispatch_pipeline)
        row = self._make_schedule_row(
            schedule_type="pipeline",
            payload_json=json.dumps({
                "repo": "my-service",
                "stages": [{"name": "explore", "prompt": "look around", "autonomy": "research"}],
            }),
        )

        with patch("workbench.scheduler.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("workbench.scheduler.update_schedule") as mock_update:
                with patch("workbench.scheduler.emit"):
                    await sched._fire(row)

        dispatch_pipeline.assert_awaited_once()
        dispatch_task.assert_not_awaited()
        call_kwargs = mock_update.call_args[1]
        assert call_kwargs["last_pipeline_id"] == "pipe_xyz"

    @pytest.mark.asyncio
    async def test_fire_invalid_json(self):
        """Invalid payload JSON should record error, not crash."""
        dispatch_task = AsyncMock()
        dispatch_pipeline = AsyncMock()
        sched = Scheduler(dispatch_task, dispatch_pipeline)
        row = self._make_schedule_row(payload_json="not valid json{{{")

        with patch("workbench.scheduler.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("workbench.scheduler.update_schedule") as mock_update:
                await sched._fire(row)

        dispatch_task.assert_not_awaited()
        # Should have recorded an error
        call_kwargs = mock_update.call_args[1]
        assert "Invalid payload" in call_kwargs["error"]

    @pytest.mark.asyncio
    async def test_fire_dispatch_exception(self):
        """If dispatch raises, error should be recorded."""
        dispatch_task = AsyncMock(side_effect=RuntimeError("boom"))
        dispatch_pipeline = AsyncMock()
        sched = Scheduler(dispatch_task, dispatch_pipeline)
        row = self._make_schedule_row()

        with patch("workbench.scheduler.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("workbench.scheduler.update_schedule") as mock_update:
                with patch("workbench.scheduler.emit"):
                    await sched._fire(row)

        call_kwargs = mock_update.call_args[1]
        assert "Dispatch failed" in call_kwargs["error"]
        assert call_kwargs["run_count"] == 1


# ---------------------------------------------------------------------------
# Scheduler._tick
# ---------------------------------------------------------------------------

class TestSchedulerTick:
    @pytest.mark.asyncio
    async def test_tick_no_due_schedules(self):
        """When nothing is due, _tick should be a no-op."""
        sched = Scheduler(AsyncMock(), AsyncMock())

        with patch("workbench.scheduler.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("workbench.scheduler.get_due_schedules", return_value=[]):
                await sched._tick()

        # Nothing should have been dispatched
        sched._dispatch_task.assert_not_awaited()
        sched._dispatch_pipeline.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tick_fires_due_schedules(self):
        """Due schedules should trigger _fire for each one."""
        sched = Scheduler(AsyncMock(return_value="t1"), AsyncMock())

        mock_row = MagicMock()
        mock_row.id = "s1"
        mock_row.name = "test"
        mock_row.cron_expr = "0 22 * * *"
        mock_row.timezone = "UTC"
        mock_row.schedule_type = "task"
        mock_row.payload_json = json.dumps({"type": "prompt", "source": "hello"})
        mock_row.run_count = 5
        mock_row.last_task_id = None
        mock_row.last_pipeline_id = None
        mock_row.error = None
        mock_row.enabled = True

        with patch("workbench.scheduler.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("workbench.scheduler.get_due_schedules", return_value=[mock_row]):
                with patch("workbench.scheduler.update_schedule"):
                    with patch("workbench.scheduler.emit"):
                        await sched._tick()

        sched._dispatch_task.assert_awaited_once()


# ---------------------------------------------------------------------------
# Scheduler start/stop lifecycle
# ---------------------------------------------------------------------------

class TestSchedulerLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Scheduler should start and stop cleanly."""
        sched = Scheduler(AsyncMock(), AsyncMock())
        assert not sched.running

        # Patch the loop to exit immediately
        with patch.object(sched, "_loop", new_callable=AsyncMock):
            await sched.start()
            assert sched._task is not None
            await sched.stop()
            assert sched._task is None

    @pytest.mark.asyncio
    async def test_double_start_ignored(self):
        """Calling start twice should not create a second task."""
        sched = Scheduler(AsyncMock(), AsyncMock())
        with patch.object(sched, "_loop", new_callable=AsyncMock):
            await sched.start()
            first_task = sched._task
            await sched.start()  # second call
            assert sched._task is first_task
            await sched.stop()


# ---------------------------------------------------------------------------
# Scheduler.trigger_now
# ---------------------------------------------------------------------------

class TestSchedulerTriggerNow:
    @pytest.mark.asyncio
    async def test_trigger_not_found(self):
        """Triggering a non-existent schedule should raise ValueError."""
        sched = Scheduler(AsyncMock(), AsyncMock())

        with patch("workbench.scheduler.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("workbench.database.get_schedule", return_value=None):
                with pytest.raises(ValueError, match="not found"):
                    await sched.trigger_now("nonexistent")
