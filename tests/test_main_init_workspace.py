"""Tests for the `workbench init-workspace` CLI handler."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from workbench.main import _init_workspace
from workbench.workspace_setup import TOOL_FILES, install_workspace


def test_init_workspace_installs_canonical_tools(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    _init_workspace(SimpleNamespace(target=str(workspace)))

    tools_dir = workspace / ".opencode" / "tools"
    installed = sorted(p.name for p in tools_dir.glob("*.ts"))
    assert installed == sorted(TOOL_FILES)

    package_data = json.loads(
        (workspace / ".opencode" / "package.json").read_text(encoding="utf-8")
    )
    assert package_data["dependencies"]["@opencode-ai/plugin"] == "^1.2.15"


def test_init_workspace_tool_set_matches_install_workspace(tmp_path: Path):
    cli_workspace = tmp_path / "cli-workspace"
    script_workspace = tmp_path / "script-workspace"
    workbench_repo = Path(__file__).resolve().parents[1]
    package_tools_dir = workbench_repo / "opencode-tools"

    cli_workspace.mkdir()
    script_workspace.mkdir()

    _init_workspace(SimpleNamespace(target=str(cli_workspace)))

    install_workspace(
        workspace_root=script_workspace,
        workbench_repo=workbench_repo,
        package_tools_dir=package_tools_dir,
    )

    cli_installed = sorted(p.name for p in (cli_workspace / ".opencode" / "tools").glob("*.ts"))
    script_installed = sorted(
        p.name for p in (script_workspace / ".opencode" / "tools").glob("*.ts")
    )
    assert cli_installed == script_installed == sorted(TOOL_FILES)
