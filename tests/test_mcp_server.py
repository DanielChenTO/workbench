"""Tests for workbench.mcp_server — MCP tool registration, handlers, and error handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from workbench.mcp_server import (
    _TOOL_MAP,
    TOOLS,
    handle_call_tool,
    handle_list_tools,
    server,
)

# ---------------------------------------------------------------------------
# Expected tool names — must match the spec exactly
# ---------------------------------------------------------------------------

EXPECTED_TOOL_NAMES = [
    "list_tasks",
    "get_task",
    "create_task",
    "cancel_task",
    "unblock_task",
    "list_pipelines",
    "get_pipeline",
    "create_pipeline",
    "list_schedules",
    "create_schedule",
    "update_schedule",
    "delete_schedule",
    "morning_report",
    "health_check",
    "list_todos",
    "create_todo",
    "update_todo",
]


# ---------------------------------------------------------------------------
# Tool registration tests
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify all expected tools are registered with valid schemas."""

    def test_all_tools_registered(self):
        """All 17 expected tools are present."""
        registered_names = [t.name for t in TOOLS]
        assert registered_names == EXPECTED_TOOL_NAMES

    def test_tool_count(self):
        assert len(TOOLS) == 17

    def test_all_tools_have_descriptions(self):
        for tool in TOOLS:
            assert tool.description, f"Tool {tool.name} has no description"

    def test_all_tools_have_input_schemas(self):
        for tool in TOOLS:
            schema = tool.inputSchema
            assert isinstance(schema, dict), f"Tool {tool.name} has no inputSchema"
            assert schema.get("type") == "object", (
                f"Tool {tool.name} inputSchema type should be 'object'"
            )

    def test_required_fields_present(self):
        """Tools with required params declare them correctly."""
        # get_task requires task_id
        schema = _TOOL_MAP["get_task"].inputSchema
        assert "task_id" in schema.get("required", [])

        # create_task requires type and source
        schema = _TOOL_MAP["create_task"].inputSchema
        assert "type" in schema.get("required", [])
        assert "source" in schema.get("required", [])

        # unblock_task requires task_id and response
        schema = _TOOL_MAP["unblock_task"].inputSchema
        assert "task_id" in schema.get("required", [])
        assert "response" in schema.get("required", [])

        # create_pipeline requires stages
        schema = _TOOL_MAP["create_pipeline"].inputSchema
        assert "stages" in schema.get("required", [])

        # create_schedule requires name, cron_expr, schedule_type, payload
        schema = _TOOL_MAP["create_schedule"].inputSchema
        required = schema.get("required", [])
        assert "name" in required
        assert "cron_expr" in required
        assert "schedule_type" in required
        assert "payload" in required

    def test_tool_map_matches_list(self):
        assert len(_TOOL_MAP) == len(TOOLS)
        for tool in TOOLS:
            assert _TOOL_MAP[tool.name] is tool

    async def test_list_tools_handler(self):
        """The list_tools MCP handler returns all tools."""
        tools = await handle_list_tools()
        assert tools is TOOLS
        assert len(tools) == 17


# ---------------------------------------------------------------------------
# Input schema validation tests
# ---------------------------------------------------------------------------


class TestInputSchemas:
    """Verify input schemas have valid JSON Schema structure."""

    @pytest.mark.parametrize("tool", TOOLS, ids=[t.name for t in TOOLS])
    def test_schema_is_valid_json_schema(self, tool):
        """Each tool's inputSchema is a valid JSON Schema object."""
        schema = tool.inputSchema
        assert isinstance(schema, dict)
        assert schema["type"] == "object"
        # properties should be a dict (even if empty)
        props = schema.get("properties", {})
        assert isinstance(props, dict)

    def test_list_tasks_schema(self):
        schema = _TOOL_MAP["list_tasks"].inputSchema
        props = schema["properties"]
        assert "status" in props
        assert "limit" in props
        assert props["limit"]["type"] == "integer"

    def test_create_task_schema(self):
        schema = _TOOL_MAP["create_task"].inputSchema
        props = schema["properties"]
        assert "type" in props
        assert props["type"]["enum"] == ["prompt", "jira", "github_issue", "prompt_file"]
        assert "source" in props
        assert "repo" in props
        assert "autonomy" in props
        assert "context" in props

    def test_create_pipeline_schema(self):
        schema = _TOOL_MAP["create_pipeline"].inputSchema
        props = schema["properties"]
        assert "stages" in props
        assert props["stages"]["type"] == "array"
        # Stage items should have required fields
        stage_props = props["stages"]["items"]["properties"]
        assert "name" in stage_props
        assert "autonomy" in stage_props
        assert "prompt" in stage_props


