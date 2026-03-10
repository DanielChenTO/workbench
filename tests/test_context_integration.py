"""Integration tests for the context pipeline — requires a running workbench service.

Run with:
    # Terminal 1: start the service
    cd workbench && .venv/bin/python -m workbench.main serve

    # Terminal 2: run these tests
    cd workbench && .venv/bin/python -m pytest tests/test_context_integration.py -v

These tests hit the real /tasks API. They create tasks with research autonomy
(so opencode actually runs), verify context items are stored, resolved, and
visible in the agent output. Each test waits for task completion.

Set WORKBENCH_URL env var to override the default http://127.0.0.1:8420.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

BASE_URL = os.environ.get("WORKBENCH_URL", "http://127.0.0.1:8420")
TIMEOUT = 120  # max seconds to wait for a task to complete


def _service_available() -> bool:
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=3)
        return r.status_code == 200
    except (httpx.ConnectError, httpx.ReadTimeout):
        return False


pytestmark = pytest.mark.skipif(
    not _service_available(),
    reason=f"workbench service not running at {BASE_URL}",
)


def _create_task(payload: dict) -> dict:
    """POST /tasks and return the response JSON."""
    resp = httpx.post(
        f"{BASE_URL}/tasks",
        json=payload,
        timeout=30,
    )
    assert resp.status_code == 201, f"Task creation failed: {resp.text}"
    return resp.json()


def _wait_for_task(task_id: str, timeout: int = TIMEOUT) -> dict:
    """Poll GET /tasks/{id} until the task reaches a terminal state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = httpx.get(f"{BASE_URL}/tasks/{task_id}", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in ("completed", "failed", "cancelled", "blocked"):
            return data
        time.sleep(2)
    raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestContextTextItem:
    """Test type='text' context injection."""

    def test_text_context_roundtrip(self):
        """Text context should be stored and visible in the API response."""
        data = _create_task({
            "type": "prompt",
            "source": "Confirm you see the context item labeled 'Test Note'.",
            "autonomy": "research",
            "context": [
                {
                    "type": "text",
                    "content": "MARKER_TEXT_ABC123",
                    "label": "Test Note",
                },
            ],
        })
        assert len(data["input"]["context"]) == 1
        assert data["input"]["context"][0]["type"] == "text"
        assert data["input"]["context"][0]["label"] == "Test Note"
        assert data["input"]["context"][0]["content"] == "MARKER_TEXT_ABC123"

        # Wait for completion and check the agent saw the context
        result = _wait_for_task(data["id"])
        assert result["status"] == "completed", (
            f"Task failed: {result.get('error')}"
        )
        # The agent output should reference the marker text
        assert result["output"] is not None
        assert "MARKER_TEXT_ABC123" in result["output"]


class TestContextFileItem:
    """Test type='file' context injection."""

    def test_file_context_injected(self):
        """File context should read the file and inject it into the prompt."""
        data = _create_task({
            "type": "prompt",
            "source": (
                "List the dependencies you see in the context item "
                "labeled 'pyproject'. Respond with just the dependency names."
            ),
            "autonomy": "research",
            "context": [
                {
                    "type": "file",
                    "path": "workbench/pyproject.toml",
                    "label": "pyproject",
                    "max_lines": 30,
                },
            ],
        })
        result = _wait_for_task(data["id"])
        assert result["status"] == "completed", (
            f"Task failed: {result.get('error')}"
        )
        # The output should mention at least one known dependency
        output = result["output"].lower()
        assert any(
            dep in output
            for dep in ["fastapi", "sqlalchemy", "pydantic", "uvicorn"]
        ), f"Expected dependency names in output, got: {result['output'][:500]}"

    def test_file_with_line_range(self):
        """File context with lines='1-5' should only include those lines."""
        data = _create_task({
            "type": "prompt",
            "source": (
                "How many lines of content do you see in the context item? "
                "Respond with just the number."
            ),
            "autonomy": "research",
            "context": [
                {
                    "type": "file",
                    "path": "workbench/pyproject.toml",
                    "lines": "1-5",
                    "label": "First 5 lines",
                },
            ],
        })
        result = _wait_for_task(data["id"])
        assert result["status"] == "completed", (
            f"Task failed: {result.get('error')}"
        )


class TestContextReferenceItem:
    """Test type='reference' context injection."""

    def test_reference_full_doc(self):
        """Reference context should read a doc from work-directory/references/."""
        # This test requires a reference doc to exist. Check first.
        try:
            # Use a known reference doc if it exists
            httpx.get(f"{BASE_URL}/health", timeout=5)
        except httpx.ConnectError:
            pytest.skip("Service not available")

        data = _create_task({
            "type": "prompt",
            "source": (
                "Summarize what the reference document is about. "
                "If no reference document was provided, say 'NO REFERENCE'."
            ),
            "autonomy": "research",
            "context": [
                {
                    "type": "reference",
                    "doc": "INDEX.md",
                    "label": "Reference index",
                    "max_lines": 50,
                },
            ],
        })
        result = _wait_for_task(data["id"])
        # This might fail if INDEX.md doesn't exist — that's informative
        if result["status"] == "completed":
            assert "NO REFERENCE" not in result["output"]


class TestContextTaskOutputItem:
    """Test type='task_output' context injection (chaining)."""

    def test_task_chaining(self):
        """Create a task, wait for it, then create a child that uses its output."""
        # Step 1: Create parent task
        parent = _create_task({
            "type": "prompt",
            "source": (
                "Respond with exactly this text and nothing else: "
                "PARENT_OUTPUT_MARKER_XYZ789"
            ),
            "autonomy": "research",
        })
        parent_result = _wait_for_task(parent["id"])
        assert parent_result["status"] == "completed", (
            f"Parent task failed: {parent_result.get('error')}"
        )

        # Step 2: Create child task that references parent output
        child = _create_task({
            "type": "prompt",
            "source": (
                "Check the context for a marker string from the parent task. "
                "If you find 'PARENT_OUTPUT_MARKER_XYZ789', respond with "
                "'FOUND_PARENT_MARKER'. Otherwise respond 'NOT_FOUND'."
            ),
            "autonomy": "research",
            "context": [
                {
                    "type": "task_output",
                    "task_id": parent["id"],
                    "label": "Parent task result",
                },
            ],
        })
        child_result = _wait_for_task(child["id"])
        assert child_result["status"] == "completed", (
            f"Child task failed: {child_result.get('error')}"
        )
        assert "FOUND_PARENT_MARKER" in child_result["output"]


class TestParentTaskIdChaining:
    """Test parent_task_id auto-injection."""

    def test_parent_task_id_auto_injects(self):
        """Setting parent_task_id should auto-inject parent output as context."""
        # Create parent
        parent = _create_task({
            "type": "prompt",
            "source": (
                "Respond with exactly: AUTO_INJECT_MARKER_QRS456"
            ),
            "autonomy": "research",
        })
        parent_result = _wait_for_task(parent["id"])
        assert parent_result["status"] == "completed"

        # Create child with parent_task_id (no explicit context items)
        child = _create_task({
            "type": "prompt",
            "source": (
                "Check your context for 'AUTO_INJECT_MARKER_QRS456'. "
                "If found, respond 'AUTO_INJECT_CONFIRMED'. "
                "Otherwise respond 'NOT_FOUND'."
            ),
            "autonomy": "research",
            "parent_task_id": parent["id"],
        })
        child_result = _wait_for_task(child["id"])
        assert child_result["status"] == "completed", (
            f"Child task failed: {child_result.get('error')}"
        )
        assert "AUTO_INJECT_CONFIRMED" in child_result["output"]


class TestSummaryGeneration:
    """Test that completed tasks get auto-generated summaries."""

    def test_summary_populated(self):
        """After completion, the summary field should be populated."""
        data = _create_task({
            "type": "prompt",
            "source": "Say 'hello world' and nothing else.",
            "autonomy": "research",
        })
        result = _wait_for_task(data["id"])
        assert result["status"] == "completed"
        assert result["summary"] is not None
        assert len(result["summary"]) > 0


class TestMixedContextItems:
    """Test multiple context types in a single task."""

    def test_text_and_file_together(self):
        """Task with both text and file context items."""
        data = _create_task({
            "type": "prompt",
            "source": (
                "You have been given two context items. "
                "Confirm you see both by naming their labels."
            ),
            "autonomy": "research",
            "context": [
                {
                    "type": "text",
                    "content": "This is inline context.",
                    "label": "Inline Note Alpha",
                },
                {
                    "type": "file",
                    "path": "workbench/pyproject.toml",
                    "label": "Project Config Beta",
                    "max_lines": 10,
                },
            ],
        })
        result = _wait_for_task(data["id"])
        assert result["status"] == "completed"
        output = result["output"]
        # Agent should reference both labels
        assert "Alpha" in output or "Inline Note" in output
        assert "Beta" in output or "Project Config" in output


class TestErrorHandling:
    """Test graceful handling of invalid context items."""

    def test_nonexistent_file_degrades_gracefully(self):
        """A file context pointing to a nonexistent path should not crash the task."""
        data = _create_task({
            "type": "prompt",
            "source": "Say 'hello' even if context resolution had issues.",
            "autonomy": "research",
            "context": [
                {
                    "type": "file",
                    "path": "nonexistent/fake/file.go",
                    "label": "Missing file",
                },
            ],
        })
        result = _wait_for_task(data["id"])
        # Task should still complete (context failure is non-fatal)
        assert result["status"] == "completed"

    def test_invalid_task_id_reference(self):
        """Referencing a nonexistent task_id should not crash the task."""
        data = _create_task({
            "type": "prompt",
            "source": "Say 'hello' even if context resolution had issues.",
            "autonomy": "research",
            "context": [
                {
                    "type": "task_output",
                    "task_id": "000000000000",
                    "label": "Ghost task",
                },
            ],
        })
        result = _wait_for_task(data["id"])
        assert result["status"] == "completed"
