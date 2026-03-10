"""Unit tests for workbench.pipeline — multi-stage pipeline orchestration."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from workbench.models import Autonomy, PipelineStatus, StageConfig
from workbench.review import ReviewResult, ReviewFinding


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stage(
    name: str = "implement",
    autonomy: str = "local",
    prompt: str = "Do the work",
    review_gate: bool = False,
    loop_to: int | None = None,
    model: str | None = None,
    extra_instructions: str | None = None,
) -> dict:
    """Return a stage dict suitable for stages_json."""
    return {
        "name": name,
        "autonomy": autonomy,
        "prompt": prompt,
        "review_gate": review_gate,
        "loop_to": loop_to,
        "model": model,
        "extra_instructions": extra_instructions,
    }


def _make_pipeline_row(
    pipeline_id: str = "pipe_001",
    repo: str | None = "my-repo",
    stages: list[dict] | None = None,
    current_stage_index: int = 0,
    current_task_id: str | None = None,
    status: str = "running",
    max_review_iterations: int = 3,
    review_iteration: int = 0,
    model: str | None = None,
    task_ids: list[str] | None = None,
    error: str | None = None,
    depends_on_json: str | None = None,
) -> MagicMock:
    """Build a mock PipelineRow."""
    if stages is None:
        stages = [_make_stage()]
    row = MagicMock()
    row.id = pipeline_id
    row.repo = repo
    row.stages_json = json.dumps(stages)
    row.current_stage_index = current_stage_index
    row.current_task_id = current_task_id
    row.status = status
    row.max_review_iterations = max_review_iterations
    row.review_iteration = review_iteration
    row.model = model
    row.task_ids_json = json.dumps(task_ids or [])
    row.error = error
    row.depends_on_json = depends_on_json
    return row


def _make_task_row(
    task_id: str = "task_001",
    pipeline_id: str | None = "pipe_001",
    stage_name: str | None = "implement",
    status: str = "completed",
    output: str | None = None,
    error: str | None = None,
    autonomy: str = "local",
    branch: str | None = None,
) -> MagicMock:
    """Build a mock TaskRow."""
    row = MagicMock()
    row.id = task_id
    row.pipeline_id = pipeline_id
    row.stage_name = stage_name
    row.status = status
    row.output = output
    row.error = error
    row.autonomy = autonomy
    row.branch = branch
    return row


def _mock_async_session():
    """Create a mock async_session context manager.

    Returns (patcher, mock_session) where patcher is the patch object and
    mock_session is the AsyncMock session object.
    """
    mock_session = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx, mock_session


# ---------------------------------------------------------------------------
# start_pipeline
# ---------------------------------------------------------------------------

class TestStartPipeline:
    """Tests for start_pipeline()."""

    @pytest.mark.asyncio
    async def test_dispatches_first_stage(self):
        """start_pipeline should fetch the pipeline and dispatch stage 0."""
        stages = [
            _make_stage(name="explore", autonomy="research", prompt="Look around"),
            _make_stage(name="implement", autonomy="local", prompt="Do work"),
        ]
        pipeline = _make_pipeline_row(stages=stages)
        enqueue_fn = MagicMock()

        mock_task_row = _make_task_row(task_id="task_new")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=pipeline) as mock_get, \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch:
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx

            from workbench.pipeline import start_pipeline
            await start_pipeline("pipe_001", enqueue_fn)

        mock_get.assert_awaited_once()
        mock_dispatch.assert_awaited_once()
        # Verify it dispatches stage index 0
        args = mock_dispatch.call_args
        assert args[0][2] == 0  # stage_idx
        assert args[0][0] is pipeline

    @pytest.mark.asyncio
    async def test_pipeline_not_found(self):
        """start_pipeline should log error and return when pipeline not found."""
        enqueue_fn = MagicMock()

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=None), \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch:
            mock_ctx, _ = _mock_async_session()
            mock_session_cls.return_value = mock_ctx

            from workbench.pipeline import start_pipeline
            await start_pipeline("nonexistent", enqueue_fn)

        mock_dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pipeline_no_stages(self):
        """start_pipeline should log error and return when pipeline has no stages."""
        pipeline = _make_pipeline_row(stages=[])
        enqueue_fn = MagicMock()

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=pipeline), \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch:
            mock_ctx, _ = _mock_async_session()
            mock_session_cls.return_value = mock_ctx

            from workbench.pipeline import start_pipeline
            await start_pipeline("pipe_001", enqueue_fn)

        mock_dispatch.assert_not_awaited()


# ---------------------------------------------------------------------------
# _dispatch_stage
# ---------------------------------------------------------------------------

class TestDispatchStage:
    """Tests for _dispatch_stage()."""

    @pytest.mark.asyncio
    async def test_creates_task_with_correct_params(self):
        """_dispatch_stage should create a task with the stage prompt and metadata."""
        stages = [_make_stage(name="implement", autonomy="local", prompt="Build it")]
        pipeline = _make_pipeline_row(stages=stages, repo="my-repo", model="gpt-4")
        stage = StageConfig(**stages[0])
        enqueue_fn = MagicMock()

        mock_task = _make_task_row(task_id="task_new")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.create_task", new_callable=AsyncMock, return_value=mock_task) as mock_create, \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock), \
             patch("workbench.database.update_task", new_callable=AsyncMock), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx

            from workbench.pipeline import _dispatch_stage
            await _dispatch_stage(pipeline, stage, 0, enqueue_fn, parent_task_id=None)

        # Verify create_task was called
        mock_create.assert_awaited_once()
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["input_type"] == "prompt"
        assert call_kwargs["source"] == "Build it"
        assert call_kwargs["repo"] == "my-repo"
        assert call_kwargs["autonomy"] == "local"
        assert call_kwargs["model"] == "gpt-4"

        # Verify enqueue was called with the task ID
        enqueue_fn.assert_called_once_with("task_new")

    @pytest.mark.asyncio
    async def test_includes_extra_instructions_with_stage_metadata(self):
        """Extra instructions should include stage position info."""
        stages = [
            _make_stage(name="explore"),
            _make_stage(name="implement", extra_instructions="Be careful"),
        ]
        pipeline = _make_pipeline_row(stages=stages)
        stage = StageConfig(**stages[1])
        enqueue_fn = MagicMock()
        mock_task = _make_task_row(task_id="task_new")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.create_task", new_callable=AsyncMock, return_value=mock_task) as mock_create, \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock), \
             patch("workbench.database.update_task", new_callable=AsyncMock), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx

            from workbench.pipeline import _dispatch_stage
            await _dispatch_stage(pipeline, stage, 1, enqueue_fn)

        call_kwargs = mock_create.call_args[1]
        extra = call_kwargs["extra_instructions"]
        assert "Be careful" in extra
        assert "Stage 2 of 2" in extra
        assert "stage 'implement'" in extra

    @pytest.mark.asyncio
    async def test_context_injection_from_parent_task(self):
        """When parent_task_id is provided, context_json should include task_output."""
        stages = [_make_stage()]
        pipeline = _make_pipeline_row(stages=stages)
        stage = StageConfig(**stages[0])
        enqueue_fn = MagicMock()
        mock_task = _make_task_row(task_id="task_new")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.create_task", new_callable=AsyncMock, return_value=mock_task) as mock_create, \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock), \
             patch("workbench.database.update_task", new_callable=AsyncMock), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx

            from workbench.pipeline import _dispatch_stage
            await _dispatch_stage(pipeline, stage, 0, enqueue_fn, parent_task_id="parent_123")

        call_kwargs = mock_create.call_args[1]
        context = json.loads(call_kwargs["context_json"])
        assert len(context) == 1
        assert context[0]["type"] == "task_output"
        assert context[0]["task_id"] == "parent_123"

    @pytest.mark.asyncio
    async def test_no_context_without_parent(self):
        """When no parent_task_id, context_json should be None."""
        stages = [_make_stage()]
        pipeline = _make_pipeline_row(stages=stages)
        stage = StageConfig(**stages[0])
        enqueue_fn = MagicMock()
        mock_task = _make_task_row(task_id="task_new")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.create_task", new_callable=AsyncMock, return_value=mock_task) as mock_create, \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock), \
             patch("workbench.database.update_task", new_callable=AsyncMock), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx

            from workbench.pipeline import _dispatch_stage
            await _dispatch_stage(pipeline, stage, 0, enqueue_fn, parent_task_id=None)

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["context_json"] is None

    @pytest.mark.asyncio
    async def test_extra_context_appended_to_prompt(self):
        """Extra context (review feedback) should be appended to the prompt."""
        stages = [_make_stage(prompt="Original prompt")]
        pipeline = _make_pipeline_row(stages=stages)
        stage = StageConfig(**stages[0])
        enqueue_fn = MagicMock()
        mock_task = _make_task_row(task_id="task_new")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.create_task", new_callable=AsyncMock, return_value=mock_task) as mock_create, \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock), \
             patch("workbench.database.update_task", new_callable=AsyncMock), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx

            from workbench.pipeline import _dispatch_stage
            await _dispatch_stage(
                pipeline, stage, 0, enqueue_fn,
                extra_context="\n\nFix the bug in line 42",
            )

        call_kwargs = mock_create.call_args[1]
        assert "Original prompt" in call_kwargs["source"]
        assert "Fix the bug in line 42" in call_kwargs["source"]

    @pytest.mark.asyncio
    async def test_updates_pipeline_state(self):
        """_dispatch_stage should update the pipeline's task_ids and current state."""
        stages = [_make_stage()]
        pipeline = _make_pipeline_row(stages=stages, task_ids=["existing_task"])
        stage = StageConfig(**stages[0])
        enqueue_fn = MagicMock()
        mock_task = _make_task_row(task_id="task_new")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.create_task", new_callable=AsyncMock, return_value=mock_task), \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock) as mock_update, \
             patch("workbench.database.update_task", new_callable=AsyncMock), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx

            from workbench.pipeline import _dispatch_stage
            await _dispatch_stage(pipeline, stage, 0, enqueue_fn)

        mock_update.assert_awaited_once()
        update_kwargs = mock_update.call_args[1]
        assert update_kwargs["current_task_id"] == "task_new"
        assert update_kwargs["status"] == "running"
        task_ids = json.loads(update_kwargs["task_ids_json"])
        assert "existing_task" in task_ids
        assert "task_new" in task_ids

    @pytest.mark.asyncio
    async def test_stage_model_overrides_pipeline_model(self):
        """If a stage has a model set, it should take precedence over pipeline model."""
        stages = [_make_stage(model="claude-sonnet")]
        pipeline = _make_pipeline_row(stages=stages, model="gpt-4")
        stage = StageConfig(**stages[0])
        enqueue_fn = MagicMock()
        mock_task = _make_task_row(task_id="task_new")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.create_task", new_callable=AsyncMock, return_value=mock_task) as mock_create, \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock), \
             patch("workbench.database.update_task", new_callable=AsyncMock), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx

            from workbench.pipeline import _dispatch_stage
            await _dispatch_stage(pipeline, stage, 0, enqueue_fn)

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet"

    @pytest.mark.asyncio
    async def test_pipeline_model_used_when_stage_has_none(self):
        """If stage has no model, pipeline model should be used."""
        stages = [_make_stage(model=None)]
        pipeline = _make_pipeline_row(stages=stages, model="gpt-4")
        stage = StageConfig(**stages[0])
        enqueue_fn = MagicMock()
        mock_task = _make_task_row(task_id="task_new")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.create_task", new_callable=AsyncMock, return_value=mock_task) as mock_create, \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock), \
             patch("workbench.database.update_task", new_callable=AsyncMock), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx

            from workbench.pipeline import _dispatch_stage
            await _dispatch_stage(pipeline, stage, 0, enqueue_fn)

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["model"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_review_gate_adds_verdict_instructions(self):
        """A review-gated stage should inject APPROVE/REJECT instructions."""
        stages = [_make_stage(review_gate=True, prompt="Review the code")]
        pipeline = _make_pipeline_row(stages=stages, repo=None)
        stage = StageConfig(**stages[0])
        enqueue_fn = MagicMock()
        mock_task = _make_task_row(task_id="task_new")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.create_task", new_callable=AsyncMock, return_value=mock_task) as mock_create, \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock), \
             patch("workbench.database.update_task", new_callable=AsyncMock), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx

            from workbench.pipeline import _dispatch_stage
            await _dispatch_stage(pipeline, stage, 0, enqueue_fn)

        call_kwargs = mock_create.call_args[1]
        extra = call_kwargs["extra_instructions"]
        assert "APPROVE" in extra
        assert "REJECT" in extra


# ---------------------------------------------------------------------------
# on_task_completed — advance to next stage
# ---------------------------------------------------------------------------

class TestOnTaskCompletedAdvance:
    """Tests for on_task_completed() — normal stage advancement."""

    @pytest.mark.asyncio
    async def test_advances_to_next_stage(self):
        """After stage 0 completes, should dispatch stage 1."""
        stages = [
            _make_stage(name="explore", autonomy="research"),
            _make_stage(name="implement", autonomy="local"),
        ]
        pipeline = _make_pipeline_row(
            stages=stages, current_stage_index=0, status="running",
        )
        task = _make_task_row(task_id="task_001", pipeline_id="pipe_001", output="Done")
        enqueue_fn = MagicMock()

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=pipeline), \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock) as mock_update, \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch, \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task)

            from workbench.pipeline import on_task_completed
            await on_task_completed("task_001", enqueue_fn)

        # Should update pipeline to move to stage 1
        mock_update.assert_awaited()
        update_kwargs = mock_update.call_args[1]
        assert update_kwargs["current_stage_index"] == 1

        # Should dispatch stage 1
        mock_dispatch.assert_awaited_once()
        dispatch_args = mock_dispatch.call_args
        assert dispatch_args[0][2] == 1  # stage_idx
        assert dispatch_args[1]["parent_task_id"] == "task_001"

    @pytest.mark.asyncio
    async def test_completes_pipeline_on_last_stage(self):
        """After the last stage completes, should complete the pipeline."""
        stages = [
            _make_stage(name="implement"),
        ]
        pipeline = _make_pipeline_row(
            stages=stages, current_stage_index=0, status="running",
        )
        task = _make_task_row(task_id="task_001", pipeline_id="pipe_001", output="All done")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=pipeline), \
             patch("workbench.pipeline._complete_pipeline", new_callable=AsyncMock) as mock_complete, \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch, \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task)

            from workbench.pipeline import on_task_completed
            await on_task_completed("task_001", MagicMock())

        mock_complete.assert_awaited_once_with("pipe_001", "completed")
        mock_dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_task_not_found_is_noop(self):
        """If task not found, on_task_completed should return silently."""
        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch:
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=None)

            from workbench.pipeline import on_task_completed
            await on_task_completed("nonexistent", MagicMock())

        mock_dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_task_without_pipeline_is_noop(self):
        """If task has no pipeline_id, should return silently."""
        task = _make_task_row(pipeline_id=None)

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch:
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task)

            from workbench.pipeline import on_task_completed
            await on_task_completed("task_001", MagicMock())

        mock_dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pipeline_not_running_is_ignored(self):
        """If pipeline is not in RUNNING status, should ignore completion."""
        pipeline = _make_pipeline_row(status="completed")
        task = _make_task_row(pipeline_id="pipe_001")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=pipeline), \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch, \
             patch("workbench.pipeline._complete_pipeline", new_callable=AsyncMock) as mock_complete:
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task)

            from workbench.pipeline import on_task_completed
            await on_task_completed("task_001", MagicMock())

        mock_dispatch.assert_not_awaited()
        mock_complete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pipeline_not_found_after_task(self):
        """If pipeline not found after fetching task, should return silently."""
        task = _make_task_row(pipeline_id="pipe_001")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=None), \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch:
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task)

            from workbench.pipeline import on_task_completed
            await on_task_completed("task_001", MagicMock())

        mock_dispatch.assert_not_awaited()


