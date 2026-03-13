"""Tests for experimental machine bootstrap planning helpers."""

from __future__ import annotations

from pathlib import Path

from workbench.bootstrap_machine import BootstrapConfig, build_bootstrap_plan


def test_build_bootstrap_plan_with_existing_workbench(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workbench_root = tmp_path / "workbench"
    workspace.mkdir()
    workbench_root.mkdir()
    (workbench_root / "pyproject.toml").write_text(
        "[project]\nname='workbench'\n", encoding="utf-8"
    )

    plan = build_bootstrap_plan(
        BootstrapConfig(
            workspace_root=workspace,
            workbench_root=workbench_root,
            install_opencode_dependencies=False,
        )
    )

    names = [step.name for step in plan]
    assert "clone_workbench" not in names
    assert "create_venv" in names
    assert "install_workbench" in names
    assert "migrate_database" in names
    assert "install_workspace_integration" in names


def test_build_bootstrap_plan_with_fresh_workbench_and_external_db(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workbench_root = tmp_path / "workbench"
    workspace.mkdir()

    plan = build_bootstrap_plan(
        BootstrapConfig(
            workspace_root=workspace,
            workbench_root=workbench_root,
            database_url="postgresql+asyncpg://example",
            use_docker_db=False,
            install_opencode_dependencies=True,
        )
    )

    names = [step.name for step in plan]
    assert names[0] == "clone_workbench"
    assert "start_database" not in names
    assert "install_opencode_dependencies" in names
