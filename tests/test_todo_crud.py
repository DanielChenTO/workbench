"""Tests for todo CRUD operations in workbench.database and workbench.models."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from workbench.database import (
    TodoRow,
    count_todos,
    delete_todo,
    get_todo,
    list_todos,
    shift_todo_siblings,
    update_todo,
)
from workbench.main import (
    _extract_repo_hints,
    _task_matches_todo,
    list_todo_coverage_route,
    reorder_todo_route,
    update_todo_route,
)
from workbench.models import (
    Autonomy,
    TaskCreate,
    TaskInfo,
    TaskInputType,
    TaskStatus,
    TodoCoverageInfo,
    TodoCoverageListResponse,
    TodoCoverageSummary,
    TodoCreate,
    TodoInfo,
    TodoListResponse,
    TodoReorder,
    TodoUpdate,
    todo_row_to_info,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_todo_row(**overrides) -> SimpleNamespace:
    """Build a minimal fake TodoRow suitable for todo_row_to_info."""
    now = datetime.now(UTC)
    defaults = dict(
        id="todo-001",
        title="Test todo",
        description="A test todo item",
        status="backlog",
        priority="medium",
        column_order=0,
        tags=None,
        jira_key=None,
        jira_url=None,
        jira_status=None,
        jira_last_synced=None,
        source="manual",
        source_ref=None,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ===========================================================================
# Pydantic model tests
# ===========================================================================


class TestTodoCreate:
    def test_minimal(self):
        tc = TodoCreate(title="Buy milk")
        assert tc.title == "Buy milk"
        assert tc.description is None
        assert tc.status == "backlog"
        assert tc.priority == "medium"
        assert tc.tags is None

    def test_all_fields(self):
        tc = TodoCreate(
            title="Implement feature",
            description="Add the thing",
            status="todo",
            priority="high",
            tags=["backend", "api"],
        )
        assert tc.title == "Implement feature"
        assert tc.description == "Add the thing"
        assert tc.status == "todo"
        assert tc.priority == "high"
        assert tc.tags == ["backend", "api"]

    def test_title_required(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TodoCreate()  # type: ignore[call-arg]


class TestTodoUpdate:
    def test_all_none_by_default(self):
        tu = TodoUpdate()
        assert tu.title is None
        assert tu.description is None
        assert tu.status is None
        assert tu.priority is None
        assert tu.tags is None
        assert tu.column_order is None

    def test_partial_update(self):
        tu = TodoUpdate(title="New title", status="in_progress")
        assert tu.title == "New title"
        assert tu.status == "in_progress"
        assert tu.description is None

    def test_tags_update(self):
        tu = TodoUpdate(tags=["urgent", "feature"])
        assert tu.tags == ["urgent", "feature"]

    def test_column_order_update(self):
        tu = TodoUpdate(column_order=5)
        assert tu.column_order == 5


class TestTodoReorder:
    def test_construction(self):
        tr = TodoReorder(status="in_progress", order=3)
        assert tr.status == "in_progress"
        assert tr.order == 3

    def test_required_fields(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TodoReorder()  # type: ignore[call-arg]

        with pytest.raises(ValidationError):
            TodoReorder(status="todo")  # type: ignore[call-arg]

        with pytest.raises(ValidationError):
            TodoReorder(order=1)  # type: ignore[call-arg]


class TestTodoInfo:
    def test_minimal(self):
        now = datetime.now(UTC)
        info = TodoInfo(
            id="todo-1",
            title="Test",
            created_at=now,
            updated_at=now,
        )
        assert info.id == "todo-1"
        assert info.title == "Test"
        assert info.status == "backlog"
        assert info.priority == "medium"
        assert info.column_order == 0
        assert info.tags is None
        assert info.jira_key is None
        assert info.source == "manual"

    def test_all_fields(self):
        now = datetime.now(UTC)
        info = TodoInfo(
            id="todo-2",
            title="Full todo",
            description="Detailed desc",
            status="in_progress",
            priority="high",
            column_order=3,
            tags=["backend"],
            jira_key="PROJ-42",
            jira_url="https://jira.test/PROJ-42",
            jira_status="In Progress",
            jira_last_synced=now,
            source="jira",
            source_ref="PROJ-42",
            created_at=now,
            updated_at=now,
        )
        assert info.description == "Detailed desc"
        assert info.tags == ["backend"]
        assert info.jira_key == "PROJ-42"
        assert info.source == "jira"


# ===========================================================================
# todo_row_to_info converter tests
# ===========================================================================


class TestTodoRowToInfo:
    def test_basic_conversion(self):
        row = _make_todo_row()
        info = todo_row_to_info(row)
        assert info.id == "todo-001"
        assert info.title == "Test todo"
        assert info.description == "A test todo item"
        assert info.status == "backlog"
        assert info.priority == "medium"
        assert info.column_order == 0
        assert info.tags is None
        assert info.source == "manual"

    def test_tags_json_parsed(self):
        row = _make_todo_row(tags=json.dumps(["backend", "api"]))
        info = todo_row_to_info(row)
        assert info.tags == ["backend", "api"]

    def test_tags_none(self):
        row = _make_todo_row(tags=None)
        info = todo_row_to_info(row)
        assert info.tags is None

    def test_tags_invalid_json(self):
        """Malformed tags JSON should not crash — returns None."""
        row = _make_todo_row(tags="not valid json{{{")
        info = todo_row_to_info(row)
        assert info.tags is None

    def test_tags_empty_string(self):
        """Empty string tags should result in None (falsy)."""
        row = _make_todo_row(tags="")
        info = todo_row_to_info(row)
        assert info.tags is None

    def test_jira_fields_mapped(self):
        now = datetime.now(UTC)
        row = _make_todo_row(
            jira_key="PROJ-99",
            jira_url="https://jira.test/PROJ-99",
            jira_status="In Progress",
            jira_last_synced=now,
            source="jira",
            source_ref="PROJ-99",
        )
        info = todo_row_to_info(row)
        assert info.jira_key == "PROJ-99"
        assert info.jira_url == "https://jira.test/PROJ-99"
        assert info.jira_status == "In Progress"
        assert info.jira_last_synced == now
        assert info.source == "jira"
        assert info.source_ref == "PROJ-99"

    def test_returns_todo_info_instance(self):
        row = _make_todo_row()
        info = todo_row_to_info(row)
        assert isinstance(info, TodoInfo)


# ===========================================================================
# Database CRUD function tests (mocked async sessions)
# ===========================================================================


class TestGetTodo:
    @pytest.mark.asyncio
    async def test_returns_todo_when_found(self):
        """get_todo should return the row when found."""
        mock_row = MagicMock(spec=TodoRow)
        mock_row.id = "todo-123"

        session = AsyncMock()
        session.get.return_value = mock_row

        result = await get_todo(session, "todo-123")
        assert result is mock_row
        session.get.assert_called_once_with(TodoRow, "todo-123")

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        """get_todo should return None when not found."""
        session = AsyncMock()
        session.get.return_value = None

        result = await get_todo(session, "nonexistent")
        assert result is None


class TestListTodos:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        """list_todos should return a list of TodoRow objects."""
        mock_row1 = MagicMock(spec=TodoRow)
        mock_row2 = MagicMock(spec=TodoRow)

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_row1, mock_row2]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        session = AsyncMock()
        session.execute.return_value = mock_result

        result = await list_todos(session)
        assert len(result) == 2
        assert result[0] is mock_row1
        assert result[1] is mock_row2

    @pytest.mark.asyncio
    async def test_filter_by_status(self):
        """list_todos with status filter should pass it to the query."""
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        session = AsyncMock()
        session.execute.return_value = mock_result

        result = await list_todos(session, status="in_progress")
        assert result == []
        # Verify execute was called (the filter is applied via SQLAlchemy query)
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_filter_by_source(self):
        """list_todos with source filter should pass it to the query."""
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        session = AsyncMock()
        session.execute.return_value = mock_result

        result = await list_todos(session, source="jira")
        assert result == []
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_filter_by_status_and_source(self):
        """list_todos with both filters should apply both."""
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        session = AsyncMock()
        session.execute.return_value = mock_result

        result = await list_todos(session, status="todo", source="manual")
        assert result == []
        session.execute.assert_called_once()


class TestDeleteTodo:
    @pytest.mark.asyncio
    async def test_returns_true_when_deleted(self):
        """delete_todo should return True when the todo exists."""
        mock_row = MagicMock(spec=TodoRow)

        session = AsyncMock()
        session.get.return_value = mock_row

        result = await delete_todo(session, "todo-123")
        assert result is True
        session.delete.assert_called_once_with(mock_row)
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self):
        """delete_todo should return False when the todo doesn't exist."""
        session = AsyncMock()
        session.get.return_value = None

        result = await delete_todo(session, "nonexistent")
        assert result is False
        session.delete.assert_not_called()
        session.commit.assert_not_called()


