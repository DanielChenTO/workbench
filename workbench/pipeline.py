"""Pipeline manager — auto-dispatch, review parsing, and loop logic.

Handles the lifecycle of multi-stage pipelines:
  1. Dispatch the first stage as a task
  2. On task completion, parse output and decide next action
  3. For review-gated stages: APPROVE → advance, REJECT → loop back
  4. Emit events to the event log for async supervision
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from .config import settings
from .database import (
    PipelineRow,
    TaskRow,
    async_session,
    check_pipeline_dependencies_met,
    create_task,
    get_pipeline,
    update_pipeline,
)
from .events import emit
from .models import Autonomy, PipelineStatus, StageConfig
from .review import (
    build_review_prompt,
    collect_implementation_diff,
    parse_structured_review,
)

log = logging.getLogger(__name__)


def _get_stages(pipeline: PipelineRow) -> list[StageConfig]:
    """Deserialize stages from JSON."""
    return [StageConfig(**s) for s in json.loads(pipeline.stages_json)]


def _get_task_ids(pipeline: PipelineRow) -> list[str]:
    """Deserialize task ID list from JSON."""
    return json.loads(pipeline.task_ids_json or "[]")


async def start_pipeline(pipeline_id: str, enqueue_fn) -> None:
    """Kick off the first stage of a pipeline.

    Called by the API endpoint after creating the pipeline row.
    Checks pipeline dependencies before dispatching the first stage.
    """
    async with async_session() as session:
        pipeline = await get_pipeline(session, pipeline_id)

    if pipeline is None:
        log.error("start_pipeline: pipeline %s not found", pipeline_id)
        return

    stages = _get_stages(pipeline)
    if not stages:
        log.error("Pipeline %s has no stages", pipeline_id)
        return

    # Check pipeline dependencies before starting
    if pipeline.depends_on_json:
        async with async_session() as session:
            deps_met, deps_reason = await check_pipeline_dependencies_met(
                session, pipeline_id
            )
        if not deps_met:
            if deps_reason:
                # A dependency failed/cancelled — fail this pipeline
                log.warning(
                    "Pipeline %s dependency not met: %s", pipeline_id, deps_reason,
                )
                async with async_session() as session:
                    await update_pipeline(
                        session,
                        pipeline_id,
                        status="failed",
                        error=deps_reason,
                        completed_at=datetime.now(UTC),
                    )
                await emit(
                    "pipeline_failed",
                    pipeline_id=pipeline_id,
                    detail=deps_reason,
                )
                return
            else:
                # Dependencies not yet complete — leave in pending status
                log.info(
                    "Pipeline %s dependencies not ready, staying in pending",
                    pipeline_id,
                )
                return

    await _dispatch_stage(pipeline, stages[0], 0, enqueue_fn, parent_task_id=None)


async def on_task_completed(task_id: str, enqueue_fn) -> None:
    """Called by the worker after a pipeline-linked task completes.

    Decides whether to advance, loop back, or complete the pipeline.
    """
    async with async_session() as session:
        task = await session.get(TaskRow, task_id)
    if task is None or task.pipeline_id is None:
        return

    pipeline_id = task.pipeline_id

    async with async_session() as session:
        pipeline = await get_pipeline(session, pipeline_id)
    if pipeline is None:
        log.error("on_task_completed: pipeline %s not found", pipeline_id)
        return

    if pipeline.status != PipelineStatus.RUNNING:
        log.warning("Pipeline %s is %s, ignoring task completion", pipeline_id, pipeline.status)
        return

    stages = _get_stages(pipeline)
    stage_idx = pipeline.current_stage_index
    stage = stages[stage_idx]

    await emit(
        "stage_completed",
        pipeline_id=pipeline_id,
        stage=stage.name,
        task_id=task_id,
        detail=f"stage {stage_idx}/{len(stages)-1}",
    )

    # --- Review gate logic ---
    if stage.review_gate:
        review = parse_structured_review(task.output or "")

        if not review.approved:
            review_iter = pipeline.review_iteration + 1

            if review_iter >= pipeline.max_review_iterations:
                # Max iterations — fail the pipeline
                error_msg = (
                    f"Review rejected {review_iter} times (max={pipeline.max_review_iterations}). "
                    f"Last: {review.summary_line()}"
                )
                await _complete_pipeline(pipeline_id, "failed", error=error_msg)
                await emit(
                    "pipeline_failed",
                    pipeline_id=pipeline_id,
                    detail=error_msg,
                )
                return

            # Loop back to the target stage with review feedback
            loop_to = stage.loop_to if stage.loop_to is not None else max(0, stage_idx - 1)
            loop_stage = stages[loop_to]

            await emit(
                "review_rejected",
                pipeline_id=pipeline_id,
                stage=stage.name,
                detail=(
                    f"iteration {review_iter}, looping to '{loop_stage.name}' (stage {loop_to}). "
                    f"{review.summary_line()}"
                ),
            )

            async with async_session() as session:
                await update_pipeline(
                    session, pipeline_id,
                    current_stage_index=loop_to,
                    review_iteration=review_iter,
                )

            # Use structured feedback when findings are available
            feedback_instructions = f"\n\n{review.feedback_for_implementer()}"
            await _dispatch_stage(
                pipeline, loop_stage, loop_to, enqueue_fn,
                parent_task_id=task_id,
                extra_context=feedback_instructions,
            )
            return

        # Approved
        await emit(
            "review_approved",
            pipeline_id=pipeline_id,
            stage=stage.name,
            detail=review.summary_line(),
        )

    # --- Advance to next stage or complete ---
    next_idx = stage_idx + 1
    if next_idx >= len(stages):
        # Pipeline complete
        await _complete_pipeline(pipeline_id, "completed")
        await emit(
            "pipeline_completed",
            pipeline_id=pipeline_id,
            detail=f"all {len(stages)} stages done",
        )
        return

    # Dispatch next stage
    next_stage = stages[next_idx]
    async with async_session() as session:
        await update_pipeline(session, pipeline_id, current_stage_index=next_idx)

    await _dispatch_stage(
        pipeline, next_stage, next_idx, enqueue_fn,
        parent_task_id=task_id,
    )


async def on_task_failed(task_id: str) -> None:
    """Called when a pipeline-linked task fails. Fails the pipeline."""
    async with async_session() as session:
        task = await session.get(TaskRow, task_id)
    if task is None or task.pipeline_id is None:
        return

    error_msg = f"Stage '{task.stage_name}' failed: {task.error or 'unknown error'}"
    await _complete_pipeline(task.pipeline_id, "failed", error=error_msg)
    await emit(
        "pipeline_failed",
        pipeline_id=task.pipeline_id,
        stage=task.stage_name,
        detail=error_msg,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _dispatch_stage(
    pipeline: PipelineRow,
    stage: StageConfig,
    stage_idx: int,
    enqueue_fn,
    *,
    parent_task_id: str | None = None,
    extra_context: str | None = None,
) -> None:
    """Create a task for a pipeline stage and enqueue it."""
    # Build prompt with optional extra context (review feedback)
    prompt = stage.prompt
    if extra_context:
        prompt = prompt + extra_context

    # Build extra instructions
    extra = stage.extra_instructions or ""
    if extra:
        extra += "\n\n"
    extra += (
        f"You are executing stage '{stage.name}' of a multi-stage pipeline.\n"
        f"Stage {stage_idx + 1} of {len(json.loads(pipeline.stages_json))} total."
    )

    # --- Structured review: inject diff + review instructions ---
    if stage.review_gate:
        repo_path = settings.resolve_repo_path(pipeline.repo) if pipeline.repo else None

        if repo_path is not None:
            try:
                diff, stat = await collect_implementation_diff(pipeline.id, str(repo_path))
                prompt = build_review_prompt(
                    diff=diff,
                    diff_stat=stat,
                    stage_prompt=stage.prompt,
                    review_iteration=pipeline.review_iteration,
                )
                log.info(
                    "Pipeline %s stage '%s': injected diff (%d lines) into review prompt",
                    pipeline.id, stage.name, diff.count("\n"),
                )
            except Exception:
                log.exception(
                    "Pipeline %s: failed to collect diff for review stage '%s', "
                    "falling back to basic review instructions",
                    pipeline.id, stage.name,
                )
                # Fall through to the basic instructions below

        # If diff injection failed or no repo, at least add the basic verdict instructions
        if "## Code Review Instructions" not in prompt:
            extra += (
                "\n\nIMPORTANT: Your output will be parsed for a review verdict.\n"
                "You MUST include exactly one of these lines in your output:\n"
                "  APPROVE — if the changes are correct and complete\n"
                "  REJECT: <reason> — if changes need improvement, with specific feedback"
            )

    # Re-apply extra_context to the review prompt (feedback from prior rejection)
    if extra_context and stage.review_gate and "## Review Feedback" not in prompt:
        prompt = prompt + extra_context

    # Determine model: stage override > pipeline default > None
    model = stage.model or pipeline.model

    # Build context items
    context_items = []
    if parent_task_id:
        context_items.append({
            "type": "task_output",
            "task_id": parent_task_id,
            "label": "Previous stage output",
        })
    context_json = json.dumps(context_items) if context_items else None

    async with async_session() as session:
        task_row = await create_task(
            session,
            input_type="prompt",
            source=prompt,
            repo=pipeline.repo,
            autonomy=stage.autonomy,
            model=model,
            extra_instructions=extra,
            context_json=context_json,
            parent_task_id=parent_task_id,
        )

        # Link task to pipeline
        from .database import update_task
        await update_task(
            session, task_row.id,
            pipeline_id=pipeline.id,
            stage_name=stage.name,
        )

        # Update pipeline state
        task_ids = _get_task_ids(pipeline)
        task_ids.append(task_row.id)
        await update_pipeline(
            session, pipeline.id,
            current_task_id=task_row.id,
            current_stage_index=stage_idx,
            status="running",
            task_ids_json=json.dumps(task_ids),
        )

    enqueue_fn(task_row.id)

    await emit(
        "stage_dispatched",
        pipeline_id=pipeline.id,
        stage=stage.name,
        task_id=task_row.id,
        detail=f"autonomy={stage.autonomy}",
    )
    log.info(
        "Pipeline %s: dispatched stage '%s' (idx=%d) as task %s",
        pipeline.id, stage.name, stage_idx, task_row.id,
    )


async def _complete_pipeline(
    pipeline_id: str,
    status: str,
    *,
    error: str | None = None,
) -> None:
    """Mark a pipeline as completed or failed.

    On successful completion, auto-merge any local-autonomy branches into the
    default branch so the work doesn't stay stranded.
    """
    async with async_session() as session:
        pipeline = await get_pipeline(session, pipeline_id)

    if pipeline is None:
        log.error("_complete_pipeline: pipeline %s not found", pipeline_id)
        return

    # --- Auto-merge for successful local-autonomy pipelines ---
    if status == "completed":
        await _merge_pipeline_branches(pipeline)

    async with async_session() as session:
        await update_pipeline(
            session, pipeline_id,
            status=status,
            error=error,
            completed_at=datetime.now(UTC),
        )
    log.info("Pipeline %s → %s%s", pipeline_id, status, f": {error}" if error else "")


async def _merge_pipeline_branches(pipeline: PipelineRow) -> None:
    """Merge all local-autonomy branches from a completed pipeline into main.

    Iterates through the pipeline's tasks in order. For each task that has a
    branch set and used local autonomy, merges that branch into the default
    branch. Skips tasks with full autonomy (those have PRs on GitHub).
    """
    from . import git_ops

    task_ids = _get_task_ids(pipeline)
    if not task_ids:
        return

    repo_path = settings.resolve_repo_path(pipeline.repo)
    if repo_path is None:
        log.warning(
            "Pipeline %s: cannot merge — repo %r not found in known_repos",
            pipeline.id, pipeline.repo,
        )
        return

    merged_count = 0
    async with async_session() as session:
        for task_id in task_ids:
            task = await session.get(TaskRow, task_id)
            if task is None:
                continue

            # Only merge local-autonomy tasks that have a branch and completed
            if task.autonomy != "local" or not task.branch or task.status != "completed":
                continue

            try:
                await git_ops.merge_branch(repo_path, task.branch)
                merged_count += 1
                await emit(
                    "branch_merged",
                    pipeline_id=pipeline.id,
                    stage=task.stage_name,
                    task_id=task_id,
                    detail=f"merged {task.branch} into default",
                )
            except git_ops.GitError as e:
                log.error(
                    "Pipeline %s: failed to merge branch %s for task %s: %s",
                    pipeline.id, task.branch, task_id, e,
                )
                await emit(
                    "merge_failed",
                    pipeline_id=pipeline.id,
                    stage=task.stage_name,
                    task_id=task_id,
                    detail=str(e),
                )

    if merged_count:
        log.info(
            "Pipeline %s: merged %d branch(es) into default",
            pipeline.id, merged_count,
        )
