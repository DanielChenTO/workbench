"""Structured code review for pipeline review stages.

Provides:
  - Review prompt template building with severity levels and citation format
  - Diff collection from implementation branches
  - Enhanced verdict parsing that extracts structured findings
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Severity levels for review findings
# ---------------------------------------------------------------------------

SEVERITY_P0 = "P0"  # Blocking — must fix before merge (bugs, data loss, security)
SEVERITY_P1 = "P1"  # Important — should fix before merge (logic errors, bad patterns)
SEVERITY_P2 = "P2"  # Suggestion — nice to have (style, naming, minor improvements)

_VALID_SEVERITIES = {SEVERITY_P0, SEVERITY_P1, SEVERITY_P2}


# ---------------------------------------------------------------------------
# Structured review data
# ---------------------------------------------------------------------------

@dataclass
class ReviewFinding:
    """A single structured finding from a code review."""
    severity: str        # P0, P1, P2
    file: str | None     # file path (if cited)
    line: str | None     # line number or range (if cited)
    description: str     # what the finding is about


@dataclass
class ReviewResult:
    """Parsed result of a structured code review."""
    approved: bool
    reason: str
    findings: list[ReviewFinding] = field(default_factory=list)

    @property
    def p0_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEVERITY_P0)

    @property
    def p1_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEVERITY_P1)

    @property
    def p2_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEVERITY_P2)

    def summary_line(self) -> str:
        """One-line summary like 'REJECT (2 P0, 1 P1, 3 P2)'."""
        verdict = "APPROVE" if self.approved else "REJECT"
        if not self.findings:
            return f"{verdict}: {self.reason}"
        counts = []
        for sev, cnt in [(SEVERITY_P0, self.p0_count), (SEVERITY_P1, self.p1_count), (SEVERITY_P2, self.p2_count)]:
            if cnt:
                counts.append(f"{cnt} {sev}")
        parts = ", ".join(counts) if counts else "no findings"
        return f"{verdict} ({parts}): {self.reason}"

    def feedback_for_implementer(self) -> str:
        """Format findings as actionable feedback for a rejection loop."""
        if not self.findings:
            return self.reason

        lines = [f"## Review Feedback — {self.summary_line()}\n"]

        # Group by severity, P0 first
        for severity in [SEVERITY_P0, SEVERITY_P1, SEVERITY_P2]:
            items = [f for f in self.findings if f.severity == severity]
            if not items:
                continue
            lines.append(f"### {severity} Findings\n")
            for i, item in enumerate(items, 1):
                location = ""
                if item.file:
                    location = f" (`{item.file}"
                    if item.line:
                        location += f":{item.line}"
                    location += "`)"
                lines.append(f"{i}. {item.description}{location}")
            lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Review prompt builder
# ---------------------------------------------------------------------------

_REVIEW_INSTRUCTIONS_TEMPLATE = """
## Code Review Instructions

You are reviewing code changes from the implementation stage of a pipeline.
Your job is to evaluate the changes for correctness, completeness, and quality.

### What to check

1. **Correctness** — Does the code do what it's supposed to? Are there bugs, off-by-one errors, race conditions, or unhandled edge cases?
2. **Completeness** — Does the implementation fully address the requirements from the original task? Are there missing pieces?
3. **Error handling** — Are errors handled properly? Are there failure modes that could cause silent data loss or crashes?
4. **Security** — Are there injection risks, exposed secrets, or permission issues?
5. **Code quality** — Is the code readable, well-structured, and maintainable? Are there obvious code smells or anti-patterns?

### Severity levels

Classify each finding by severity:

- **P0 (Blocking)** — Must fix before merge. Bugs, data loss risks, security vulnerabilities, broken functionality.
- **P1 (Important)** — Should fix before merge. Logic errors, bad patterns, missing validation, incomplete error handling.
- **P2 (Suggestion)** — Nice to have. Style improvements, naming, minor refactoring opportunities.

### Output format

Structure your review output EXACTLY as follows:

```
## Findings

- P0: <description> (`file/path.py:42`)
- P0: <description> (`file/path.py:100-110`)
- P1: <description> (`file/path.py:200`)
- P2: <description>

## Verdict

APPROVE
```

OR if rejecting:

```
## Findings

- P0: <description> (`file/path.py:42`)
...

## Verdict

REJECT: <one-line reason summarizing the key issues>
```

### Rules

