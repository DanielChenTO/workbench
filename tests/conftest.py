"""Shared fixtures for workbench tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace root with reference docs and sample files."""
    # Create work-directory/references/ structure
    refs_dir = tmp_path / "work-directory" / "references"
    refs_dir.mkdir(parents=True)

    # Create a sample reference doc
    (refs_dir / "sample-reference.md").write_text(
        "# Sample Reference\n"
        "\n"
        "Overview text.\n"
        "\n"
        "## Architecture\n"
        "\n"
        "The system has three layers:\n"
        "- API layer\n"
        "- Service layer\n"
        "- Database layer\n"
        "\n"
        "## Implementation\n"
        "\n"
        "Implementation details go here.\n"
        "Line 2 of implementation.\n"
        "Line 3 of implementation.\n",
        encoding="utf-8",
    )

    # Create a sample source file
    src_dir = tmp_path / "my-repo" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "handler.go").write_text(
        "\n".join(f"line {i}: content" for i in range(1, 101)),
        encoding="utf-8",
    )

    # Create a .git directory so it's treated as a repo
    (tmp_path / "my-repo" / ".git").mkdir()

    return tmp_path


@pytest.fixture
def patch_settings(tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    """Patch workbench settings to use the temporary workspace."""
    monkeypatch.setattr(
        "workbench.config.settings.workspace_root", tmp_workspace
    )
    return tmp_workspace
