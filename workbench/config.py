"""Configuration for workbench."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


def _detect_workspace_root() -> Path:
    """Auto-detect workspace root.

    Walks up from this file looking for a directory that contains at least one
    git repo.  If nothing is found, tries the current working directory.
    Falls back to a safe default if neither works.
    """
    # Strategy 1: walk up from source tree (workbench/ is typically a child of the workspace)
    candidate = Path(__file__).resolve().parent.parent.parent
    if candidate.is_dir() and any(
        (child / ".git").exists() for child in candidate.iterdir() if child.is_dir()
    ):
        return candidate

    # Strategy 2: check current working directory
    cwd = Path.cwd()
    if (
        cwd != candidate
        and cwd.is_dir()
        and any((child / ".git").exists() for child in cwd.iterdir() if child.is_dir())
    ):
        logger.info(
            "Auto-detected workspace root from cwd: %s",
            cwd,
        )
        return cwd

    # Nothing found — warn loudly and return cwd as last resort
    logger.warning(
        "Could not auto-detect workspace root from source tree (%s) or cwd (%s). "
        "Set WORKBENCH_WORKSPACE_ROOT explicitly.",
        candidate,
        cwd,
    )
    return cwd


class Settings(BaseSettings):
    """Service configuration, loaded from environment variables with WORKBENCH_ prefix."""

    model_config = {"env_prefix": "WORKBENCH_"}

    # Private cache for known_repos (not a pydantic field).
    _known_repos_cache: dict[str, Path] | None = None

    # --- Workspace ---
    workspace_root: Path = _detect_workspace_root()
    # Absolute path to the workspace root containing git repos.

    references_dir: Path | None = None
    # Directory containing reference docs for the 'reference' context type.
    # Defaults to <workspace_root>/work-directory/references if not set.

    # --- OpenCode ---
    opencode_bin: str = "opencode"
    # Path to the opencode binary.

    opencode_model: str | None = None
    # Override the default model for opencode run. None = use opencode's default.

    # --- Workers ---
    max_workers: int = 4
    # Maximum number of concurrent task workers.

    task_timeout: int = 1800
    # Per-task timeout in seconds (default 30 minutes).

    orchestrator_timeout: int = 7200
    # Default timeout for orchestrator tasks in seconds (default 2 hours).
    # Orchestrators coordinate long-running multi-task workflows and need
    # significantly more time than individual worker tasks.

    log_buffer_maxsize: int = 1000
    # Maximum size for per-task log subscriber queues (SSE streaming).
    # When a queue is full, new messages drop the oldest entry so a slow
    # consumer can never cause unbounded memory growth.

    # --- Git ---
    branch_prefix: str = "agent"
    # Prefix for auto-created branches, e.g. agent/task-abc123.

    default_base_branch: str = "main"
    # Default base branch for PRs.

    # --- API ---
    host: str = "127.0.0.1"
    port: int = 8420

    # --- Database ---
    database_url: str = "postgresql+asyncpg://workbench:workbench_dev@localhost:5433/workbench"
    # Async SQLAlchemy connection string. Matches docker-compose.yml defaults.

    # --- External services ---
    jira_base_url: str = ""
    jira_api_token: str | None = None
    jira_user_email: str | None = None

    github_token: str | None = None
    # Used for GitHub issue resolution. Falls back to `gh` CLI auth.

    @property
    def worktree_base_dir(self) -> Path:
        """Base directory for git worktrees, one per task.

        Defaults to <workspace_root>/.worktrees.  Each task gets a
        sub-directory named by its task ID, e.g. .worktrees/abc123def456/.
        """
        return self.workspace_root / ".worktrees"

    @property
    def resolved_references_dir(self) -> Path:
        """Return the references directory, falling back to workspace default."""
        if self.references_dir is not None:
            return self.references_dir
        return self.workspace_root / "work-directory" / "references"

    def refresh_known_repos(self) -> dict[str, Path]:
        """Re-scan workspace_root and refresh the known repository cache."""
        repos: dict[str, Path] = {}
        if not self.workspace_root.is_dir():
            self._known_repos_cache = repos
            return repos
        for child in sorted(self.workspace_root.iterdir()):
            if child.is_dir() and (child / ".git").exists():
                repos[child.name] = child
        self._known_repos_cache = repos
        return repos

    @property
    def known_repos(self) -> dict[str, Path]:
        """Map of repo short names to absolute paths, discovered under workspace_root.

        The first call populates a cache. Call ``refresh_known_repos`` when you
        need to re-scan the filesystem (for example after workspace changes).
        """
        if self._known_repos_cache is not None:
            return self._known_repos_cache
        return self.refresh_known_repos()

    def resolve_repo_path(self, repo_name: str | None) -> Path | None:
        """Resolve a repo short name to an absolute path.

        Performs exact then unique-partial matching. If the cached repo list
        misses, re-scans once before returning None so runtime execution does not
        fail because of stale startup cache.
        """
        if repo_name is None:
            return None

        query = repo_name.strip()
        if not query:
            return None

        def _lookup(name: str, repos: dict[str, Path]) -> Path | None:
            if name in repos:
                return repos[name]
            matches = [repo for repo in repos if name in repo]
            if len(matches) == 1:
                return repos[matches[0]]
            return None

        repos = self.known_repos
        resolved = _lookup(query, repos)
        if resolved is not None:
            return resolved

        # Runtime safety: one cache refresh on miss, then retry.
        repos = self.refresh_known_repos()
        return _lookup(query, repos)


# Singleton — import this instance everywhere.
settings = Settings()
