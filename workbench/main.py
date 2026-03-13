"""FastAPI application — HTTP API for submitting and tracking agent tasks."""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from .config import settings
from .database import (
    PipelineRow,
    TaskRow,
    async_session,
    check_db,
    check_pipeline_dependencies_met,
    close_db,
    count_todos,
    create_pipeline,
    create_schedule,
    create_task,
    create_todo,
    delete_schedule,
    delete_todo,
    get_dependents,
    get_pipeline,
    get_schedule,
    get_task,
    get_todo,
    init_db,
    list_pipelines,
    list_pipelines_since,
    list_schedules,
    list_tasks,
    list_tasks_since,
    list_todos,
    update_pipeline,
    update_schedule,
    update_task,
    update_todo,
    validate_pipeline_dependencies,
    validate_task_dependencies,
)
from .models import (
    JiraSyncRequest,
    JiraSyncResult,
    PipelineCreate,
    PipelineInfo,
    PipelineListResponse,
    PipelineStatus,
    ScheduleCreate,
    ScheduleInfo,
    ScheduleListResponse,
    ScheduleUpdate,
    TaskCreate,
    TaskInfo,
    TaskInputType,
    TaskListResponse,
    TodoCoverageInfo,
    TodoCoverageListResponse,
    TodoCoverageSummary,
    TodoCoverageTaskRef,
    TodoCreate,
    TodoInfo,
    TodoListResponse,
    TodoReconcileItem,
    TodoReconcileRequest,
    TodoReconcileResponse,
    TodoReorder,
    TodoUpdate,
    schedule_row_to_info,
    task_row_to_info,
    todo_row_to_info,
)
from .worker import WorkerPool
from .workspace_setup import TOOL_FILES, install_workspace

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Worker pool singleton
# ---------------------------------------------------------------------------

pool = WorkerPool()


# ---------------------------------------------------------------------------
# Scheduler dispatch callbacks
# ---------------------------------------------------------------------------


async def _dispatch_task_from_schedule(payload: dict) -> str:
    """Create and enqueue a task from a schedule payload dict.

    Returns the new task ID.
    """
    import json as _json

    from .models import TaskCreate

    body = TaskCreate(**payload)

    # Validate repo
    if body.repo:
        resolved = settings.resolve_repo_path(body.repo)
        if resolved is None:
            raise ValueError(f"Unknown repository: {body.repo!r}")

    # Serialize context
    context_json: str | None = None
    if body.context:
        context_json = _json.dumps([item.model_dump(exclude_none=True) for item in body.context])

    # Serialize depends_on
    depends_on_json: str | None = None
    if body.depends_on:
        depends_on_json = _json.dumps(body.depends_on)

    async with async_session() as session:
        row = await create_task(
            session,
            input_type=body.type.value,
            source=body.source,
            repo=body.repo,
            autonomy=body.autonomy.value,
            model=body.model,
            extra_instructions=body.extra_instructions,
            file_path=body.file_path,
            file_content=body.file_content,
            file_format=body.file_format,
            context_json=context_json,
            parent_task_id=body.parent_task_id,
            depends_on_json=depends_on_json,
            role=body.role,
            timeout=body.timeout,
        )

    pool.enqueue(row.id)
    return row.id


async def _dispatch_pipeline_from_schedule(payload: dict) -> str:
    """Create and start a pipeline from a schedule payload dict.

    Returns the new pipeline ID.
    """
    import json as _json

    from .models import PipelineCreate

    body = PipelineCreate(**payload)

    # Validate repo
    if body.repo:
        resolved = settings.resolve_repo_path(body.repo)
        if resolved is None:
            raise ValueError(f"Unknown repository: {body.repo!r}")

    stages_json = _json.dumps([s.model_dump(exclude_none=True) for s in body.stages])

    # Serialize depends_on
    depends_on_json: str | None = None
    if body.depends_on:
        depends_on_json = _json.dumps(body.depends_on)

    async with async_session() as session:
        row = await create_pipeline(
            session,
            repo=body.repo,
            stages_json=stages_json,
            max_review_iterations=body.max_review_iterations,
            model=body.model,
            depends_on_json=depends_on_json,
        )

    from .pipeline import start_pipeline

    await start_pipeline(row.id, pool.enqueue)
    return row.id


# ---------------------------------------------------------------------------
# Scheduler singleton
# ---------------------------------------------------------------------------

from .scheduler import Scheduler, compute_next_run, validate_cron_expr  # noqa: E402

scheduler = Scheduler(
    dispatch_task_fn=_dispatch_task_from_schedule,
    dispatch_pipeline_fn=_dispatch_pipeline_from_schedule,
)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log.info(
        "workbench starting (workers=%d, workspace=%s)",
        settings.max_workers,
        settings.workspace_root,
    )
    log.info("Known repos: %s", list(settings.known_repos.keys()))

    # Initialize database tables
    await init_db()
    log.info("Database initialized (url=%s)", settings.database_url.split("@")[-1])

    # Clean up any stale worktrees from a previous crash
    from .git_ops import prune_stale_worktrees

    await prune_stale_worktrees(settings.worktree_base_dir, settings.known_repos)

    # Start worker pool (re-enqueues incomplete tasks from DB)
    await pool.start()

    # Start cron scheduler
    await scheduler.start()

    yield

    await scheduler.stop()
    await pool.stop()
    await close_db()
    log.info("workbench stopped")


