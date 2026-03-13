"""Unit tests for workbench.worker behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from workbench.worker import WorkerPool


@pytest.mark.asyncio
async def test_find_progress_stalled_tasks_returns_active_stalled_rows():
    worker = WorkerPool(max_workers=1)
    worker._last_progress["task-stalled"] = datetime.now(UTC) - timedelta(seconds=10_000)
    worker._last_progress["task-fresh"] = datetime.now(UTC)

    stalled_row = SimpleNamespace(id="task-stalled", status="running")
    fresh_row = SimpleNamespace(id="task-fresh", status="running")

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(side_effect=[stalled_row, fresh_row])
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_session
    mock_ctx.__aexit__.return_value = False

    with patch("workbench.worker.async_session", return_value=mock_ctx):
        rows = await worker._find_progress_stalled_tasks()

    assert rows == [stalled_row]


def test_set_phase_marks_progress_timestamp() -> None:
    worker = WorkerPool(max_workers=1)
    before = datetime.now(UTC)
    worker._mark_progress("task-123")
    assert worker._last_progress["task-123"] >= before
