"""MCP (Model Context Protocol) server for the workbench API.

Wraps the workbench REST API as MCP tools so AI assistants can discover
and use workbench capabilities via MCP tool discovery.

Run standalone:  python -m workbench.mcp_server
Via CLI:         workbench mcp
"""

from __future__ import annotations

import os

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

WORKBENCH_URL = os.environ.get("WORKBENCH_URL", "http://127.0.0.1:8420")

server = Server("workbench")

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    # -- Task Management --
    Tool(
        name="list_tasks",
        description=(
            "List workbench tasks, optionally filtered by status. "
            "Returns task summaries with id, status, repo, and creation time."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Filter by status: queued, resolving, running, creating_pr, "
                        "blocked, stuck, completed, failed, cancelled"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of tasks to return (default 20)",
                    "default": 20,
                },
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_task",
        description=(
            "Get full details for a specific task including status, output, "
            "branch, PR URL, and error information."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID to look up",
                },
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="create_task",
        description=(
            "Create a new workbench task for autonomous execution. "
            "Supports prompt, jira, github_issue, and prompt_file types."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["prompt", "jira", "github_issue", "prompt_file"],
                    "description": "Task input type",
                },
                "source": {
                    "type": "string",
                    "description": (
                        "Jira key, GitHub issue URL, or plain-text prompt"
                    ),
                },
                "repo": {
                    "type": "string",
                    "description": "Target repository short name (optional)",
                },
                "autonomy": {
                    "type": "string",
                    "enum": ["full", "local", "plan_only", "research"],
                    "description": "Autonomy level (default: full)",
                    "default": "full",
                },
                "extra_instructions": {
                    "type": "string",
                    "description": "Additional instructions appended to the agent prompt",
                },
                "context": {
                    "type": "array",
                    "description": "Context items to inject into the agent prompt",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["task_output", "reference", "file", "text"],
                            },
                            "task_id": {"type": "string"},
                            "doc": {"type": "string"},
                            "section": {"type": "string"},
                            "path": {"type": "string"},
                            "lines": {"type": "string"},
                            "content": {"type": "string"},
                            "label": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["type", "source"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="cancel_task",
        description="Cancel a running or queued task.",
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID to cancel",
                },
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="unblock_task",
        description=(
            "Unblock a task that is waiting for human input by providing "
            "a response to the blocked question."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID to unblock",
                },
                "response": {
                    "type": "string",
                    "description": "The response text to unblock the task with",
                },
            },
            "required": ["task_id", "response"],
            "additionalProperties": False,
        },
    ),
    # -- Pipeline Management --
    Tool(
        name="list_pipelines",
        description=(
            "List multi-stage pipelines, optionally filtered by status. "
            "Returns pipeline summaries with stage progress."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Filter by status: pending, running, completed, failed, cancelled"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of pipelines to return (default 20)",
                    "default": 20,
                },
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_pipeline",
        description=(
            "Get full details for a specific pipeline including stage "
            "configurations, current progress, and task IDs."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pipeline_id": {
                    "type": "string",
                    "description": "The pipeline ID to look up",
                },
            },
            "required": ["pipeline_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="create_pipeline",
        description=(
            "Create and start a multi-stage pipeline with ordered stages, "
            "optional review gates, and configurable autonomy per stage."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Target repository for all stages",
                },
                "stages": {
                    "type": "array",
                    "description": "Ordered list of pipeline stages",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Human-readable stage name",
                            },
                            "autonomy": {
                                "type": "string",
                                "enum": ["full", "local", "plan_only", "research"],
                                "description": "Autonomy level for this stage",
                            },
                            "prompt": {
                                "type": "string",
                                "description": "Prompt text for this stage",
                            },
                            "review_gate": {
                                "type": "boolean",
                                "description": "Parse output for APPROVE/REJECT verdict",
                                "default": False,
                            },
                            "loop_to": {
                                "type": "integer",
                                "description": "Stage index to loop back to on rejection",
                            },
                        },
                        "required": ["name", "autonomy", "prompt"],
                    },
                },
                "max_review_iterations": {
                    "type": "integer",
                    "description": "Max review rejection loops (default 3)",
                    "default": 3,
                },
            },
            "required": ["stages"],
            "additionalProperties": False,
        },
    ),
    # -- Schedule Management --
    Tool(
        name="list_schedules",
        description="List all cron schedules for recurring task/pipeline dispatch.",
        inputSchema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    Tool(
        name="create_schedule",
        description=(
            "Create a new cron schedule for recurring task or pipeline dispatch."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Human-readable name for this schedule",
                },
                "cron_expr": {
                    "type": "string",
                    "description": (
                        "5-field cron expression "
                        "(e.g. '0 22 * * *' for daily at 10pm)"
                    ),
                },
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone (default: US/Pacific)",
                    "default": "US/Pacific",
                },
                "schedule_type": {
                    "type": "string",
                    "enum": ["task", "pipeline"],
                    "description": "What to dispatch: task or pipeline",
                },
                "payload": {
                    "type": "object",
                    "description": (
                        "Task or pipeline definition to dispatch on each trigger"
                    ),
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Whether the schedule is active (default true)",
                    "default": True,
                },
            },
            "required": ["name", "cron_expr", "schedule_type", "payload"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="update_schedule",
        description="Update fields on an existing schedule (partial update).",
        inputSchema={
            "type": "object",
            "properties": {
                "schedule_id": {
                    "type": "string",
                    "description": "The schedule ID to update",
                },
                "name": {"type": "string", "description": "New name"},
                "cron_expr": {"type": "string", "description": "New cron expression"},
                "timezone": {"type": "string", "description": "New timezone"},
                "enabled": {"type": "boolean", "description": "Enable/disable"},
                "payload": {"type": "object", "description": "New payload"},
            },
            "required": ["schedule_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="delete_schedule",
        description="Permanently delete a schedule.",
        inputSchema={
            "type": "object",
            "properties": {
                "schedule_id": {
                    "type": "string",
                    "description": "The schedule ID to delete",
                },
            },
            "required": ["schedule_id"],
            "additionalProperties": False,
        },
    ),
    # -- Reporting & Status --
    Tool(
        name="morning_report",
        description=(
            "Generate a summary report of all workbench activity in the last N hours. "
            "Includes completed tasks, failures, PRs created, and pipeline status."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "How many hours back to report on (default 12)",
                    "default": 12,
                },
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="health_check",
        description=(
            "Check workbench service health status, database connectivity, "
            "and worker config."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    # -- Todo Management (future endpoints) --
    Tool(
        name="list_todos",
        description="List todo items, optionally filtered by status or source.",
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by todo status",
                },
                "source": {
                    "type": "string",
                    "description": "Filter by source",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of todos to return (default 20)",
                    "default": 20,
                },
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="create_todo",
        description="Create a new todo item.",
        inputSchema={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title of the todo",
                },
                "description": {
                    "type": "string",
                    "description": "Detailed description",
                },
                "status": {
                    "type": "string",
                    "description": "Initial status (default: pending)",
                    "default": "pending",
                },
                "priority": {
                    "type": "string",
                    "description": "Priority level: low, medium, high",
                    "default": "medium",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization",
                },
            },
            "required": ["title"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="update_todo",
        description="Update fields on an existing todo item (partial update).",
        inputSchema={
            "type": "object",
            "properties": {
                "todo_id": {
                    "type": "string",
                    "description": "The todo ID to update",
                },
                "title": {"type": "string"},
                "description": {"type": "string"},
                "status": {"type": "string"},
                "priority": {"type": "string"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["todo_id"],
            "additionalProperties": False,
        },
    ),
]

# Build a name -> Tool lookup for the call_tool handler
_TOOL_MAP: dict[str, Tool] = {t.name: t for t in TOOLS}


# ---------------------------------------------------------------------------
# MCP handlers
# ---------------------------------------------------------------------------


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    """Dispatch an MCP tool call to the appropriate workbench API endpoint."""
    arguments = arguments or {}

    try:
        if name == "list_tasks":
            return await _list_tasks(arguments)
        elif name == "get_task":
            return await _get_task(arguments)
        elif name == "create_task":
            return await _create_task(arguments)
        elif name == "cancel_task":
            return await _cancel_task(arguments)
        elif name == "unblock_task":
            return await _unblock_task(arguments)
        elif name == "list_pipelines":
            return await _list_pipelines(arguments)
        elif name == "get_pipeline":
            return await _get_pipeline(arguments)
        elif name == "create_pipeline":
            return await _create_pipeline(arguments)
        elif name == "list_schedules":
            return await _list_schedules(arguments)
        elif name == "create_schedule":
            return await _create_schedule(arguments)
        elif name == "update_schedule":
            return await _update_schedule(arguments)
        elif name == "delete_schedule":
            return await _delete_schedule(arguments)
        elif name == "morning_report":
            return await _morning_report(arguments)
        elif name == "health_check":
            return await _health_check(arguments)
        elif name == "list_todos":
            return await _list_todos(arguments)
        elif name == "create_todo":
            return await _create_todo(arguments)
        elif name == "update_todo":
            return await _update_todo(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except httpx.ConnectError:
        return [TextContent(
            type="text",
            text=f"Error: cannot connect to workbench at {WORKBENCH_URL}. Is the service running?",
        )]
    except httpx.HTTPStatusError as exc:
        return [TextContent(
            type="text",
            text=f"API error ({exc.response.status_code}): {exc.response.text}",
        )]
    except Exception as exc:
        return [TextContent(type="text", text=f"Error: {exc}")]


# ---------------------------------------------------------------------------
# Tool implementations — Task Management
# ---------------------------------------------------------------------------


async def _list_tasks(args: dict) -> list[TextContent]:
    params: dict = {}
    if args.get("status"):
        params["status"] = args["status"]
    params["limit"] = args.get("limit", 20)

    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.get("/tasks", params=params)
        resp.raise_for_status()
        data = resp.json()

    tasks = data.get("tasks", [])
    if not tasks:
        return [TextContent(type="text", text="No tasks found.")]

    lines = [f"Found {data.get('total', len(tasks))} tasks (showing {len(tasks)}):"]
    for t in tasks:
        line = f"  [{t['status']}] {t['id']}"
        inp = t.get("input", {})
        if inp.get("repo"):
            line += f" (repo: {inp['repo']})"
        if t.get("pr_url"):
            line += f" PR: {t['pr_url']}"
        if t.get("error"):
            line += f" error: {t['error'][:80]}"
        lines.append(line)

    return [TextContent(type="text", text="\n".join(lines))]


async def _get_task(args: dict) -> list[TextContent]:
    task_id = args["task_id"]
    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.get(f"/tasks/{task_id}")
        resp.raise_for_status()
        t = resp.json()

    inp = t.get("input", {})
    lines = [
        f"Task: {t['id']}",
        f"Status: {t['status']}",
        f"Type: {inp.get('type', '?')}",
        f"Source: {inp.get('source', '?')[:200]}",
    ]
    if inp.get("repo"):
        lines.append(f"Repo: {inp['repo']}")
    if t.get("phase"):
        lines.append(f"Phase: {t['phase']}")
    if t.get("branch"):
        lines.append(f"Branch: {t['branch']}")
    if t.get("pr_url"):
        lines.append(f"PR: {t['pr_url']}")
    if t.get("summary"):
        lines.append(f"Summary: {t['summary']}")
    if t.get("error"):
        lines.append(f"Error: {t['error'][:500]}")
    if t.get("stale"):
        lines.append("WARNING: Task appears stale (no heartbeat)")
    lines.append(f"Created: {t.get('created_at', '?')}")
    if t.get("started_at"):
        lines.append(f"Started: {t['started_at']}")
    if t.get("completed_at"):
        lines.append(f"Completed: {t['completed_at']}")

    return [TextContent(type="text", text="\n".join(lines))]


async def _create_task(args: dict) -> list[TextContent]:
    body: dict = {
        "type": args["type"],
        "source": args["source"],
    }
    for key in ("repo", "autonomy", "extra_instructions", "context"):
        if args.get(key) is not None:
            body[key] = args[key]

    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.post("/tasks", json=body)
        resp.raise_for_status()
        t = resp.json()

    return [TextContent(
        type="text",
        text=f"Task created: {t['id']} (status: {t['status']})",
    )]


async def _cancel_task(args: dict) -> list[TextContent]:
    task_id = args["task_id"]
    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.post(f"/tasks/{task_id}/cancel")
        resp.raise_for_status()
        data = resp.json()

    return [TextContent(
        type="text",
        text=f"Task {task_id} cancelled: {data.get('status', 'cancelled')}",
    )]


async def _unblock_task(args: dict) -> list[TextContent]:
    task_id = args["task_id"]
    response_text = args["response"]
    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.post(
            f"/tasks/{task_id}/unblock",
            json={"response": response_text},
        )
        resp.raise_for_status()
        data = resp.json()

    return [TextContent(
        type="text",
        text=f"Task {task_id} unblocked: {data.get('status', 'unblocked')}",
    )]


# ---------------------------------------------------------------------------
# Tool implementations — Pipeline Management
# ---------------------------------------------------------------------------


async def _list_pipelines(args: dict) -> list[TextContent]:
    params: dict = {}
    if args.get("status"):
        params["status"] = args["status"]
    params["limit"] = args.get("limit", 20)

    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.get("/pipelines", params=params)
        resp.raise_for_status()
        data = resp.json()

    pipelines = data.get("pipelines", [])
    if not pipelines:
        return [TextContent(type="text", text="No pipelines found.")]

    lines = [f"Found {data.get('total', len(pipelines))} pipelines (showing {len(pipelines)}):"]
    for p in pipelines:
        stages = p.get("stages", [])
        stage_count = len(stages)
        current = p.get("current_stage_index", 0)
        line = (
            f"  [{p['status']}] {p['id']} — "
            f"stage {current + 1}/{stage_count}"
        )
        if p.get("repo"):
            line += f" (repo: {p['repo']})"
        if p.get("error"):
            line += f" error: {p['error'][:80]}"
        lines.append(line)

    return [TextContent(type="text", text="\n".join(lines))]


async def _get_pipeline(args: dict) -> list[TextContent]:
    pipeline_id = args["pipeline_id"]
    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.get(f"/pipelines/{pipeline_id}")
        resp.raise_for_status()
        p = resp.json()

    stages = p.get("stages", [])
    lines = [
        f"Pipeline: {p['id']}",
        f"Status: {p['status']}",
        f"Repo: {p.get('repo', 'none')}",
        f"Progress: stage {p.get('current_stage_index', 0) + 1}/{len(stages)}",
        f"Review iterations: {p.get('review_iteration', 0)}/{p.get('max_review_iterations', 3)}",
        "",
        "Stages:",
    ]
    for i, stage in enumerate(stages):
        marker = ">>>" if i == p.get("current_stage_index", 0) else "   "
        gate = " [review-gate]" if stage.get("review_gate") else ""
        lines.append(f"  {marker} {i + 1}. {stage['name']} ({stage['autonomy']}){gate}")

    if p.get("task_ids"):
        lines.append(f"\nTask IDs: {', '.join(p['task_ids'])}")
    if p.get("current_task_id"):
        lines.append(f"Current task: {p['current_task_id']}")
    if p.get("error"):
        lines.append(f"Error: {p['error'][:500]}")

    lines.append(f"Created: {p.get('created_at', '?')}")
    if p.get("completed_at"):
        lines.append(f"Completed: {p['completed_at']}")

    return [TextContent(type="text", text="\n".join(lines))]


async def _create_pipeline(args: dict) -> list[TextContent]:
    body: dict = {"stages": args["stages"]}
    if args.get("repo"):
        body["repo"] = args["repo"]
    if args.get("max_review_iterations") is not None:
        body["max_review_iterations"] = args["max_review_iterations"]

    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.post("/pipelines", json=body)
        resp.raise_for_status()
        p = resp.json()

    stage_names = [s["name"] for s in p.get("stages", [])]
    return [TextContent(
        type="text",
        text=(
            f"Pipeline created: {p['id']} (status: {p['status']})\n"
            f"Stages: {' → '.join(stage_names)}"
        ),
    )]


# ---------------------------------------------------------------------------
# Tool implementations — Schedule Management
# ---------------------------------------------------------------------------


async def _list_schedules(args: dict) -> list[TextContent]:
    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.get("/schedules")
        resp.raise_for_status()
        data = resp.json()

    schedules = data.get("schedules", [])
    if not schedules:
        return [TextContent(type="text", text="No schedules found.")]

    lines = [f"Found {data.get('total', len(schedules))} schedules:"]
    for s in schedules:
        enabled = "enabled" if s.get("enabled") else "disabled"
        line = (
            f"  [{enabled}] {s['id']} — {s['name']} "
            f"({s['cron_expr']} {s.get('timezone', 'UTC')})"
        )
        if s.get("next_run_at"):
            line += f" next: {s['next_run_at']}"
        lines.append(line)

    return [TextContent(type="text", text="\n".join(lines))]


async def _create_schedule(args: dict) -> list[TextContent]:
    body: dict = {
        "name": args["name"],
        "cron_expr": args["cron_expr"],
        "schedule_type": args["schedule_type"],
        "payload": args["payload"],
    }
    if args.get("timezone"):
        body["timezone"] = args["timezone"]
    if args.get("enabled") is not None:
        body["enabled"] = args["enabled"]

    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.post("/schedules", json=body)
        resp.raise_for_status()
        s = resp.json()

    return [TextContent(
        type="text",
        text=(
            f"Schedule created: {s['id']}\n"
            f"Name: {s['name']}\n"
            f"Cron: {s['cron_expr']} ({s.get('timezone', 'UTC')})\n"
            f"Next run: {s.get('next_run_at', '?')}"
        ),
    )]


async def _update_schedule(args: dict) -> list[TextContent]:
    schedule_id = args["schedule_id"]
    body: dict = {}
    for key in ("name", "cron_expr", "timezone", "enabled", "payload"):
        if args.get(key) is not None:
            body[key] = args[key]

    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.patch(f"/schedules/{schedule_id}", json=body)
        resp.raise_for_status()
        s = resp.json()

    return [TextContent(
        type="text",
        text=f"Schedule {schedule_id} updated: {s.get('name', '?')} ({s.get('cron_expr', '?')})",
    )]


async def _delete_schedule(args: dict) -> list[TextContent]:
    schedule_id = args["schedule_id"]
    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.delete(f"/schedules/{schedule_id}")
        resp.raise_for_status()

    return [TextContent(
        type="text",
        text=f"Schedule {schedule_id} deleted.",
    )]


# ---------------------------------------------------------------------------
# Tool implementations — Reporting & Status
# ---------------------------------------------------------------------------


async def _morning_report(args: dict) -> list[TextContent]:
    params: dict = {}
    hours = args.get("hours", 12)
    params["hours"] = hours

    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.get("/morning-report", params=params)
        resp.raise_for_status()
        data = resp.json()

    lines = [data.get("summary", f"No activity in the last {hours} hours.")]

    # Add PR details if present
    prs = data.get("prs", [])
    if prs:
        lines.append("\nPRs created:")
        for pr in prs:
            lines.append(f"  - {pr.get('repo', '?')}: {pr.get('pr_url', '?')}")
            if pr.get("summary"):
                lines.append(f"    {pr['summary'][:200]}")

    # Add failure details if present
    failures = data.get("failed_tasks", [])
    if failures:
        lines.append("\nFailed tasks:")
        for f in failures:
            lines.append(f"  - {f.get('id', '?')}: {(f.get('error') or 'unknown')[:200]}")

    return [TextContent(type="text", text="\n".join(lines))]


async def _health_check(args: dict) -> list[TextContent]:
    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=10) as client:
        resp = await client.get("/health")
        resp.raise_for_status()
        data = resp.json()

    lines = [
        f"Status: {data.get('status', '?')}",
        f"Database: {data.get('database', '?')}",
        f"Workers: {data.get('workers', '?')}",
        f"Workspace: {data.get('workspace', '?')}",
    ]
    repos = data.get("repos", [])
    if repos:
        lines.append(f"Repos: {', '.join(repos)}")

    return [TextContent(type="text", text="\n".join(lines))]


# ---------------------------------------------------------------------------
# Tool implementations — Todo Management
# ---------------------------------------------------------------------------


async def _list_todos(args: dict) -> list[TextContent]:
    params: dict = {}
    if args.get("status"):
        params["status"] = args["status"]
    if args.get("source"):
        params["source"] = args["source"]
    params["limit"] = args.get("limit", 20)

    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.get("/todos", params=params)
        resp.raise_for_status()
        data = resp.json()

    todos = data.get("todos", []) if isinstance(data, dict) else data
    if not todos:
        return [TextContent(type="text", text="No todos found.")]

    lines = [f"Found {len(todos)} todos:"]
    for t in todos:
        priority = t.get("priority", "?")
        status = t.get("status", "?")
        title = t.get("title", "?")
        lines.append(f"  [{status}] ({priority}) {t.get('id', '?')}: {title}")

    return [TextContent(type="text", text="\n".join(lines))]


async def _create_todo(args: dict) -> list[TextContent]:
    body: dict = {"title": args["title"]}
    for key in ("description", "status", "priority", "tags"):
        if args.get(key) is not None:
            body[key] = args[key]

    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.post("/todos", json=body)
        resp.raise_for_status()
        t = resp.json()

    return [TextContent(
        type="text",
        text=f"Todo created: {t.get('id', '?')} — {t.get('title', args['title'])}",
    )]


async def _update_todo(args: dict) -> list[TextContent]:
    todo_id = args["todo_id"]
    body: dict = {}
    for key in ("title", "description", "status", "priority", "tags"):
        if args.get(key) is not None:
            body[key] = args[key]

    async with httpx.AsyncClient(base_url=WORKBENCH_URL, timeout=30) as client:
        resp = await client.patch(f"/todos/{todo_id}", json=body)
        resp.raise_for_status()
        t = resp.json()

    return [TextContent(
        type="text",
        text=f"Todo {todo_id} updated: {t.get('title', '?')} ({t.get('status', '?')})",
    )]


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def run_mcp() -> None:
    """Start the MCP server in stdio mode."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(run_mcp())
