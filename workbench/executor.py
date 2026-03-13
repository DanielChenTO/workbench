"""OpenCode CLI executor — runs `opencode run` as a subprocess with optional log streaming."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .config import settings
from .exceptions import ExecutorError
from .models import Autonomy

if TYPE_CHECKING:
    from .context import ResolvedContext

log = logging.getLogger(__name__)

ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

SILENT_FAILURE_CLASSIFIERS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(
            r"Cannot find module|Cannot find package|ERR_MODULE_NOT_FOUND|"
            r"MODULE_NOT_FOUND|npm ERR!|pnpm ERR!|yarn (?:error|ERR)|node_modules",
            re.IGNORECASE,
        ),
        "tooling_bootstrap_failure",
        "Likely tool/dependency load failure. Verify `.opencode` dependencies are installed "
        "(for example: `npm install` in `.opencode`) and retry.",
    ),
    (
        re.compile(r"permission denied|EACCES|EPERM", re.IGNORECASE),
        "environment_permission_failure",
        "Likely environment/permission issue while launching tools. Check filesystem and "
        "runtime permissions, then retry.",
    ),
)

# Re-export for backward compatibility
__all__ = ["ExecutorError", "build_prompt", "run_opencode"]


def _normalize_usable_output(text: str) -> str:
    """Return output with control sequences removed for usability checks."""
    return ANSI_ESCAPE_PATTERN.sub("", text).strip()


def _classify_silent_failure(stderr_text: str) -> tuple[str, str]:
    """Classify zero-usable-output runs into a narrower failure class."""
    for pattern, failure_class, guidance in SILENT_FAILURE_CLASSIFIERS:
        if pattern.search(stderr_text):
            return failure_class, guidance

    return (
        "silent_runtime_failure",
        "OpenCode exited 0 without usable output. Inspect worker logs and stderr for early "
        "termination details before retrying.",
    )


def build_prompt(
    resolved_text: str,
    autonomy: Autonomy,
    repo_name: str | None,
    extra_instructions: str | None = None,
    unblock_response: str | None = None,
    context_blocks: list[ResolvedContext] | None = None,
    worktree_path: str | None = None,
) -> str:
    """Compose the full prompt sent to opencode run, including autonomy guardrails.

    Parameters
    ----------
    resolved_text:
        The resolved task description (from input resolver).
    autonomy:
        Autonomy level controlling what the agent is allowed to do.
    repo_name:
        Target repository short name.
    extra_instructions:
        Additional instructions appended to the prompt.
    unblock_response:
        Human response to a blocked question (from a previous run).
    context_blocks:
        Resolved context items to inject as a labeled ## Context section.
        Each block renders as a ### subsection with source attribution.
    worktree_path:
        Absolute path to the git worktree where the agent should make changes.
        When set, the agent is instructed to work exclusively in this directory.
    """

    sections: list[str] = []

    # --- Header ---
    sections.append("# Autonomous Agent Task")
    sections.append("")

    # --- Autonomy level ---
    if autonomy == Autonomy.FULL:
        sections.append(
            "## Instructions\n"
            "You are operating as an autonomous agent. Complete the following task end-to-end:\n"
            "1. Research the codebase to understand the relevant code.\n"
            "2. Implement the required changes.\n"
            "3. Run tests and linting. Fix any failures.\n"
            "4. Commit your changes with a clear commit message.\n"
            "5. Do NOT open a PR — the caller will handle that.\n"
            "\n"
            "Be thorough but focused. Make the minimal set of changes needed."
        )
    elif autonomy == Autonomy.LOCAL:
        sections.append(
            "## Instructions\n"
            "You are operating as an autonomous agent in LOCAL mode.\n"
            "1. Research the codebase to understand the relevant code.\n"
            "2. Implement the required changes.\n"
            "3. Run tests and linting. Fix any failures.\n"
            "4. Commit your changes with a clear commit message.\n"
            "5. Do NOT push to any remote. Do NOT create a pull request.\n"
            "6. Do NOT run `git push` or `gh pr create` under any circumstances.\n"
            "\n"
            "Be thorough but focused. Make the minimal set of changes needed."
        )
    elif autonomy == Autonomy.PLAN_ONLY:
        sections.append(
            "## Instructions\n"
            "You are operating as an autonomous agent in PLAN-ONLY mode.\n"
            "1. Research the codebase to understand the relevant code.\n"
            "2. Produce a detailed implementation plan with specific files and changes.\n"
            "3. Do NOT make any code changes, commits, or PRs.\n"
            "4. Output the plan as your final response."
        )
    elif autonomy == Autonomy.RESEARCH_ONLY:
        sections.append(
            "## Instructions\n"
            "You are operating as an autonomous agent in RESEARCH mode.\n"
            "1. Investigate the codebase to answer the question or understand the topic.\n"
            "2. Summarize your findings concisely.\n"
            "3. Do NOT make any code changes, commits, or PRs.\n"
            "4. Output your findings as your final response."
        )

    # --- Safety guardrails ---
    sections.append(
        "\n## Safety Rules\n"
        "- NEVER push directly to main/master.\n"
        "- NEVER run destructive git operations (force push, hard reset).\n"
        "- NEVER modify CI/CD configuration files unless the task explicitly requires it.\n"
        "- NEVER commit secrets, tokens, or credentials.\n"
        "- If you are unsure about a change, err on the side of caution and skip it."
    )

    # --- Injected context ---
    if context_blocks:
        ctx_parts = ["\n## Context"]
        ctx_parts.append(
            "The following context has been provided for this task. "
            "Use it as reference — do not re-research information already given here."
        )
        ctx_parts.append("")
        for block in context_blocks:
            ctx_parts.append(block.render())
            ctx_parts.append("")
        sections.append("\n".join(ctx_parts))

    # --- Repo context ---
    if repo_name:
        sections.append(f"\n## Target Repository\n`{repo_name}`")

    # --- Worktree (isolated checkout for concurrent execution) ---
    if worktree_path:
        sections.append(
            f"\n## Working Directory\n"
            f"You are working in a **git worktree** — an isolated checkout of the repository.\n"
            f"All file reads, edits, and git operations MUST target this directory:\n\n"
            f"  `{worktree_path}`\n\n"
            f"**IMPORTANT:**\n"
            f"- Use absolute paths rooted at `{worktree_path}` for all file operations.\n"
            f"- Do NOT modify files in the main repository checkout.\n"
            f"- The branch is already checked out — do NOT create or switch branches.\n"
            f"- `git add` and `git commit` will be handled automatically after you finish."
        )

    # --- The actual task ---
    sections.append(f"\n## Task\n{resolved_text}")

    # --- Extra instructions ---
    if extra_instructions:
        sections.append(f"\n## Additional Instructions\n{extra_instructions}")

    # --- Unblock context (human answered a question from a previous run) ---
    if unblock_response:
        sections.append(
            "\n## Previously Blocked — Human Response\n"
            "In a previous execution, you indicated you were blocked and needed "
            "human input. The human has provided the following response. "
            "Use this to continue your work:\n\n"
            f"> {unblock_response}"
        )

    return "\n".join(sections)


async def run_opencode(
    prompt: str,
    work_dir: Path,
    model: str | None = None,
    timeout: int | None = None,
    log_callback: Callable[[str], None] | None = None,
    process_callback: Callable[[asyncio.subprocess.Process], None] | None = None,
) -> str:
    """Execute `opencode run` with the given prompt and return its output.

    Parameters
    ----------
    prompt:
        The full prompt to send to opencode.
    work_dir:
        Directory to run opencode in (the workspace root, typically).
    model:
        Optional model override.
    timeout:
        Per-task timeout in seconds. Defaults to ``settings.task_timeout``.
    log_callback:
        Optional callback invoked with each chunk of stdout/stderr output
        as it arrives. Used for real-time log streaming to SSE clients.
    process_callback:
        Optional callback invoked immediately after the subprocess is created.
        Used by the worker pool to register the process for watchdog kill support.
    """
    if timeout is None:
        timeout = settings.task_timeout

    cmd = [settings.opencode_bin, "run"]

    if model or settings.opencode_model:
        cmd.extend(["--model", model or settings.opencode_model])  # type: ignore[arg-type]

    # We pass the prompt as the positional argument
    cmd.append(prompt)

    log.info("Executing: %s (cwd=%s, timeout=%ds)", cmd[0], work_dir, timeout)
    log.debug("Full command: %s", cmd)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(work_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Register process with caller (e.g. worker pool for watchdog kill)
    if process_callback:
        process_callback(proc)

    collected_stdout: list[str] = []
    collected_stderr: list[str] = []

    async def _read_stream(
        stream: asyncio.StreamReader,
        collector: list[str],
        is_stderr: bool = False,
    ) -> None:
        """Read a stream line by line, invoking the callback for each chunk."""
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode(errors="replace")
            collector.append(text)
            if log_callback:
                log_callback(text)

    async def _collect_and_wait() -> None:
        # Read stdout and stderr concurrently
        await asyncio.gather(
            _read_stream(proc.stdout, collected_stdout),  # type: ignore[arg-type]
            _read_stream(proc.stderr, collected_stderr, is_stderr=True),  # type: ignore[arg-type]
        )
        await proc.wait()

    try:
        await asyncio.wait_for(_collect_and_wait(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise ExecutorError(f"opencode run timed out after {timeout}s")

    stdout_text = "".join(collected_stdout)
    stderr_text = "".join(collected_stderr)

    if proc.returncode != 0:
        log.error("opencode run failed (rc=%d): %s", proc.returncode, stderr_text[:500])
        raise ExecutorError(
            f"opencode run exited with code {proc.returncode}.\n"
            f"stderr: {stderr_text[:1000]}\n"
            f"stdout: {stdout_text[:1000]}"
        )

    usable_stdout = _normalize_usable_output(stdout_text)

    # Detect silent failures: opencode exits 0 but produces no usable output.
    # This includes fully empty output and control-sequence-only output where
    # the process appears successful but no final content is available.
    if not usable_stdout:
        failure_class, guidance = _classify_silent_failure(stderr_text)
        log.error(
            "opencode run produced no usable output (class=%s). stderr: %s",
            failure_class,
            stderr_text[:1000],
        )
        raise ExecutorError(
            "opencode run exited successfully but produced no usable output "
            f"(class={failure_class}). {guidance}\n"
            f"stderr: {stderr_text[:1000]}"
        )

    log.info("opencode run completed successfully (%d bytes output)", len(stdout_text))
    return stdout_text
