"""Data models for workbench tasks and pipelines."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TaskInputType(StrEnum):
    JIRA = "jira"
    GITHUB_ISSUE = "github_issue"
    PROMPT = "prompt"
    PROMPT_FILE = "prompt_file"


class Autonomy(StrEnum):
    FULL = "full"  # Research, code, test, lint, open PR
    LOCAL = "local"  # Branch, execute, commit — no push, no PR
    PLAN_ONLY = "plan_only"  # Research and produce a plan — no code changes
    RESEARCH_ONLY = "research"  # Investigate and summarize — no plan or code


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RESOLVING = "resolving"  # Resolving input (fetching Jira, GH issue, etc.)
    RUNNING = "running"  # OpenCode is executing
    CREATING_PR = "creating_pr"  # Pushing branch and opening draft PR
    BLOCKED = "blocked"  # Agent needs human input
    STUCK = "stuck"  # Watchdog detected stale heartbeat
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Context items — precise context injection for agent tasks
# ---------------------------------------------------------------------------


class ContextItem(BaseModel):
    """A single piece of context to inject into the agent prompt.

    Callers specify exactly what context each task needs. The context
    resolver fetches the actual content; only the resolved text lands
    in the prompt. This keeps prompts lean and precise.
    """

    type: Literal["task_output", "reference", "file", "text"]

    # --- task_output: inject a prior task's output ---
    task_id: str | None = Field(
        default=None,
        description="Task ID whose output should be injected. Used when type='task_output'.",
    )

    # --- reference: inject a section from a workspace reference doc ---
    doc: str | None = Field(
        default=None,
        description=(
            "Reference document filename (e.g. 'preupgrade-check-feasibility.md'). "
            "Looked up under work-directory/references/. Used when type='reference'."
        ),
    )
    section: str | None = Field(
        default=None,
        description=(
            "Optional section heading within the reference doc. "
            "If omitted, the entire document is included."
        ),
    )

    # --- file: inject a file from the workspace ---
    path: str | None = Field(
        default=None,
        description=(
            "File path relative to workspace root "
            "(e.g. 'my-service/pkg/api/handler.go'). "
            "Used when type='file'."
        ),
    )
    lines: str | None = Field(
        default=None,
        description=(
            "Optional line range, e.g. '10-50'. If omitted, the full file is included "
            "(truncated at 500 lines to avoid prompt bloat)."
        ),
    )

    # --- text: raw inline context ---
    content: str | None = Field(
        default=None,
        description="Raw text content. Used when type='text'.",
    )

    # --- Common ---
    label: str | None = Field(
        default=None,
        description=(
            "Human-readable label for this context block in the prompt. "
            "E.g. 'Prior research findings', 'API handler reference'. "
            "Auto-generated if omitted."
        ),
    )
    max_lines: int | None = Field(
        default=None,
        description="Override the default line limit for this context item.",
    )


# ---------------------------------------------------------------------------
# API request / response schemas
# ---------------------------------------------------------------------------


class TaskCreate(BaseModel):
    """Request body for POST /tasks."""

    type: TaskInputType
    source: str = Field(
        default="",
        description=(
            "Jira key (e.g. PROJ-1234), GitHub issue URL, or a plain-text prompt. "
            "Can be empty when type=prompt_file and file_content or file_path is provided."
        ),
    )
    repo: str | None = Field(
        default=None,
        description=(
            "Target repository short name (e.g. 'my-service'). "
            "If omitted the service will try to infer it from the source."
        ),
    )
    autonomy: Autonomy = Autonomy.FULL
    model: str | None = Field(
        default=None,
        description="Override the LLM model for this task.",
    )
    extra_instructions: str | None = Field(
        default=None,
        description="Additional instructions appended to the agent prompt.",
    )
    file_path: str | None = Field(
        default=None,
        description=(
            "Path to a .md or .json prompt file on disk. "
            "Used when type=prompt_file. Mutually exclusive with file_content."
        ),
    )
    file_content: str | None = Field(
        default=None,
        description=(
            "Inline content of a prompt file. "
            "Used when type=prompt_file. Mutually exclusive with file_path."
        ),
    )
    file_format: str | None = Field(
        default=None,
        description=(
            "Format hint: 'md' or 'json'. Auto-detected from file extension or content if omitted."
        ),
    )

    # --- Context injection ---
    context: list[ContextItem] = Field(
        default_factory=list,
        description=(
            "Explicit context items to inject into the agent prompt. "
            "Each item is resolved and placed in a labeled ## Context section. "
            "Use this for precise context control — only what the task needs."
        ),
    )
    parent_task_id: str | None = Field(
        default=None,
        description=(
            "Parent task ID for chaining. The parent's output and summary "
            "are automatically injected as context. The task does NOT wait "
            "for the parent — it must already be completed."
        ),
    )

    # --- Dependency tracking ---
    depends_on: list[str] = Field(
        default_factory=list,
        description=(
            "List of task IDs that must complete before this task can start. "
            "If any dependency fails or is cancelled, this task is automatically failed."
        ),
    )

    # --- Orchestrator support ---
    role: str = Field(
        default="worker",
        description=(
            "Task role: 'worker' (default) executes specialist work directly, "
            "'orchestrator' plans and dispatches specialist tasks. "
            "Orchestrators get longer timeouts and should not modify code directly."
        ),
    )
    timeout: int | None = Field(
        default=None,
        description=(
            "Per-task timeout in seconds. Overrides the default. "
            "When not set, orchestrators default to 7200s (2h), workers to 1800s (30m)."
        ),
    )


class TaskInfo(BaseModel):
    """Serialised task state returned by the API."""

    id: str
    input: TaskCreate
    status: TaskStatus
    phase: str | None = None
    branch: str | None = None
    pr_url: str | None = None
    resolved_prompt: str | None = None
    output: str | None = None
    summary: str | None = None
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    blocked_reason: str | None = None
    unblock_response: str | None = None
    parent_task_id: str | None = None
    pipeline_id: str | None = None
    stage_name: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_heartbeat: datetime | None = None
    depends_on: list[str] = Field(default_factory=list)
    dependencies_met: bool = True
    stale: bool = False
    role: str = "worker"
    timeout: int | None = None


class TaskListResponse(BaseModel):
    tasks: list[TaskInfo]
    total: int


# ---------------------------------------------------------------------------
# Pipeline models — multi-stage workflows with review-gated loops
# ---------------------------------------------------------------------------


class PipelineStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageConfig(BaseModel):
    """Configuration for a single pipeline stage."""

    name: str = Field(
        description="Human-readable stage name (e.g. 'explore', 'implement', 'review')."
    )
    autonomy: Autonomy = Field(description="Autonomy level for this stage.")
    prompt: str = Field(
        description="Prompt text for this stage. Previous stage output is auto-injected as context."
    )
    review_gate: bool = Field(
        default=False,
        description=(
            "If true, parse the stage output for APPROVE/REJECT verdict. "
            "On REJECT, loop back to `loop_to` stage with review feedback."
        ),
    )
    loop_to: int | None = Field(
        default=None,
        description=(
            "Stage index to loop back to on review rejection. "
            "Defaults to the stage immediately before this one."
        ),
    )
    model: str | None = Field(default=None, description="Override model for this stage.")
    extra_instructions: str | None = Field(
        default=None, description="Extra instructions for this stage."
    )


class PipelineCreate(BaseModel):
    """Request body for POST /pipelines."""

    repo: str | None = Field(
        default=None,
        description="Target repository for all stages (can be overridden per-stage via extra_instructions).",
    )
    stages: list[StageConfig] = Field(
        min_length=1,
        description="Ordered list of stages to execute.",
    )
    max_review_iterations: int = Field(
        default=3,
        description="Maximum times a review-gated stage can reject before failing the pipeline.",
    )
    model: str | None = Field(default=None, description="Default model for all stages.")

    # --- Dependency tracking ---
    depends_on: list[str] = Field(
        default_factory=list,
        description=(
            "List of pipeline IDs that must complete before this pipeline can start. "
            "If any dependency fails or is cancelled, this pipeline is automatically failed."
        ),
    )


class PipelineInfo(BaseModel):
    """Serialised pipeline state returned by the API."""

    id: str
    repo: str | None = None
    stages: list[StageConfig]
    current_stage_index: int = 0
    current_task_id: str | None = None
    status: PipelineStatus
    max_review_iterations: int = 3
    review_iteration: int = 0
    model: str | None = None
    task_ids: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    dependencies_met: bool = True
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class PipelineListResponse(BaseModel):
    pipelines: list[PipelineInfo]
    total: int


# ---------------------------------------------------------------------------
# Schedule models — cron-based recurring task/pipeline dispatch
# ---------------------------------------------------------------------------


class ScheduleType(StrEnum):
    TASK = "task"
    PIPELINE = "pipeline"


class ScheduleCreate(BaseModel):
    """Request body for POST /schedules."""

    name: str = Field(description="Human-readable name for this schedule.")
    cron_expr: str = Field(
        description=(
            "Standard 5-field cron expression (minute hour day-of-month month day-of-week). "
            "Examples: '0 22 * * *' (daily at 10pm), '0 22 * * 1-5' (weeknights at 10pm), "
            "'*/30 * * * *' (every 30 minutes)."
        ),
    )
    timezone: str = Field(
        default="US/Pacific",
        description="IANA timezone for the cron expression (e.g. 'US/Pacific', 'UTC').",
    )
    schedule_type: ScheduleType = Field(
        description="What to dispatch: 'task' for a single task, 'pipeline' for a multi-stage pipeline.",
    )
    payload: TaskCreate | PipelineCreate = Field(
        description=(
            "The task or pipeline definition to dispatch on each trigger. "
            "Must match schedule_type: TaskCreate for 'task', PipelineCreate for 'pipeline'."
        ),
    )
    enabled: bool = Field(default=True, description="Whether the schedule is active.")


class ScheduleInfo(BaseModel):
    """Serialised schedule state returned by the API."""

    id: str
    name: str
    cron_expr: str
    timezone: str
    schedule_type: ScheduleType
    payload: dict  # Raw JSON payload (TaskCreate or PipelineCreate)
    enabled: bool
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_task_id: str | None = None
    last_pipeline_id: str | None = None
    run_count: int = 0
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class ScheduleUpdate(BaseModel):
    """Request body for PATCH /schedules/{id}."""

    name: str | None = None
    cron_expr: str | None = None
    timezone: str | None = None
    enabled: bool | None = None
    payload: dict | None = None


class ScheduleListResponse(BaseModel):
    schedules: list[ScheduleInfo]
    total: int


# ---------------------------------------------------------------------------
# Staleness threshold
# ---------------------------------------------------------------------------

HEARTBEAT_STALE_SECONDS = 120  # Consider a task stale if no heartbeat for 2 minutes


# ---------------------------------------------------------------------------
# Todo / kanban models
# ---------------------------------------------------------------------------


class TodoCreate(BaseModel):
    """Request body for POST /todos."""

    title: str = Field(description="Title of the todo item.")
    description: str | None = Field(default=None, description="Optional description.")
    status: str = Field(
        default="backlog", description="Kanban column (e.g. backlog, todo, in_progress, done)."
    )
    priority: str = Field(default="medium", description="Priority level (high, medium, low).")
    tags: list[str] | None = Field(default=None, description="Optional tags.")


class TodoUpdate(BaseModel):
    """Request body for PATCH /todos/{id}."""

    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    tags: list[str] | None = None
    column_order: int | None = None


class TodoReorder(BaseModel):
    """Request body for POST /todos/{id}/reorder — move to new column/position."""

    status: str = Field(description="Target kanban column.")
    order: int = Field(description="Target position within the column.")


class TodoInfo(BaseModel):
    """Serialised todo state returned by the API."""

    id: str
    title: str
    description: str | None = None
    status: str = "backlog"
    priority: str = "medium"
    column_order: int = 0
    tags: list[str] | None = None

    # Jira integration
    jira_key: str | None = None
    jira_url: str | None = None
    jira_status: str | None = None
    jira_last_synced: datetime | None = None

    # Source tracking
    source: str = "manual"
    source_ref: str | None = None

    # Timestamps
    created_at: datetime
    updated_at: datetime


class TodoListResponse(BaseModel):
    """Paginated list of todos."""

    todos: list[TodoInfo]
    total: int


class TodoCoverageTaskRef(BaseModel):
    """Task reference shown in todo coverage summaries."""

    id: str
    status: TaskStatus
    repo: str | None = None
    summary: str | None = None
    pipeline_id: str | None = None
    stage_name: str | None = None
    created_at: datetime


class TodoCoverageInfo(BaseModel):
    """Coverage summary linking one todo to related tasks/pipelines."""

    todo_id: str
    jira_key: str | None = None
    source_ref: str | None = None
    initiative_tags: list[str] = Field(default_factory=list)
    repo_hints: list[str] = Field(default_factory=list)
    related_active_task_count: int = 0
    related_recent_task_count: int = 0
    related_pipeline_count: int = 0
    active_tasks: list[TodoCoverageTaskRef] = Field(default_factory=list)
    recent_tasks: list[TodoCoverageTaskRef] = Field(default_factory=list)
    related_pipeline_ids: list[str] = Field(default_factory=list)
    needs_task: bool = True


class TodoCoverageSummary(BaseModel):
    """High-level coverage counters for the board."""

    total_todos: int
    covered_todos: int
    uncovered_todos: int
    active_linked_todos: int


class TodoCoverageListResponse(BaseModel):
    """Todo coverage response returned by GET /todos/coverage."""

    generated_at: datetime
    recent_hours: int
    coverages: list[TodoCoverageInfo]
    summary: TodoCoverageSummary


# ---------------------------------------------------------------------------
# Workflow memory metadata models
# ---------------------------------------------------------------------------


class WorkflowMemoryCreate(BaseModel):
    """Request body for creating workflow memory metadata."""

    repo: str = Field(description="Repository short name for this memory artifact.")
    kind: str = Field(description="Memory artifact kind (e.g. decision, plan, summary).")
    artifact_ref: str = Field(description="Stable artifact reference identifier.")
    tags: list[str] | None = Field(default=None, description="Optional tag labels.")
    summary: str | None = Field(default=None, description="Optional human-readable summary.")
    artifact_path: str | None = Field(default=None, description="Optional artifact file path.")
    task_id: str | None = Field(default=None, description="Optional source task linkage.")
    pipeline_id: str | None = Field(default=None, description="Optional source pipeline linkage.")


class WorkflowMemoryInfo(BaseModel):
    """Serialized workflow memory metadata record."""

    id: str
    repo: str
    kind: str
    artifact_ref: str
    tags: list[str] | None = None
    summary: str | None = None
    artifact_path: str | None = None
    task_id: str | None = None
    pipeline_id: str | None = None
    created_at: datetime
    updated_at: datetime


class WorkflowMemoryListResponse(BaseModel):
    """Paginated list of workflow memory metadata records."""

    memories: list[WorkflowMemoryInfo]
    total: int


# ---------------------------------------------------------------------------
# Jira sync models
# ---------------------------------------------------------------------------


class JiraSyncRequest(BaseModel):
    """Request body for POST /todos/sync-jira."""

    jql: str = Field(description="JQL query to fetch issues from Jira.")
    max_results: int = Field(
        default=50,
        description="Maximum results per page (pagination fetches all matching issues).",
    )
    status_mapping: dict[str, str] | None = Field(
        default=None,
        description=(
            "Custom mapping from Jira status name to local kanban column. "
            'Default: {"To Do": "todo", "In Progress": "in_progress", '
            '"In Review": "review", "Done": "done"}. '
            'Unmapped statuses fall back to "backlog".'
        ),
    )


class JiraSyncResult(BaseModel):
    """Response from a Jira sync operation."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list[str] = Field(default_factory=list)
    synced_at: datetime