# ---------------------------------------------------------------------------
# on_task_completed — review gate logic
# ---------------------------------------------------------------------------

class TestReviewGate:
    """Tests for review gate logic in on_task_completed()."""

    @pytest.mark.asyncio
    async def test_review_approved_advances(self):
        """APPROVE verdict should advance to the next stage."""
        stages = [
            _make_stage(name="implement"),
            _make_stage(name="review", review_gate=True),
            _make_stage(name="deploy"),
        ]
        pipeline = _make_pipeline_row(
            stages=stages, current_stage_index=1, status="running",
        )
        task = _make_task_row(
            task_id="task_review", pipeline_id="pipe_001",
            output="Everything looks good.\n\nAPPROVE",
        )
        enqueue_fn = MagicMock()

        approved_review = ReviewResult(approved=True, reason="approved")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=pipeline), \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock) as mock_update, \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch, \
             patch("workbench.pipeline.parse_structured_review", return_value=approved_review), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task)

            from workbench.pipeline import on_task_completed
            await on_task_completed("task_review", enqueue_fn)

        # Should advance to stage 2 (deploy)
        mock_dispatch.assert_awaited_once()
        dispatch_args = mock_dispatch.call_args
        assert dispatch_args[0][2] == 2  # stage_idx

    @pytest.mark.asyncio
    async def test_review_rejected_loops_back(self):
        """REJECT verdict should loop back and re-dispatch the target stage."""
        stages = [
            _make_stage(name="implement"),
            _make_stage(name="review", review_gate=True, loop_to=0),
        ]
        pipeline = _make_pipeline_row(
            stages=stages, current_stage_index=1, status="running",
            review_iteration=0, max_review_iterations=3,
        )
        task = _make_task_row(
            task_id="task_review", pipeline_id="pipe_001",
            output="REJECT: needs more tests",
        )
        enqueue_fn = MagicMock()

        rejected_review = ReviewResult(
            approved=False, reason="needs more tests",
            findings=[ReviewFinding(severity="P0", file="main.py", line="42", description="Missing tests")],
        )

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=pipeline), \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock) as mock_update, \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch, \
             patch("workbench.pipeline._complete_pipeline", new_callable=AsyncMock) as mock_complete, \
             patch("workbench.pipeline.parse_structured_review", return_value=rejected_review), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task)

            from workbench.pipeline import on_task_completed
            await on_task_completed("task_review", enqueue_fn)

        # Should update pipeline: loop back to stage 0, increment review_iteration
        mock_update.assert_awaited()
        update_kwargs = mock_update.call_args[1]
        assert update_kwargs["current_stage_index"] == 0
        assert update_kwargs["review_iteration"] == 1

        # Should dispatch stage 0 (implement) again with feedback
        mock_dispatch.assert_awaited_once()
        dispatch_args = mock_dispatch.call_args
        assert dispatch_args[0][2] == 0  # loop back to stage 0
        assert dispatch_args[1]["parent_task_id"] == "task_review"
        assert dispatch_args[1]["extra_context"] is not None
        assert "Review Feedback" in dispatch_args[1]["extra_context"]

        mock_complete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_review_rejected_default_loop_to(self):
        """When loop_to is None, should loop to stage_idx - 1."""
        stages = [
            _make_stage(name="implement"),
            _make_stage(name="review", review_gate=True, loop_to=None),
        ]
        pipeline = _make_pipeline_row(
            stages=stages, current_stage_index=1, status="running",
            review_iteration=0, max_review_iterations=3,
        )
        task = _make_task_row(
            task_id="task_review", pipeline_id="pipe_001",
            output="REJECT: bad code",
        )

        rejected_review = ReviewResult(approved=False, reason="bad code")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=pipeline), \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock) as mock_update, \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch, \
             patch("workbench.pipeline.parse_structured_review", return_value=rejected_review), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task)

            from workbench.pipeline import on_task_completed
            await on_task_completed("task_review", MagicMock())

        # Should loop back to stage 0 (max(0, 1 - 1) = 0)
        dispatch_args = mock_dispatch.call_args
        assert dispatch_args[0][2] == 0

    @pytest.mark.asyncio
    async def test_max_rejections_fails_pipeline(self):
        """After max review iterations, pipeline should be marked failed."""
        stages = [
            _make_stage(name="implement"),
            _make_stage(name="review", review_gate=True, loop_to=0),
        ]
        pipeline = _make_pipeline_row(
            stages=stages, current_stage_index=1, status="running",
            review_iteration=2, max_review_iterations=3,
        )
        task = _make_task_row(
            task_id="task_review", pipeline_id="pipe_001",
            output="REJECT: still broken",
        )

        rejected_review = ReviewResult(approved=False, reason="still broken")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=pipeline), \
             patch("workbench.pipeline._complete_pipeline", new_callable=AsyncMock) as mock_complete, \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch, \
             patch("workbench.pipeline.parse_structured_review", return_value=rejected_review), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task)

            from workbench.pipeline import on_task_completed
            await on_task_completed("task_review", MagicMock())

        # Pipeline should be completed as failed
        mock_complete.assert_awaited_once()
        complete_args = mock_complete.call_args
        assert complete_args[0][1] == "failed"
        assert "Review rejected 3 times" in complete_args[1]["error"]

        # Should NOT dispatch another stage
        mock_dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejection_at_boundary(self):
        """Rejection at review_iteration = max - 1 should fail (review_iter becomes max)."""
        stages = [
            _make_stage(name="implement"),
            _make_stage(name="review", review_gate=True),
        ]
        pipeline = _make_pipeline_row(
            stages=stages, current_stage_index=1, status="running",
            review_iteration=1, max_review_iterations=2,
        )
        task = _make_task_row(
            task_id="task_review", pipeline_id="pipe_001",
            output="REJECT: nope",
        )

        rejected_review = ReviewResult(approved=False, reason="nope")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=pipeline), \
             patch("workbench.pipeline._complete_pipeline", new_callable=AsyncMock) as mock_complete, \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch, \
             patch("workbench.pipeline.parse_structured_review", return_value=rejected_review), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task)

            from workbench.pipeline import on_task_completed
            await on_task_completed("task_review", MagicMock())

        # review_iter = 1 + 1 = 2, which == max_review_iterations = 2
        mock_complete.assert_awaited_once()
        assert mock_complete.call_args[0][1] == "failed"
        mock_dispatch.assert_not_awaited()


