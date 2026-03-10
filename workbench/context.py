"""Context resolver — fetches and assembles precise context for agent tasks.

Each ContextItem declares what the task needs. The resolver fetches only
that content and returns labeled text blocks ready for prompt injection.

Supported context types:
  - task_output: Output (or summary) from a prior completed task
  - reference:   Section from a workspace reference doc
  - file:        File content from the workspace (with optional line range)
  - text:        Raw inline text (passed through as-is)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .config import settings
from .exceptions import ContextResolveError
from .models import ContextItem

log = logging.getLogger(__name__)

# Default line limits to prevent prompt bloat
DEFAULT_FILE_MAX_LINES = 500
DEFAULT_TASK_OUTPUT_MAX_LINES = 200
DEFAULT_REFERENCE_MAX_LINES = 300


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class ResolvedContext:
    """A resolved context block ready for prompt injection."""

    __slots__ = ("label", "content", "source_type", "source_ref")

    def __init__(
        self,
        label: str,
        content: str,
        source_type: str,
        source_ref: str,
    ) -> None:
        self.label = label
        self.content = content
        self.source_type = source_type
        self.source_ref = source_ref

    def render(self) -> str:
        """Render as a labeled markdown block for the prompt."""
        return (
            f"### {self.label}\n"
            f"<!-- source: {self.source_type}:{self.source_ref} -->\n"
            f"{self.content}"
        )


async def resolve_context(
    items: list[ContextItem],
    parent_task_id: str | None = None,
) -> list[ResolvedContext]:
    """Resolve a list of ContextItems into prompt-ready text blocks.

    If parent_task_id is set and no explicit task_output item references
    the parent, the parent's output is auto-injected as the first context block.
    """
    resolved: list[ResolvedContext] = []

    # Auto-inject parent task output if chaining
    parent_already_included = any(
        item.type == "task_output" and item.task_id == parent_task_id
        for item in items
    )
    if parent_task_id and not parent_already_included:
        parent_ctx = await _resolve_task_output(
            task_id=parent_task_id,
            label="Parent task output",
            max_lines=DEFAULT_TASK_OUTPUT_MAX_LINES,
        )
        if parent_ctx:
            resolved.append(parent_ctx)

    # Resolve each explicit item
    for item in items:
        try:
            ctx = await _resolve_item(item)
            if ctx:
                resolved.append(ctx)
        except ContextResolveError as e:
            log.warning("Failed to resolve context item %s: %s", item.type, e)
            # Non-fatal: skip this item, log the failure, continue
            resolved.append(ResolvedContext(
                label=item.label or f"[FAILED] {item.type}",
                content=f"(Could not resolve: {e})",
                source_type=item.type,
                source_ref="error",
            ))

    return resolved


# ---------------------------------------------------------------------------
# Per-type resolvers
# ---------------------------------------------------------------------------

async def _resolve_item(item: ContextItem) -> ResolvedContext | None:
    """Dispatch to the correct resolver based on item type."""
    if item.type == "task_output":
        if not item.task_id:
            raise ContextResolveError("task_output requires task_id")
        return await _resolve_task_output(
            task_id=item.task_id,
            label=item.label or f"Output from task {item.task_id[:8]}",
            max_lines=item.max_lines or DEFAULT_TASK_OUTPUT_MAX_LINES,
        )
    elif item.type == "reference":
        if not item.doc:
            raise ContextResolveError("reference requires doc")
        return _resolve_reference(
            doc=item.doc,
            section=item.section,
            label=item.label,
            max_lines=item.max_lines or DEFAULT_REFERENCE_MAX_LINES,
        )
    elif item.type == "file":
        if not item.path:
            raise ContextResolveError("file requires path")
        return _resolve_file(
            path=item.path,
            lines=item.lines,
            label=item.label,
            max_lines=item.max_lines or DEFAULT_FILE_MAX_LINES,
        )
    elif item.type == "text":
        if not item.content:
            raise ContextResolveError("text requires content")
        return ResolvedContext(
            label=item.label or "Additional context",
            content=item.content,
            source_type="text",
            source_ref="inline",
        )
    else:
        raise ContextResolveError(f"Unknown context type: {item.type}")


async def _resolve_task_output(
    task_id: str,
    label: str,
    max_lines: int,
) -> ResolvedContext | None:
    """Fetch a task's summary or output from the database."""
    # Import here to avoid circular import (database imports config, models)
    from .database import TaskRow, async_session

    async with async_session() as session:
        row = await session.get(TaskRow, task_id)

    if row is None:
        raise ContextResolveError(f"Task {task_id} not found")

    if row.status not in ("completed", "failed"):
        raise ContextResolveError(
            f"Task {task_id} is not finished (status={row.status}). "
            "Only completed/failed tasks can be used as context."
        )

    # Prefer summary (compact) over full output (verbose)
    text = row.summary or row.output or "(no output)"
    text = _truncate(text, max_lines)

    return ResolvedContext(
        label=label,
        content=text,
        source_type="task_output",
        source_ref=task_id,
    )