# ---------------------------------------------------------------------------
# Conversion helper: DB row -> API response
# ---------------------------------------------------------------------------


def task_row_to_info(row) -> TaskInfo:
    """Convert a database.TaskRow into a TaskInfo API response.

    Accepts any object with the TaskRow column attributes (duck-typed so we
    don't import database.py here and create a circular dependency).
    """
    # Compute staleness: if task is actively running and heartbeat is old
    stale = False
    if row.status in ("resolving", "running", "creating_pr") and row.last_heartbeat is not None:
        age = (datetime.now(UTC) - row.last_heartbeat).total_seconds()
        stale = age > HEARTBEAT_STALE_SECONDS

    # Reconstruct context items from stored JSON
    context_items: list[ContextItem] = []
    raw_context = getattr(row, "context_json", None)
    if raw_context:
        try:
            context_items = [ContextItem(**item) for item in json.loads(raw_context)]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Parse depends_on from stored JSON
    depends_on: list[str] = []
    raw_depends = getattr(row, "depends_on_json", None)
    if raw_depends:
        try:
            depends_on = json.loads(raw_depends)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    return TaskInfo(
        id=row.id,
        input=TaskCreate(
            type=TaskInputType(row.input_type),
            source=row.source,
            repo=row.repo,
            autonomy=Autonomy(row.autonomy),
            model=row.model,
            extra_instructions=row.extra_instructions,
            file_path=row.file_path,
            file_content=None,  # Don't echo back inline content in responses
            file_format=row.file_format,
            context=context_items,
            parent_task_id=getattr(row, "parent_task_id", None),
            role=getattr(row, "role", "worker") or "worker",
            timeout=getattr(row, "timeout", None),
        ),
        status=TaskStatus(row.status),
        phase=row.phase,
        branch=row.branch,
        pr_url=row.pr_url,
        resolved_prompt=row.resolved_prompt,
        output=row.output,
        summary=getattr(row, "summary", None),
        error=row.error,
        retry_count=getattr(row, "retry_count", 0) or 0,
        max_retries=getattr(row, "max_retries", 3) or 3,
        blocked_reason=getattr(row, "blocked_reason", None),
        unblock_response=getattr(row, "unblock_response", None),
        parent_task_id=getattr(row, "parent_task_id", None),
        pipeline_id=getattr(row, "pipeline_id", None),
        stage_name=getattr(row, "stage_name", None),
        depends_on=depends_on,
        dependencies_met=getattr(row, "_dependencies_met", True),
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        last_heartbeat=row.last_heartbeat,
        stale=stale,
        role=getattr(row, "role", "worker") or "worker",
        timeout=getattr(row, "timeout", None),
    )


