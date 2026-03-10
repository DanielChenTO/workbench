"""Database layer — SQLAlchemy async engine, ORM model, and CRUD operations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import settings

# ---------------------------------------------------------------------------
# Engine & session factory
# ---------------------------------------------------------------------------

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=settings.max_workers + 5,  # headroom for API queries
    max_overflow=10,
)

async_session = async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM model
# ---------------------------------------------------------------------------


class TaskRow(Base):
    """Persistent task record."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)

    # --- Input fields ---
    input_type: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(Text, default="")
    repo: Mapped[str | None] = mapped_column(String(100), nullable=True)
    autonomy: Mapped[str] = mapped_column(String(20), default="full")
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    extra_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_format: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # --- State ---
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    phase: Mapped[str | None] = mapped_column(String(50), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pr_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Timestamps ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- FSM / supervision ---
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    unblock_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Context pipeline ---
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-serialised list of ContextItem dicts. Stored so we can reconstruct
    # what context was injected into the prompt for debugging/audit.

    # --- Dependency tracking ---
    depends_on_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-serialised list of task IDs that must complete before this task runs.

    parent_task_id: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    # If set, the parent task's output is auto-injected as context.

    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Post-completion summary of what the task accomplished. Used as compact
    # context when this task is referenced by downstream tasks.

    # --- Pipeline link ---
    pipeline_id: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    stage_name: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # --- Orchestrator support ---
    role: Mapped[str] = mapped_column(String(20), default="worker", index=True)
    # 'worker' (default) or 'orchestrator'. Orchestrators get longer timeouts
    # and are expected to dispatch specialist tasks rather than implement directly.

    timeout: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Per-task timeout override in seconds. When NULL, falls back to
    # config.orchestrator_timeout (for orchestrators) or config.task_timeout (for workers).


class PipelineRow(Base):
    """Persistent pipeline record — multi-stage workflow with review-gated loops."""

    __tablename__ = "pipelines"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    repo: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stages_json: Mapped[str] = mapped_column(Text)  # JSON array of StageConfig dicts
    current_stage_index: Mapped[int] = mapped_column(Integer, default=0)
    current_task_id: Mapped[str | None] = mapped_column(String(12), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    max_review_iterations: Mapped[int] = mapped_column(Integer, default=3)
    review_iteration: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    task_ids_json: Mapped[str] = mapped_column(Text, default="[]")  # JSON array of task IDs
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Dependency tracking ---
    depends_on_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-serialised list of pipeline IDs that must complete before this pipeline starts.


class TodoRow(Base):
    """Local kanban todo item — may originate from Jira sync or manual creation."""

    __tablename__ = "todos"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="backlog")
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    column_order: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Jira integration ---
    jira_key: Mapped[str | None] = mapped_column(String(50), nullable=True, unique=True, index=True)
    jira_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    jira_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    jira_last_synced: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Source tracking ---
    source: Mapped[str] = mapped_column(String(50), default="manual")
    source_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # --- Timestamps ---
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ScheduleRow(Base):
    """Persistent schedule record — cron-based recurring task/pipeline dispatch."""

    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))  # Human-readable name
    cron_expr: Mapped[str] = mapped_column(String(100))  # Standard cron expression (5 fields)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")

    # What to dispatch: either a single task or a pipeline
    schedule_type: Mapped[str] = mapped_column(String(20))  # "task" or "pipeline"
    payload_json: Mapped[str] = mapped_column(Text)  # JSON: TaskCreate or PipelineCreate body

    # State
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_task_id: Mapped[str | None] = mapped_column(String(12), nullable=True)
    last_pipeline_id: Mapped[str | None] = mapped_column(String(12), nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # Last dispatch error

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Schema management
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """Create all tables if they don't exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose of the engine's connection pool."""
    await engine.dispose()


async def check_db() -> bool:
    """Return True if the database is reachable."""
    try:
        async with engine.connect() as conn:
            await conn.execute(select(func.now()))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


async def create_task(
    session: AsyncSession,
    *,
    input_type: str,
    source: str,
    repo: str | None,
    autonomy: str,
    model: str | None,
    extra_instructions: str | None,
    file_path: str | None = None,
    file_content: str | None = None,
    file_format: str | None = None,
    context_json: str | None = None,
    parent_task_id: str | None = None,
    depends_on_json: str | None = None,
    role: str = "worker",
    timeout: int | None = None,
) -> TaskRow:
    """Insert a new task and return it."""
    row = TaskRow(
        id=_new_id(),
        input_type=input_type,
        source=source,
        repo=repo,
        autonomy=autonomy,
        model=model,
        extra_instructions=extra_instructions,
        file_path=file_path,
        file_content=file_content,
        file_format=file_format,
        context_json=context_json,
        parent_task_id=parent_task_id,
        depends_on_json=depends_on_json,
        role=role,
        timeout=timeout,
        status="queued",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_task(session: AsyncSession, task_id: str) -> TaskRow | None:
    """Fetch a single task by ID."""
    return await session.get(TaskRow, task_id)


async def list_tasks(
    session: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[TaskRow], int]:
    """Return (tasks, total_count) with optional status filter."""
    query = select(TaskRow)
    count_query = select(func.count(TaskRow.id))

    if status:
        query = query.where(TaskRow.status == status)
        count_query = count_query.where(TaskRow.status == status)

    query = query.order_by(TaskRow.created_at.desc()).offset(offset).limit(limit)

    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query)).scalars().all()
    return list(rows), total


