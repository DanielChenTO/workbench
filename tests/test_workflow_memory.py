"""Tests for workflow memory metadata CRUD helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from workbench.database import (
    WorkflowMemoryRow,
    create_workflow_memory,
    get_workflow_memory,
    list_workflow_memory,
    query_workflow_memory,
)


class TestCreateWorkflowMemory:
    @pytest.mark.asyncio
    async def test_create_and_refresh(self):
        session = MagicMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        row = await create_workflow_memory(
            session,
            repo="workbench",
            kind="summary",
            artifact_ref="artifact-1",
            tags='["pipeline","stage:implement"]',
            summary="Implementation summary",
            artifact_path="work-directory/references/memory.md",
            task_id="task123",
            pipeline_id="pipe123",
        )

        assert isinstance(row, WorkflowMemoryRow)
        assert row.repo == "workbench"
        assert row.kind == "summary"
        assert row.artifact_ref == "artifact-1"
        assert row.task_id == "task123"
        assert row.pipeline_id == "pipe123"
        session.add.assert_called_once()
        session.commit.assert_called_once()
        session.refresh.assert_called_once_with(row)


class TestGetWorkflowMemory:
    @pytest.mark.asyncio
    async def test_returns_row(self):
        mock_row = MagicMock(spec=WorkflowMemoryRow)
        session = AsyncMock()
        session.get.return_value = mock_row

        result = await get_workflow_memory(session, "mem-1")
        assert result is mock_row
        session.get.assert_called_once_with(WorkflowMemoryRow, "mem-1")


class TestListWorkflowMemory:
    @pytest.mark.asyncio
    async def test_returns_rows(self):
        mock_row1 = MagicMock(spec=WorkflowMemoryRow)
        mock_row2 = MagicMock(spec=WorkflowMemoryRow)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_row1, mock_row2]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        session = AsyncMock()
        session.execute.return_value = mock_result

        result = await list_workflow_memory(session)
        assert result == [mock_row1, mock_row2]
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_filters_compile(self):
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        session = AsyncMock()
        session.execute.return_value = mock_result

        since = datetime.now(UTC) - timedelta(hours=2)
        await list_workflow_memory(session, repo="workbench", kind="summary", since=since)

        stmt = session.execute.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "workflow_memory" in compiled
        assert "repo" in compiled
        assert "kind" in compiled
        assert "created_at" in compiled


class TestQueryWorkflowMemory:
    @pytest.mark.asyncio
    async def test_query_filters_compile(self):
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        session = AsyncMock()
        session.execute.return_value = mock_result

        await query_workflow_memory(
            session,
            repo="workbench",
            kind="summary",
            tag="pipeline",
            summary_query="implement",
            recent_hours=24,
            limit=20,
            offset=5,
        )

        stmt = session.execute.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "workflow_memory" in compiled
        assert "repo" in compiled
        assert "kind" in compiled
        assert "tags" in compiled
        assert "summary" in compiled
        assert "created_at" in compiled

    @pytest.mark.asyncio
    async def test_ignores_negative_recent_hours(self):
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        session = AsyncMock()
        session.execute.return_value = mock_result

        await query_workflow_memory(session, recent_hours=-1)

        stmt = session.execute.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "workflow_memory" in compiled