app = FastAPI(
    title="workbench",
    description="Autonomous agent service — accepts work via API, delegates to OpenCode",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    db_ok = await check_db()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "unreachable",
        "workers": settings.max_workers,
        "workspace": str(settings.workspace_root),
        "repos": list(settings.known_repos.keys()),
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Live task monitoring dashboard."""
    from .dashboard import DASHBOARD_HTML

    return HTMLResponse(content=DASHBOARD_HTML)


@app.get("/diagrams", response_class=HTMLResponse)
async def diagrams():
    """Architecture and workflow diagrams page."""
    from .diagrams import DIAGRAMS_HTML

    return HTMLResponse(content=DIAGRAMS_HTML)


@app.get("/kanban")
async def kanban():
    """Redirect to dashboard (kanban is now the Board tab)."""
    return RedirectResponse(url="/dashboard", status_code=301)


@app.get("/diagrams/data")
async def diagrams_data():
    """Return live data for annotating diagrams.

    Returns task state counts, pipeline status counts, FSM transitions,
    and active worktree count.
    """
    from sqlalchemy import func, select

    from .fsm import TRANSITIONS, State

    # Task state counts
    task_state_counts: dict[str, int] = {}
    async with async_session() as session:
        for state in State:
            query = select(func.count()).select_from(TaskRow).where(TaskRow.status == state.value)
            count = (await session.execute(query)).scalar_one()
            task_state_counts[state.value] = count

    # Pipeline status counts
    pipeline_status_counts: dict[str, int] = {}
    async with async_session() as session:
        for status_val in ("pending", "running", "completed", "failed", "cancelled"):
            query = (
                select(func.count())
                .select_from(PipelineRow)
                .where(PipelineRow.status == status_val)
            )
            count = (await session.execute(query)).scalar_one()
            pipeline_status_counts[status_val] = count

    # FSM transitions (serializable)
    fsm_transitions = {
        state.value: [t.value for t in targets] for state, targets in TRANSITIONS.items()
    }

    # Active worktrees: count directories in worktree base
    active_worktrees = 0
    worktree_base = settings.worktree_base_dir
    if worktree_base.is_dir():
        active_worktrees = sum(1 for p in worktree_base.iterdir() if p.is_dir())

    return {
        "task_state_counts": task_state_counts,
        "pipeline_status_counts": pipeline_status_counts,
        "fsm_transitions": fsm_transitions,
        "active_worktrees": active_worktrees,
    }


@app.post("/tasks", response_model=TaskInfo, status_code=201)
async def create_task_route(body: TaskCreate):
    """Submit a new task for autonomous execution."""
    # Validate repo if provided
    if body.repo:
        resolved = settings.resolve_repo_path(body.repo)
        if resolved is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown repository: {body.repo!r}. "
                    f"Known repos: {list(settings.known_repos.keys())}"
                ),
            )

    # Validate prompt_file inputs
    if body.type == TaskInputType.PROMPT_FILE:
        if not body.file_path and not body.file_content:
            raise HTTPException(
                status_code=400,
                detail="type=prompt_file requires either file_path or file_content",
            )
        if body.file_path and body.file_content:
            raise HTTPException(
                status_code=400,
                detail="file_path and file_content are mutually exclusive",
            )

    # Persist to DB
    async with async_session() as session:
        # Serialize context items to JSON for storage
        import json

        context_json: str | None = None
        if body.context:
            context_json = json.dumps([item.model_dump(exclude_none=True) for item in body.context])

        # Serialize depends_on to JSON for storage
        depends_on_json: str | None = None
        if body.depends_on:
            depends_on_json = json.dumps(body.depends_on)

        row = await create_task(
            session,
            input_type=body.type.value,
            source=body.source,
            repo=body.repo,
            autonomy=body.autonomy.value,
            model=body.model,
            extra_instructions=body.extra_instructions,
            file_path=body.file_path,
            file_content=body.file_content,
            file_format=body.file_format,
            context_json=context_json,
            parent_task_id=body.parent_task_id,
            depends_on_json=depends_on_json,
            role=body.role,
            timeout=body.timeout,
        )

        # Validate dependencies (after creation so we have the row ID)
        if body.depends_on:
            try:
                await validate_task_dependencies(session, row.id, body.depends_on)
            except ValueError as e:
                # Roll back: delete the just-created task
                from sqlalchemy import delete as sa_delete

                from .database import TaskRow

                await session.execute(sa_delete(TaskRow).where(TaskRow.id == row.id))
                await session.commit()
                raise HTTPException(status_code=400, detail=str(e))

    # Enqueue for processing
    pool.enqueue(row.id)
    return task_row_to_info(row)


@app.get("/tasks", response_model=TaskListResponse)
async def list_tasks_route(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List all tasks, optionally filtered by status."""
    async with async_session() as session:
        rows, total = await list_tasks(session, status=status, limit=limit, offset=offset)

    return TaskListResponse(
        tasks=[task_row_to_info(r) for r in rows],
        total=total,
    )


@app.get("/tasks/{task_id}", response_model=TaskInfo)
async def get_task_route(task_id: str):
    """Get details for a specific task."""
    async with async_session() as session:
        row = await get_task(session, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return task_row_to_info(row)


@app.post("/tasks/{task_id}/cancel")
async def cancel_task_route(task_id: str):
    """Cancel a task that hasn't completed yet.

    Sets the DB status to cancelled and kills the subprocess if one is running.
    """
    async with async_session() as session:
        row = await get_task(session, task_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        if row.status in ("completed", "failed", "cancelled"):
            raise HTTPException(
                status_code=409,
                detail=f"Task {task_id} cannot be cancelled (status={row.status})",
            )

        await update_task(
            session,
            task_id,
            status="cancelled",
            phase="cancelled",
            completed_at=datetime.now(UTC),
        )

    # Kill the subprocess (SIGTERM -> grace period -> SIGKILL)
    await pool.cancel_task(task_id)

    return {"status": "cancelled", "task_id": task_id}


@app.post("/tasks/{task_id}/unblock")
async def unblock_task_route(task_id: str, request: Request):
    """Unblock a task that is waiting for human input.

    Request body: {"response": "the human's answer to the blocked question"}

    The task will be re-enqueued with the response appended to its context.
    """
    body = await request.json()
    response_text = body.get("response", "").strip()
    if not response_text:
        raise HTTPException(
            status_code=400,
            detail="Request body must include a non-empty 'response' field",
        )

    try:
        await pool.unblock_task(task_id, response_text)
    except ValueError as e:
        status_code = 404 if "not found" in str(e) else 409
        raise HTTPException(status_code=status_code, detail=str(e))

    return {"status": "unblocked", "task_id": task_id}


@app.get("/tasks/{task_id}/logs")
async def stream_task_logs(task_id: str, request: Request):
    """Stream task logs in real time via Server-Sent Events (SSE).

    Events:
    - data: {"type": "log", "data": "..."}      — output chunk
    - data: {"type": "phase", "phase": "..."}   — phase transition
    - data: {"type": "done", "status": "..."}    — task completed
    - data: {"type": "error", "error": "..."}    — task error

    If the task is already completed/failed, returns the stored output as a
    single event and closes the stream.
    """
    import json

    async with async_session() as session:
        row = await get_task(session, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    # If task is already done, return stored output as a one-shot stream
    if row.status in ("completed", "failed", "cancelled", "blocked"):

        async def _replay():
            if row.output:
                yield f"data: {json.dumps({'type': 'log', 'data': row.output})}\n\n"
            if row.error:
                yield f"data: {json.dumps({'type': 'error', 'error': row.error})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'status': row.status})}\n\n"

        return StreamingResponse(_replay(), media_type="text/event-stream")

    # Subscribe to the live log buffer
    buf = pool.subscribe_logs(task_id)

    async def _stream():
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                try:
                    event_type, payload = await asyncio.wait_for(buf.get(), timeout=30)
                except TimeoutError:
                    # Send keepalive comment to prevent connection timeout
                    yield ": keepalive\n\n"
                    continue

                if event_type == "log":
                    yield f"data: {json.dumps({'type': 'log', 'data': payload})}\n\n"
                elif event_type == "phase":
                    yield f"data: {json.dumps({'type': 'phase', 'phase': payload})}\n\n"
                elif event_type == "done":
                    yield f"data: {json.dumps({'type': 'done', 'status': payload})}\n\n"
                    break
                elif event_type == "error":
                    yield f"data: {json.dumps({'type': 'error', 'error': payload})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            # Clean up the subscriber queue so it doesn't leak if the
            # client disconnects before the task finishes.
            pool.unsubscribe_logs(task_id)

    return StreamingResponse(_stream(), media_type="text/event-stream")


@app.get("/tasks/{task_id}/dependents", response_model=TaskListResponse)
async def get_task_dependents_route(task_id: str):
    """Get all tasks that depend on a specific task."""
    async with async_session() as session:
        # Verify the task exists
        row = await get_task(session, task_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        dependent_rows = await get_dependents(session, task_id)

    return TaskListResponse(
        tasks=[task_row_to_info(r) for r in dependent_rows],
        total=len(dependent_rows),
    )


# ---------------------------------------------------------------------------
# Jira sync routes
# ---------------------------------------------------------------------------


@app.post("/todos/sync-jira", response_model=JiraSyncResult)
async def sync_jira_route(body: JiraSyncRequest):
    """Trigger a read-only sync of Jira issues matching the given JQL query.

    Creates or updates local todo items based on Jira issue data.
    NEVER writes to Jira — only reads via GET requests.

    Returns 503 if Jira is not configured (WORKBENCH_JIRA_BASE_URL not set).
    """
    from .jira_sync import JiraConfigurationError, sync_jira_issues

    try:
        async with async_session() as session:
            result = await sync_jira_issues(
                session,
                jql=body.jql,
                max_results=body.max_results,
                status_mapping=body.status_mapping,
            )
    except JiraConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return JiraSyncResult(**result)


@app.get("/todos/jira")
async def list_jira_todos_route():
    """List all locally-synced Jira todo items."""
    from .database import list_jira_todos

    async with async_session() as session:
        rows = await list_jira_todos(session)

    return {
        "todos": [
            {
                "id": r.id,
                "title": r.title,
                "status": r.status,
                "priority": r.priority,
                "jira_key": r.jira_key,
                "jira_url": r.jira_url,
                "jira_status": r.jira_status,
                "jira_last_synced": r.jira_last_synced.isoformat() if r.jira_last_synced else None,
                "source": r.source,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }


# ---------------------------------------------------------------------------
# Todo CRUD routes
# ---------------------------------------------------------------------------


@app.get("/todos", response_model=TodoListResponse)
async def list_todos_route(
    status: str | None = None,
    source: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    """List all todos with optional status/source filters."""
    async with async_session() as session:
        rows = await list_todos(session, status=status, source=source, limit=limit, offset=offset)
        total = await count_todos(session, status=status, source=source)
    return TodoListResponse(
        todos=[todo_row_to_info(r) for r in rows],
        total=total,
    )


_ACTIVE_TASK_STATUSES = {"queued", "resolving", "running", "creating_pr", "blocked", "stuck"}


def _extract_repo_hints(todo: TodoInfo) -> list[str]:
    hints: list[str] = []
    source_ref = todo.source_ref or ""

    if source_ref:
        for token in re.findall(r"[A-Za-z0-9_.\-/]+", source_ref):
            if "/" in token and token not in hints:
                hints.append(token)

    for tag in todo.tags or []:
        if not tag:
            continue
        if tag.startswith("repo:"):
            repo = tag.split(":", 1)[1].strip()
            if repo and repo not in hints:
                hints.append(repo)
        elif "/" in tag and tag not in hints:
            hints.append(tag)

    return hints


def _extract_initiative_tags(todo: TodoInfo) -> list[str]:
    tags = todo.tags or []
    result: list[str] = []
    for tag in tags:
        lower = tag.lower()
        if any(
            lower.startswith(prefix)
            for prefix in ("initiative:", "epic:", "program:", "stream:", "theme:", "project:")
        ):
            result.append(tag)
    return result


def _bounded_match(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])")
    return pattern.search(haystack) is not None


def _repo_hint_candidates(repo_hints: list[str]) -> set[str]:
    candidates: set[str] = set()
    for hint in repo_hints:
        value = hint.strip().lower()
        if not value:
            continue
        candidates.add(value)
        if "/" in value:
            candidates.add(value.rsplit("/", 1)[-1])
    return candidates


def _initiative_value_is_specific(value: str) -> bool:
    if not value:
        return False
    return any(ch.isdigit() for ch in value) or any(ch in value for ch in "-_/") or len(value) >= 8


def _task_matches_todo(todo: TodoInfo, task: TaskInfo, repo_hints: list[str]) -> bool:
    source = task.input.source or ""
    summary = task.summary or ""
    haystack = "\n".join((source, summary)).lower()

    jira_key = (todo.jira_key or "").strip().lower()
    if jira_key and _bounded_match(haystack, jira_key):
        return True

    source_ref = (todo.source_ref or "").strip().lower()
    if source_ref and len(source_ref) >= 6 and _bounded_match(haystack, source_ref):
        return True

    task_repo = (task.input.repo or "").strip().lower()
    if task_repo:
        repo_candidates = _repo_hint_candidates(repo_hints)
        if task_repo in repo_candidates:
            return True

    for tag in _extract_initiative_tags(todo):
        tag_lower = tag.strip().lower()
        if tag_lower and _bounded_match(haystack, tag_lower):
            return True
        value = tag_lower.split(":", 1)[1].strip() if ":" in tag_lower else ""
        if _initiative_value_is_specific(value) and _bounded_match(haystack, value):
            return True

    return False


def _to_coverage_task_ref(task: TaskInfo) -> TodoCoverageTaskRef:
    return TodoCoverageTaskRef(
        id=task.id,
        status=task.status,
        repo=task.input.repo,
        summary=task.summary,
        pipeline_id=task.pipeline_id,
        stage_name=task.stage_name,
        created_at=task.created_at,
    )


def _normalize_runtime_validation_key(todo: TodoInfo) -> str | None:
    tokens = [todo.title or "", todo.source_ref or ""] + (todo.tags or [])
    joined = " ".join(tokens).lower()
    if "runtime validation" not in joined and "runtime-validation" not in joined:
        return None

    normalized = re.sub(r"[^a-z0-9]+", " ", (todo.title or "").lower()).strip()
    if not normalized:
        normalized = (todo.jira_key or todo.source_ref or todo.id).lower()
    return normalized


def _merge_tags(existing: list[str] | None, new_tag: str) -> list[str]:
    tags = list(existing or [])
    if new_tag not in tags:
        tags.append(new_tag)
    return tags


@app.get("/todos/coverage", response_model=TodoCoverageListResponse)
async def list_todo_coverage_route(recent_hours: int = 72):
    """Return todo-to-task/pipeline linkage coverage for the board UX."""
    from datetime import timedelta

    if recent_hours <= 0:
        raise HTTPException(status_code=400, detail="recent_hours must be > 0")

    cutoff = datetime.now(UTC) - timedelta(hours=recent_hours)

    async with async_session() as session:
        todo_rows = await list_todos(session, limit=1000)
        recent_task_rows = await list_tasks_since(session, since=cutoff)
        active_task_rows: list[TaskRow] = []
        for status in sorted(_ACTIVE_TASK_STATUSES):
            status_rows, _ = await list_tasks(session, status=status, limit=1000, offset=0)
            active_task_rows.extend(status_rows)
        pipeline_rows = await list_pipelines_since(session, since=cutoff)

    todos = [todo_row_to_info(row) for row in todo_rows]
    recent_tasks = [task_row_to_info(row) for row in recent_task_rows]
    all_task_rows_by_id: dict[str, TaskRow] = {row.id: row for row in recent_task_rows}
    for row in active_task_rows:
        all_task_rows_by_id[row.id] = row
    all_tasks = [task_row_to_info(row) for row in all_task_rows_by_id.values()]
    pipeline_ids = {p.id for p in pipeline_rows}

    coverages: list[TodoCoverageInfo] = []
    covered_todos = 0
    active_linked_todos = 0

    for todo in todos:
        repo_hints = _extract_repo_hints(todo)
        initiative_tags = _extract_initiative_tags(todo)
        related_all = [t for t in all_tasks if _task_matches_todo(todo, t, repo_hints)]
        related_recent = [t for t in recent_tasks if _task_matches_todo(todo, t, repo_hints)]

        active_tasks = [t for t in related_all if t.status.value in _ACTIVE_TASK_STATUSES]
        recent_tasks_for_todo = sorted(related_recent, key=lambda t: t.created_at, reverse=True)[:5]

        related_pipeline_ids = sorted(
            {
                t.pipeline_id
                for t in related_all
                if t.pipeline_id
                and (t.pipeline_id in pipeline_ids or t.status.value in _ACTIVE_TASK_STATUSES)
            }
        )

        needs_task = len(related_all) == 0
        if not needs_task:
            covered_todos += 1
        if active_tasks:
            active_linked_todos += 1

        coverages.append(
            TodoCoverageInfo(
                todo_id=todo.id,
                jira_key=todo.jira_key,
                source_ref=todo.source_ref,
                initiative_tags=initiative_tags,
                repo_hints=repo_hints,
                related_active_task_count=len(active_tasks),
                related_recent_task_count=len(related_recent),
                related_pipeline_count=len(related_pipeline_ids),
                active_tasks=[_to_coverage_task_ref(t) for t in active_tasks[:3]],
                recent_tasks=[_to_coverage_task_ref(t) for t in recent_tasks_for_todo],
                related_pipeline_ids=related_pipeline_ids,
                needs_task=needs_task,
            )
        )

    summary = TodoCoverageSummary(
        total_todos=len(todos),
        covered_todos=covered_todos,
        uncovered_todos=max(len(todos) - covered_todos, 0),
        active_linked_todos=active_linked_todos,
    )

    return TodoCoverageListResponse(
        generated_at=datetime.now(UTC),
        recent_hours=recent_hours,
        coverages=coverages,
        summary=summary,
    )


@app.post("/todos/reconcile", response_model=TodoReconcileResponse)
async def reconcile_todos_route(body: TodoReconcileRequest):
    """Reconcile todo/task/pipeline state conservatively.

    Default mode is report-only. When ``apply_fixes=true``, applies low-risk
    metadata/status updates only.
    """
    import json as _json
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(hours=72)

    async with async_session() as session:
        todo_rows = await list_todos(session, limit=1000)
        recent_task_rows = await list_tasks_since(session, since=cutoff)
        active_task_rows: list[TaskRow] = []
        for status in sorted(_ACTIVE_TASK_STATUSES):
            status_rows, _ = await list_tasks(session, status=status, limit=1000, offset=0)
            active_task_rows.extend(status_rows)
        pipeline_rows, _ = await list_pipelines(session, limit=2000, offset=0)

    todos = [todo_row_to_info(row) for row in todo_rows]
    recent_tasks = [task_row_to_info(row) for row in recent_task_rows]
    all_task_rows_by_id: dict[str, TaskRow] = {row.id: row for row in recent_task_rows}
    for row in active_task_rows:
        all_task_rows_by_id[row.id] = row
    all_tasks = [task_row_to_info(row) for row in all_task_rows_by_id.values()]
    pipelines = [_pipeline_row_to_info(row) for row in pipeline_rows]
    pipeline_status_by_id = {p.id: p.status.value for p in pipelines}

    auto_fixed: list[TodoReconcileItem] = []
    report_only: list[TodoReconcileItem] = []

    runtime_validation_groups: dict[str, list[TodoInfo]] = {}
    for todo in todos:
        key = _normalize_runtime_validation_key(todo)
        if key:
            runtime_validation_groups.setdefault(key, []).append(todo)

    duplicate_runtime_validation_ids: set[str] = set()
    for group in runtime_validation_groups.values():
        if len(group) <= 1:
            continue
        sorted_group = sorted(group, key=lambda t: t.created_at)
        for dup in sorted_group[1:]:
            duplicate_runtime_validation_ids.add(dup.id)

    async with async_session() as session:
        for todo in todos:
            repo_hints = _extract_repo_hints(todo)
            related_all = [t for t in all_tasks if _task_matches_todo(todo, t, repo_hints)]
            active_tasks = [t for t in related_all if t.status.value in _ACTIVE_TASK_STATUSES]
            recent_related = [t for t in recent_tasks if _task_matches_todo(todo, t, repo_hints)]

            related_pipeline_ids = sorted({t.pipeline_id for t in related_all if t.pipeline_id})
            related_pipeline_statuses = [
                pipeline_status_by_id[pid]
                for pid in related_pipeline_ids
                if pid in pipeline_status_by_id
            ]

            if todo.status == "in_progress" and not active_tasks:
                detail = "Todo is in_progress but has no linked active task."
                if body.apply_fixes:
                    row = await update_todo(
                        session,
                        todo.id,
                        status="review",
                        tags=_json.dumps(_merge_tags(todo.tags, "reconcile:auto-paused")),
                    )
                    if row is not None:
                        auto_fixed.append(
                            TodoReconcileItem(
                                todo_id=todo.id,
                                issue="stale_in_progress_no_running_task",
                                detail=detail,
                                action="Moved to review and tagged reconcile:auto-paused.",
                            )
                        )
                        todo = todo_row_to_info(row)
                else:
                    report_only.append(
                        TodoReconcileItem(
                            todo_id=todo.id,
                            issue="stale_in_progress_no_running_task",
                            detail=detail,
                            action="Report-only: consider moving to review/blocked.",
                        )
                    )

            if todo.status == "in_progress" and active_tasks:
                active_statuses = {t.status.value for t in active_tasks}
                if active_statuses.issubset({"blocked", "stuck"}):
                    detail = "All linked active tasks are blocked/stuck."
                    if body.apply_fixes:
                        row = await update_todo(
                            session,
                            todo.id,
                            status="review",
                            tags=_json.dumps(_merge_tags(todo.tags, "reconcile:blocked-like")),
                        )
                        if row is not None:
                            auto_fixed.append(
                                TodoReconcileItem(
                                    todo_id=todo.id,
                                    issue="blocked_like_in_progress",
                                    detail=detail,
                                    action="Moved to review and tagged reconcile:blocked-like.",
                                )
                            )
                            todo = todo_row_to_info(row)
                    else:
                        report_only.append(
                            TodoReconcileItem(
                                todo_id=todo.id,
                                issue="blocked_like_in_progress",
                                detail=detail,
                                action="Report-only: likely blocked; consider review column.",
                            )
                        )

            if todo.status == "done" and active_tasks:
                report_only.append(
                    TodoReconcileItem(
                        todo_id=todo.id,
                        issue="done_with_active_work",
                        detail="Todo is done but linked active work still exists.",
                        action="Report-only: verify whether todo should be reopened.",
                    )
                )

            if (
                todo.status in {"in_progress", "review"}
                and related_pipeline_statuses
                and all(s in {"failed", "cancelled"} for s in related_pipeline_statuses)
            ):
                report_only.append(
                    TodoReconcileItem(
                        todo_id=todo.id,
                        issue="pipeline_state_mismatch",
                        detail=(
                            "Linked pipelines are terminal failed/cancelled "
                            "while todo remains active."
                        ),
                        action="Report-only: likely needs manual triage or blocked handling.",
                    )
                )

            if todo.id in duplicate_runtime_validation_ids:
                detail = "Duplicate runtime-validation todo detected."
                if body.apply_fixes and todo.status != "done":
                    row = await update_todo(
                        session,
                        todo.id,
                        status="review",
                        tags=_json.dumps(
                            _merge_tags(todo.tags, "reconcile:duplicate-runtime-validation")
                        ),
                    )
                    if row is not None:
                        auto_fixed.append(
                            TodoReconcileItem(
                                todo_id=todo.id,
                                issue="duplicate_runtime_validation",
                                detail=detail,
                                action=(
                                    "Moved duplicate to review and tagged "
                                    "reconcile:duplicate-runtime-validation."
                                ),
                            )
                        )
                else:
                    report_only.append(
                        TodoReconcileItem(
                            todo_id=todo.id,
                            issue="duplicate_runtime_validation",
                            detail=detail,
                            action="Report-only: consolidate duplicate runtime-validation todos.",
                        )
                    )

            if todo.status == "in_progress" and not active_tasks and recent_related:
                report_only.append(
                    TodoReconcileItem(
                        todo_id=todo.id,
                        issue="stale_claim_possible",
                        detail=(
                            "Todo has linked recent tasks but none active; "
                            "stale ownership/claim is possible but not "
                            "explicitly modeled."
                        ),
                        action="Report-only: verify owner/claim outside current schema.",
                    )
                )

    return TodoReconcileResponse(
        analyzed_todos=len(todos),
        analyzed_tasks=len(all_tasks),
        analyzed_pipelines=len(pipelines),
        auto_fixed=auto_fixed,
        report_only=report_only,
    )


@app.post("/todos", response_model=TodoInfo, status_code=201)
async def create_todo_route(body: TodoCreate):
    """Create a new todo item."""
    import json as _json

    tags_json: str | None = None
    if body.tags:
        tags_json = _json.dumps(body.tags)

    async with async_session() as session:
        row = await create_todo(
            session,
            title=body.title,
            description=body.description,
            status=body.status,
            priority=body.priority,
            tags=tags_json,
        )
    return todo_row_to_info(row)


@app.get("/todos/{todo_id}", response_model=TodoInfo)
async def get_todo_route(todo_id: str):
    """Get a single todo by ID."""
    async with async_session() as session:
        row = await get_todo(session, todo_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Todo {todo_id} not found")
    return todo_row_to_info(row)


@app.patch("/todos/{todo_id}", response_model=TodoInfo)
async def update_todo_route(todo_id: str, body: TodoUpdate):
    """Update a todo item."""
    import json as _json

    async with async_session() as session:
        row = await get_todo(session, todo_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Todo {todo_id} not found")

        update_fields: dict = {}
        if body.title is not None:
            update_fields["title"] = body.title
        if body.description is not None:
            update_fields["description"] = body.description
        if body.status is not None:
            update_fields["status"] = body.status
        if body.priority is not None:
            update_fields["priority"] = body.priority
        if body.tags is not None:
            update_fields["tags"] = _json.dumps(body.tags)
        if body.column_order is not None:
            update_fields["column_order"] = body.column_order

        if not update_fields:
            raise HTTPException(status_code=400, detail="No fields to update")

        updated = await update_todo(session, todo_id, **update_fields)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Todo {todo_id} not found")
    return todo_row_to_info(updated)


@app.delete("/todos/{todo_id}", status_code=204)
async def delete_todo_route(todo_id: str):
    """Delete a todo item."""
    async with async_session() as session:
        deleted = await delete_todo(session, todo_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Todo {todo_id} not found")
    return None


@app.post("/todos/{todo_id}/reorder", response_model=TodoInfo)
async def reorder_todo_route(todo_id: str, body: TodoReorder):
    """Move a todo to a new column/position, shifting siblings as needed."""
    from .database import shift_todo_siblings

    async with async_session() as session:
        row = await get_todo(session, todo_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Todo {todo_id} not found")

        # Make room: bump column_order for items at or after the target position
        await shift_todo_siblings(
            session,
            target_status=body.status,
            target_order=body.order,
            exclude_id=todo_id,
        )

        updated = await update_todo(
            session,
            todo_id,
            status=body.status,
            column_order=body.order,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Todo {todo_id} not found")
    return todo_row_to_info(updated)


# ---------------------------------------------------------------------------
# Pipeline routes
# ---------------------------------------------------------------------------


def _pipeline_row_to_info(
    row,
    *,
    dependencies_met: bool = True,
    stalled: bool = False,
    stalled_reason: str | None = None,
) -> PipelineInfo:
    """Convert a PipelineRow to a PipelineInfo API response."""
    import json as _json

    from .models import StageConfig

    stages = [StageConfig(**s) for s in _json.loads(row.stages_json)]
    task_ids = _json.loads(row.task_ids_json or "[]")

    # Parse depends_on from stored JSON
    depends_on: list[str] = []
    raw_depends = getattr(row, "depends_on_json", None)
    if raw_depends:
        try:
            depends_on = _json.loads(raw_depends)
        except (_json.JSONDecodeError, TypeError, ValueError):
            pass

    return PipelineInfo(
        id=row.id,
        repo=row.repo,
        stages=stages,
        current_stage_index=row.current_stage_index,
        current_task_id=row.current_task_id,
        status=PipelineStatus(row.status),
        max_review_iterations=row.max_review_iterations,
        review_iteration=row.review_iteration,
        model=row.model,
        task_ids=task_ids,
        depends_on=depends_on,
        dependencies_met=dependencies_met,
        stalled=stalled,
        stalled_reason=stalled_reason,
        error=row.error,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


async def _compute_pipeline_health(session, row: PipelineRow) -> tuple[bool, bool, str | None]:
    """Compute dependency and stall signals for operator triage.

    Stalled means pipeline state appears internally inconsistent and likely
    needs operator intervention (or a manual restart).
    """
    dependencies_met = True
    dependencies_reason: str | None = None

    if row.depends_on_json:
        dependencies_met, dependencies_reason = await check_pipeline_dependencies_met(
            session,
            row.id,
        )

    status = str(row.status)
    stalled = False
    stalled_reason: str | None = None

    if status == "pending" and row.current_task_id is None:
        if dependencies_met:
            stalled = True
            stalled_reason = "pending_without_current_task_id"
        elif dependencies_reason:
            stalled = True
            stalled_reason = f"dependency_resolution_error: {dependencies_reason}"
        else:
            stalled_reason = "waiting_on_dependencies"
    elif status == "running" and row.current_task_id is None:
        stalled = True
        stalled_reason = "running_without_current_task_id"

    return dependencies_met, stalled, stalled_reason


@app.post("/pipelines", response_model=PipelineInfo, status_code=201)
async def create_pipeline_route(body: PipelineCreate):
    """Create and start a multi-stage pipeline."""
    import json as _json

    # Validate repo if provided
    if body.repo:
        resolved = settings.resolve_repo_path(body.repo)
        if resolved is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown repository: {body.repo!r}. "
                    f"Known repos: {list(settings.known_repos.keys())}"
                ),
            )

    # Serialize stages to JSON
    stages_json = _json.dumps([s.model_dump(exclude_none=True) for s in body.stages])

    # Serialize depends_on to JSON for storage
    depends_on_json: str | None = None
    if body.depends_on:
        depends_on_json = _json.dumps(body.depends_on)

    # Create pipeline row
    async with async_session() as session:
        row = await create_pipeline(
            session,
            repo=body.repo,
            stages_json=stages_json,
            max_review_iterations=body.max_review_iterations,
            model=body.model,
            depends_on_json=depends_on_json,
        )

        # Validate dependencies (after creation so we have the row ID)
        if body.depends_on:
            try:
                await validate_pipeline_dependencies(session, row.id, body.depends_on)
            except ValueError as e:
                # Roll back: delete the just-created pipeline
                from sqlalchemy import delete as sa_delete

                from .database import PipelineRow

                await session.execute(sa_delete(PipelineRow).where(PipelineRow.id == row.id))
                await session.commit()
                raise HTTPException(status_code=400, detail=str(e))

    # Start the pipeline (dispatches first stage)
    from .pipeline import start_pipeline

    await start_pipeline(row.id, pool.enqueue)

    # Refresh to get updated state after start
    async with async_session() as session:
        row = await get_pipeline(session, row.id)
        dependencies_met, stalled, stalled_reason = await _compute_pipeline_health(session, row)

    return _pipeline_row_to_info(
        row,
        dependencies_met=dependencies_met,
        stalled=stalled,
        stalled_reason=stalled_reason,
    )


@app.get("/pipelines", response_model=PipelineListResponse)
async def list_pipelines_route(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List all pipelines, optionally filtered by status."""
    async with async_session() as session:
        rows, total = await list_pipelines(session, status=status, limit=limit, offset=offset)

        infos: list[PipelineInfo] = []
        for row in rows:
            dependencies_met, stalled, stalled_reason = await _compute_pipeline_health(session, row)
            infos.append(
                _pipeline_row_to_info(
                    row,
                    dependencies_met=dependencies_met,
                    stalled=stalled,
                    stalled_reason=stalled_reason,
                )
            )

    return PipelineListResponse(
        pipelines=infos,
        total=total,
    )


@app.get("/pipelines/{pipeline_id}", response_model=PipelineInfo)
async def get_pipeline_route(pipeline_id: str):
    """Get details for a specific pipeline."""
    async with async_session() as session:
        row = await get_pipeline(session, pipeline_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")
        dependencies_met, stalled, stalled_reason = await _compute_pipeline_health(session, row)
    return _pipeline_row_to_info(
        row,
        dependencies_met=dependencies_met,
        stalled=stalled,
        stalled_reason=stalled_reason,
    )


@app.post("/pipelines/{pipeline_id}/cancel")
async def cancel_pipeline_route(pipeline_id: str):
    """Cancel a running pipeline and its current task.

    Sets the pipeline status to cancelled. If there's an active task,
    cancels it too.
    """
    async with async_session() as session:
        row = await get_pipeline(session, pipeline_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")

        if row.status in ("completed", "failed", "cancelled"):
            raise HTTPException(
                status_code=409,
                detail=f"Pipeline {pipeline_id} cannot be cancelled (status={row.status})",
            )

        await update_pipeline(
            session,
            pipeline_id,
            status="cancelled",
            completed_at=datetime.now(UTC),
        )

        # Cancel the current task if one is running
        current_task_id = row.current_task_id

    if current_task_id:
        async with async_session() as session:
            task_row = await get_task(session, current_task_id)
            if task_row and task_row.status not in ("completed", "failed", "cancelled"):
                await update_task(
                    session,
                    current_task_id,
                    status="cancelled",
                    phase="cancelled",
                    completed_at=datetime.now(UTC),
                )
        await pool.cancel_task(current_task_id)

    return {"status": "cancelled", "pipeline_id": pipeline_id}


# ---------------------------------------------------------------------------
# Schedule routes
# ---------------------------------------------------------------------------


@app.post("/schedules", response_model=ScheduleInfo, status_code=201)
async def create_schedule_route(body: ScheduleCreate):
    """Create a new cron schedule for recurring task/pipeline dispatch."""
    import json as _json

    # Validate cron expression
    if not validate_cron_expr(body.cron_expr):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid cron expression: {body.cron_expr!r}",
        )

    # Validate timezone
    import zoneinfo

    try:
        zoneinfo.ZoneInfo(body.timezone)
    except (KeyError, zoneinfo.ZoneInfoNotFoundError):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown timezone: {body.timezone!r}",
        )

    # Validate payload matches schedule_type
    from .models import PipelineCreate as PipelineCreateModel
    from .models import TaskCreate as TaskCreateModel

    if body.schedule_type == "task":
        try:
            TaskCreateModel(**body.payload.model_dump()) if isinstance(
                body.payload, TaskCreateModel
            ) else TaskCreateModel(**body.payload.model_dump())
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid task payload: {e}")
    elif body.schedule_type == "pipeline":
        try:
            PipelineCreateModel(**body.payload.model_dump()) if isinstance(
                body.payload, PipelineCreateModel
            ) else PipelineCreateModel(**body.payload.model_dump())
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid pipeline payload: {e}")

    # Compute first next_run time
    next_run = compute_next_run(body.cron_expr, body.timezone)

    # Serialize payload to JSON
    payload_json = _json.dumps(body.payload.model_dump(exclude_none=True))

    async with async_session() as session:
        row = await create_schedule(
            session,
            name=body.name,
            cron_expr=body.cron_expr,
            timezone=body.timezone,
            schedule_type=body.schedule_type.value,
            payload_json=payload_json,
            next_run_at=next_run,
        )

    return schedule_row_to_info(row)


@app.get("/schedules", response_model=ScheduleListResponse)
async def list_schedules_route(
    enabled_only: bool = False,
    limit: int = 50,
    offset: int = 0,
):
    """List all schedules."""
    async with async_session() as session:
        rows, total = await list_schedules(
            session, enabled_only=enabled_only, limit=limit, offset=offset
        )
    return ScheduleListResponse(
        schedules=[schedule_row_to_info(r) for r in rows],
        total=total,
    )


@app.get("/schedules/{schedule_id}", response_model=ScheduleInfo)
async def get_schedule_route(schedule_id: str):
    """Get details for a specific schedule."""
    async with async_session() as session:
        row = await get_schedule(session, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")
    return schedule_row_to_info(row)


@app.patch("/schedules/{schedule_id}", response_model=ScheduleInfo)
async def update_schedule_route(schedule_id: str, body: ScheduleUpdate):
    """Update a schedule's fields (name, cron, timezone, enabled, payload)."""
    import json as _json

    async with async_session() as session:
        row = await get_schedule(session, schedule_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")

    update_fields: dict = {}

    if body.name is not None:
        update_fields["name"] = body.name
    if body.enabled is not None:
        update_fields["enabled"] = body.enabled
    if body.payload is not None:
        update_fields["payload_json"] = _json.dumps(body.payload)

    # If cron or timezone changed, recompute next_run
    new_cron = body.cron_expr or row.cron_expr
    new_tz = body.timezone or row.timezone

    if body.cron_expr is not None:
        if not validate_cron_expr(body.cron_expr):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid cron expression: {body.cron_expr!r}",
            )
        update_fields["cron_expr"] = body.cron_expr

    if body.timezone is not None:
        import zoneinfo

        try:
            zoneinfo.ZoneInfo(body.timezone)
        except (KeyError, zoneinfo.ZoneInfoNotFoundError):
            raise HTTPException(
                status_code=400,
                detail=f"Unknown timezone: {body.timezone!r}",
            )
        update_fields["timezone"] = body.timezone

    if body.cron_expr is not None or body.timezone is not None:
        update_fields["next_run_at"] = compute_next_run(new_cron, new_tz)

    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    async with async_session() as session:
        updated = await update_schedule(session, schedule_id, **update_fields)
    return schedule_row_to_info(updated)


@app.delete("/schedules/{schedule_id}")
async def delete_schedule_route(schedule_id: str):
    """Delete a schedule permanently."""
    async with async_session() as session:
        deleted = await delete_schedule(session, schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")
    return {"status": "deleted", "schedule_id": schedule_id}


@app.post("/schedules/{schedule_id}/trigger")
async def trigger_schedule_route(schedule_id: str):
    """Manually trigger a schedule immediately, regardless of cron time."""
    try:
        dispatched_id = await scheduler.trigger_now(schedule_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "status": "triggered",
        "schedule_id": schedule_id,
        "dispatched_id": dispatched_id,
    }


@app.get("/events")
async def get_events(n: int = 50):
    """Return the last N lines from the event log."""
    from .events import tail

    lines = await tail(n)
    return {"events": lines, "count": len(lines)}


@app.get("/morning-report")
async def morning_report(hours: int = 12):
    """Generate a summary report of all work done in the last N hours.

    Returns structured data: completed tasks, failed tasks, PRs created,
    pipeline status, and an overall summary suitable for display.
    """
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    # Fetch only tasks/pipelines in the time window (pushes filter to DB)
    async with async_session() as session:
        recent_task_rows = await list_tasks_since(session, since=cutoff)
        recent_pipeline_rows = await list_pipelines_since(session, since=cutoff)

    recent_tasks = [task_row_to_info(t) for t in recent_task_rows]
    recent_pipelines = [_pipeline_row_to_info(p) for p in recent_pipeline_rows]

    # Categorize tasks
    completed = [t for t in recent_tasks if t.status.value == "completed"]
    failed = [t for t in recent_tasks if t.status.value == "failed"]
    running = [t for t in recent_tasks if t.status.value in ("running", "resolving", "creating_pr")]
    queued = [t for t in recent_tasks if t.status.value == "queued"]
    cancelled = [t for t in recent_tasks if t.status.value == "cancelled"]

    # PRs created
    prs = [
        {"task_id": t.id, "pr_url": t.pr_url, "repo": t.input.repo, "summary": t.summary}
        for t in completed
        if t.pr_url
    ]

    # Pipeline summaries
    pipeline_summaries = []
    for p in recent_pipelines:
        stages_done = (
            min(p.current_stage_index + 1, len(p.stages)) if p.status.value != "pending" else 0
        )
        pipeline_summaries.append(
            {
                "id": p.id,
                "status": p.status.value,
                "repo": p.repo,
                "stages_total": len(p.stages),
                "stages_completed": stages_done,
                "review_iterations": p.review_iteration,
                "max_review_iterations": p.max_review_iterations,
                "error": p.error,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "completed_at": p.completed_at.isoformat() if p.completed_at else None,
            }
        )

    # Task summaries (completed)
    task_summaries = []
    for t in completed:
        task_summaries.append(
            {
                "id": t.id,
                "repo": t.input.repo,
                "autonomy": t.input.autonomy.value,
                "summary": t.summary,
                "pr_url": t.pr_url,
                "branch": t.branch,
                "pipeline_id": t.pipeline_id,
                "stage_name": t.stage_name,
                "elapsed_seconds": (
                    (t.completed_at - t.started_at).total_seconds()
                    if t.completed_at and t.started_at
                    else None
                ),
            }
        )

    # Failed task details
    failure_details = []
    for t in failed:
        failure_details.append(
            {
                "id": t.id,
                "repo": t.input.repo,
                "error": t.error,
                "summary": t.summary,
                "pipeline_id": t.pipeline_id,
                "stage_name": t.stage_name,
            }
        )

    # Build overall summary text
    summary_lines = [f"Workbench report for the last {hours} hours:"]
    summary_lines.append(
        "  "
        f"{len(recent_tasks)} tasks dispatched, "
        f"{len(completed)} completed, "
        f"{len(failed)} failed, "
        f"{len(running)} still running"
    )
    if prs:
        summary_lines.append(f"  {len(prs)} draft PRs created:")
        for pr in prs:
            summary_lines.append(f"    - {pr['repo']}: {pr['pr_url']}")
    if recent_pipelines:
        summary_lines.append(f"  {len(recent_pipelines)} pipelines:")
        for p in pipeline_summaries:
            summary_lines.append(
                "    "
                f"- {p['id'][:12]} ({p['status']}): "
                f"{p['stages_completed']}/{p['stages_total']} stages, "
                f"{p['review_iterations']} review loops"
            )
    if failed:
        summary_lines.append(f"  {len(failed)} failures:")
        for f_detail in failure_details:
            summary_lines.append(
                f"    - {f_detail['id'][:12]}: {(f_detail['error'] or 'unknown')[:100]}"
            )

    return {
        "hours": hours,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": "\n".join(summary_lines),
        "counts": {
            "total": len(recent_tasks),
            "completed": len(completed),
            "failed": len(failed),
            "running": len(running),
            "queued": len(queued),
            "cancelled": len(cancelled),
            "prs_created": len(prs),
            "pipelines": len(recent_pipelines),
        },
        "prs": prs,
        "completed_tasks": task_summaries,
        "failed_tasks": failure_details,
        "pipelines": pipeline_summaries,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _run_prompt_file(args) -> None:
    """Handle `workbench run <file> [--repo ...] [--autonomy ...] [--model ...]`."""
    import httpx as _httpx

    file_path = Path(args.file)
    if not file_path.is_file():
        print(f"Error: file not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    content = file_path.read_text(encoding="utf-8")

    base_url = f"http://{args.api_host}:{args.api_port}"
    payload = {
        "type": "prompt_file",
        "source": str(file_path),
        "file_content": content,
        "repo": args.repo,
        "autonomy": args.autonomy,
        "model": args.model,
    }

    try:
        resp = _httpx.post(f"{base_url}/tasks", json=payload, timeout=30)
    except _httpx.ConnectError:
        print(
            f"Error: cannot connect to workbench at {base_url}. Is the service running?",
            file=sys.stderr,
        )
        sys.exit(1)

    if resp.status_code == 201:
        data = resp.json()
        task_id = data["id"]
        status = data["status"]
        print(f"Task created: {task_id} (status={status})")
        print(f"Track:  curl {base_url}/tasks/{task_id}")
        print(f"Stream: curl {base_url}/tasks/{task_id}/logs")
        if not args.no_follow:
            print()
            _follow_task(base_url, task_id)
    else:
        print(f"Error ({resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)


def _follow_task(base_url: str, task_id: str) -> None:
    """Follow a task's log stream via SSE until completion."""
    import httpx as _httpx

    url = f"{base_url}/tasks/{task_id}/logs"
    print(f"Following logs for task {task_id}...")
    print("---")

    try:
        with _httpx.stream("GET", url, timeout=None) as resp:
            if resp.status_code != 200:
                print(f"Error: SSE endpoint returned {resp.status_code}", file=sys.stderr)
                return

            for line in resp.iter_lines():
                if line.startswith("data: "):
                    import json as _json

                    try:
                        event = _json.loads(line[6:])
                        etype = event.get("type", "")
                        if etype == "log":
                            text = event.get("data", "")
                            sys.stdout.write(text)
                            sys.stdout.flush()
                        elif etype == "phase":
                            print(f"\n[phase: {event.get('phase', '?')}]")
                        elif etype == "done":
                            print(f"\n--- Task {task_id} finished: {event.get('status', '?')} ---")
                            return
                        elif etype == "error":
                            print(
                                f"\n--- Task {task_id} error: {event.get('error', '?')} ---",
                                file=sys.stderr,
                            )
                            return
                    except (ValueError, KeyError):
                        pass
    except KeyboardInterrupt:
        print(f"\nStopped following task {task_id}.")
    except _httpx.ConnectError:
        print(f"Error: lost connection to {base_url}", file=sys.stderr)


def cli():
    """Entry point for the `workbench` command."""
    parser = argparse.ArgumentParser(description="workbench — autonomous agent service")
    subparsers = parser.add_subparsers(dest="command")

    # --- serve (default behavior when no subcommand) ---
    serve_parser = subparsers.add_parser("serve", help="Start the API server")
    serve_parser.add_argument("--host", default=settings.host, help="Bind host")
    serve_parser.add_argument("--port", type=int, default=settings.port, help="Bind port")
    serve_parser.add_argument(
        "--workers",
        type=int,
        default=settings.max_workers,
        help="Max workers",
    )
    serve_parser.add_argument("--log-level", default="info", help="Log level")

    # --- run (submit a prompt file) ---
    run_parser = subparsers.add_parser("run", help="Submit a prompt file as a task")
    run_parser.add_argument("file", help="Path to .md or .json prompt file")
    run_parser.add_argument("--repo", default=None, help="Target repo short name")
    run_parser.add_argument(
        "--autonomy",
        default="full",
        choices=["full", "local", "plan_only", "research"],
        help="Autonomy level",
    )
    run_parser.add_argument("--model", default=None, help="Override LLM model")
    run_parser.add_argument(
        "--no-follow",
        action="store_true",
        help="Don't stream logs after submitting",
    )
    run_parser.add_argument(
        "--api-host",
        default=settings.host,
        help="API host",
    )
    run_parser.add_argument(
        "--api-port",
        type=int,
        default=settings.port,
        help="API port",
    )

    # --- status (quick task lookup) ---
    status_parser = subparsers.add_parser("status", help="Check task status")
    status_parser.add_argument("task_id", help="Task ID to check")
    status_parser.add_argument("--api-host", default=settings.host, help="API host")
    status_parser.add_argument("--api-port", type=int, default=settings.port, help="API port")

    # --- mcp (run MCP server in stdio mode) ---
    subparsers.add_parser("mcp", help="Run workbench MCP server (stdio mode)")

    # --- init-workspace (install workbench integration into a target workspace) ---
    init_parser = subparsers.add_parser(
        "init-workspace",
        help="Install workbench OpenCode integration into a workspace",
    )
    init_parser.add_argument(
        "target",
        nargs="?",
        default=".",
        help="Path to the workspace root (default: current directory)",
    )

    args = parser.parse_args()

    if args.command == "init-workspace":
        _init_workspace(args)
    elif args.command == "mcp":
        _run_mcp_server()
    elif args.command == "run":
        _run_prompt_file(args)
    elif args.command == "status":
        _check_status(args)
    elif args.command == "serve" or args.command is None:
        # Default: start the server
        if args.command == "serve":
            host = args.host
            port = args.port
            log_level = args.log_level
            if args.workers != settings.max_workers:
                pool.max_workers = args.workers
        else:
            host = settings.host
            port = settings.port
            log_level = "info"

        uvicorn.run(
            "workbench.main:app",
            host=host,
            port=port,
            log_level=log_level,
        )


def _run_mcp_server() -> None:
    """Handle `workbench mcp` — run the MCP server in stdio mode."""
    import asyncio

    from .mcp_server import run_mcp

    asyncio.run(run_mcp())


def _init_workspace(args) -> None:
    """Handle `workbench init-workspace [target]`.

    Installs the canonical OpenCode integration into the target workspace,
    including tool files, helper scripts, and required OpenCode config.
    """
    target = Path(args.target).resolve()
    workbench_repo = Path(__file__).resolve().parent.parent
    pkg_tools = workbench_repo / "opencode-tools"
    if not pkg_tools.is_dir():
        print(f"Error: cannot find opencode-tools/ at {pkg_tools}", file=sys.stderr)
        sys.exit(1)

    result = install_workspace(
        workspace_root=target,
        workbench_repo=workbench_repo,
        package_tools_dir=pkg_tools,
    )

    print(f"Workspace prepared: {target}")
    print("Installed tools:")
    for name in TOOL_FILES:
        path = result.tools_dir / name
        if path.exists():
            print(f"  - {path}")
    print(f"Updated OpenCode package: {result.package_json_path}")
    print(f"Updated OpenCode config:   {result.opencode_json_path}")
    print(f"Workbench env file:       {result.env_path}")
    print(f"Serve helper:             {result.serve_script_path}")
    print(f"MCP helper:               {result.mcp_script_path}")
    print("")
    print("Next steps:")
    print("  1. Ensure workbench itself is installed and migrated on this machine")
    print(f"  2. Start workbench: {result.serve_script_path}")
    print("  3. Verify workbench health: workbench doctor")
    print("  4. Verify workspace wiring: workbench smoke-test")
    print(
        f"  5. If needed, install .opencode dependencies: cd {target / '.opencode'} && npm install"
    )
    print("  6. Open a new OpenCode session in the workspace")
    print("  7. MCP integration is enabled in opencode.json")
    print("Set WORKBENCH_URL if the service is not at http://127.0.0.1:8420")


def _check_status(args) -> None:
    """Handle `workbench status <task_id>`."""
    import httpx as _httpx

    base_url = f"http://{args.api_host}:{args.api_port}"
    try:
        resp = _httpx.get(f"{base_url}/tasks/{args.task_id}", timeout=10)
    except _httpx.ConnectError:
        print(f"Error: cannot connect to {base_url}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code == 200:
        data = resp.json()
        print(f"Task:     {data['id']}")
        print(f"Status:   {data['status']}")
        if data.get("phase"):
            print(f"Phase:    {data['phase']}")
        if data.get("stale"):
            print("WARNING:  Task appears stale (no heartbeat)")
        if data.get("branch"):
            print(f"Branch:   {data['branch']}")
        if data.get("pr_url"):
            print(f"PR:       {data['pr_url']}")
        if data.get("error"):
            print(f"Error:    {data['error']}")
        print(f"Created:  {data['created_at']}")
        if data.get("started_at"):
            print(f"Started:  {data['started_at']}")
        if data.get("completed_at"):
            print(f"Finished: {data['completed_at']}")
    elif resp.status_code == 404:
        print(f"Task {args.task_id} not found", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"Error ({resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    cli()