class TestReorderTodo:
    """Test that update_todo can be used to reorder (update status + column_order)."""

    @pytest.mark.asyncio
    async def test_reorder_updates_status_and_column_order(self):
        """Reorder should update both status and column_order via update_todo."""
        mock_row = MagicMock(spec=TodoRow)
        mock_row.id = "todo-123"
        mock_row.status = "in_progress"
        mock_row.column_order = 5

        session = AsyncMock()
        session.execute.return_value = MagicMock()
        session.get.return_value = mock_row

        result = await update_todo(
            session,
            "todo-123",
            status="in_progress",
            column_order=5,
        )

        # update_todo calls execute (for the UPDATE stmt), commit, then get
        session.execute.assert_called_once()
        session.commit.assert_called_once()
        session.get.assert_called_once_with(TodoRow, "todo-123")
        assert result is mock_row


# ===========================================================================
# update_todo injects updated_at
# ===========================================================================


class TestUpdateTodoTimestamp:
    """Verify update_todo auto-injects updated_at=func.now()."""

    @pytest.mark.asyncio
    async def test_updated_at_injected_when_not_provided(self):
        """update_todo should add updated_at=func.now() to values."""

        session = AsyncMock()
        session.execute.return_value = MagicMock()
        session.get.return_value = MagicMock(spec=TodoRow)

        await update_todo(session, "todo-001", title="New title")

        # The execute call receives a SQLAlchemy Update statement — check it was called
        session.execute.assert_called_once()
        stmt = session.execute.call_args[0][0]
        # The compiled statement should include updated_at
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "updated_at" in compiled

    @pytest.mark.asyncio
    async def test_updated_at_not_overwritten_when_provided(self):
        """If caller explicitly passes updated_at, it should not be overwritten."""
        from datetime import datetime

        fixed_time = datetime(2025, 1, 1, tzinfo=UTC)

        session = AsyncMock()
        session.execute.return_value = MagicMock()
        session.get.return_value = MagicMock(spec=TodoRow)

        await update_todo(session, "todo-001", title="X", updated_at=fixed_time)

        # The statement should still contain updated_at (the caller's value)
        stmt = session.execute.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "updated_at" in compiled