def _resolve_reference(
    doc: str,
    section: str | None,
    label: str | None,
    max_lines: int,
) -> ResolvedContext:
    """Read a reference document (or a specific section) from the workspace."""
    refs_dir = settings.resolved_references_dir
    doc_path = refs_dir / doc

    if not doc_path.is_file():
        raise ContextResolveError(
            f"Reference doc not found: {doc} (looked in {refs_dir})"
        )

    full_text = doc_path.read_text(encoding="utf-8")

    if section:
        extracted = _extract_section(full_text, section)
        if extracted is None:
            raise ContextResolveError(
                f"Section '{section}' not found in {doc}"
            )
        text = extracted
        auto_label = f"{doc} > {section}"
    else:
        text = full_text
        auto_label = doc

    text = _truncate(text, max_lines)

    return ResolvedContext(
        label=label or auto_label,
        content=text,
        source_type="reference",
        source_ref=f"{doc}:{section}" if section else doc,
    )


def _resolve_file(
    path: str,
    lines: str | None,
    label: str | None,
    max_lines: int,
) -> ResolvedContext:
    """Read a file from the workspace, optionally a specific line range."""
    file_path = settings.workspace_root / path

    if not file_path.is_file():
        raise ContextResolveError(f"File not found: {path}")

    # Security: ensure the file is within the workspace
    try:
        file_path.resolve().relative_to(settings.workspace_root.resolve())
    except ValueError:
        raise ContextResolveError(
            f"File {path} is outside the workspace root"
        )

    all_lines = file_path.read_text(encoding="utf-8").splitlines()

    if lines:
        start, end = _parse_line_range(lines, len(all_lines))
        selected = all_lines[start - 1 : end]  # 1-indexed to 0-indexed
        source_ref = f"{path}:{lines}"
        auto_label = f"{Path(path).name} (L{start}-{end})"
    else:
        selected = all_lines
        source_ref = path
        auto_label = Path(path).name

    # Apply line limit
    if len(selected) > max_lines:
        selected = selected[:max_lines]
        selected.append(f"\n... (truncated at {max_lines} lines)")

    text = "\n".join(selected)

    return ResolvedContext(
        label=label or auto_label,
        content=f"```\n{text}\n```",
        source_type="file",
        source_ref=source_ref,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(text: str, max_lines: int) -> str:
    """Truncate text to max_lines, appending a notice if truncated."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    truncated = lines[:max_lines]
    truncated.append(f"\n... (truncated at {max_lines} lines, {len(lines)} total)")
    return "\n".join(truncated)


def _extract_section(text: str, section_name: str) -> str | None:
    """Extract a markdown section by heading name.

    Finds the heading (any level) matching section_name and returns
    everything until the next heading of equal or higher level.
    """
    lines = text.splitlines()
    start_idx = None
    start_level = None

    for i, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+)", line)
        if match:
            level = len(match.group(1))
            heading = match.group(2).strip()
            if start_idx is None:
                # Looking for the target section
                if heading.lower() == section_name.lower():
                    start_idx = i
                    start_level = level
            else:
                # Found the start, looking for the end
                if level <= start_level:  # type: ignore[operator]
                    return "\n".join(lines[start_idx:i]).strip()

    if start_idx is not None:
        # Section runs to end of file
        return "\n".join(lines[start_idx:]).strip()

    return None


def _parse_line_range(spec: str, total_lines: int) -> tuple[int, int]:
    """Parse a line range spec like '10-50' into (start, end) 1-indexed."""
    parts = spec.split("-")
    if len(parts) != 2:
        raise ContextResolveError(f"Invalid line range: {spec!r} (expected 'start-end')")
    try:
        start = max(1, int(parts[0]))
        end = min(total_lines, int(parts[1]))
    except ValueError:
        raise ContextResolveError(f"Invalid line range: {spec!r}")
    if start > end:
        raise ContextResolveError(f"Invalid line range: start ({start}) > end ({end})")
    return start, end
