"""Unit tests for workbench.git_ops — worktree cleanup and utilities."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


@pytest.fixture
def tmp_worktree_base(tmp_path: Path) -> Path:
    """Create a temporary worktree base directory with some stale entries."""
    base = tmp_path / ".worktrees"
    base.mkdir()
    # Simulate stale worktree directories
    (base / "task_abc123").mkdir()
    (base / "task_abc123" / "some_file.py").write_text("stale")
    (base / "task_def456").mkdir()
    (base / "task_def456" / "another_file.py").write_text("stale")
    return base


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo for testing."""
    repo = tmp_path / "test-repo"
    repo.mkdir()

    async def _init():
        proc = await asyncio.create_subprocess_exec(
            "git", "init",
            cwd=str(repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        # Need at least one commit for worktree prune to work
        (repo / "README.md").write_text("test")
        proc = await asyncio.create_subprocess_exec(
            "git", "add", "-A",
            cwd=str(repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", "init",
            cwd=str(repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    asyncio.get_event_loop().run_until_complete(_init())
    return repo


class TestPruneStaleWorktrees:
    """Tests for prune_stale_worktrees()."""

    def test_removes_stale_directories(self, tmp_worktree_base: Path, tmp_git_repo: Path):
        """Stale worktree directories should be removed."""
        from workbench.git_ops import prune_stale_worktrees

        known_repos = {"test-repo": tmp_git_repo}
        removed = asyncio.get_event_loop().run_until_complete(
            prune_stale_worktrees(tmp_worktree_base, known_repos)
        )

        assert removed == 2
        assert not (tmp_worktree_base / "task_abc123").exists()
        assert not (tmp_worktree_base / "task_def456").exists()
        # Base dir itself should still exist
        assert tmp_worktree_base.is_dir()

    def test_no_stale_directories(self, tmp_path: Path, tmp_git_repo: Path):
        """Empty worktree base should return 0 removed."""
        from workbench.git_ops import prune_stale_worktrees

        base = tmp_path / ".worktrees"
        base.mkdir()
        known_repos = {"test-repo": tmp_git_repo}
        removed = asyncio.get_event_loop().run_until_complete(
            prune_stale_worktrees(base, known_repos)
        )

        assert removed == 0

    def test_missing_base_dir(self, tmp_path: Path, tmp_git_repo: Path):
        """Non-existent worktree base should return 0 without error."""
        from workbench.git_ops import prune_stale_worktrees

        base = tmp_path / ".worktrees"  # Does not exist
        known_repos = {"test-repo": tmp_git_repo}
        removed = asyncio.get_event_loop().run_until_complete(
            prune_stale_worktrees(base, known_repos)
        )

        assert removed == 0

    def test_no_known_repos(self, tmp_worktree_base: Path):
        """Should still remove directories even with no known repos."""
        from workbench.git_ops import prune_stale_worktrees

        removed = asyncio.get_event_loop().run_until_complete(
            prune_stale_worktrees(tmp_worktree_base, {})
        )

        assert removed == 2
        assert not (tmp_worktree_base / "task_abc123").exists()