async def update_task(
    session: AsyncSession,
    task_id: str,
    **fields,
) -> TaskRow | None:
    """Update arbitrary fields on a task. Returns the updated row."""
    stmt = update(TaskRow).where(TaskRow.id == task_id).values(**fields)
    await session.execute(stmt)
    await session.commit()
    return await get_task(session, task_id)


async def load_queued_tasks(session: AsyncSession) -> list[TaskRow]:
    """Load tasks that were queued or active when the service last stopped.

    This is called on startup to re-enqueue incomplete work.
    Includes stuck and blocked tasks — the worker pool / watchdog will handle them.
    """
    query = (
        select(TaskRow)
        .where(TaskRow.status.in_(["queued", "resolving", "running", "stuck", "blocked"]))
        .order_by(TaskRow.created_at.asc())
    )
    rows = (await session.execute(query)).scalars().all()
    return list(rows)


async def find_stale_active_tasks(
    session: AsyncSession,
    stale_seconds: float = 120,
) -> list[TaskRow]:
    """Find active tasks whose heartbeat is older than `stale_seconds`.

    Used by the watchdog to detect stuck tasks.
    """
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    query = (
        select(TaskRow)
        .where(
            TaskRow.status.in_(["resolving", "running", "creating_pr"]),
            TaskRow.last_heartbeat < cutoff,
        )
        .order_by(TaskRow.last_heartbeat.asc())
    )
    rows = (await session.execute(query)).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# Dependency tracking helpers
# ---------------------------------------------------------------------------


async def check_dependencies_met(
    session: AsyncSession,
    task_id: str,
) -> tuple[bool, str | None]:
    """Check whether all dependencies of a task have completed.

    Returns (met, reason):
      - (True, None) if all dependencies are completed or there are none.
      - (False, None) if some dependencies are still pending/running (not ready yet).
      - (False, "Dependency <id> failed/cancelled") if any dependency is in a
        terminal failure state.
    """
    import json as _json

    row = await session.get(TaskRow, task_id)
    if row is None:
        return True, None

    raw = row.depends_on_json
    if not raw:
        return True, None

    try:
        dep_ids: list[str] = _json.loads(raw)
    except (ValueError, TypeError):
        return True, None

    if not dep_ids:
        return True, None

    for dep_id in dep_ids:
        dep_row = await session.get(TaskRow, dep_id)
        if dep_row is None:
            # Dependency doesn't exist — treat as failed
            return False, f"Dependency {dep_id} not found"

        if dep_row.status in ("failed", "cancelled"):
            return False, f"Dependency {dep_id} {dep_row.status}"

        if dep_row.status != "completed":
            # Not ready yet — still in progress
            return False, None

    return True, None


async def check_pipeline_dependencies_met(
    session: AsyncSession,
    pipeline_id: str,
) -> tuple[bool, str | None]:
    """Check whether all dependencies of a pipeline have completed.

    Same semantics as check_dependencies_met but for PipelineRow.
    """
    import json as _json

    row = await session.get(PipelineRow, pipeline_id)
    if row is None:
        return True, None

    raw = row.depends_on_json
    if not raw:
        return True, None

    try:
        dep_ids: list[str] = _json.loads(raw)
    except (ValueError, TypeError):
        return True, None

    if not dep_ids:
        return True, None

    for dep_id in dep_ids:
        dep_row = await session.get(PipelineRow, dep_id)
        if dep_row is None:
            return False, f"Dependency pipeline {dep_id} not found"

        if dep_row.status in ("failed", "cancelled"):
            return False, f"Dependency pipeline {dep_id} {dep_row.status}"

        if dep_row.status != "completed":
            return False, None

    return True, None


