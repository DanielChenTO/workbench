"""Tests for portable OpenCode workspace setup helpers."""

from __future__ import annotations

import json
from pathlib import Path

from workbench.workspace_setup import TOOL_FILES, install_workspace


def _make_pkg_tools(tmp_path: Path) -> Path:
    pkg_tools = tmp_path / "opencode-tools"
    pkg_tools.mkdir()
    for name in TOOL_FILES:
        (pkg_tools / name).write_text(f"// {name}\n", encoding="utf-8")
    return pkg_tools


def test_install_workspace_writes_tools_and_scripts(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workbench_repo = tmp_path / "workbench-repo"
    workspace.mkdir()
    workbench_repo.mkdir()
    pkg_tools = _make_pkg_tools(tmp_path)

    result = install_workspace(
        workspace_root=workspace,
        workbench_repo=workbench_repo,
        package_tools_dir=pkg_tools,
    )

    for name in TOOL_FILES:
        assert (result.tools_dir / name).exists()

    assert result.package_json_path.exists()
    package_data = json.loads(result.package_json_path.read_text(encoding="utf-8"))
    assert package_data["dependencies"]["@opencode-ai/plugin"] == "^1.2.15"

    assert result.opencode_json_path.exists()
    config = json.loads(result.opencode_json_path.read_text(encoding="utf-8"))
    assert config["mcp"]["workbench"]["enabled"] is True

    assert result.env_path.exists()
    assert result.serve_script_path.exists()
    assert result.mcp_script_path.exists()
    assert (workspace / "work-directory" / "log.md").exists()
    assert (workspace / "work-directory" / "backlog.md").exists()
    assert (workspace / "work-directory" / "references" / "INDEX.md").exists()


def test_install_workspace_preserves_existing_opencode_json(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workbench_repo = tmp_path / "workbench-repo"
    workspace.mkdir()
    workbench_repo.mkdir()
    pkg_tools = _make_pkg_tools(tmp_path)
    opencode_json = workspace / "opencode.json"
    opencode_json.write_text(
        json.dumps({"instructions": [".opencode/rules/*.md"]}), encoding="utf-8"
    )

    install_workspace(
        workspace_root=workspace,
        workbench_repo=workbench_repo,
        package_tools_dir=pkg_tools,
        enable_mcp=False,
    )

    config = json.loads(opencode_json.read_text(encoding="utf-8"))
    assert config["instructions"] == [".opencode/rules/*.md"]
    assert config["mcp"]["workbench"]["enabled"] is False
