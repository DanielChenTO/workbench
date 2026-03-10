"""Unit tests for workbench.review — structured code review for pipelines."""

from __future__ import annotations

import pytest

from workbench.review import (
    ReviewFinding,
    ReviewResult,
    SEVERITY_P0,
    SEVERITY_P1,
    SEVERITY_P2,
    build_review_prompt,
    parse_structured_review,
)


# ---------------------------------------------------------------------------
# ReviewResult
# ---------------------------------------------------------------------------

class TestReviewResult:
    def test_empty_findings(self):
        r = ReviewResult(approved=True, reason="approved")
        assert r.p0_count == 0
        assert r.p1_count == 0
        assert r.p2_count == 0
        assert "APPROVE" in r.summary_line()

    def test_severity_counts(self):
        r = ReviewResult(
            approved=False,
            reason="issues found",
            findings=[
                ReviewFinding(severity="P0", file="a.py", line="10", description="bug"),
                ReviewFinding(severity="P0", file="b.py", line="20", description="another bug"),
                ReviewFinding(severity="P1", file="c.py", line=None, description="bad pattern"),
                ReviewFinding(severity="P2", file=None, line=None, description="naming"),
            ],
        )
        assert r.p0_count == 2
        assert r.p1_count == 1
        assert r.p2_count == 1
        assert "REJECT" in r.summary_line()
        assert "2 P0" in r.summary_line()
        assert "1 P1" in r.summary_line()
        assert "1 P2" in r.summary_line()

    def test_feedback_for_implementer_no_findings(self):
        r = ReviewResult(approved=False, reason="looks wrong")
        assert r.feedback_for_implementer() == "looks wrong"

    def test_feedback_for_implementer_with_findings(self):
        r = ReviewResult(
            approved=False,
            reason="issues found",
            findings=[
                ReviewFinding(severity="P0", file="a.py", line="10", description="null deref"),
                ReviewFinding(severity="P2", file=None, line=None, description="rename var"),
            ],
        )
        feedback = r.feedback_for_implementer()
        assert "P0 Findings" in feedback
        assert "null deref" in feedback
        assert "`a.py:10`" in feedback
        assert "P2 Findings" in feedback
        assert "rename var" in feedback
        # P1 section should not appear (no P1 findings)
        assert "P1 Findings" not in feedback

    def test_feedback_includes_file_without_line(self):
        r = ReviewResult(
            approved=False,
            reason="x",
            findings=[
                ReviewFinding(severity="P1", file="foo.go", line=None, description="issue"),
            ],
        )
        feedback = r.feedback_for_implementer()
        assert "`foo.go`" in feedback
        assert ":None" not in feedback  # should not include None as line


# ---------------------------------------------------------------------------
# parse_structured_review
# ---------------------------------------------------------------------------

