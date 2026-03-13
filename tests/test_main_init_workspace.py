"""Tests for the `workbench init-workspace` CLI handler."""

from __future__ import annotations

import json
import subprocess
import sys
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


def test_init_workspace_matches_setup_script_toolset(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    setup_script = repo_root / "scripts" / "setup-opencode-workspace.py"
    assert setup_script.is_file()

    cli_workspace = tmp_path / "cli-script-workspace"
    script_workspace = tmp_path / "script-workspace-2"
    cli_workspace.mkdir()
    script_workspace.mkdir()

    _init_workspace(SimpleNamespace(target=str(cli_workspace)))
    subprocess.run(
        [
            sys.executable,
            str(setup_script),
            str(script_workspace),
            "--workbench-repo",
            str(repo_root),
        ],
        check=True,
    )

    cli_tools = {p.name for p in (cli_workspace / ".opencode" / "tools").iterdir() if p.is_file()}
    script_tools = {
        p.name for p in (script_workspace / ".opencode" / "tools").iterdir() if p.is_file()
    }
    assert cli_tools == script_tools == set(TOOL_FILES)


def test_init_workspace_next_steps_verify_doctor_before_smoke(capsys, tmp_path: Path):
    workspace = tmp_path / "workspace-next-steps"
    workspace.mkdir()

    _init_workspace(SimpleNamespace(target=str(workspace)))
    out = capsys.readouterr().out

    doctor_pos = out.index("3. Verify workbench health: workbench doctor")
    smoke_pos = out.index("4. Verify workspace wiring: workbench smoke-test")
    assert doctor_pos < smoke_pos
