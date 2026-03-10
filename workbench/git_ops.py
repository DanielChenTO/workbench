"""Git operations — worktrees, branching, pushing, and draft PR creation.

Concurrency safety:
    Tasks use git worktrees for isolation — each task gets its own checkout
    directory sharing the same .git database.  This allows concurrent execution
    without checkout races.

    The per-repo lock (managed by the WorkerPool) is only held briefly for
    merge operations when a pipeline completes.

    Functions that create/remove worktrees are safe to call without the lock —
    git handles concurrent worktree operations internally.

    Functions that mutate the *main* working tree (checkout, merge, branch
    creation) must only be called while holding the per-repo lock.

    Functions that only read refs (diff_branch_vs_default, current_branch,
    default_branch) are safe to call without the lock, though results may be
    stale if another task is mid-operation.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from .config import settings
from .exceptions import GitOperationError

log = logging.getLogger(__name__)

# Backward-compatible alias
GitError = GitOperationError


async def _run(cmd: list[str], cwd: Path) -> str:
    """Run a subprocess and return stdout. Raises GitError on non-zero exit."""
    log.debug("git: %s (cwd=%s)", " ".join(cmd), cwd)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise GitError(
            f"Command {' '.join(cmd)!r} failed (rc={proc.returncode}):\n"
            f"stderr: {stderr.decode(errors='replace').strip()}\n"
            f"stdout: {stdout.decode(errors='replace').strip()}"
        )
    return stdout.decode(errors="replace").strip()


# ---------------------------------------------------------------------------
# Working-tree protection: stash uncommitted changes around checkout ops
# ---------------------------------------------------------------------------

_STASH_MSG_PREFIX = "workbench-auto-stash"


async def _stash_if_dirty(repo_path: Path) -> bool:
    """Stash uncommitted changes if the working tree is dirty.

    Returns True if a stash was created, False if tree was clean.
    """
    status = await _run(["git", "status", "--porcelain"], repo_path)
    if not status.strip():
        return False

    msg = f"{_STASH_MSG_PREFIX}: protect uncommitted changes"
    await _run(["git", "stash", "push", "-m", msg, "--include-untracked"], repo_path)
    log.warning("Stashed uncommitted changes in %s", repo_path)
    return True


async def _unstash(repo_path: Path) -> None:
    """Pop the most recent stash if it was created by us.

    Silently skips if the top stash wasn't ours (safety guard against
    popping user stashes).
    """
    try:
        top_msg = await _run(["git", "stash", "list", "-1", "--format=%s"], repo_path)
    except GitError:
        return
    if not top_msg.startswith(_STASH_MSG_PREFIX):
        log.debug("Top stash not ours (%r), not popping", top_msg[:60])
        return

    try:
        await _run(["git", "stash", "pop"], repo_path)
        log.info("Restored stashed changes in %s", repo_path)
    except GitError as e:
        # Stash pop can fail if there are conflicts — log but don't crash
        log.error("git stash pop failed in %s: %s", repo_path, e)


@asynccontextmanager
async def _protect_working_tree(repo_path: Path):
    """Context manager: stash uncommitted changes, yield, then restore.

    Use this around any sequence that does git checkout on the working tree.
    """
    stashed = await _stash_if_dirty(repo_path)
    try:
        yield stashed
    finally:
        if stashed:
            await _unstash(repo_path)


# ---------------------------------------------------------------------------
# Worktree operations: isolated checkouts for concurrent agent tasks
# ---------------------------------------------------------------------------


async def create_worktree(
    repo_path: Path,
    worktree_path: Path,
    branch_name: str,
    base: str | None = None,
) -> None:
    """Create a git worktree with a new branch for an agent task.

    The worktree is an independent checkout sharing the same .git database.
    Multiple worktrees can exist concurrently (one per task), enabling true
    parallel execution without checkout races.

    Parameters
    ----------
    repo_path:
        Path to the main repository (where .git lives).
    worktree_path:
        Absolute path for the new worktree directory.
    branch_name:
        New branch to create in the worktree (e.g. ``agent/abc123``).
    base:
        Base ref to branch from. Defaults to the repo's default branch.
    """
    if base is None:
        base = await default_branch(repo_path)

    # Ensure parent directory exists
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    # Fetch latest so the base ref is up to date
    await fetch_latest(repo_path)

    # Create worktree with a new branch from base
    await _run(
        ["git", "worktree", "add", str(worktree_path), "-b", branch_name, base],
        repo_path,
    )
    log.info(
        "Created worktree %s (branch=%s, base=%s) for repo %s",
        worktree_path, branch_name, base, repo_path,
    )


async def remove_worktree(repo_path: Path, worktree_path: Path) -> None:
    """Remove a git worktree and prune stale entries.

    Safe to call even if the worktree directory was already deleted — the
    ``git worktree prune`` call cleans up orphaned metadata.
    """
    try:
        await _run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            repo_path,
        )
        log.info("Removed worktree %s", worktree_path)
    except GitError as e:
        log.warning("git worktree remove failed (non-fatal): %s", e)
        # Fallback: prune stale worktree entries
        try:
            await _run(["git", "worktree", "prune"], repo_path)
            log.info("Pruned stale worktree entries for %s", repo_path)
        except GitError:
            pass


async def prune_stale_worktrees(worktree_base: Path, known_repos: dict[str, Path]) -> int:
    """Remove leftover worktree directories and prune stale git metadata.

    Called at startup to clean up worktrees from a previous crash or ungraceful
    shutdown.  Iterates over any directories remaining in ``worktree_base``,
    removes them, then runs ``git worktree prune`` on every known repo so git's
    internal worktree bookkeeping is consistent.

    Returns the number of stale worktree directories removed.
    """
    removed = 0

    if worktree_base.is_dir():
        for entry in worktree_base.iterdir():
            if entry.is_dir():
                log.warning("Removing stale worktree directory: %s", entry)
                try:
                    import shutil
                    shutil.rmtree(entry)
                    removed += 1
                except OSError as e:
                    log.error("Failed to remove stale worktree %s: %s", entry, e)

    # Prune git worktree metadata for all known repos
    for repo_name, repo_path in known_repos.items():
        try:
            await _run(["git", "worktree", "prune"], repo_path)
            if removed:
                log.info("Pruned worktree metadata for %s", repo_name)
        except GitError as e:
            log.warning("git worktree prune failed for %s (non-fatal): %s", repo_name, e)

    if removed:
        log.info("Cleaned up %d stale worktree(s) at startup", removed)
    else:
        log.debug("No stale worktrees found at startup")

    return removed


async def current_branch(repo_path: Path) -> str:
    """Return the name of the current branch."""
    return await _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_path)


async def default_branch(repo_path: Path) -> str:
    """Determine the default branch (main or master)."""
    try:
        await _run(["git", "rev-parse", "--verify", "main"], repo_path)
        return "main"
    except GitError:
        try:
            await _run(["git", "rev-parse", "--verify", "master"], repo_path)
            return "master"
        except GitError:
            return settings.default_base_branch


async def fetch_latest(repo_path: Path) -> None:
    """Fetch latest from origin."""
    try:
        await _run(["git", "fetch", "origin"], repo_path)
    except GitError as e:
        log.warning("git fetch failed (non-fatal): %s", e)


async def create_branch(repo_path: Path, branch_name: str, base: str | None = None) -> None:
    """Create and check out a new branch from the given base (or default branch).

    Automatically stashes any uncommitted changes before checkout and restores
    them afterward (on the new branch), so dirty working trees are never lost.
    """
    if base is None:
        base = await default_branch(repo_path)

    await fetch_latest(repo_path)

    async with _protect_working_tree(repo_path):
        # Make sure we start from latest origin/<base>
        try:
            await _run(["git", "checkout", base], repo_path)
            await _run(["git", "pull", "--ff-only", "origin", base], repo_path)
        except GitError as e:
            log.warning("Could not fast-forward %s (non-fatal): %s", base, e)

        await _run(["git", "checkout", "-b", branch_name], repo_path)
    log.info("Created branch %s from %s in %s", branch_name, base, repo_path)


async def has_changes(repo_path: Path) -> bool:
    """Check if there are uncommitted changes (staged or unstaged)."""
    status = await _run(["git", "status", "--porcelain"], repo_path)
    return len(status.strip()) > 0


async def diff_branch_vs_default(repo_path: Path, branch_name: str, *, stat_only: bool = False) -> str:
    """Get the diff of a branch against the default branch.

    Returns the full unified diff (or --stat summary if stat_only=True).
    Useful for generating review prompts from implementation branches.
    """
    base = await default_branch(repo_path)
    cmd = ["git", "diff", f"{base}...{branch_name}"]
    if stat_only:
        cmd.append("--stat")
    return await _run(cmd, repo_path)


async def add_and_commit(repo_path: Path, message: str) -> bool:
    """Stage all changes and commit. Returns True if a commit was made."""
    if not await has_changes(repo_path):
        log.info("No changes to commit in %s", repo_path)
        return False
    await _run(["git", "add", "-A"], repo_path)
    await _run(["git", "commit", "-m", message], repo_path)
    log.info("Committed: %s", message)
    return True


async def push_branch(repo_path: Path, branch_name: str) -> None:
    """Push a branch to origin."""
    await _run(["git", "push", "-u", "origin", branch_name], repo_path)
    log.info("Pushed %s to origin", branch_name)


async def create_draft_pr(
    repo_path: Path,
    branch_name: str,
    title: str,
    body: str,
    base: str | None = None,
) -> str:
    """Create a draft pull request using the gh CLI. Returns the PR URL."""
    if base is None:
        base = await default_branch(repo_path)

    cmd = [
        "gh", "pr", "create",
        "--draft",
        "--title", title,
        "--body", body,
        "--base", base,
        "--head", branch_name,
    ]
    pr_url = await _run(cmd, repo_path)
    log.info("Created draft PR: %s", pr_url)
    return pr_url


async def cleanup_branch(repo_path: Path, branch_name: str) -> None:
    """Switch back to the default branch and delete the working branch locally.

    Stashes uncommitted changes before checkout and restores afterward.
    """
    base = await default_branch(repo_path)
    try:
        async with _protect_working_tree(repo_path):
            await _run(["git", "checkout", base], repo_path)
        await _run(["git", "branch", "-D", branch_name], repo_path)
    except GitError as e:
        log.warning("Branch cleanup failed (non-fatal): %s", e)


async def merge_branch(repo_path: Path, branch_name: str, *, delete_after: bool = True) -> None:
    """Merge a branch into the default branch.

    Stashes any uncommitted changes before checkout and restores them afterward.
    Uses --no-ff to preserve the branch history in a merge commit.

    On merge conflict: aborts the merge to leave the repo in a clean state,
    then raises GitError with details about the conflict.
    """
    base = await default_branch(repo_path)

    async with _protect_working_tree(repo_path):
        current = await current_branch(repo_path)
        if current != base:
            await _run(["git", "checkout", base], repo_path)

        try:
            await _run(["git", "merge", "--no-ff", branch_name, "-m",
                         f"Merge branch '{branch_name}' into {base}"], repo_path)
        except GitError as merge_err:
            # Abort the merge to leave repo clean
            log.error("Merge conflict merging %s into %s: %s", branch_name, base, merge_err)
            try:
                await _run(["git", "merge", "--abort"], repo_path)
            except GitError:
                log.warning("git merge --abort also failed; repo may be in dirty state")
            raise

    log.info("Merged %s into %s in %s", branch_name, base, repo_path)

    if delete_after:
        try:
            await _run(["git", "branch", "-d", branch_name], repo_path)
            log.info("Deleted branch %s after merge", branch_name)
        except GitError as e:
            log.warning("Could not delete branch %s after merge: %s", branch_name, e)


async def switch_back_to_default(repo_path: Path) -> None:
    """Switch back to the default branch without deleting anything.

    Stashes uncommitted changes before checkout and restores afterward.
    """
    base = await default_branch(repo_path)
    try:
        async with _protect_working_tree(repo_path):
            await _run(["git", "checkout", base], repo_path)
    except GitError as e:
        log.warning("Could not switch to %s: %s", base, e)
