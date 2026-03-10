"""Event log system — append-only file-based event log for async supervision.

Writes structured events to a log file so the main agent can check pipeline
progress without polling the API.  Events are one-per-line with a fixed
format for easy grepping:

    YYYY-MM-DD HH:MM:SS | event_type | pipeline_id | stage | detail

The file path defaults to <workspace_root>/work-directory/workbench-events.log
and is created on first write.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from .config import settings

log = logging.getLogger(__name__)

# Resolve once at import time; the file is created lazily on first emit().
_EVENT_LOG_PATH: Path = settings.workspace_root / "work-directory" / "workbench-events.log"


def _ensure_dir() -> None:
    """Create the parent directory if it doesn't exist."""
    _EVENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _emit_sync(
    event_type: str,
    *,
    pipeline_id: str = "-",
    stage: str | None = None,
    task_id: str | None = None,
    detail: str = "",
) -> None:
    """Synchronous implementation of event emission (runs in a thread)."""
    try:
        _ensure_dir()

        ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        stage_str = stage or "-"
        task_str = task_id or "-"
        # Sanitize detail to single line
        detail_clean = detail.replace("\n", " ").strip()

        line = f"{ts} | {event_type} | {pipeline_id} | {stage_str} | {task_str} | {detail_clean}\n"

        with _EVENT_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)

    except Exception:
        # Event logging must never crash the pipeline manager
        log.warning("Failed to write event to %s", _EVENT_LOG_PATH, exc_info=True)


async def emit(
    event_type: str,
    *,
    pipeline_id: str = "-",
    stage: str | None = None,
    task_id: str | None = None,
    detail: str = "",
) -> None:
    """Append a single event line to the event log.

    Runs the blocking file I/O in a thread to avoid blocking the asyncio
    event loop.

    Parameters
    ----------
    event_type : str
        e.g. "stage_dispatched", "review_rejected", "pipeline_completed"
    pipeline_id : str
        Pipeline ID (or "-" if not pipeline-related)
    stage : str | None
        Stage name if applicable
    task_id : str | None
        Related task ID if applicable
    detail : str
        Free-form detail text (single line; newlines are stripped)
    """
    await asyncio.to_thread(
        _emit_sync,
        event_type,
        pipeline_id=pipeline_id,
        stage=stage,
        task_id=task_id,
        detail=detail,
    )


def _tail_sync(n: int = 20) -> list[str]:
    """Synchronous implementation of tail (runs in a thread)."""
    if not _EVENT_LOG_PATH.exists():
        return []
    try:
        lines = _EVENT_LOG_PATH.read_text(encoding="utf-8").splitlines()
        return lines[-n:]
    except Exception:
        log.warning("Failed to read event log", exc_info=True)
        return []


async def tail(n: int = 20) -> list[str]:
    """Return the last N lines from the event log.

    Runs the blocking file I/O in a thread to avoid blocking the asyncio
    event loop.

    Returns an empty list if the file doesn't exist yet.
    """
    return await asyncio.to_thread(_tail_sync, n)