def schedule_row_to_info(row) -> ScheduleInfo:
    """Convert a database.ScheduleRow into a ScheduleInfo API response."""
    try:
        payload = json.loads(row.payload_json)
    except (json.JSONDecodeError, TypeError):
        payload = {}

    return ScheduleInfo(
        id=row.id,
        name=row.name,
        cron_expr=row.cron_expr,
        timezone=row.timezone,
        schedule_type=ScheduleType(row.schedule_type),
        payload=payload,
        enabled=row.enabled,
        last_run_at=row.last_run_at,
        next_run_at=row.next_run_at,
        last_task_id=row.last_task_id,
        last_pipeline_id=row.last_pipeline_id,
        run_count=row.run_count,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def todo_row_to_info(row) -> TodoInfo:
    """Convert a database.TodoRow into a TodoInfo API response."""
    # Parse tags from JSON string
    tags: list[str] | None = None
    raw_tags = getattr(row, "tags", None)
    if raw_tags:
        try:
            tags = json.loads(raw_tags)
        except (json.JSONDecodeError, TypeError, ValueError):
            tags = None

    return TodoInfo(
        id=row.id,
        title=row.title,
        description=row.description,
        status=row.status,
        priority=row.priority,
        column_order=row.column_order,
        tags=tags,
        jira_key=row.jira_key,
        jira_url=row.jira_url,
        jira_status=row.jira_status,
        jira_last_synced=row.jira_last_synced,
        source=row.source,
        source_ref=row.source_ref,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def workflow_memory_row_to_info(row) -> WorkflowMemoryInfo:
    """Convert a database.WorkflowMemoryRow into WorkflowMemoryInfo."""
    tags: list[str] | None = None
    raw_tags = getattr(row, "tags", None)
    if raw_tags:
        try:
            tags = json.loads(raw_tags)
        except (json.JSONDecodeError, TypeError, ValueError):
            tags = None

    return WorkflowMemoryInfo(
        id=row.id,
        repo=row.repo,
        kind=row.kind,
        artifact_ref=row.artifact_ref,
        tags=tags,
        summary=row.summary,
        artifact_path=row.artifact_path,
        task_id=row.task_id,
        pipeline_id=row.pipeline_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