- Any P0 finding MUST result in REJECT.
- Use APPROVE only when there are no P0 or P1 findings, OR when P1 findings are minor enough to be acceptable.
- Always cite the file and line number/range when possible.
- Be specific and actionable — the implementer needs to understand exactly what to fix.
- Do NOT nitpick style issues as P0 or P1.
- If there are no issues, you may APPROVE with an empty findings section.
""".strip()


def build_review_prompt(
    *,
    diff: str,
    diff_stat: str | None = None,
    stage_prompt: str | None = None,
    pipeline_context: str | None = None,
    review_iteration: int = 0,
    max_diff_lines: int = 2000,
) -> str:
    """Build a complete structured review prompt with diff and instructions.

    Args:
        diff: The unified diff of implementation changes.
        diff_stat: Optional --stat summary of the diff.
        stage_prompt: The original stage prompt (user-provided review instructions).
        pipeline_context: Optional context about the pipeline (e.g., what was requested).
        review_iteration: Which review iteration this is (0 = first review).
        max_diff_lines: Truncate diff after this many lines to avoid prompt bloat.

    Returns:
        The complete prompt string to send to the review agent.
    """
    sections: list[str] = []

    # User's stage prompt first (if any)
    if stage_prompt:
        sections.append(stage_prompt.strip())

    # Pipeline context (original request, etc.)
    if pipeline_context:
        sections.append(f"## Pipeline Context\n\n{pipeline_context.strip()}")

    # Review instructions
    sections.append(_REVIEW_INSTRUCTIONS_TEMPLATE)

    # Iteration note
    if review_iteration > 0:
        sections.append(
            f"**Note:** This is review iteration {review_iteration + 1}. "
            f"Previous review(s) rejected the implementation. "
            f"Focus on whether the feedback from prior rejection(s) was addressed."
        )

    # Diff stat (quick overview)
    if diff_stat:
        sections.append(f"## Change Summary\n\n```\n{diff_stat.strip()}\n```")

    # The actual diff
    diff_lines = diff.split("\n")
    if len(diff_lines) > max_diff_lines:
        truncated_diff = "\n".join(diff_lines[:max_diff_lines])
        sections.append(
            f"## Diff (truncated to {max_diff_lines} of {len(diff_lines)} lines)\n\n"
            f"```diff\n{truncated_diff}\n```\n\n"
            f"*Diff truncated. Focus your review on the visible portion.*"
        )
    elif diff.strip():
        sections.append(f"## Diff\n\n```diff\n{diff.strip()}\n```")
    else:
        sections.append(
            "## Diff\n\n*No diff available — the implementation branch has no changes "
            "relative to the default branch. This may indicate a problem.*"
        )

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Enhanced verdict parsing
# ---------------------------------------------------------------------------

# Patterns for structured findings
_FINDING_PATTERN = re.compile(
    r"[-*]\s*(P[012]):\s*(.+?)(?:\(`([^`]+?)`\))?$",
    re.MULTILINE,
)

# Verdict patterns (same as pipeline.py but kept here for the enhanced parser)
_APPROVE_RE = re.compile(
    r"(?:^|\n)\s*(?:VERDICT:\s*)?APPROVE\b",
    re.IGNORECASE,
)
_REJECT_RE = re.compile(
    r"(?:^|\n)\s*(?:VERDICT:\s*)?REJECT[:\s]*(.+?)(?:\n|$)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_citation(citation: str | None) -> tuple[str | None, str | None]:
    """Parse a citation like 'file/path.py:42' into (file, line)."""
    if not citation:
        return None, None
    citation = citation.strip()
    if ":" in citation:
        parts = citation.rsplit(":", 1)
        return parts[0].strip(), parts[1].strip()
    return citation, None


def parse_structured_review(output: str) -> ReviewResult:
    """Parse task output for structured review findings and verdict.

    This is the enhanced version of parse_review_verdict() that also
    extracts individual findings with severity and citations.

    Returns a ReviewResult with the verdict and all parsed findings.
    """
    if not output:
        return ReviewResult(approved=True, reason="no output — auto-approved")

    # --- Parse findings ---
    findings: list[ReviewFinding] = []
    for m in _FINDING_PATTERN.finditer(output):
        severity = m.group(1).upper()
        description = m.group(2).strip().rstrip("(").strip()
        citation = m.group(3)
        file_path, line = _parse_citation(citation)

        if severity in _VALID_SEVERITIES:
            findings.append(ReviewFinding(
                severity=severity,
                file=file_path,
                line=line,
                description=description,
            ))

    # --- Parse verdict ---
    reject_match = _REJECT_RE.search(output)
    if reject_match:
        reason = reject_match.group(1).strip()
        return ReviewResult(
            approved=False,
            reason=reason or "rejected (no reason given)",
            findings=findings,
        )

    if _APPROVE_RE.search(output):
        return ReviewResult(
            approved=True,
            reason="approved",
            findings=findings,
        )

    # No explicit verdict — infer from findings
    if any(f.severity == SEVERITY_P0 for f in findings):
        return ReviewResult(
            approved=False,
            reason="no explicit verdict, but P0 findings present — auto-rejected",
            findings=findings,
        )

    # Default: approve with warning
    log.warning("Review output has no APPROVE/REJECT verdict — defaulting to APPROVE")
    return ReviewResult(
        approved=True,
        reason="no verdict found — auto-approved",
        findings=findings,
    )


# ---------------------------------------------------------------------------
# Helpers for pipeline integration
# ---------------------------------------------------------------------------

async def collect_implementation_diff(
    pipeline_id: str,
    repo_path: str,
) -> tuple[str, str]:
    """Collect the combined diff from all implementation branches in a pipeline.

    Finds all completed tasks in the pipeline that have branches (i.e.,
    local/full autonomy implementation stages) and combines their diffs.

    Returns (diff, stat) — the unified diff and the stat summary.
    """
    from pathlib import Path
    from . import git_ops
    from .database import TaskRow, async_session

    path = Path(repo_path)
    diffs: list[str] = []
    stats: list[str] = []

    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(TaskRow)
            .where(TaskRow.pipeline_id == pipeline_id)
            .where(TaskRow.branch.isnot(None))
            .where(TaskRow.status == "completed")
            .order_by(TaskRow.created_at)
        )
        tasks = result.scalars().all()

    for task in tasks:
        try:
            diff = await git_ops.diff_branch_vs_default(path, task.branch)
            stat = await git_ops.diff_branch_vs_default(path, task.branch, stat_only=True)
            if diff.strip():
                diffs.append(f"# Changes from stage '{task.stage_name}' (branch: {task.branch})\n\n{diff}")
                stats.append(f"Stage '{task.stage_name}':\n{stat}")
        except git_ops.GitError as e:
            log.warning(
                "Could not get diff for branch %s (task %s): %s",
                task.branch, task.id, e,
            )
            diffs.append(f"# Changes from stage '{task.stage_name}' — diff unavailable: {e}")

    combined_diff = "\n\n".join(diffs) if diffs else ""
    combined_stat = "\n\n".join(stats) if stats else ""

    return combined_diff, combined_stat
