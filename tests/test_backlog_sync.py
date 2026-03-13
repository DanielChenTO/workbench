"""Tests for autopilot backlog mirroring helpers in workbench.main."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from workbench.main import _normalize_backlog_item, _sync_autopilot_backlog_from_rows


def test_normalize_backlog_item_without_description():
    assert _normalize_backlog_item("Task A") == "Task A"


def test_normalize_backlog_item_with_description():
    assert _normalize_backlog_item("Task A", "Needs tests") == "Task A — Needs tests"


def test_sync_autopilot_backlog_from_rows_writes_active_and_review(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("workbench.main.settings.workspace_root", tmp_path)
    rows = [
        SimpleNamespace(
            title="Implement helper", description="Add tests", status="todo", tags='["autopilot"]'
        ),
        SimpleNamespace(
            title="Validation failure",
            description="Missing evidence",
            status="review",
            tags='["autopilot", "review_queue"]',
        ),
        SimpleNamespace(title="Ignore me", description=None, status="todo", tags='["manual"]'),
    ]

    _sync_autopilot_backlog_from_rows(rows)

    content = (tmp_path / "work-directory" / "backlog.md").read_text(encoding="utf-8")
    assert "Implement helper — Add tests" in content
    assert "Validation failure — Missing evidence" in content
    assert "Ignore me" not in content
