"""Unit tests for workbench.executor.build_prompt — context block rendering."""

from __future__ import annotations

from workbench.context import ResolvedContext
from workbench.executor import build_prompt
from workbench.models import Autonomy


class TestBuildPromptContextBlocks:
    """Test that context blocks are correctly rendered in the prompt."""

    def _make_block(
        self, label: str, content: str, source_type: str = "text"
    ) -> ResolvedContext:
        return ResolvedContext(
            label=label,
            content=content,
            source_type=source_type,
            source_ref="test",
        )

    def test_no_context_blocks(self):
        """Prompt without context should not contain ## Context."""
        prompt = build_prompt(
            "Do something",
            Autonomy.FULL,
            "my-repo",
        )
        assert "## Context" not in prompt
        assert "Do something" in prompt

    def test_single_text_block(self):
        """Single context block should produce ## Context with one ### subsection."""
        blocks = [self._make_block("Prior findings", "The API uses REST.")]
        prompt = build_prompt(
            "Implement the change",
            Autonomy.FULL,
            "my-repo",
            context_blocks=blocks,
        )
        assert "## Context" in prompt
        assert "### Prior findings" in prompt
        assert "The API uses REST." in prompt

    def test_multiple_blocks(self):
        """Multiple context blocks should each appear as ### subsections."""
        blocks = [
            self._make_block("Research output", "Found 3 relevant files."),
            self._make_block("API spec", "GET /users returns 200."),
            self._make_block("Note", "Use the new pattern."),
        ]
        prompt = build_prompt(
            "Implement feature",
            Autonomy.RESEARCH_ONLY,
            "my-repo",
            context_blocks=blocks,
        )
        assert "### Research output" in prompt
        assert "### API spec" in prompt
        assert "### Note" in prompt
        assert "Found 3 relevant files." in prompt

    def test_context_before_task(self):
        """Context section should appear before the ## Task section."""
        blocks = [self._make_block("Ref", "Reference content.")]
        prompt = build_prompt(
            "The actual task",
            Autonomy.FULL,
            "my-repo",
            context_blocks=blocks,
        )
        ctx_pos = prompt.index("## Context")
        task_pos = prompt.index("## Task")
        assert ctx_pos < task_pos

    def test_context_after_safety(self):
        """Context section should appear after ## Safety Rules."""
        blocks = [self._make_block("Ref", "Content.")]
        prompt = build_prompt(
            "Task text",
            Autonomy.FULL,
            "my-repo",
            context_blocks=blocks,
        )
        safety_pos = prompt.index("## Safety Rules")
        ctx_pos = prompt.index("## Context")
        assert safety_pos < ctx_pos

    def test_source_attribution_in_context(self):
        """Each context block should include source attribution comment."""
        blocks = [
            ResolvedContext(
                label="Handler file",
                content="package main",
                source_type="file",
                source_ref="my-repo/src/handler.go",
            ),
        ]
        prompt = build_prompt(
            "Review the code",
            Autonomy.RESEARCH_ONLY,
            None,
            context_blocks=blocks,
        )
        assert "<!-- source: file:my-repo/src/handler.go -->" in prompt

    def test_empty_context_blocks_list(self):
        """Empty context_blocks list should not produce ## Context."""
        prompt = build_prompt(
            "Do task",
            Autonomy.FULL,
            "my-repo",
            context_blocks=[],
        )
        assert "## Context" not in prompt

    def test_context_with_all_other_sections(self):
        """Context works alongside extra_instructions and unblock_response."""
        blocks = [self._make_block("Ref", "Some ref.")]
        prompt = build_prompt(
            "The task",
            Autonomy.FULL,
            "my-repo",
            extra_instructions="Also run tests.",
            unblock_response="Yes, proceed with option A.",
            context_blocks=blocks,
        )
        assert "## Context" in prompt
        assert "## Additional Instructions" in prompt
        assert "## Previously Blocked" in prompt
        assert "Also run tests." in prompt
        assert "option A" in prompt

    def test_context_guidance_text(self):
        """Context section should include guidance about using provided context."""
        blocks = [self._make_block("Ref", "Content.")]
        prompt = build_prompt(
            "Task",
            Autonomy.FULL,
            "my-repo",
            context_blocks=blocks,
        )
        assert "do not re-research" in prompt.lower()

    def test_worktree_path_included(self):
        """When worktree_path is set, prompt should include Working Directory section."""
        prompt = build_prompt(
            "Implement feature",
            Autonomy.FULL,
            "my-repo",
            worktree_path="/workspace/.worktrees/abc123",
        )
        assert "## Working Directory" in prompt
        assert "/workspace/.worktrees/abc123" in prompt
        assert "git worktree" in prompt.lower()
        assert "do NOT create or switch branches" in prompt

    def test_worktree_path_absent_by_default(self):
        """When worktree_path is not set, prompt should not include Working Directory."""
        prompt = build_prompt(
            "Implement feature",
            Autonomy.FULL,
            "my-repo",
        )
        assert "## Working Directory" not in prompt
        assert "worktree" not in prompt.lower()


class TestBuildPromptAutonomyInstructions:
    """Test that each autonomy level gets the correct instructions."""

    def test_full_autonomy_instructions(self):
        """FULL autonomy should include commit instructions."""
        prompt = build_prompt("Do task", Autonomy.FULL, "repo")
        assert "## Instructions" in prompt
        assert "autonomous agent" in prompt.lower()
        assert "Commit your changes" in prompt

    def test_local_autonomy_instructions(self):
        """LOCAL autonomy should include commit but forbid push/PR."""
        prompt = build_prompt("Do task", Autonomy.LOCAL, "repo")
        assert "## Instructions" in prompt
        assert "LOCAL mode" in prompt
        assert "Do NOT push" in prompt
        assert "Do NOT create a pull request" in prompt
        assert "git push" in prompt
        assert "gh pr create" in prompt

    def test_plan_only_autonomy_instructions(self):
        """PLAN_ONLY autonomy should forbid code changes."""
        prompt = build_prompt("Do task", Autonomy.PLAN_ONLY, "repo")
        assert "## Instructions" in prompt
        assert "PLAN-ONLY" in prompt
        assert "Do NOT make any code changes" in prompt

    def test_research_autonomy_instructions(self):
        """RESEARCH autonomy should forbid code changes."""
        prompt = build_prompt("Do task", Autonomy.RESEARCH_ONLY, "repo")
        assert "## Instructions" in prompt
        assert "RESEARCH mode" in prompt
        assert "Do NOT make any code changes" in prompt
