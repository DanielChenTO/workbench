"""Unit tests for workbench.context — the context resolver pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workbench.context import (
    ContextResolveError,
    ResolvedContext,
    _extract_section,
    _parse_line_range,
    _resolve_file,
    _resolve_reference,
    _truncate,
    resolve_context,
)
from workbench.models import ContextItem

# ---------------------------------------------------------------------------
# ResolvedContext.render()
# ---------------------------------------------------------------------------

class TestResolvedContext:
    def test_render_basic(self):
        ctx = ResolvedContext(
            label="My Label",
            content="Some content here.",
            source_type="text",
            source_ref="inline",
        )
        rendered = ctx.render()
        assert "### My Label" in rendered
        assert "<!-- source: text:inline -->" in rendered
        assert "Some content here." in rendered

    def test_render_file_source(self):
        ctx = ResolvedContext(
            label="handler.go",
            content="```\npackage main\n```",
            source_type="file",
            source_ref="my-repo/src/handler.go",
        )
        rendered = ctx.render()
        assert "file:my-repo/src/handler.go" in rendered


# ---------------------------------------------------------------------------
# _truncate()
# ---------------------------------------------------------------------------

class TestTruncate:
    def test_no_truncation_needed(self):
        text = "line 1\nline 2\nline 3"
        assert _truncate(text, 10) == text

    def test_exact_limit(self):
        text = "line 1\nline 2\nline 3"
        assert _truncate(text, 3) == text

    def test_truncation_applied(self):
        text = "\n".join(f"line {i}" for i in range(100))
        result = _truncate(text, 5)
        lines = result.splitlines()
        # 5 content lines + truncation notice (which starts with \n so adds a blank line)
        assert lines[0] == "line 0"
        assert lines[4] == "line 4"
        assert "truncated at 5 lines" in result
        assert "100 total" in result


# ---------------------------------------------------------------------------
# _extract_section()
# ---------------------------------------------------------------------------

class TestExtractSection:
    SAMPLE_DOC = (
        "# Title\n"
        "\n"
        "Intro.\n"
        "\n"
        "## Architecture\n"
        "\n"
        "Arch content line 1.\n"
        "Arch content line 2.\n"
        "\n"
        "## Implementation\n"
        "\n"
        "Impl content.\n"
        "\n"
        "### Sub-section\n"
        "\n"
        "Sub content.\n"
    )

    def test_extract_h2_section(self):
        result = _extract_section(self.SAMPLE_DOC, "Architecture")
        assert result is not None
        assert "Arch content line 1." in result
        assert "Arch content line 2." in result
        # Should NOT include the Implementation section
        assert "Impl content" not in result

    def test_extract_case_insensitive(self):
        result = _extract_section(self.SAMPLE_DOC, "architecture")
        assert result is not None
        assert "Arch content" in result

    def test_extract_section_to_end(self):
        """Section that runs to end of file."""
        result = _extract_section(self.SAMPLE_DOC, "Sub-section")
        assert result is not None
        assert "Sub content." in result

    def test_section_not_found(self):
        result = _extract_section(self.SAMPLE_DOC, "Nonexistent")
        assert result is None

    def test_extract_implementation_stops_at_subsection(self):
        """Implementation (h2) should include its h3 sub-section."""
        result = _extract_section(self.SAMPLE_DOC, "Implementation")
        assert result is not None
        assert "Impl content" in result
        # h3 is lower level than h2, so it's included
        assert "Sub content" in result


# ---------------------------------------------------------------------------
# _parse_line_range()
# ---------------------------------------------------------------------------

class TestParseLineRange:
    def test_valid_range(self):
        start, end = _parse_line_range("10-50", 100)
        assert start == 10
        assert end == 50

    def test_clamp_to_bounds(self):
        start, end = _parse_line_range("0-200", 100)
        assert start == 1  # clamped to 1
        assert end == 100  # clamped to total

    def test_invalid_format(self):
        with pytest.raises(ContextResolveError, match="Invalid line range"):
            _parse_line_range("10", 100)

    def test_invalid_numbers(self):
        with pytest.raises(ContextResolveError, match="Invalid line range"):
            _parse_line_range("abc-def", 100)

    def test_start_greater_than_end(self):
        with pytest.raises(ContextResolveError, match="start.*>.*end"):
            _parse_line_range("50-10", 100)


# ---------------------------------------------------------------------------
# _resolve_file() — synchronous, uses patch_settings fixture
# ---------------------------------------------------------------------------

class TestResolveFile:
    def test_resolve_full_file(self, patch_settings):
        result = _resolve_file(
            path="my-repo/src/handler.go",
            lines=None,
            label=None,
            max_lines=500,
        )
        assert result.source_type == "file"
        assert "handler.go" in result.label
        assert "line 1: content" in result.content
        assert "line 100: content" in result.content

    def test_resolve_with_line_range(self, patch_settings):
        result = _resolve_file(
            path="my-repo/src/handler.go",
            lines="5-10",
            label="Lines 5-10",
            max_lines=500,
        )
        assert result.label == "Lines 5-10"
        assert "line 5: content" in result.content
        assert "line 10: content" in result.content
        # Should NOT have lines outside range
        assert "line 1: content" not in result.content
        assert "line 11: content" not in result.content

    def test_resolve_with_truncation(self, patch_settings):
        result = _resolve_file(
            path="my-repo/src/handler.go",
            lines=None,
            label=None,
            max_lines=5,
        )
        assert "truncated at 5 lines" in result.content

    def test_resolve_custom_label(self, patch_settings):
        result = _resolve_file(
            path="my-repo/src/handler.go",
            lines=None,
            label="API handler",
            max_lines=500,
        )
        assert result.label == "API handler"

    def test_file_not_found(self, patch_settings):
        with pytest.raises(ContextResolveError, match="File not found"):
            _resolve_file(
                path="nonexistent/file.go",
                lines=None,
                label=None,
                max_lines=500,
            )

    def test_path_traversal_blocked(self, patch_settings, tmp_workspace):
        """Paths that escape the workspace root should be rejected."""
        # Create a file outside the workspace that the path would resolve to
        # The security check runs after the file existence check, so we need
        # the traversal path to actually exist OR we test that "not found" is
        # raised for non-existent traversal paths (which is also safe).
        # For a true traversal test, create a file that exists but is outside:
        outside_dir = tmp_workspace.parent / "outside"
        outside_dir.mkdir(exist_ok=True)
        (outside_dir / "secret.txt").write_text("secret")

        # Construct a path that traverses out of workspace
        relative_escape = "../outside/secret.txt"
        with pytest.raises(ContextResolveError, match="outside the workspace"):
            _resolve_file(
                path=relative_escape,
                lines=None,
                label=None,
                max_lines=500,
            )


# ---------------------------------------------------------------------------
# _resolve_reference() — synchronous, uses patch_settings fixture
# ---------------------------------------------------------------------------

class TestResolveReference:
    def test_resolve_full_doc(self, patch_settings):
        result = _resolve_reference(
            doc="sample-reference.md",
            section=None,
            label=None,
            max_lines=300,
        )
        assert result.source_type == "reference"
        assert result.label == "sample-reference.md"
        assert "Architecture" in result.content
        assert "Implementation" in result.content

    def test_resolve_with_section(self, patch_settings):
        result = _resolve_reference(
            doc="sample-reference.md",
            section="Architecture",
            label=None,
            max_lines=300,
        )
        assert "Architecture" in result.label
        assert "API layer" in result.content
        assert "Implementation details" not in result.content

    def test_resolve_with_custom_label(self, patch_settings):
        result = _resolve_reference(
            doc="sample-reference.md",
            section=None,
            label="System overview",
            max_lines=300,
        )
        assert result.label == "System overview"

    def test_doc_not_found(self, patch_settings):
        with pytest.raises(ContextResolveError, match="not found"):
            _resolve_reference(
                doc="nonexistent.md",
                section=None,
                label=None,
                max_lines=300,
            )

    def test_section_not_found(self, patch_settings):
        with pytest.raises(ContextResolveError, match="not found"):
            _resolve_reference(
                doc="sample-reference.md",
                section="Nonexistent Section",
                label=None,
                max_lines=300,
            )


# ---------------------------------------------------------------------------
# resolve_context() — async, needs DB mock for task_output type
# ---------------------------------------------------------------------------

class TestResolveContext:
    @pytest.mark.asyncio
    async def test_resolve_text_item(self):
        items = [
            ContextItem(type="text", content="Hello world", label="Greeting"),
        ]
        result = await resolve_context(items)
        assert len(result) == 1
        assert result[0].label == "Greeting"
        assert result[0].content == "Hello world"
        assert result[0].source_type == "text"

    @pytest.mark.asyncio
    async def test_resolve_file_item(self, patch_settings):
        items = [
            ContextItem(
                type="file",
                path="my-repo/src/handler.go",
                lines="1-5",
                label="Handler head",
            ),
        ]
        result = await resolve_context(items)
        assert len(result) == 1
        assert result[0].label == "Handler head"
        assert "line 1: content" in result[0].content

    @pytest.mark.asyncio
    async def test_resolve_reference_item(self, patch_settings):
        items = [
            ContextItem(
                type="reference",
                doc="sample-reference.md",
                section="Architecture",
            ),
        ]
        result = await resolve_context(items)
        assert len(result) == 1
        assert "API layer" in result[0].content

    @pytest.mark.asyncio
    async def test_resolve_task_output_item(self):
        """Mock the DB to return a completed task with output."""
        mock_row = MagicMock()
        mock_row.status = "completed"
        mock_row.summary = "Task completed successfully with 3 files changed."
        mock_row.output = "Full verbose output..."

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_row)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.database.async_session", return_value=mock_session):
            items = [
                ContextItem(
                    type="task_output",
                    task_id="abc123def456",
                    label="Prior research",
                ),
            ]
            result = await resolve_context(items)

        assert len(result) == 1
        assert result[0].label == "Prior research"
        # Should prefer summary over full output
        assert "Task completed successfully" in result[0].content

    @pytest.mark.asyncio
    async def test_resolve_task_output_not_found(self):
        """task_output with a non-existent task_id should produce a FAILED block."""
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=None)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.database.async_session", return_value=mock_session):
            items = [
                ContextItem(type="task_output", task_id="nonexistent1"),
            ]
            result = await resolve_context(items)

        assert len(result) == 1
        assert "[FAILED]" in result[0].label
        assert "not found" in result[0].content

    @pytest.mark.asyncio
    async def test_resolve_task_output_not_finished(self):
        """Referencing a running task should produce a FAILED block."""
        mock_row = MagicMock()
        mock_row.status = "running"

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_row)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.database.async_session", return_value=mock_session):
            items = [
                ContextItem(type="task_output", task_id="running12345"),
            ]
            result = await resolve_context(items)

        assert len(result) == 1
        assert "[FAILED]" in result[0].label
        assert "not finished" in result[0].content

    @pytest.mark.asyncio
    async def test_parent_task_auto_injected(self):
        """When parent_task_id is set, parent output is auto-injected."""
        mock_row = MagicMock()
        mock_row.status = "completed"
        mock_row.summary = "Parent summary."
        mock_row.output = "Parent full output."

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_row)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.database.async_session", return_value=mock_session):
            items = [
                ContextItem(type="text", content="Extra info"),
            ]
            result = await resolve_context(items, parent_task_id="parent123456")

        assert len(result) == 2
        # Parent should be first
        assert result[0].label == "Parent task output"
        assert "Parent summary." in result[0].content
        # Then the explicit text item
        assert result[1].content == "Extra info"

    @pytest.mark.asyncio
    async def test_parent_not_duplicated_if_explicit(self):
        """If an explicit task_output references the parent, don't auto-inject."""
        mock_row = MagicMock()
        mock_row.status = "completed"
        mock_row.summary = "Parent summary."
        mock_row.output = "Parent full output."

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_row)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.database.async_session", return_value=mock_session):
            items = [
                ContextItem(
                    type="task_output",
                    task_id="parent123456",
                    label="Explicit parent ref",
                ),
            ]
            result = await resolve_context(
                items, parent_task_id="parent123456"
            )

        # Should NOT have duplicate — only the explicit one
        assert len(result) == 1
        assert result[0].label == "Explicit parent ref"

    @pytest.mark.asyncio
    async def test_multiple_items_mixed_types(self, patch_settings):
        """Resolve a mix of text, file, and reference items."""
        items = [
            ContextItem(type="text", content="Inline note", label="Note"),
            ContextItem(
                type="file",
                path="my-repo/src/handler.go",
                lines="1-3",
                label="Handler",
            ),
            ContextItem(
                type="reference",
                doc="sample-reference.md",
                section="Architecture",
                label="Arch ref",
            ),
        ]
        result = await resolve_context(items)
        assert len(result) == 3
        assert result[0].label == "Note"
        assert result[1].label == "Handler"
        assert result[2].label == "Arch ref"

    @pytest.mark.asyncio
    async def test_text_item_missing_content(self):
        """Text item without content should produce a FAILED block."""
        items = [ContextItem(type="text", content=None)]
        result = await resolve_context(items)
        assert len(result) == 1
        assert "[FAILED]" in result[0].label

    @pytest.mark.asyncio
    async def test_file_item_missing_path(self):
        """File item without path should produce a FAILED block."""
        items = [ContextItem(type="file", path=None)]
        result = await resolve_context(items)
        assert len(result) == 1
        assert "[FAILED]" in result[0].label

    @pytest.mark.asyncio
    async def test_reference_item_missing_doc(self):
        """Reference item without doc should produce a FAILED block."""
        items = [ContextItem(type="reference", doc=None)]
        result = await resolve_context(items)
        assert len(result) == 1
        assert "[FAILED]" in result[0].label

    @pytest.mark.asyncio
    async def test_empty_items_list(self):
        """Empty context list should return empty results."""
        result = await resolve_context([])
        assert result == []