async def get_dependents(session: AsyncSession, task_id: str) -> list[TaskRow]:
    """Find tasks whose depends_on_json contains the given task_id.

    Uses SQL LIKE for the lookup — sufficient for the expected volume of
    tasks and avoids requiring a separate join table.
    """
    query = (
        select(TaskRow)
        .where(TaskRow.depends_on_json.isnot(None))
        .where(TaskRow.depends_on_json.contains(task_id))
        .order_by(TaskRow.created_at.asc())
    )
    rows = (await session.execute(query)).scalars().all()
    return list(rows)


async def validate_task_dependencies(
    session: AsyncSession,
    task_id: str | None,
    depends_on: list[str],
) -> None:
    """Validate a list of dependency task IDs at creation time.

    Raises ValueError if:
    - A dependency ID refers to the task itself (self-dependency).
    - A dependency ID does not exist in the database.
    - A dependency is in a terminal failed/cancelled state.
    - Adding these dependencies would create a circular dependency.
    """
    import json as _json

    if not depends_on:
        return

    # Check self-dependency
    if task_id and task_id in depends_on:
        raise ValueError(f"Self-dependency not allowed: {task_id}")

    # Check duplicates
    if len(depends_on) != len(set(depends_on)):
        raise ValueError("Duplicate dependency IDs")

    for dep_id in depends_on:
        dep_row = await session.get(TaskRow, dep_id)
        if dep_row is None:
            raise ValueError(f"Dependency task {dep_id} not found")

        if dep_row.status in ("failed", "cancelled"):
            raise ValueError(f"Dependency task {dep_id} is already {dep_row.status}")

    # Check for circular dependencies (BFS from each dep)
    if task_id:
        visited: set[str] = set()
        queue = list(depends_on)
        while queue:
            current = queue.pop(0)
            if current == task_id:
                raise ValueError(f"Circular dependency detected: {task_id} -> ... -> {task_id}")
            if current in visited:
                continue
            visited.add(current)
            row = await session.get(TaskRow, current)
            if row and row.depends_on_json:
                try:
                    upstream = _json.loads(row.depends_on_json)
                    queue.extend(upstream)
                except (ValueError, TypeError):
                    pass


async def validate_pipeline_dependencies(
    session: AsyncSession,
    pipeline_id: str | None,
    depends_on: list[str],
) -> None:
    """Validate a list of dependency pipeline IDs at creation time.

    Same validation rules as validate_task_dependencies but for pipelines.
    """
    import json as _json

    if not depends_on:
        return

    if pipeline_id and pipeline_id in depends_on:
        raise ValueError(f"Self-dependency not allowed: {pipeline_id}")

    if len(depends_on) != len(set(depends_on)):
        raise ValueError("Duplicate dependency IDs")

    for dep_id in depends_on:
        dep_row = await session.get(PipelineRow, dep_id)
        if dep_row is None:
            raise ValueError(f"Dependency pipeline {dep_id} not found")

        if dep_row.status in ("failed", "cancelled"):
            raise ValueError(f"Dependency pipeline {dep_id} is already {dep_row.status}")

    if pipeline_id:
        visited: set[str] = set()
        queue = list(depends_on)
        while queue:
            current = queue.pop(0)
            if current == pipeline_id:
                raise ValueError(
                    f"Circular dependency detected: {pipeline_id} -> ... -> {pipeline_id}"
                )
            if current in visited:
                continue
            visited.add(current)
            row = await session.get(PipelineRow, current)
            if row and row.depends_on_json:
                try:
                    upstream = _json.loads(row.depends_on_json)
                    queue.extend(upstream)
                except (ValueError, TypeError):
                    pass


# ---------------------------------------------------------------------------
# Pipeline CRUD
# ---------------------------------------------------------------------------