class TestParseStructuredReview:
    def test_empty_output(self):
        r = parse_structured_review("")
        assert r.approved is True
        assert "auto-approved" in r.reason

    def test_simple_approve(self):
        r = parse_structured_review("Everything looks good.\n\nAPPROVE")
        assert r.approved is True
        assert r.reason == "approved"

    def test_simple_reject(self):
        r = parse_structured_review("Issues found.\n\nREJECT: Missing error handling")
        assert r.approved is False
        assert "Missing error handling" in r.reason

    def test_verdict_prefix_approve(self):
        r = parse_structured_review("VERDICT: APPROVE")
        assert r.approved is True

    def test_verdict_prefix_reject(self):
        r = parse_structured_review("VERDICT: REJECT: off-by-one error in loop")
        assert r.approved is False
        assert "off-by-one" in r.reason

    def test_case_insensitive(self):
        r = parse_structured_review("approve")
        assert r.approved is True

        r = parse_structured_review("reject: bad code")
        assert r.approved is False

    def test_structured_findings_with_approve(self):
        output = """## Findings

- P2: Could improve variable naming (`utils.py:15`)
- P2: Consider adding docstring (`utils.py:1`)

## Verdict

APPROVE"""
        r = parse_structured_review(output)
        assert r.approved is True
        assert len(r.findings) == 2
        assert all(f.severity == "P2" for f in r.findings)
        assert r.findings[0].file == "utils.py"
        assert r.findings[0].line == "15"

    def test_structured_findings_with_reject(self):
        output = """## Findings

- P0: SQL injection vulnerability in user input handling (`api/handler.py:42`)
- P1: Missing validation on request body (`api/handler.py:55-60`)
- P2: Unused import (`api/handler.py:3`)

## Verdict

REJECT: Critical security vulnerability must be fixed"""
        r = parse_structured_review(output)
        assert r.approved is False
        assert r.p0_count == 1
        assert r.p1_count == 1
        assert r.p2_count == 1
        assert r.findings[0].file == "api/handler.py"
        assert r.findings[0].line == "42"
        assert r.findings[1].line == "55-60"
        assert "security vulnerability" in r.reason

    def test_no_verdict_with_p0_auto_rejects(self):
        output = """## Findings

- P0: Data loss risk when connection drops (`db.py:100`)

No explicit verdict here."""
        r = parse_structured_review(output)
        assert r.approved is False
        assert "P0 findings present" in r.reason
        assert r.p0_count == 1

    def test_no_verdict_no_findings_auto_approves(self):
        output = "The code looks reasonable overall. No major issues spotted."
        r = parse_structured_review(output)
        assert r.approved is True
        assert "auto-approved" in r.reason

    def test_findings_without_citations(self):
        output = """## Findings

- P1: Error messages are not user-friendly
- P2: Consider using constants instead of magic numbers

## Verdict

REJECT: Error handling needs work"""
        r = parse_structured_review(output)
        assert r.approved is False
        assert len(r.findings) == 2
        assert r.findings[0].file is None
        assert r.findings[0].line is None

    def test_star_bullet_findings(self):
        """Findings can use * bullets too."""
        output = """## Findings

* P0: Race condition in cache invalidation (`cache.py:88`)

## Verdict

REJECT: race condition"""
        r = parse_structured_review(output)
        assert r.p0_count == 1
        assert r.findings[0].file == "cache.py"


# ---------------------------------------------------------------------------
# build_review_prompt
# ---------------------------------------------------------------------------

class TestBuildReviewPrompt:
    def test_basic_prompt_with_diff(self):
        prompt = build_review_prompt(
            diff="--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new",
        )
        assert "## Code Review Instructions" in prompt
        assert "```diff" in prompt
        assert "+new" in prompt
        assert "P0 (Blocking)" in prompt
        assert "APPROVE" in prompt
        assert "REJECT" in prompt

    def test_includes_stage_prompt(self):
        prompt = build_review_prompt(
            diff="some diff",
            stage_prompt="Focus on performance implications.",
        )
        assert "Focus on performance implications." in prompt
        assert "## Code Review Instructions" in prompt

    def test_includes_diff_stat(self):
        prompt = build_review_prompt(
            diff="the diff",
            diff_stat=" foo.py | 10 +++++-----\n 1 file changed",
        )
        assert "## Change Summary" in prompt
        assert "foo.py" in prompt

    def test_empty_diff(self):
        prompt = build_review_prompt(diff="")
        assert "No diff available" in prompt

    def test_diff_truncation(self):
        # Build a diff with more lines than the limit
        long_diff = "\n".join([f"+line {i}" for i in range(100)])
        prompt = build_review_prompt(diff=long_diff, max_diff_lines=50)
        assert "truncated" in prompt.lower()
        # Should only include 50 lines of the diff
        assert "+line 49" in prompt
        assert "+line 99" not in prompt

    def test_review_iteration_note(self):
        prompt = build_review_prompt(diff="diff", review_iteration=2)
        assert "iteration 3" in prompt  # 0-indexed → display as 1-indexed
        assert "prior rejection" in prompt.lower()

    def test_no_iteration_note_on_first_review(self):
        prompt = build_review_prompt(diff="diff", review_iteration=0)
        assert "iteration" not in prompt.lower()

    def test_pipeline_context(self):
        prompt = build_review_prompt(
            diff="diff",
            pipeline_context="The user asked to add a retry mechanism.",
        )
        assert "## Pipeline Context" in prompt
        assert "retry mechanism" in prompt
