"""Unit tests for workbench.config — Settings and helper functions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from workbench.config import Settings, _detect_workspace_root

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_repos(root: Path, names: list[str]) -> None:
    """Create fake repos (directories with .git subdirs) under *root*."""
    for name in names:
        repo = root / name
        repo.mkdir(parents=True, exist_ok=True)
        (repo / ".git").mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# known_repos
# ---------------------------------------------------------------------------


class TestKnownRepos:
    def test_returns_correct_dict(self, tmp_path: Path) -> None:
        """known_repos should discover directories containing .git."""
        _make_fake_repos(tmp_path, ["alpha", "bravo", "charlie"])
        # Also create a non-repo directory (no .git)
        (tmp_path / "not-a-repo").mkdir()
        # And a plain file
        (tmp_path / "somefile.txt").write_text("hi")

        s = Settings(workspace_root=tmp_path)
        repos = s.known_repos

        assert set(repos.keys()) == {"alpha", "bravo", "charlie"}
        for name in ("alpha", "bravo", "charlie"):
            assert repos[name] == tmp_path / name

    def test_empty_workspace(self, tmp_path: Path) -> None:
        """known_repos should return an empty dict when no repos exist."""
        s = Settings(workspace_root=tmp_path)
        assert s.known_repos == {}

    def test_nonexistent_workspace(self, tmp_path: Path) -> None:
        """known_repos should return empty dict for a missing workspace root."""
        s = Settings(workspace_root=tmp_path / "does-not-exist")
        assert s.known_repos == {}

    def test_cache_is_used(self, tmp_path: Path) -> None:
        """After first access, known_repos should return cached result."""
        _make_fake_repos(tmp_path, ["repo-a"])
        s = Settings(workspace_root=tmp_path)

        first = s.known_repos
        assert "repo-a" in first

        # Add another repo on disk — cache should prevent it from appearing.
        _make_fake_repos(tmp_path, ["repo-b"])
        second = s.known_repos
        assert second is first  # exact same object
        assert "repo-b" not in second


# ---------------------------------------------------------------------------
# resolve_repo_path
# ---------------------------------------------------------------------------


class TestResolveRepoPath:
    def test_exact_match(self, tmp_path: Path) -> None:
        _make_fake_repos(tmp_path, ["my-service"])
        s = Settings(workspace_root=tmp_path)
        assert s.resolve_repo_path("my-service") == tmp_path / "my-service"

    def test_partial_match(self, tmp_path: Path) -> None:
        _make_fake_repos(tmp_path, ["my-service"])
        s = Settings(workspace_root=tmp_path)
        # "service" is a substring of "my-service" and unique
        assert s.resolve_repo_path("service") == tmp_path / "my-service"

    def test_ambiguous_partial_match_returns_none(self, tmp_path: Path) -> None:
        _make_fake_repos(tmp_path, ["my-service", "my-service-helm"])
        s = Settings(workspace_root=tmp_path)
        # "service" matches both repos — ambiguous
        assert s.resolve_repo_path("service") is None

    def test_no_match(self, tmp_path: Path) -> None:
        _make_fake_repos(tmp_path, ["my-service"])
        s = Settings(workspace_root=tmp_path)
        assert s.resolve_repo_path("nonexistent") is None

    def test_none_input(self, tmp_path: Path) -> None:
        s = Settings(workspace_root=tmp_path)
        assert s.resolve_repo_path(None) is None

    def test_refreshes_cache_on_miss(self, tmp_path: Path) -> None:
        """resolve_repo_path should re-scan once when cached repos miss."""
        _make_fake_repos(tmp_path, ["alpha"])
        s = Settings(workspace_root=tmp_path)

        # Prime cache with only alpha
        assert s.resolve_repo_path("alpha") == tmp_path / "alpha"

        # Add repo after cache creation; resolution should refresh and find it.
        _make_fake_repos(tmp_path, ["workbench"])
        assert s.resolve_repo_path("workbench") == tmp_path / "workbench"

    def test_empty_string_input(self, tmp_path: Path) -> None:
        s = Settings(workspace_root=tmp_path)
        assert s.resolve_repo_path("") is None


# ---------------------------------------------------------------------------
# worktree_base_dir
# ---------------------------------------------------------------------------


class TestWorktreeBaseDir:
    def test_returns_expected_path(self, tmp_path: Path) -> None:
        s = Settings(workspace_root=tmp_path)
        assert s.worktree_base_dir == tmp_path / ".worktrees"

    def test_is_child_of_workspace_root(self, tmp_path: Path) -> None:
        s = Settings(workspace_root=tmp_path)
        assert s.worktree_base_dir.parent == tmp_path


# ---------------------------------------------------------------------------
# resolved_references_dir
# ---------------------------------------------------------------------------


class TestResolvedReferencesDir:
    def test_default_fallback(self, tmp_path: Path) -> None:
        """Without explicit references_dir, should fall back to workspace default."""
        s = Settings(workspace_root=tmp_path)
        assert s.resolved_references_dir == tmp_path / "work-directory" / "references"

    def test_explicit_references_dir(self, tmp_path: Path) -> None:
        """When references_dir is set, resolved_references_dir should use it."""
        custom = tmp_path / "custom-refs"
        s = Settings(workspace_root=tmp_path, references_dir=custom)
        assert s.resolved_references_dir == custom


# ---------------------------------------------------------------------------
# _detect_workspace_root
# ---------------------------------------------------------------------------


class TestDetectWorkspaceRoot:
    def test_returns_path_when_git_repos_found(self, tmp_path: Path) -> None:
        """_detect_workspace_root should return the candidate when it has git repos."""
        # Simulate: __file__ is at <candidate>/workbench/workbench/config.py
        # so parent.parent.parent = candidate
        fake_config = tmp_path / "workbench" / "workbench" / "config.py"
        fake_config.parent.mkdir(parents=True)
        fake_config.write_text("")

        # Put a git repo in the candidate directory
        _make_fake_repos(tmp_path, ["some-repo"])

        from workbench import config as config_mod

        original = config_mod.__file__
        try:
            config_mod.__file__ = str(fake_config)
            result = _detect_workspace_root()
            assert result == tmp_path
        finally:
            config_mod.__file__ = original

    def test_falls_back_to_cwd_when_no_repos(self, tmp_path: Path) -> None:
        """_detect_workspace_root should fallback to cwd when no repos found."""
        # candidate has no git repos
        fake_config = tmp_path / "workbench" / "workbench" / "config.py"
        fake_config.parent.mkdir(parents=True)
        fake_config.write_text("")

        from workbench import config as config_mod

        original = config_mod.__file__
        try:
            config_mod.__file__ = str(fake_config)
            with patch.object(Path, "cwd", return_value=tmp_path / "fallback"):
                result = _detect_workspace_root()
                assert result == tmp_path / "fallback"
        finally:
            config_mod.__file__ = original
