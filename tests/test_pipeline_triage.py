"""Tests for pipeline stall triage signals exposed by API helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from workbench.main import _compute_pipeline_health, _pipeline_row_to_info


def _make_pipeline_row(**overrides) -> SimpleNamespace:
    now = datetime.now(UTC)
    defaults = dict(
        id="pipe-001",
        repo="workbench",
        stages_json=json.dumps(
            [
                {"name": "explore", "autonomy": "research", "prompt": "look"},
            ]
        ),
        current_stage_index=0,
        current_task_id=None,
        status="pending",
        max_review_iterations=3,
        review_iteration=0,
        model=None,
        task_ids_json="[]",
        depends_on_json=None,
        error=None,
        created_at=now,
        completed_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestComputePipelineHealth:
    @pytest.mark.asyncio
    async def test_pending_without_task_is_marked_stalled(self):
        row = _make_pipeline_row(status="pending", current_task_id=None, depends_on_json=None)
        session = AsyncMock()

        dependencies_met, stalled, stalled_reason = await _compute_pipeline_health(session, row)

        assert dependencies_met is True
        assert stalled is True
        assert stalled_reason == "pending_without_current_task_id"

    @pytest.mark.asyncio
    async def test_pending_without_task_waiting_on_dependencies_is_not_stalled(self):
        row = _make_pipeline_row(
            status="pending",
            current_task_id=None,
            depends_on_json=json.dumps(["dep-1"]),
        )
        session = AsyncMock()

        with patch(
            "workbench.main.check_pipeline_dependencies_met",
            new=AsyncMock(return_value=(False, None)),
        ):
            dependencies_met, stalled, stalled_reason = await _compute_pipeline_health(session, row)

        assert dependencies_met is False
        assert stalled is False
        assert stalled_reason == "waiting_on_dependencies"

    @pytest.mark.asyncio
    async def test_pending_dependency_error_is_marked_stalled(self):
        row = _make_pipeline_row(
            status="pending",
            current_task_id=None,
            depends_on_json=json.dumps(["dep-404"]),
        )
        session = AsyncMock()

        with patch(
            "workbench.main.check_pipeline_dependencies_met",
            new=AsyncMock(return_value=(False, "Dependency pipeline dep-404 not found")),
        ):
            dependencies_met, stalled, stalled_reason = await _compute_pipeline_health(session, row)

        assert dependencies_met is False
        assert stalled is True
        assert (
            stalled_reason == "dependency_resolution_error: Dependency pipeline dep-404 not found"
        )


class TestPipelineRowToInfoTriageFields:
    def test_includes_stalled_and_dependency_flags(self):
        row = _make_pipeline_row()

        info = _pipeline_row_to_info(
            row,
            dependencies_met=False,
            stalled=True,
            stalled_reason="pending_without_current_task_id",
        )

        assert info.dependencies_met is False
        assert info.stalled is True
        assert info.stalled_reason == "pending_without_current_task_id"