# ---------------------------------------------------------------------------
# on_task_completed — no review gate (edge cases)
# ---------------------------------------------------------------------------

class TestNoReviewGate:
    """Tests for pipelines without review gates."""

    @pytest.mark.asyncio
    async def test_single_stage_pipeline_completes(self):
        """A single-stage pipeline should complete after its only stage."""
        stages = [_make_stage(name="do-everything")]
        pipeline = _make_pipeline_row(
            stages=stages, current_stage_index=0, status="running",
        )
        task = _make_task_row(pipeline_id="pipe_001", output="Done")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=pipeline), \
             patch("workbench.pipeline._complete_pipeline", new_callable=AsyncMock) as mock_complete, \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch, \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task)

            from workbench.pipeline import on_task_completed
            await on_task_completed("task_001", MagicMock())

        mock_complete.assert_awaited_once_with("pipe_001", "completed")
        mock_dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_multi_stage_no_review_gates(self):
        """A multi-stage pipeline with no review gates should advance linearly."""
        stages = [
            _make_stage(name="explore", autonomy="research"),
            _make_stage(name="implement", autonomy="local"),
            _make_stage(name="test", autonomy="local"),
        ]
        pipeline = _make_pipeline_row(
            stages=stages, current_stage_index=0, status="running",
        )
        task = _make_task_row(pipeline_id="pipe_001", output="Explored")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=pipeline), \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock), \
             patch("workbench.pipeline._dispatch_stage", new_callable=AsyncMock) as mock_dispatch, \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task)

            from workbench.pipeline import on_task_completed
            await on_task_completed("task_001", MagicMock())

        # Should advance to stage 1
        mock_dispatch.assert_awaited_once()
        dispatch_args = mock_dispatch.call_args
        assert dispatch_args[0][1].name == "implement"
        assert dispatch_args[0][2] == 1


