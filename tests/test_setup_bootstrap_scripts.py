"""Tests for setup/bootstrap script guidance text."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_script_module(script_name: str) -> ModuleType:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(script_name.replace("-", "_"), script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load script module: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_next_steps_start_before_smoke_test(tmp_path: Path):
    module = _load_script_module("setup-opencode-workspace.py")

    workspace = tmp_path / "workspace"
    serve_script = workspace / ".opencode" / "scripts" / "workbench-serve.sh"
    steps = module._next_steps(
        workspace_root=workspace,
        serve_script_path=serve_script,
        mcp_enabled=True,
    )

    assert steps.index(f"Start workbench: {serve_script}") < steps.index(
        "Verify workspace wiring: workbench smoke-test"
    )
    assert steps.index("Verify workbench health: workbench doctor") < steps.index(
        "Verify workspace wiring: workbench smoke-test"
    )


def test_bootstrap_summary_includes_sections_and_command_order(capsys, tmp_path: Path):
    module = _load_script_module("bootstrap-opencode-machine.py")

    config = module.BootstrapConfig(
        workspace_root=tmp_path / "workspace",
        workbench_root=tmp_path / "workbench",
        install_opencode_dependencies=False,
    )

    module._print_bootstrap_summary(config)
    out = capsys.readouterr().out

    assert "Auto-wired during bootstrap:" in out
    assert "Manual prerequisites/steps remaining:" in out
    assert "Next commands:" in out

    start_pos = out.index("1. Start workbench")
    doctor_pos = out.index("2. Verify health: workbench doctor")
    smoke_pos = out.index("3. Verify setup: workbench smoke-test")
    assert start_pos < doctor_pos < smoke_pos