# ---------------------------------------------------------------------------
# Helper to build mock httpx responses
# ---------------------------------------------------------------------------


def _mock_response(status_code: int = 200, json_data: dict | list | None = None) -> httpx.Response:
    """Create a mock httpx.Response with the given status and JSON body."""
    resp = httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "http://test"),
    )
    return resp


# ---------------------------------------------------------------------------
# Tool handler tests — each tool with mocked HTTP
# ---------------------------------------------------------------------------


class TestListTasks:
    async def test_returns_formatted_tasks(self):
        mock_data = {
            "tasks": [
                {
                    "id": "task-001",
                    "status": "completed",
                    "input": {"repo": "my-repo", "type": "prompt", "source": "do stuff"},
                    "pr_url": "https://github.com/org/repo/pull/1",
                    "error": None,
                },
                {
                    "id": "task-002",
                    "status": "running",
                    "input": {"repo": None, "type": "jira", "source": "PROJ-123"},
                    "pr_url": None,
                    "error": None,
                },
            ],
            "total": 2,
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(200, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("list_tasks", {"limit": 20})

        assert len(result) == 1
        text = result[0].text
        assert "task-001" in text
        assert "task-002" in text
        assert "completed" in text
        assert "my-repo" in text
        assert "pull/1" in text

    async def test_empty_task_list(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_mock_response(200, {"tasks": [], "total": 0})
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("list_tasks", {})

        assert "No tasks found" in result[0].text


class TestGetTask:
    async def test_returns_task_details(self):
        mock_data = {
            "id": "task-001",
            "status": "completed",
            "input": {"type": "prompt", "source": "implement feature X", "repo": "my-repo"},
            "phase": "done",
            "branch": "workbench/task-001",
            "pr_url": "https://github.com/org/repo/pull/42",
            "summary": "Implemented feature X with tests",
            "error": None,
            "stale": False,
            "created_at": "2025-01-01T00:00:00Z",
            "started_at": "2025-01-01T00:01:00Z",
            "completed_at": "2025-01-01T00:05:00Z",
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(200, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("get_task", {"task_id": "task-001"})

        text = result[0].text
        assert "task-001" in text
        assert "completed" in text
        assert "my-repo" in text
        assert "workbench/task-001" in text
        assert "pull/42" in text
        assert "Implemented feature X" in text


class TestCreateTask:
    async def test_creates_task(self):
        mock_data = {"id": "task-new", "status": "queued"}
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(201, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("create_task", {
                "type": "prompt",
                "source": "do something",
                "repo": "my-repo",
                "autonomy": "full",
            })

        text = result[0].text
        assert "task-new" in text
        assert "queued" in text

        # Verify the POST body
        call_args = mock_client.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body["type"] == "prompt"
        assert body["source"] == "do something"
        assert body["repo"] == "my-repo"


class TestCancelTask:
    async def test_cancels_task(self):
        mock_data = {"status": "cancelled", "task_id": "task-001"}
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(200, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("cancel_task", {"task_id": "task-001"})

        assert "cancelled" in result[0].text


class TestUnblockTask:
    async def test_unblocks_task(self):
        mock_data = {"status": "unblocked", "task_id": "task-001"}
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(200, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("unblock_task", {
                "task_id": "task-001",
                "response": "yes, proceed",
            })

        assert "unblocked" in result[0].text

        # Verify response body
        call_args = mock_client.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body["response"] == "yes, proceed"


class TestListPipelines:
    async def test_returns_pipelines(self):
        mock_data = {
            "pipelines": [
                {
                    "id": "pipe-001",
                    "status": "running",
                    "repo": "my-repo",
                    "stages": [
                        {"name": "explore", "autonomy": "research", "prompt": "..."},
                        {"name": "implement", "autonomy": "full", "prompt": "..."},
                    ],
                    "current_stage_index": 1,
                    "error": None,
                },
            ],
            "total": 1,
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(200, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("list_pipelines", {})

        text = result[0].text
        assert "pipe-001" in text
        assert "running" in text
        assert "stage 2/2" in text


class TestGetPipeline:
    async def test_returns_pipeline_details(self):
        mock_data = {
            "id": "pipe-001",
            "status": "running",
            "repo": "my-repo",
            "stages": [
                {"name": "explore", "autonomy": "research", "prompt": "find stuff"},
                {
                    "name": "implement",
                    "autonomy": "full",
                    "prompt": "build it",
                    "review_gate": True,
                },
            ],
            "current_stage_index": 1,
            "current_task_id": "task-005",
            "task_ids": ["task-004", "task-005"],
            "review_iteration": 1,
            "max_review_iterations": 3,
            "error": None,
            "created_at": "2025-01-01T00:00:00Z",
            "completed_at": None,
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(200, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("get_pipeline", {"pipeline_id": "pipe-001"})

        text = result[0].text
        assert "pipe-001" in text
        assert "running" in text
        assert "explore" in text
        assert "implement" in text
        assert "review-gate" in text
        assert "task-005" in text


class TestCreatePipeline:
    async def test_creates_pipeline(self):
        mock_data = {
            "id": "pipe-new",
            "status": "running",
            "stages": [
                {"name": "research", "autonomy": "research", "prompt": "..."},
                {"name": "implement", "autonomy": "full", "prompt": "..."},
            ],
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(201, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("create_pipeline", {
                "stages": [
                    {"name": "research", "autonomy": "research", "prompt": "look around"},
                    {"name": "implement", "autonomy": "full", "prompt": "build it"},
                ],
            })

        text = result[0].text
        assert "pipe-new" in text
        assert "research" in text
        assert "implement" in text


class TestListSchedules:
    async def test_returns_schedules(self):
        mock_data = {
            "schedules": [
                {
                    "id": "sched-001",
                    "name": "Nightly build",
                    "cron_expr": "0 22 * * *",
                    "timezone": "US/Pacific",
                    "enabled": True,
                    "next_run_at": "2025-01-02T06:00:00Z",
                },
            ],
            "total": 1,
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(200, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("list_schedules", {})

        text = result[0].text
        assert "sched-001" in text
        assert "Nightly build" in text
        assert "0 22 * * *" in text


class TestCreateSchedule:
    async def test_creates_schedule(self):
        mock_data = {
            "id": "sched-new",
            "name": "Daily cleanup",
            "cron_expr": "0 3 * * *",
            "timezone": "UTC",
            "next_run_at": "2025-01-02T03:00:00Z",
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(201, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("create_schedule", {
                "name": "Daily cleanup",
                "cron_expr": "0 3 * * *",
                "schedule_type": "task",
                "payload": {"type": "prompt", "source": "run cleanup"},
            })

        text = result[0].text
        assert "sched-new" in text
        assert "Daily cleanup" in text


class TestUpdateSchedule:
    async def test_updates_schedule(self):
        mock_data = {
            "id": "sched-001",
            "name": "Updated schedule",
            "cron_expr": "0 4 * * *",
        }
        mock_client = AsyncMock()
        mock_client.patch = AsyncMock(return_value=_mock_response(200, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("update_schedule", {
                "schedule_id": "sched-001",
                "name": "Updated schedule",
            })

        assert "sched-001" in result[0].text
        assert "Updated schedule" in result[0].text


class TestDeleteSchedule:
    async def test_deletes_schedule(self):
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=_mock_response(200, {"status": "deleted"}))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("delete_schedule", {
                "schedule_id": "sched-001",
            })

        assert "sched-001" in result[0].text
        assert "deleted" in result[0].text


class TestMorningReport:
    async def test_returns_report(self):
        mock_data = {
            "hours": 12,
            "summary": (
                "Workbench report for the last 12 hours:\n"
                "  5 tasks dispatched, 3 completed, 1 failed"
            ),
            "prs": [
                {
                    "repo": "my-repo",
                    "pr_url": "https://github.com/org/repo/pull/10",
                    "summary": "Added feature",
                },
            ],
            "failed_tasks": [
                {"id": "task-bad", "error": "compilation error in main.go"},
            ],
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(200, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("morning_report", {"hours": 12})

        text = result[0].text
        assert "5 tasks dispatched" in text
        assert "pull/10" in text
        assert "compilation error" in text


class TestHealthCheck:
    async def test_returns_health(self):
        mock_data = {
            "status": "ok",
            "database": "connected",
            "workers": 4,
            "workspace": "/path/to/workspace",
            "repos": ["repo-a", "repo-b"],
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(200, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("health_check", {})

        text = result[0].text
        assert "ok" in text
        assert "connected" in text
        assert "repo-a" in text


class TestListTodos:
    async def test_returns_todos(self):
        mock_data = {
            "todos": [
                {"id": "todo-1", "title": "Fix bug", "status": "pending", "priority": "high"},
                {"id": "todo-2", "title": "Add tests", "status": "completed", "priority": "medium"},
            ],
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(200, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("list_todos", {})

        text = result[0].text
        assert "Fix bug" in text
        assert "Add tests" in text
        assert "pending" in text

    async def test_empty_todos(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_mock_response(200, {"todos": []})
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("list_todos", {})

        assert "No todos found" in result[0].text


class TestCreateTodo:
    async def test_creates_todo(self):
        mock_data = {"id": "todo-new", "title": "Write docs"}
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(201, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("create_todo", {
                "title": "Write docs",
                "priority": "high",
            })

        text = result[0].text
        assert "todo-new" in text
        assert "Write docs" in text


class TestUpdateTodo:
    async def test_updates_todo(self):
        mock_data = {"id": "todo-1", "title": "Fix bug", "status": "completed"}
        mock_client = AsyncMock()
        mock_client.patch = AsyncMock(return_value=_mock_response(200, mock_data))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("update_todo", {
                "todo_id": "todo-1",
                "status": "completed",
            })

        text = result[0].text
        assert "todo-1" in text


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_connection_refused(self):
        """Connection error returns helpful message instead of crashing."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("health_check", {})

        text = result[0].text
        assert "cannot connect" in text.lower()

    async def test_api_404(self):
        """HTTP 404 returns the error as text content."""
        mock_resp = _mock_response(404, {"detail": "Task not-exist not found"})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("get_task", {"task_id": "not-exist"})

        # The 404 response doesn't raise_for_status automatically for httpx.Response
        # constructed this way, but the handler calls raise_for_status()
        # which will trigger HTTPStatusError — caught by the handler
        assert len(result) == 1

    async def test_api_500(self):
        """HTTP 500 returns the error gracefully."""
        mock_resp = _mock_response(500, {"detail": "Internal server error"})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("health_check", {})

        assert len(result) == 1

    async def test_unknown_tool(self):
        """Unknown tool name returns a helpful message."""
        result = await handle_call_tool("nonexistent_tool", {})
        assert "Unknown tool" in result[0].text
        assert "nonexistent_tool" in result[0].text

    async def test_none_arguments(self):
        """Passing None for arguments doesn't crash."""
        mock_client = AsyncMock()
        health_data = {
            "status": "ok",
            "database": "connected",
            "workers": 4,
            "workspace": "/tmp",
            "repos": [],
        }
        mock_client.get = AsyncMock(
            return_value=_mock_response(200, health_data)
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.mcp_server.httpx.AsyncClient", return_value=mock_client):
            result = await handle_call_tool("health_check", None)

        assert len(result) == 1
        assert "ok" in result[0].text


# ---------------------------------------------------------------------------
# Server configuration tests
# ---------------------------------------------------------------------------


class TestServerConfig:
    def test_server_name(self):
        assert server.name == "workbench"

    def test_workbench_url_default(self):
        from workbench.mcp_server import WORKBENCH_URL

        # WORKBENCH_URL should be a valid URL string
        assert WORKBENCH_URL.startswith("http")
