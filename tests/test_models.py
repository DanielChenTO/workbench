"""Unit tests for workbench.models — enums, Pydantic models, and DB row converters."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from workbench.models import (
    HEARTBEAT_STALE_SECONDS,
    Autonomy,
    ContextItem,
    PipelineCreate,
    PipelineInfo,
    PipelineStatus,
    ScheduleCreate,
    ScheduleInfo,
    ScheduleType,
    ScheduleUpdate,
    StageConfig,
    TaskCreate,
    TaskInfo,
    TaskInputType,
    TaskListResponse,
    TaskStatus,
    schedule_row_to_info,
    task_row_to_info,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task_row(**overrides) -> SimpleNamespace:
    """Build a minimal fake DB row suitable for task_row_to_info."""
    now = datetime.now(UTC)
    defaults = dict(
        id="task-001",
        input_type="prompt",
        source="do something",
        repo=None,
        autonomy="full",
        model=None,
        extra_instructions=None,
        file_path=None,
        file_format=None,
        context_json=None,
        depends_on_json=None,
        parent_task_id=None,
        status="queued",
        phase=None,
        branch=None,
        pr_url=None,
        resolved_prompt=None,
        output=None,
        summary=None,
        error=None,
        retry_count=0,
        max_retries=3,
        blocked_reason=None,
        unblock_response=None,
        pipeline_id=None,
        stage_name=None,
        created_at=now,
        started_at=None,
        completed_at=None,
        last_heartbeat=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_schedule_row(**overrides) -> SimpleNamespace:
    """Build a minimal fake DB row suitable for schedule_row_to_info."""
    now = datetime.now(UTC)
    defaults = dict(
        id="sched-001",
        name="nightly",
        cron_expr="0 22 * * *",
        timezone="US/Pacific",
        schedule_type="task",
        payload_json='{"type": "prompt", "source": "do stuff"}',
        enabled=True,
        last_run_at=None,
        next_run_at=None,
        last_task_id=None,
        last_pipeline_id=None,
        run_count=0,
        error=None,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ===========================================================================
# Enum tests
# ===========================================================================


class TestTaskInputType:
    def test_all_values(self):
        assert TaskInputType.JIRA == "jira"
        assert TaskInputType.GITHUB_ISSUE == "github_issue"
        assert TaskInputType.PROMPT == "prompt"
        assert TaskInputType.PROMPT_FILE == "prompt_file"

    def test_member_count(self):
        assert len(TaskInputType) == 4

    def test_string_coercion(self):
        """StrEnum members are usable as plain strings."""
        assert f"type={TaskInputType.JIRA}" == "type=jira"


class TestAutonomy:
    def test_all_values(self):
        assert Autonomy.FULL == "full"
        assert Autonomy.LOCAL == "local"
        assert Autonomy.PLAN_ONLY == "plan_only"
        assert Autonomy.RESEARCH_ONLY == "research"

    def test_member_count(self):
        assert len(Autonomy) == 4


class TestTaskStatus:
    def test_all_values(self):
        expected = {
            "queued", "resolving", "running", "creating_pr",
            "blocked", "stuck", "completed", "failed", "cancelled",
        }
        assert {s.value for s in TaskStatus} == expected

    def test_member_count(self):
        assert len(TaskStatus) == 9

    def test_terminal_states(self):
        """Completed, failed, cancelled are terminal — tasks in these states
        should not be picked up by the worker again."""
        terminal = {"completed", "failed", "cancelled"}
        for status in TaskStatus:
            if status.value in terminal:
                assert status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)

    def test_active_states(self):
        """Resolving, running, creating_pr are active — tasks occupy a worker slot."""
        active = {TaskStatus.RESOLVING, TaskStatus.RUNNING, TaskStatus.CREATING_PR}
        assert len(active) == 3

    def test_string_equality(self):
        assert TaskStatus.QUEUED == "queued"
        assert TaskStatus.RUNNING == "running"


class TestPipelineStatus:
    def test_all_values(self):
        expected = {"pending", "running", "completed", "failed", "cancelled"}
        assert {s.value for s in PipelineStatus} == expected

    def test_member_count(self):
        assert len(PipelineStatus) == 5


class TestScheduleType:
    def test_all_values(self):
        assert ScheduleType.TASK == "task"
        assert ScheduleType.PIPELINE == "pipeline"

    def test_member_count(self):
        assert len(ScheduleType) == 2


# ===========================================================================
# ContextItem model tests
# ===========================================================================


class TestContextItem:
    def test_text_type(self):
        item = ContextItem(type="text", content="hello world")
        assert item.type == "text"
        assert item.content == "hello world"
        assert item.label is None

    def test_task_output_type(self):
        item = ContextItem(type="task_output", task_id="abc123")
        assert item.type == "task_output"
        assert item.task_id == "abc123"

    def test_reference_type(self):
        item = ContextItem(type="reference", doc="design.md", section="Architecture")
        assert item.type == "reference"
        assert item.doc == "design.md"
        assert item.section == "Architecture"

    def test_file_type(self):
        item = ContextItem(type="file", path="src/main.go", lines="10-50")
        assert item.type == "file"
        assert item.path == "src/main.go"
        assert item.lines == "10-50"

    def test_file_type_no_lines(self):
        item = ContextItem(type="file", path="src/main.go")
        assert item.lines is None

    def test_custom_label(self):
        item = ContextItem(type="text", content="data", label="Custom Label")
        assert item.label == "Custom Label"

    def test_max_lines_override(self):
        item = ContextItem(type="file", path="big.log", max_lines=100)
        assert item.max_lines == 100

    def test_defaults_are_none(self):
        item = ContextItem(type="text", content="x")
        assert item.task_id is None
        assert item.doc is None
        assert item.section is None
        assert item.path is None
        assert item.lines is None
        assert item.label is None
        assert item.max_lines is None

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            ContextItem(type="invalid_type", content="data")

    def test_serialization_round_trip(self):
        item = ContextItem(type="reference", doc="ref.md", section="Intro", label="intro")
        data = item.model_dump()
        restored = ContextItem(**data)
        assert restored == item


# ===========================================================================
# TaskCreate model tests
# ===========================================================================


class TestTaskCreate:
    def test_minimal_prompt(self):
        tc = TaskCreate(type="prompt", source="implement feature X")
        assert tc.type == TaskInputType.PROMPT
        assert tc.source == "implement feature X"
        assert tc.autonomy == Autonomy.FULL  # default
        assert tc.repo is None
        assert tc.model is None
        assert tc.extra_instructions is None
        assert tc.context == []
        assert tc.parent_task_id is None

    def test_jira_type(self):
        tc = TaskCreate(type="jira", source="PROJ-1234")
        assert tc.type == TaskInputType.JIRA
        assert tc.source == "PROJ-1234"

    def test_github_issue_type(self):
        tc = TaskCreate(type="github_issue", source="https://github.com/org/repo/issues/42")
        assert tc.type == TaskInputType.GITHUB_ISSUE

    def test_prompt_file_with_file_path(self):
        tc = TaskCreate(
            type="prompt_file",
            source="",
            file_path="/tmp/task.md",
            file_format="md",
        )
        assert tc.type == TaskInputType.PROMPT_FILE
        assert tc.file_path == "/tmp/task.md"
        assert tc.file_content is None

    def test_prompt_file_with_file_content(self):
        tc = TaskCreate(
            type="prompt_file",
            source="",
            file_content="# Task\nDo something.",
            file_format="md",
        )
        assert tc.file_content == "# Task\nDo something."
        assert tc.file_path is None

    def test_autonomy_override(self):
        tc = TaskCreate(type="prompt", source="plan it", autonomy="research")
        assert tc.autonomy == Autonomy.RESEARCH_ONLY

    def test_invalid_autonomy_rejected(self):
        with pytest.raises(ValidationError):
            TaskCreate(type="prompt", source="x", autonomy="yolo")

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            TaskCreate(type="email", source="hello@test.com")

    def test_context_items_accepted(self):
        tc = TaskCreate(
            type="prompt",
            source="do it",
            context=[
                ContextItem(type="text", content="note"),
                ContextItem(type="file", path="foo.go"),
            ],
        )
        assert len(tc.context) == 2
        assert tc.context[0].type == "text"
        assert tc.context[1].type == "file"

    def test_source_defaults_to_empty_string(self):
        tc = TaskCreate(type="prompt_file", file_content="inline prompt")
        assert tc.source == ""

    def test_all_optional_fields(self):
        tc = TaskCreate(
            type="prompt",
            source="do stuff",
            repo="my-service",
            autonomy="local",
            model="claude-sonnet",
            extra_instructions="Be careful",
            parent_task_id="parent-001",
        )
        assert tc.repo == "my-service"
        assert tc.model == "claude-sonnet"
        assert tc.extra_instructions == "Be careful"
        assert tc.parent_task_id == "parent-001"

    def test_serialization_round_trip(self):
        tc = TaskCreate(type="jira", source="PROJ-42", repo="my-app", autonomy="plan_only")
        data = tc.model_dump()
        restored = TaskCreate(**data)
        assert restored.type == tc.type
        assert restored.source == tc.source
        assert restored.repo == tc.repo
        assert restored.autonomy == tc.autonomy


# ===========================================================================
# TaskInfo model tests
# ===========================================================================


class TestTaskInfo:
    def test_minimal_construction(self):
        now = datetime.now(UTC)
        info = TaskInfo(
            id="t-1",
            input=TaskCreate(type="prompt", source="hello"),
            status=TaskStatus.QUEUED,
            created_at=now,
        )
        assert info.id == "t-1"
        assert info.status == TaskStatus.QUEUED
        assert info.stale is False
        assert info.retry_count == 0
        assert info.max_retries == 3
        assert info.phase is None
        assert info.branch is None
        assert info.pr_url is None

    def test_stale_flag(self):
        now = datetime.now(UTC)
        info = TaskInfo(
            id="t-2",
            input=TaskCreate(type="prompt", source="x"),
            status=TaskStatus.RUNNING,
            created_at=now,
            stale=True,
        )
        assert info.stale is True


class TestTaskListResponse:
    def test_construction(self):
        now = datetime.now(UTC)
        tasks = [
            TaskInfo(
                id="t-1",
                input=TaskCreate(type="prompt", source="a"),
                status=TaskStatus.QUEUED,
                created_at=now,
            ),
        ]
        resp = TaskListResponse(tasks=tasks, total=1)
        assert resp.total == 1
        assert len(resp.tasks) == 1


# ===========================================================================
# StageConfig model tests
# ===========================================================================


class TestStageConfig:
    def test_minimal_construction(self):
        stage = StageConfig(name="explore", autonomy="research", prompt="explore the codebase")
        assert stage.name == "explore"
        assert stage.autonomy == Autonomy.RESEARCH_ONLY
        assert stage.prompt == "explore the codebase"

    def test_defaults(self):
        stage = StageConfig(name="s1", autonomy="full", prompt="go")
        assert stage.review_gate is False
        assert stage.loop_to is None
        assert stage.model is None
        assert stage.extra_instructions is None

    def test_review_gate_enabled(self):
        stage = StageConfig(
            name="review",
            autonomy="research",
            prompt="review changes",
            review_gate=True,
            loop_to=0,
        )
        assert stage.review_gate is True
        assert stage.loop_to == 0

    def test_loop_to_without_review_gate(self):
        """loop_to can be set independently — the pipeline orchestrator interprets it."""
        stage = StageConfig(
            name="s1",
            autonomy="full",
            prompt="go",
            review_gate=False,
            loop_to=1,
        )
        assert stage.loop_to == 1

    def test_model_override(self):
        stage = StageConfig(
            name="s1",
            autonomy="full",
            prompt="go",
            model="claude-opus",
        )
        assert stage.model == "claude-opus"

    def test_extra_instructions(self):
        stage = StageConfig(
            name="s1",
            autonomy="full",
            prompt="go",
            extra_instructions="Focus on tests",
        )
        assert stage.extra_instructions == "Focus on tests"

    def test_invalid_autonomy_rejected(self):
        with pytest.raises(ValidationError):
            StageConfig(name="s1", autonomy="invalid", prompt="go")

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            StageConfig(name="s1")  # type: ignore[call-arg]  # missing autonomy and prompt


# ===========================================================================
# PipelineCreate model tests
# ===========================================================================


class TestPipelineCreate:
    def _make_stage(self, name: str = "s1", **kwargs) -> StageConfig:
        return StageConfig(name=name, autonomy="full", prompt="do it", **kwargs)

    def test_minimal(self):
        pc = PipelineCreate(stages=[self._make_stage()])
        assert len(pc.stages) == 1
        assert pc.repo is None
        assert pc.max_review_iterations == 3
        assert pc.model is None

    def test_multiple_stages(self):
        pc = PipelineCreate(stages=[
            self._make_stage("explore"),
            self._make_stage("implement"),
            self._make_stage("review"),
        ])
        assert len(pc.stages) == 3
        assert pc.stages[0].name == "explore"
        assert pc.stages[2].name == "review"

    def test_empty_stages_rejected(self):
        """PipelineCreate requires at least one stage (min_length=1)."""
        with pytest.raises(ValidationError, match="List should have at least 1 item"):
            PipelineCreate(stages=[])

    def test_repo_and_model_override(self):
        pc = PipelineCreate(
            repo="my-service",
            stages=[self._make_stage()],
            model="claude-sonnet",
            max_review_iterations=5,
        )
        assert pc.repo == "my-service"
        assert pc.model == "claude-sonnet"
        assert pc.max_review_iterations == 5

    def test_stages_not_a_list_rejected(self):
        with pytest.raises(ValidationError):
            PipelineCreate(stages="not-a-list")  # type: ignore[arg-type]

    def test_serialization_round_trip(self):
        pc = PipelineCreate(
            repo="my-app",
            stages=[
                self._make_stage("plan"),
                self._make_stage("execute"),
            ],
            max_review_iterations=2,
        )
        data = pc.model_dump()
        restored = PipelineCreate(**data)
        assert len(restored.stages) == 2
        assert restored.repo == "my-app"
        assert restored.max_review_iterations == 2


# ===========================================================================
# PipelineInfo model tests
# ===========================================================================


class TestPipelineInfo:
    def test_minimal(self):
        now = datetime.now(UTC)
        info = PipelineInfo(
            id="pipe-1",
            stages=[StageConfig(name="s1", autonomy="full", prompt="go")],
            status=PipelineStatus.PENDING,
            created_at=now,
        )
        assert info.id == "pipe-1"
        assert info.status == PipelineStatus.PENDING
        assert info.current_stage_index == 0
        assert info.current_task_id is None
        assert info.max_review_iterations == 3
        assert info.review_iteration == 0
        assert info.task_ids == []
        assert info.error is None
        assert info.completed_at is None


# ===========================================================================
# Schedule model tests
# ===========================================================================


class TestScheduleCreate:
    def test_task_schedule(self):
        sc = ScheduleCreate(
            name="nightly",
            cron_expr="0 22 * * *",
            schedule_type="task",
            payload=TaskCreate(type="prompt", source="run nightly checks"),
        )
        assert sc.name == "nightly"
        assert sc.cron_expr == "0 22 * * *"
        assert sc.timezone == "US/Pacific"  # default
        assert sc.schedule_type == ScheduleType.TASK
        assert sc.enabled is True

    def test_pipeline_schedule(self):
        sc = ScheduleCreate(
            name="weekly pipeline",
            cron_expr="0 10 * * 1",
            schedule_type="pipeline",
            payload=PipelineCreate(
                stages=[StageConfig(name="s1", autonomy="full", prompt="go")],
            ),
        )
        assert sc.schedule_type == ScheduleType.PIPELINE

    def test_custom_timezone(self):
        sc = ScheduleCreate(
            name="utc job",
            cron_expr="*/30 * * * *",
            timezone="UTC",
            schedule_type="task",
            payload=TaskCreate(type="prompt", source="ping"),
        )
        assert sc.timezone == "UTC"

    def test_disabled_schedule(self):
        sc = ScheduleCreate(
            name="paused",
            cron_expr="0 0 * * *",
            schedule_type="task",
            payload=TaskCreate(type="prompt", source="x"),
            enabled=False,
        )
        assert sc.enabled is False

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            ScheduleCreate(name="incomplete")  # type: ignore[call-arg]


class TestScheduleUpdate:
    def test_all_none_by_default(self):
        su = ScheduleUpdate()
        assert su.name is None
        assert su.cron_expr is None
        assert su.timezone is None
        assert su.enabled is None
        assert su.payload is None

    def test_partial_update(self):
        su = ScheduleUpdate(name="new name", enabled=False)
        assert su.name == "new name"
        assert su.enabled is False
        assert su.cron_expr is None

    def test_payload_update(self):
        su = ScheduleUpdate(payload={"type": "prompt", "source": "updated"})
        assert su.payload == {"type": "prompt", "source": "updated"}


class TestScheduleInfo:
    def test_construction(self):
        now = datetime.now(UTC)
        info = ScheduleInfo(
            id="sched-1",
            name="nightly",
            cron_expr="0 22 * * *",
            timezone="US/Pacific",
            schedule_type=ScheduleType.TASK,
            payload={"type": "prompt", "source": "x"},
            enabled=True,
            run_count=5,
            created_at=now,
            updated_at=now,
        )
        assert info.id == "sched-1"
        assert info.run_count == 5
        assert info.last_run_at is None
        assert info.next_run_at is None
        assert info.error is None


# ===========================================================================
# task_row_to_info converter tests
# ===========================================================================


class TestTaskRowToInfo:
    def test_basic_conversion(self):
        row = _make_task_row()
        info = task_row_to_info(row)
        assert info.id == "task-001"
        assert info.status == TaskStatus.QUEUED
        assert info.input.type == TaskInputType.PROMPT
        assert info.input.source == "do something"
        assert info.stale is False

    def test_all_fields_mapped(self):
        now = datetime.now(UTC)
        row = _make_task_row(
            id="t-full",
            input_type="jira",
            source="PROJ-99",
            repo="my-app",
            autonomy="local",
            model="claude-opus",
            extra_instructions="be thorough",
            file_path="/tmp/prompt.md",
            file_format="md",
            status="running",
            phase="executing",
            branch="agent/t-full",
            pr_url="https://github.com/org/repo/pull/42",
            resolved_prompt="resolved text",
            output="output text",
            summary="summary text",
            error=None,
            retry_count=1,
            max_retries=5,
            blocked_reason=None,
            unblock_response=None,
            parent_task_id="parent-001",
            pipeline_id="pipe-001",
            stage_name="implement",
            created_at=now,
            started_at=now,
            completed_at=None,
            last_heartbeat=now,
        )
        info = task_row_to_info(row)
        assert info.id == "t-full"
        assert info.input.type == TaskInputType.JIRA
        assert info.input.source == "PROJ-99"
        assert info.input.repo == "my-app"
        assert info.input.autonomy == Autonomy.LOCAL
        assert info.input.model == "claude-opus"
        assert info.input.extra_instructions == "be thorough"
        assert info.input.file_path == "/tmp/prompt.md"
        assert info.input.file_content is None  # never echoed back
        assert info.input.file_format == "md"
        assert info.status == TaskStatus.RUNNING
        assert info.phase == "executing"
        assert info.branch == "agent/t-full"
        assert info.pr_url == "https://github.com/org/repo/pull/42"
        assert info.resolved_prompt == "resolved text"
        assert info.output == "output text"
        assert info.summary == "summary text"
        assert info.retry_count == 1
        assert info.max_retries == 5
        assert info.parent_task_id == "parent-001"
        assert info.pipeline_id == "pipe-001"
        assert info.stage_name == "implement"

    # --- Staleness detection ---

    def test_stale_when_heartbeat_old_and_running(self):
        """A running task with heartbeat older than threshold is stale."""
        old_heartbeat = datetime.now(UTC) - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 60)
        row = _make_task_row(status="running", last_heartbeat=old_heartbeat)
        info = task_row_to_info(row)
        assert info.stale is True

    def test_stale_when_heartbeat_old_and_resolving(self):
        old_heartbeat = datetime.now(UTC) - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 10)
        row = _make_task_row(status="resolving", last_heartbeat=old_heartbeat)
        info = task_row_to_info(row)
        assert info.stale is True

    def test_stale_when_heartbeat_old_and_creating_pr(self):
        old_heartbeat = datetime.now(UTC) - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 10)
        row = _make_task_row(status="creating_pr", last_heartbeat=old_heartbeat)
        info = task_row_to_info(row)
        assert info.stale is True

    def test_not_stale_when_heartbeat_recent(self):
        recent_heartbeat = datetime.now(UTC) - timedelta(seconds=30)
        row = _make_task_row(status="running", last_heartbeat=recent_heartbeat)
        info = task_row_to_info(row)
        assert info.stale is False

    def test_not_stale_when_heartbeat_just_under_threshold(self):
        """A heartbeat just under the threshold should NOT be stale."""
        # Subtract a 5-second buffer so we're clearly under the threshold,
        # even accounting for the few microseconds between creating the
        # heartbeat and the comparison inside task_row_to_info.
        heartbeat = datetime.now(UTC) - timedelta(seconds=HEARTBEAT_STALE_SECONDS - 5)
        row = _make_task_row(status="running", last_heartbeat=heartbeat)
        info = task_row_to_info(row)
        assert info.stale is False

    def test_not_stale_when_no_heartbeat(self):
        """No heartbeat at all — task just started, not stale."""
        row = _make_task_row(status="running", last_heartbeat=None)
        info = task_row_to_info(row)
        assert info.stale is False

    def test_not_stale_when_completed(self):
        """Terminal states are never stale even with old heartbeat."""
        old_heartbeat = datetime.now(UTC) - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 600)
        row = _make_task_row(status="completed", last_heartbeat=old_heartbeat)
        info = task_row_to_info(row)
        assert info.stale is False

    def test_not_stale_when_failed(self):
        old_heartbeat = datetime.now(UTC) - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 600)
        row = _make_task_row(status="failed", last_heartbeat=old_heartbeat)
        info = task_row_to_info(row)
        assert info.stale is False

    def test_not_stale_when_queued(self):
        old_heartbeat = datetime.now(UTC) - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 600)
        row = _make_task_row(status="queued", last_heartbeat=old_heartbeat)
        info = task_row_to_info(row)
        assert info.stale is False

    def test_not_stale_when_blocked(self):
        old_heartbeat = datetime.now(UTC) - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 600)
        row = _make_task_row(status="blocked", last_heartbeat=old_heartbeat)
        info = task_row_to_info(row)
        assert info.stale is False

    def test_not_stale_when_stuck(self):
        old_heartbeat = datetime.now(UTC) - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 600)
        row = _make_task_row(status="stuck", last_heartbeat=old_heartbeat)
        info = task_row_to_info(row)
        assert info.stale is False

    def test_not_stale_when_cancelled(self):
        old_heartbeat = datetime.now(UTC) - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 600)
        row = _make_task_row(status="cancelled", last_heartbeat=old_heartbeat)
        info = task_row_to_info(row)
        assert info.stale is False

    # --- Context JSON parsing ---

    def test_context_json_parsed(self):
        ctx = [{"type": "text", "content": "note"}, {"type": "file", "path": "foo.go"}]
        row = _make_task_row(context_json=json.dumps(ctx))
        info = task_row_to_info(row)
        assert len(info.input.context) == 2
        assert info.input.context[0].type == "text"
        assert info.input.context[0].content == "note"
        assert info.input.context[1].type == "file"
        assert info.input.context[1].path == "foo.go"

    def test_context_json_none(self):
        row = _make_task_row(context_json=None)
        info = task_row_to_info(row)
        assert info.input.context == []

    def test_context_json_empty_string(self):
        """Empty string should be treated as no context (falsy)."""
        row = _make_task_row(context_json="")
        info = task_row_to_info(row)
        assert info.input.context == []

    def test_context_json_invalid_json(self):
        """Malformed JSON should not crash — just produces empty context."""
        row = _make_task_row(context_json="not valid json{{{")
        info = task_row_to_info(row)
        assert info.input.context == []

    def test_context_json_invalid_items(self):
        """JSON that parses but has invalid ContextItem data should not crash."""
        row = _make_task_row(context_json='[{"type": "bogus"}]')
        info = task_row_to_info(row)
        # The invalid item should be silently skipped (caught by ValueError)
        assert info.input.context == []

    # --- Missing optional attributes (duck-typing robustness) ---

    def test_missing_optional_attrs_uses_defaults(self):
        """Rows from older schema versions may lack newer columns.
        getattr fallbacks should handle this gracefully."""
        row = SimpleNamespace(
            id="t-old",
            input_type="prompt",
            source="old task",
            repo=None,
            autonomy="full",
            model=None,
            extra_instructions=None,
            file_path=None,
            file_format=None,
            status="completed",
            phase=None,
            branch=None,
            pr_url=None,
            resolved_prompt=None,
            output="done",
            error=None,
            created_at=datetime.now(UTC),
            started_at=None,
            completed_at=datetime.now(UTC),
            last_heartbeat=None,
            # Deliberately omit: context_json, parent_task_id, summary,
            # retry_count, max_retries, blocked_reason, unblock_response,
            # pipeline_id, stage_name
        )
        info = task_row_to_info(row)
        assert info.id == "t-old"
        assert info.summary is None
        assert info.retry_count == 0
        assert info.max_retries == 3
        assert info.blocked_reason is None
        assert info.parent_task_id is None
        assert info.pipeline_id is None
        assert info.stage_name is None
        assert info.input.context == []

    # --- Return type ---

    def test_returns_task_info_instance(self):
        row = _make_task_row()
        info = task_row_to_info(row)
        assert isinstance(info, TaskInfo)


# ===========================================================================
# schedule_row_to_info converter tests
# ===========================================================================


class TestScheduleRowToInfo:
    def test_basic_conversion(self):
        row = _make_schedule_row()
        info = schedule_row_to_info(row)
        assert info.id == "sched-001"
        assert info.name == "nightly"
        assert info.cron_expr == "0 22 * * *"
        assert info.timezone == "US/Pacific"
        assert info.schedule_type == ScheduleType.TASK
        assert info.enabled is True
        assert info.run_count == 0
        assert isinstance(info.payload, dict)
        assert info.payload["type"] == "prompt"

    def test_payload_json_parsed(self):
        payload = {"type": "jira", "source": "PROJ-123", "repo": "my-app"}
        row = _make_schedule_row(payload_json=json.dumps(payload))
        info = schedule_row_to_info(row)
        assert info.payload == payload

    def test_invalid_payload_json(self):
        """Invalid JSON should result in empty dict, not crash."""
        row = _make_schedule_row(payload_json="not json!!!")
        info = schedule_row_to_info(row)
        assert info.payload == {}

    def test_null_payload_json(self):
        """None payload_json should result in empty dict."""
        row = _make_schedule_row(payload_json=None)
        info = schedule_row_to_info(row)
        assert info.payload == {}

    def test_all_fields_mapped(self):
        now = datetime.now(UTC)
        row = _make_schedule_row(
            id="sched-full",
            name="weekly pipeline",
            cron_expr="0 10 * * 1",
            timezone="UTC",
            schedule_type="pipeline",
            enabled=False,
            last_run_at=now,
            next_run_at=now + timedelta(days=7),
            last_task_id="task-999",
            last_pipeline_id="pipe-777",
            run_count=42,
            error="last run failed",
        )
        info = schedule_row_to_info(row)
        assert info.id == "sched-full"
        assert info.name == "weekly pipeline"
        assert info.schedule_type == ScheduleType.PIPELINE
        assert info.timezone == "UTC"
        assert info.enabled is False
        assert info.last_run_at == now
        assert info.next_run_at == now + timedelta(days=7)
        assert info.last_task_id == "task-999"
        assert info.last_pipeline_id == "pipe-777"
        assert info.run_count == 42
        assert info.error == "last run failed"

    def test_returns_schedule_info_instance(self):
        row = _make_schedule_row()
        info = schedule_row_to_info(row)
        assert isinstance(info, ScheduleInfo)


# ===========================================================================
# HEARTBEAT_STALE_SECONDS constant
# ===========================================================================


class TestHeartbeatConstant:
    def test_value(self):
        assert HEARTBEAT_STALE_SECONDS == 120

    def test_is_int(self):
        assert isinstance(HEARTBEAT_STALE_SECONDS, int)


# ===========================================================================
# Dependency tracking tests
# ===========================================================================


class TestTaskCreateDependsOn:
    """Tests for depends_on field on TaskCreate."""

    def test_default_empty(self):
        tc = TaskCreate(type="prompt", source="do it")
        assert tc.depends_on == []

    def test_depends_on_accepted(self):
        tc = TaskCreate(type="prompt", source="do it", depends_on=["task-a", "task-b"])
        assert tc.depends_on == ["task-a", "task-b"]

    def test_depends_on_serialization_round_trip(self):
        tc = TaskCreate(type="prompt", source="do it", depends_on=["task-a"])
        data = tc.model_dump()
        restored = TaskCreate(**data)
        assert restored.depends_on == ["task-a"]


class TestPipelineCreateDependsOn:
    """Tests for depends_on field on PipelineCreate."""

    def _make_stage(self) -> StageConfig:
        return StageConfig(name="s1", autonomy="full", prompt="go")

    def test_default_empty(self):
        pc = PipelineCreate(stages=[self._make_stage()])
        assert pc.depends_on == []

    def test_depends_on_accepted(self):
        pc = PipelineCreate(
            stages=[self._make_stage()],
            depends_on=["pipe-a", "pipe-b"],
        )
        assert pc.depends_on == ["pipe-a", "pipe-b"]

    def test_depends_on_serialization_round_trip(self):
        pc = PipelineCreate(
            stages=[self._make_stage()],
            depends_on=["pipe-a"],
        )
        data = pc.model_dump()
        restored = PipelineCreate(**data)
        assert restored.depends_on == ["pipe-a"]


class TestTaskInfoDependsOn:
    """Tests for depends_on and dependencies_met fields on TaskInfo."""

    def test_default_empty(self):
        now = datetime.now(UTC)
        info = TaskInfo(
            id="t-1",
            input=TaskCreate(type="prompt", source="hello"),
            status=TaskStatus.QUEUED,
            created_at=now,
        )
        assert info.depends_on == []
        assert info.dependencies_met is True

    def test_depends_on_set(self):
        now = datetime.now(UTC)
        info = TaskInfo(
            id="t-1",
            input=TaskCreate(type="prompt", source="hello"),
            status=TaskStatus.QUEUED,
            created_at=now,
            depends_on=["task-a", "task-b"],
        )
        assert info.depends_on == ["task-a", "task-b"]

    def test_dependencies_met_false(self):
        now = datetime.now(UTC)
        info = TaskInfo(
            id="t-1",
            input=TaskCreate(type="prompt", source="hello"),
            status=TaskStatus.QUEUED,
            created_at=now,
            depends_on=["task-a"],
            dependencies_met=False,
        )
        assert info.dependencies_met is False


class TestPipelineInfoDependsOn:
    """Tests for depends_on and dependencies_met fields on PipelineInfo."""

    def test_default_empty(self):
        now = datetime.now(UTC)
        info = PipelineInfo(
            id="pipe-1",
            stages=[StageConfig(name="s1", autonomy="full", prompt="go")],
            status=PipelineStatus.PENDING,
            created_at=now,
        )
        assert info.depends_on == []
        assert info.dependencies_met is True

    def test_depends_on_set(self):
        now = datetime.now(UTC)
        info = PipelineInfo(
            id="pipe-1",
            stages=[StageConfig(name="s1", autonomy="full", prompt="go")],
            status=PipelineStatus.PENDING,
            created_at=now,
            depends_on=["pipe-a"],
        )
        assert info.depends_on == ["pipe-a"]

    def test_dependencies_met_false(self):
        now = datetime.now(UTC)
        info = PipelineInfo(
            id="pipe-1",
            stages=[StageConfig(name="s1", autonomy="full", prompt="go")],
            status=PipelineStatus.PENDING,
            created_at=now,
            depends_on=["pipe-a"],
            dependencies_met=False,
        )
        assert info.dependencies_met is False


class TestTaskRowToInfoDependsOn:
    """Tests for depends_on parsing in task_row_to_info."""

    def test_depends_on_json_parsed(self):
        deps = ["task-a", "task-b"]
        row = _make_task_row(depends_on_json=json.dumps(deps))
        info = task_row_to_info(row)
        assert info.depends_on == ["task-a", "task-b"]

    def test_depends_on_json_none(self):
        row = _make_task_row(depends_on_json=None)
        info = task_row_to_info(row)
        assert info.depends_on == []

    def test_depends_on_json_empty_string(self):
        """Empty string should be treated as no depends_on (falsy)."""
        row = _make_task_row(depends_on_json="")
        info = task_row_to_info(row)
        assert info.depends_on == []

    def test_depends_on_json_invalid_json(self):
        """Malformed JSON should not crash — just produces empty depends_on."""
        row = _make_task_row(depends_on_json="not valid json{{{")
        info = task_row_to_info(row)
        assert info.depends_on == []

    def test_dependencies_met_from_row_attribute(self):
        """dependencies_met should be read from _dependencies_met attribute on row."""
        row = _make_task_row(depends_on_json=json.dumps(["task-a"]))
        row._dependencies_met = False
        info = task_row_to_info(row)
        assert info.dependencies_met is False

    def test_dependencies_met_defaults_true(self):
        """When _dependencies_met not on row, defaults to True."""
        row = _make_task_row(depends_on_json=json.dumps(["task-a"]))
        info = task_row_to_info(row)
        assert info.dependencies_met is True

    def test_missing_depends_on_json_attr(self):
        """Rows from older schema without depends_on_json should default to empty."""
        row = SimpleNamespace(
            id="t-old",
            input_type="prompt",
            source="old task",
            repo=None,
            autonomy="full",
            model=None,
            extra_instructions=None,
            file_path=None,
            file_format=None,
            status="completed",
            phase=None,
            branch=None,
            pr_url=None,
            resolved_prompt=None,
            output="done",
            error=None,
            created_at=datetime.now(UTC),
            started_at=None,
            completed_at=datetime.now(UTC),
            last_heartbeat=None,
            # No depends_on_json, no context_json
        )
        info = task_row_to_info(row)
        assert info.depends_on == []
        assert info.dependencies_met is True