# ---------------------------------------------------------------------------
# on_task_failed
# ---------------------------------------------------------------------------

class TestOnTaskFailed:
    """Tests for on_task_failed()."""

    @pytest.mark.asyncio
    async def test_fails_pipeline_on_task_failure(self):
        """When a pipeline task fails, the pipeline should be marked failed."""
        task = _make_task_row(
            task_id="task_001", pipeline_id="pipe_001",
            stage_name="implement", error="segfault",
        )

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline._complete_pipeline", new_callable=AsyncMock) as mock_complete, \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task)

            from workbench.pipeline import on_task_failed
            await on_task_failed("task_001")

        mock_complete.assert_awaited_once()
        assert mock_complete.call_args[0][1] == "failed"
        assert "implement" in mock_complete.call_args[1]["error"]
        assert "segfault" in mock_complete.call_args[1]["error"]

    @pytest.mark.asyncio
    async def test_task_not_found_is_noop(self):
        """If the task is not found, on_task_failed should do nothing."""
        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline._complete_pipeline", new_callable=AsyncMock) as mock_complete:
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=None)

            from workbench.pipeline import on_task_failed
            await on_task_failed("nonexistent")

        mock_complete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_task_without_pipeline_is_noop(self):
        """If task has no pipeline_id, should not try to complete any pipeline."""
        task = _make_task_row(pipeline_id=None)

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline._complete_pipeline", new_callable=AsyncMock) as mock_complete:
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task)

            from workbench.pipeline import on_task_failed
            await on_task_failed("task_001")

        mock_complete.assert_not_awaited()