async def create_pipeline(
    session: AsyncSession,
    *,
    repo: str | None,
    stages_json: str,
    max_review_iterations: int = 3,
    model: str | None = None,
    depends_on_json: str | None = None,
) -> PipelineRow:
    """Insert a new pipeline and return it."""
    row = PipelineRow(
        id=_new_id(),
        repo=repo,
        stages_json=stages_json,
        max_review_iterations=max_review_iterations,
        model=model,
        depends_on_json=depends_on_json,
        status="pending",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_pipeline(session: AsyncSession, pipeline_id: str) -> PipelineRow | None:
    """Fetch a single pipeline by ID."""
    return await session.get(PipelineRow, pipeline_id)


async def update_pipeline(
    session: AsyncSession,
    pipeline_id: str,
    **fields,
) -> PipelineRow | None:
    """Update arbitrary fields on a pipeline."""
    stmt = update(PipelineRow).where(PipelineRow.id == pipeline_id).values(**fields)
    await session.execute(stmt)
    await session.commit()
    return await get_pipeline(session, pipeline_id)


async def list_pipelines(
    session: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[PipelineRow], int]:
    """Return (pipelines, total_count) with optional status filter."""
    query = select(PipelineRow)
    count_query = select(func.count(PipelineRow.id))

    if status:
        query = query.where(PipelineRow.status == status)
        count_query = count_query.where(PipelineRow.status == status)

    query = query.order_by(PipelineRow.created_at.desc()).offset(offset).limit(limit)

    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query)).scalars().all()
    return list(rows), total


# ---------------------------------------------------------------------------
# Schedule CRUD
# ---------------------------------------------------------------------------


async def create_schedule(
    session: AsyncSession,
    *,
    name: str,
    cron_expr: str,
    timezone: str,
    schedule_type: str,
    payload_json: str,
    next_run_at: datetime | None = None,
) -> ScheduleRow:
    """Insert a new schedule and return it."""
    row = ScheduleRow(
        id=_new_id(),
        name=name,
        cron_expr=cron_expr,
        timezone=timezone,
        schedule_type=schedule_type,
        payload_json=payload_json,
        next_run_at=next_run_at,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_schedule(session: AsyncSession, schedule_id: str) -> ScheduleRow | None:
    """Fetch a single schedule by ID."""
    return await session.get(ScheduleRow, schedule_id)


async def update_schedule(
    session: AsyncSession,
    schedule_id: str,
    **fields,
) -> ScheduleRow | None:
    """Update arbitrary fields on a schedule."""
    stmt = update(ScheduleRow).where(ScheduleRow.id == schedule_id).values(**fields)
    await session.execute(stmt)
    await session.commit()
    return await get_schedule(session, schedule_id)


async def list_schedules(
    session: AsyncSession,
    *,
    enabled_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ScheduleRow], int]:
    """Return (schedules, total_count) with optional enabled filter."""
    query = select(ScheduleRow)
    count_query = select(func.count(ScheduleRow.id))

    if enabled_only:
        query = query.where(ScheduleRow.enabled.is_(True))
        count_query = count_query.where(ScheduleRow.enabled.is_(True))

    query = query.order_by(ScheduleRow.created_at.desc()).offset(offset).limit(limit)

    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query)).scalars().all()
    return list(rows), total