# ===========================================================================
# shift_todo_siblings
# ===========================================================================


class TestShiftTodoSiblings:
    """Tests for the shift_todo_siblings helper."""

    @pytest.mark.asyncio
    async def test_shifts_siblings_at_target_position(self):
        """shift_todo_siblings should execute an UPDATE for siblings."""
        session = AsyncMock()
        session.execute.return_value = MagicMock()

        await shift_todo_siblings(session, target_status="todo", target_order=2)

        session.execute.assert_called_once()
        stmt = session.execute.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "todos" in compiled
        assert "column_order" in compiled

    @pytest.mark.asyncio
    async def test_excludes_specified_id(self):
        """shift_todo_siblings with exclude_id should add a != filter."""
        session = AsyncMock()
        session.execute.return_value = MagicMock()

        await shift_todo_siblings(
            session,
            target_status="in_progress",
            target_order=0,
            exclude_id="todo-skip",
        )

        session.execute.assert_called_once()
        # Verify the statement was constructed (compile check)
        stmt = session.execute.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "column_order" in compiled

    @pytest.mark.asyncio
    async def test_no_exclude_id(self):
        """shift_todo_siblings without exclude_id should still work."""
        session = AsyncMock()
        session.execute.return_value = MagicMock()

        await shift_todo_siblings(
            session,
            target_status="done",
            target_order=5,
        )

        session.execute.assert_called_once()