# ---------------------------------------------------------------------------
# _complete_pipeline
# ---------------------------------------------------------------------------

class TestCompletePipeline:
    """Tests for _complete_pipeline()."""

    @pytest.mark.asyncio
    async def test_marks_completed(self):
        """_complete_pipeline with 'completed' should set status and timestamp."""
        pipeline = _make_pipeline_row(status="running")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=pipeline), \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock) as mock_update, \
             patch("workbench.pipeline._merge_pipeline_branches", new_callable=AsyncMock) as mock_merge:
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx

            from workbench.pipeline import _complete_pipeline
            await _complete_pipeline("pipe_001", "completed")

        # Should merge branches for completed pipelines
        mock_merge.assert_awaited_once_with(pipeline)

        # Should update pipeline status
        mock_update.assert_awaited_once()
        update_kwargs = mock_update.call_args[1]
        assert update_kwargs["status"] == "completed"
        assert update_kwargs["completed_at"] is not None
        assert update_kwargs["error"] is None

    @pytest.mark.asyncio
    async def test_marks_failed_with_error(self):
        """_complete_pipeline with 'failed' should set error and NOT merge."""
        pipeline = _make_pipeline_row(status="running")

        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=pipeline), \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock) as mock_update, \
             patch("workbench.pipeline._merge_pipeline_branches", new_callable=AsyncMock) as mock_merge:
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx

            from workbench.pipeline import _complete_pipeline
            await _complete_pipeline("pipe_001", "failed", error="Something broke")

        # Should NOT merge branches for failed pipelines
        mock_merge.assert_not_awaited()

        # Should update pipeline with error
        mock_update.assert_awaited_once()
        update_kwargs = mock_update.call_args[1]
        assert update_kwargs["status"] == "failed"
        assert update_kwargs["error"] == "Something broke"

    @pytest.mark.asyncio
    async def test_pipeline_not_found(self):
        """If pipeline not found, should log error and return without update."""
        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.get_pipeline", return_value=None), \
             patch("workbench.pipeline.update_pipeline", new_callable=AsyncMock) as mock_update:
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx

            from workbench.pipeline import _complete_pipeline
            await _complete_pipeline("nonexistent", "completed")

        mock_update.assert_not_awaited()