async def delete_schedule(session: AsyncSession, schedule_id: str) -> bool:
    """Delete a schedule by ID. Returns True if deleted, False if not found."""
    row = await session.get(ScheduleRow, schedule_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def list_tasks_since(
    session: AsyncSession,
    *,
    since: datetime,
    status: str | None = None,
) -> list[TaskRow]:
    """Return tasks created at or after `since`, ordered by created_at desc.

    Pushes the date filter to the database instead of fetching all rows
    and filtering in Python.
    """
    query = select(TaskRow).where(TaskRow.created_at >= since)

    if status:
        query = query.where(TaskRow.status == status)

    query = query.order_by(TaskRow.created_at.desc())
    rows = (await session.execute(query)).scalars().all()
    return list(rows)


async def list_pipelines_since(
    session: AsyncSession,
    *,
    since: datetime,
    status: str | None = None,
) -> list[PipelineRow]:
    """Return pipelines created at or after `since`, ordered by created_at desc.

    Pushes the date filter to the database instead of fetching all rows
    and filtering in Python.
    """
    query = select(PipelineRow).where(PipelineRow.created_at >= since)

    if status:
        query = query.where(PipelineRow.status == status)

    query = query.order_by(PipelineRow.created_at.desc())
    rows = (await session.execute(query)).scalars().all()
    return list(rows)


async def get_due_schedules(session: AsyncSession, now: datetime) -> list[ScheduleRow]:
    """Return all enabled schedules whose next_run_at is at or before `now`."""
    query = (
        select(ScheduleRow)
        .where(
            ScheduleRow.enabled.is_(True),
            ScheduleRow.next_run_at.isnot(None),
            ScheduleRow.next_run_at <= now,
        )
        .order_by(ScheduleRow.next_run_at.asc())
    )
    rows = (await session.execute(query)).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# Todo CRUD helpers (used by Jira sync and future kanban features)
# ---------------------------------------------------------------------------


async def create_todo(
    session: AsyncSession,
    *,
    title: str,
    description: str | None = None,
    status: str = "backlog",
    priority: str = "medium",
    column_order: int = 0,
    tags: str | None = None,
    jira_key: str | None = None,
    jira_url: str | None = None,
    jira_status: str | None = None,
    jira_last_synced: datetime | None = None,
    source: str = "manual",
    source_ref: str | None = None,
) -> TodoRow:
    """Insert a new todo and return it."""
    row = TodoRow(
        id=_new_id(),
        title=title,
        description=description,
        status=status,
        priority=priority,
        column_order=column_order,
        tags=tags,
        jira_key=jira_key,
        jira_url=jira_url,
        jira_status=jira_status,
        jira_last_synced=jira_last_synced,
        source=source,
        source_ref=source_ref,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_todo_by_jira_key(session: AsyncSession, jira_key: str) -> TodoRow | None:
    """Fetch a todo by its Jira issue key."""
    query = select(TodoRow).where(TodoRow.jira_key == jira_key)
    return (await session.execute(query)).scalar_one_or_none()


async def update_todo(
    session: AsyncSession,
    todo_id: str,
    **fields,
) -> TodoRow | None:
    """Update arbitrary fields on a todo. Returns the updated row."""
    stmt = update(TodoRow).where(TodoRow.id == todo_id).values(**fields)
    await session.execute(stmt)
    await session.commit()
    return await session.get(TodoRow, todo_id)


async def get_todo(session: AsyncSession, todo_id: str) -> TodoRow | None:
    """Fetch a single todo by ID."""
    return await session.get(TodoRow, todo_id)


async def list_todos(
    session: AsyncSession,
    *,
    status: str | None = None,
    source: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TodoRow]:
    """Return todos with optional status/source filters, ordered by column_order."""
    query = select(TodoRow)

    if status:
        query = query.where(TodoRow.status == status)
    if source:
        query = query.where(TodoRow.source == source)

    query = query.order_by(TodoRow.column_order.asc(), TodoRow.created_at.desc())
    query = query.offset(offset).limit(limit)

    rows = (await session.execute(query)).scalars().all()
    return list(rows)


async def count_todos(
    session: AsyncSession,
    *,
    status: str | None = None,
    source: str | None = None,
) -> int:
    """Return the total number of todos matching the given filters."""
    query = select(func.count(TodoRow.id))
    if status:
        query = query.where(TodoRow.status == status)
    if source:
        query = query.where(TodoRow.source == source)
    result = await session.execute(query)
    return result.scalar_one()


async def delete_todo(session: AsyncSession, todo_id: str) -> bool:
    """Delete a todo by ID. Returns True if deleted, False if not found."""
    row = await session.get(TodoRow, todo_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def list_jira_todos(session: AsyncSession) -> list[TodoRow]:
    """Return all todos where source='jira', ordered by created_at desc."""
    query = select(TodoRow).where(TodoRow.source == "jira").order_by(TodoRow.created_at.desc())
    rows = (await session.execute(query)).scalars().all()
    return list(rows)


async def shift_todo_siblings(
    session: AsyncSession,
    target_status: str,
    target_order: int,
    exclude_id: str | None = None,
) -> None:
    """Shift column_order of siblings at or after *target_order* up by 1.

    This makes room for an item being inserted or moved to ``target_order``
    in the given status column.  The item identified by *exclude_id* is
    skipped so it doesn't bump itself.
    """
    conditions = [
        TodoRow.status == target_status,
        TodoRow.column_order >= target_order,
    ]
    if exclude_id:
        conditions.append(TodoRow.id != exclude_id)

    stmt = update(TodoRow).where(*conditions).values(column_order=TodoRow.column_order + 1)
    await session.execute(stmt)