# ===========================================================================
# count_todos
# ===========================================================================


class TestCountTodos:
    """Tests for the count_todos helper."""

    @pytest.mark.asyncio
    async def test_returns_scalar_count(self):
        """count_todos should return the scalar count from the query."""
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 42

        session = AsyncMock()
        session.execute.return_value = mock_result

        result = await count_todos(session)
        assert result == 42
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_filters_by_status(self):
        """count_todos with status filter should apply it."""
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 5

        session = AsyncMock()
        session.execute.return_value = mock_result

        result = await count_todos(session, status="in_progress")
        assert result == 5

    @pytest.mark.asyncio
    async def test_filters_by_source(self):
        """count_todos with source filter should apply it."""
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 3

        session = AsyncMock()
        session.execute.return_value = mock_result

        result = await count_todos(session, source="jira")
        assert result == 3

    @pytest.mark.asyncio
    async def test_zero_count(self):
        """count_todos should return 0 when no rows match."""
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0

        session = AsyncMock()
        session.execute.return_value = mock_result

        result = await count_todos(session, status="nonexistent")
        assert result == 0


# ===========================================================================
# Todo mutation route guardrails
# ===========================================================================


class TestTodoMutationRoutes:
    @pytest.mark.asyncio
    async def test_update_route_returns_404_when_update_returns_none(self):
        """PATCH route should fail closed with 404, not 500."""
        session = AsyncMock()
        existing = _make_todo_row(id="todo-404")

        with patch("workbench.main.async_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("workbench.main.get_todo", AsyncMock(return_value=existing)):
                with patch("workbench.main.update_todo", AsyncMock(return_value=None)):
                    with pytest.raises(HTTPException) as exc:
                        await update_todo_route("todo-404", TodoUpdate(status="done"))

        assert exc.value.status_code == 404
        assert exc.value.detail == "Todo todo-404 not found"

    @pytest.mark.asyncio
    async def test_reorder_route_returns_404_when_update_returns_none(self):
        """Reorder route should fail closed with 404, not 500."""
        session = AsyncMock()
        existing = _make_todo_row(id="todo-404", status="todo", column_order=1)

        with patch("workbench.main.async_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("workbench.main.get_todo", AsyncMock(return_value=existing)):
                with patch("workbench.main.update_todo", AsyncMock(return_value=None)):
                    with patch("workbench.database.shift_todo_siblings", AsyncMock()):
                        with pytest.raises(HTTPException) as exc:
                            await reorder_todo_route(
                                "todo-404",
                                TodoReorder(status="review", order=0),
                            )

        assert exc.value.status_code == 404
        assert exc.value.detail == "Todo todo-404 not found"


# ===========================================================================
# TodoListResponse model
# ===========================================================================


class TestTodoListResponse:
    """Tests for the TodoListResponse Pydantic model."""

    def test_basic_construction(self):
        """TodoListResponse should hold a list of TodoInfo and a total."""
        now = datetime.now(UTC)
        todo = TodoInfo(
            id="t1",
            title="Test",
            status="backlog",
            priority="high",
            column_order=0,
            source="manual",
            created_at=now,
            updated_at=now,
        )
        resp = TodoListResponse(todos=[todo], total=1)
        assert len(resp.todos) == 1
        assert resp.total == 1
        assert resp.todos[0].id == "t1"

    def test_empty_list(self):
        """TodoListResponse with empty list should work."""
        resp = TodoListResponse(todos=[], total=0)
        assert resp.todos == []
        assert resp.total == 0

    def test_total_can_differ_from_list_length(self):
        """total represents the full count, not just the page."""
        now = datetime.now(UTC)
        todo = TodoInfo(
            id="t1",
            title="Test",
            status="backlog",
            priority="medium",
            column_order=0,
            source="manual",
            created_at=now,
            updated_at=now,
        )
        resp = TodoListResponse(todos=[todo], total=100)
        assert len(resp.todos) == 1
        assert resp.total == 100


def _make_task_info(
    *,
    id: str,
    source: str,
    status: TaskStatus,
    repo: str | None = None,
    summary: str | None = None,
    pipeline_id: str | None = None,
    stage_name: str | None = None,
    created_at: datetime | None = None,
) -> TaskInfo:
    now = created_at or datetime.now(UTC)
    return TaskInfo(
        id=id,
        input=TaskCreate(
            type=TaskInputType.PROMPT,
            source=source,
            repo=repo,
            autonomy=Autonomy.LOCAL,
        ),
        status=status,
        phase=None,
        branch=None,
        pr_url=None,
        resolved_prompt=None,
        output=None,
        summary=summary,
        error=None,
        retry_count=0,
        max_retries=3,
        blocked_reason=None,
        unblock_response=None,
        parent_task_id=None,
        pipeline_id=pipeline_id,
        stage_name=stage_name,
        depends_on=[],
        dependencies_met=True,
        created_at=now,
        started_at=now,
        completed_at=None,
        last_heartbeat=now,
        stale=False,
        role="worker",
        timeout=None,
    )


class TestTodoCoverageHelpers:
    def test_extract_repo_hints_prefers_existing_metadata(self):
        now = datetime.now(UTC)
        todo = TodoInfo(
            id="todo-1",
            title="Ship feature",
            source_ref="terraform-enterprise/workbench: EPIC-99",
            tags=["initiative:workspace-sync", "repo:terraform-enterprise", "foo/bar"],
            created_at=now,
            updated_at=now,
        )

        hints = _extract_repo_hints(todo)

        assert "terraform-enterprise/workbench" in hints
        assert "terraform-enterprise" in hints
        assert "foo/bar" in hints

    def test_task_matches_todo_by_jira_key(self):
        now = datetime.now(UTC)
        todo = TodoInfo(
            id="todo-1",
            title="Fix PROD-12",
            jira_key="PROD-12",
            created_at=now,
            updated_at=now,
        )
        task = _make_task_info(
            id="task-1",
            source="Investigate PROD-12 rollout",
            status=TaskStatus.RUNNING,
        )

        assert _task_matches_todo(todo, task, repo_hints=[]) is True

    def test_task_does_not_match_short_source_ref_substring(self):
        now = datetime.now(UTC)
        todo = TodoInfo(
            id="todo-1",
            title="Core tracking",
            source_ref="core",
            created_at=now,
            updated_at=now,
        )
        task = _make_task_info(
            id="task-1",
            source="Investigate hardcore retry behavior",
            status=TaskStatus.RUNNING,
        )

        assert _task_matches_todo(todo, task, repo_hints=[]) is False

    def test_task_matches_exact_repo_hint_via_task_repo_field(self):
        now = datetime.now(UTC)
        todo = TodoInfo(
            id="todo-1",
            title="Repo-bound work",
            tags=["repo:terraform-enterprise"],
            created_at=now,
            updated_at=now,
        )
        task = _make_task_info(
            id="task-1",
            source="Investigate rollout",
            status=TaskStatus.RUNNING,
            repo="terraform-enterprise",
        )

        assert _task_matches_todo(todo, task, repo_hints=["terraform-enterprise"]) is True


class TestTodoCoverageRoute:
    @pytest.mark.asyncio
    async def test_returns_linked_and_unlinked_coverage(self):
        session = AsyncMock()

        linked_todo = _make_todo_row(
            id="todo-linked",
            title="Fix checkout",
            jira_key="PROJ-42",
            tags=json.dumps(["initiative:checkout"]),
        )
        unlinked_todo = _make_todo_row(
            id="todo-unlinked", title="Write docs", tags=json.dumps(["initiative:docs"])
        )

        linked_task = _make_task_info(
            id="task-linked",
            source="Implement PROJ-42 bug fix",
            status=TaskStatus.RUNNING,
            repo="workbench",
            pipeline_id="pipe-1",
            stage_name="implement",
            summary="Investigating and fixing checkout crash",
        )
        unrelated_task = _make_task_info(
            id="task-other",
            source="Unrelated maintenance",
            status=TaskStatus.COMPLETED,
            repo="atlas",
        )

        with patch("workbench.main.async_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "workbench.main.list_todos", AsyncMock(return_value=[linked_todo, unlinked_todo])
            ):
                with patch(
                    "workbench.main.list_tasks_since",
                    AsyncMock(
                        return_value=[
                            SimpleNamespace(id="recent-1"),
                            SimpleNamespace(id="recent-2"),
                        ]
                    ),
                ):
                    with patch("workbench.main.list_tasks", AsyncMock(return_value=([], 0))):
                        with patch(
                            "workbench.main.list_pipelines_since",
                            AsyncMock(return_value=[SimpleNamespace(id="pipe-1")]),
                        ):
                            row_to_task = {
                                "recent-1": linked_task,
                                "recent-2": unrelated_task,
                            }

                            def _task_row_to_info_side_effect(row):
                                return row_to_task[row.id]

                            with patch(
                                "workbench.main.task_row_to_info",
                                side_effect=_task_row_to_info_side_effect,
                            ):
                                result = await list_todo_coverage_route(recent_hours=72)

        assert isinstance(result, TodoCoverageListResponse)
        assert isinstance(result.summary, TodoCoverageSummary)
        assert result.summary.total_todos == 2
        assert result.summary.covered_todos == 1
        assert result.summary.uncovered_todos == 1
        assert result.summary.active_linked_todos == 1

        by_id = {c.todo_id: c for c in result.coverages}
        assert by_id["todo-linked"].needs_task is False
        assert by_id["todo-linked"].related_active_task_count == 1
        assert by_id["todo-linked"].related_pipeline_count == 1
        assert isinstance(by_id["todo-linked"], TodoCoverageInfo)
        assert by_id["todo-linked"].active_tasks[0].id == "task-linked"

        assert by_id["todo-unlinked"].needs_task is True
        assert by_id["todo-unlinked"].related_recent_task_count == 0

    @pytest.mark.asyncio
    async def test_includes_active_tasks_older_than_recent_cutoff(self):
        session = AsyncMock()

        linked_todo = _make_todo_row(
            id="todo-linked",
            title="Long-running rollout",
            jira_key="PROJ-77",
        )
        very_old = datetime.now(UTC).replace(year=2024)
        active_old_task = _make_task_info(
            id="task-old-active",
            source="Continue PROJ-77 migration",
            status=TaskStatus.RUNNING,
            repo="workbench",
            created_at=very_old,
        )

        async def _list_tasks_side_effect(_session, *, status=None, limit=50, offset=0):
            if status == "running":
                return [SimpleNamespace(id="active-1")], 1
            return [], 0

        with patch("workbench.main.async_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("workbench.main.list_todos", AsyncMock(return_value=[linked_todo])):
                with patch("workbench.main.list_tasks_since", AsyncMock(return_value=[])):
                    with patch(
                        "workbench.main.list_tasks",
                        AsyncMock(side_effect=_list_tasks_side_effect),
                    ):
                        with patch(
                            "workbench.main.list_pipelines_since",
                            AsyncMock(return_value=[]),
                        ):
                            with patch(
                                "workbench.main.task_row_to_info",
                                side_effect=[active_old_task],
                            ):
                                result = await list_todo_coverage_route(recent_hours=1)

        by_id = {c.todo_id: c for c in result.coverages}
        assert by_id["todo-linked"].needs_task is False
        assert by_id["todo-linked"].related_active_task_count == 1
        assert by_id["todo-linked"].related_recent_task_count == 0
        assert result.summary.covered_todos == 1
        assert result.summary.uncovered_todos == 0

    @pytest.mark.asyncio
    async def test_rejects_non_positive_recent_hours(self):
        with pytest.raises(HTTPException) as exc:
            await list_todo_coverage_route(recent_hours=0)

        assert exc.value.status_code == 400
        assert "recent_hours" in exc.value.detail