# ---------------------------------------------------------------------------
# _merge_pipeline_branches
# ---------------------------------------------------------------------------

class TestMergePipelineBranches:
    """Tests for _merge_pipeline_branches()."""

    @pytest.mark.asyncio
    async def test_merges_local_autonomy_branches(self):
        """Should merge completed local-autonomy tasks with branches."""
        pipeline = _make_pipeline_row(
            task_ids=["task_1", "task_2"], repo="my-repo",
        )

        task1 = _make_task_row(
            task_id="task_1", autonomy="local",
            branch="agent/task-1", status="completed",
            stage_name="implement",
        )
        task2 = _make_task_row(
            task_id="task_2", autonomy="local",
            branch="agent/task-2", status="completed",
            stage_name="test",
        )

        mock_merge = AsyncMock()
        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.settings") as mock_settings, \
             patch("workbench.git_ops.merge_branch", mock_merge), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(side_effect=lambda cls, tid: {
                "task_1": task1, "task_2": task2,
            }.get(tid))
            mock_settings.resolve_repo_path.return_value = "/repos/my-repo"

            from workbench.pipeline import _merge_pipeline_branches
            await _merge_pipeline_branches(pipeline)

        assert mock_merge.await_count == 2
        mock_merge.assert_any_await("/repos/my-repo", "agent/task-1")
        mock_merge.assert_any_await("/repos/my-repo", "agent/task-2")

    @pytest.mark.asyncio
    async def test_skips_full_autonomy_tasks(self):
        """Full-autonomy tasks (which create PRs) should not be merged."""
        pipeline = _make_pipeline_row(task_ids=["task_1"], repo="my-repo")

        task1 = _make_task_row(
            task_id="task_1", autonomy="full",
            branch="agent/task-1", status="completed",
        )

        mock_merge = AsyncMock()
        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.settings") as mock_settings, \
             patch("workbench.git_ops.merge_branch", mock_merge), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task1)
            mock_settings.resolve_repo_path.return_value = "/repos/my-repo"

            from workbench.pipeline import _merge_pipeline_branches
            await _merge_pipeline_branches(pipeline)

        mock_merge.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_tasks_without_branch(self):
        """Tasks without a branch (e.g. research) should be skipped."""
        pipeline = _make_pipeline_row(task_ids=["task_1"], repo="my-repo")

        task1 = _make_task_row(
            task_id="task_1", autonomy="local",
            branch=None, status="completed",
        )

        mock_merge = AsyncMock()
        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.settings") as mock_settings, \
             patch("workbench.git_ops.merge_branch", mock_merge), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task1)
            mock_settings.resolve_repo_path.return_value = "/repos/my-repo"

            from workbench.pipeline import _merge_pipeline_branches
            await _merge_pipeline_branches(pipeline)

        mock_merge.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_incomplete_tasks(self):
        """Tasks that are not completed should be skipped."""
        pipeline = _make_pipeline_row(task_ids=["task_1"], repo="my-repo")

        task1 = _make_task_row(
            task_id="task_1", autonomy="local",
            branch="agent/task-1", status="failed",
        )

        mock_merge = AsyncMock()
        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.settings") as mock_settings, \
             patch("workbench.git_ops.merge_branch", mock_merge), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(return_value=task1)
            mock_settings.resolve_repo_path.return_value = "/repos/my-repo"

            from workbench.pipeline import _merge_pipeline_branches
            await _merge_pipeline_branches(pipeline)

        mock_merge.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_merge_failure(self):
        """If a merge fails, should continue to next task and emit event."""
        pipeline = _make_pipeline_row(
            task_ids=["task_1", "task_2"], repo="my-repo",
        )

        task1 = _make_task_row(
            task_id="task_1", autonomy="local",
            branch="agent/task-1", status="completed",
            stage_name="stage1",
        )
        task2 = _make_task_row(
            task_id="task_2", autonomy="local",
            branch="agent/task-2", status="completed",
            stage_name="stage2",
        )

        from workbench.git_ops import GitError

        # First merge fails, second succeeds
        mock_merge = AsyncMock(side_effect=[GitError("conflict"), None])
        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.settings") as mock_settings, \
             patch("workbench.git_ops.merge_branch", mock_merge), \
             patch("workbench.pipeline.emit") as mock_emit:
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(side_effect=lambda cls, tid: {
                "task_1": task1, "task_2": task2,
            }.get(tid))
            mock_settings.resolve_repo_path.return_value = "/repos/my-repo"

            from workbench.pipeline import _merge_pipeline_branches
            await _merge_pipeline_branches(pipeline)

        # Both merges should have been attempted
        assert mock_merge.await_count == 2
        # merge_failed event should have been emitted for task_1
        emit_calls = [c for c in mock_emit.call_args_list if c[0][0] == "merge_failed"]
        assert len(emit_calls) == 1
        # branch_merged event should have been emitted for task_2
        merged_calls = [c for c in mock_emit.call_args_list if c[0][0] == "branch_merged"]
        assert len(merged_calls) == 1

    @pytest.mark.asyncio
    async def test_no_task_ids_is_noop(self):
        """If pipeline has no task IDs, should return without doing anything."""
        pipeline = _make_pipeline_row(task_ids=[])

        mock_merge = AsyncMock()
        with patch("workbench.pipeline.settings") as mock_settings, \
             patch("workbench.git_ops.merge_branch", mock_merge):
            mock_settings.resolve_repo_path.return_value = "/repos/my-repo"

            from workbench.pipeline import _merge_pipeline_branches
            await _merge_pipeline_branches(pipeline)

        mock_merge.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_repo_not_found_skips_merge(self):
        """If repo path cannot be resolved, should skip merging."""
        pipeline = _make_pipeline_row(task_ids=["task_1"], repo="unknown-repo")

        mock_merge = AsyncMock()
        with patch("workbench.pipeline.settings") as mock_settings, \
             patch("workbench.git_ops.merge_branch", mock_merge):
            mock_settings.resolve_repo_path.return_value = None

            from workbench.pipeline import _merge_pipeline_branches
            await _merge_pipeline_branches(pipeline)

        mock_merge.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_none_tasks(self):
        """If a task_id returns None from DB, should skip it gracefully."""
        pipeline = _make_pipeline_row(
            task_ids=["task_1", "task_missing"], repo="my-repo",
        )

        task1 = _make_task_row(
            task_id="task_1", autonomy="local",
            branch="agent/task-1", status="completed",
        )

        mock_merge = AsyncMock()
        with patch("workbench.pipeline.async_session") as mock_session_cls, \
             patch("workbench.pipeline.settings") as mock_settings, \
             patch("workbench.git_ops.merge_branch", mock_merge), \
             patch("workbench.pipeline.emit"):
            mock_ctx, mock_session = _mock_async_session()
            mock_session_cls.return_value = mock_ctx
            mock_session.get = AsyncMock(side_effect=lambda cls, tid: {
                "task_1": task1,
            }.get(tid))
            mock_settings.resolve_repo_path.return_value = "/repos/my-repo"

            from workbench.pipeline import _merge_pipeline_branches
            await _merge_pipeline_branches(pipeline)

        # Only task_1 should have been merged
        mock_merge.assert_awaited_once()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestHelpers:
    """Tests for internal helper functions."""

    def test_get_stages_deserializes(self):
        """_get_stages should deserialize stages from JSON."""
        stages_data = [
            _make_stage(name="explore"),
            _make_stage(name="implement"),
        ]
        pipeline = _make_pipeline_row(stages=stages_data)

        from workbench.pipeline import _get_stages
        stages = _get_stages(pipeline)

        assert len(stages) == 2
        assert isinstance(stages[0], StageConfig)
        assert stages[0].name == "explore"
        assert stages[1].name == "implement"

    def test_get_task_ids_deserializes(self):
        """_get_task_ids should deserialize task IDs from JSON."""
        pipeline = _make_pipeline_row(task_ids=["t1", "t2", "t3"])

        from workbench.pipeline import _get_task_ids
        ids = _get_task_ids(pipeline)

        assert ids == ["t1", "t2", "t3"]

    def test_get_task_ids_empty(self):
        """_get_task_ids should return empty list when task_ids_json is null."""
        pipeline = MagicMock()
        pipeline.task_ids_json = None

        from workbench.pipeline import _get_task_ids
        ids = _get_task_ids(pipeline)

        assert ids == []
