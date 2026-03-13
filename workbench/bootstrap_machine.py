"""Bootstrap helpers for preparing workbench on a new machine or workspace."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
import os
from pathlib import Path


DEFAULT_WORKBENCH_REPO_URL = "https://github.com/DanielChenTO/workbench.git"


@dataclass
class BootstrapConfig:
    workspace_root: Path
    workbench_root: Path
    workbench_repo_url: str = DEFAULT_WORKBENCH_REPO_URL
    workbench_url: str = "http://127.0.0.1:8420"
    package_manager: str = "npm"
    install_opencode_dependencies: bool = True
    use_docker_db: bool = True
    database_url: str | None = None
    enable_mcp: bool = True


@dataclass
class BootstrapStep:
    name: str
    command: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)


def required_commands(config: BootstrapConfig) -> list[str]:
    commands = ["git", "python3", "opencode"]
    if config.use_docker_db:
        commands.append("docker")
    if config.install_opencode_dependencies:
        commands.append(config.package_manager)
    return commands


def missing_commands(config: BootstrapConfig) -> list[str]:
    return [cmd for cmd in required_commands(config) if shutil.which(cmd) is None]


def build_bootstrap_plan(config: BootstrapConfig) -> list[BootstrapStep]:
    workspace_root = config.workspace_root.resolve()
    workbench_root = config.workbench_root.resolve()
    workspace_opencode = workspace_root / ".opencode"
    workspace_setup_script = workbench_root / "scripts" / "setup-opencode-workspace.py"
    venv_python = workbench_root / ".venv" / "bin" / "python"
    venv_pip = workbench_root / ".venv" / "bin" / "pip"
    venv_alembic = workbench_root / ".venv" / "bin" / "alembic"

    steps: list[BootstrapStep] = []
    if not (workbench_root / "pyproject.toml").exists():
        steps.append(
            BootstrapStep(
                name="clone_workbench",
                command=["git", "clone", config.workbench_repo_url, str(workbench_root)],
                cwd=workbench_root.parent,
            )
        )

    steps.extend(
        [
            BootstrapStep(
                name="create_venv",
                command=["python3", "-m", "venv", ".venv"],
                cwd=workbench_root,
            ),
            BootstrapStep(
                name="install_workbench",
                command=[str(venv_pip), "install", "-e", ".[dev]"],
                cwd=workbench_root,
            ),
        ]
    )

    if config.use_docker_db:
        steps.append(
            BootstrapStep(
                name="start_database",
                command=["docker", "compose", "up", "-d"],
                cwd=workbench_root,
            )
        )

    migrate_env: dict[str, str] = {}
    if config.database_url:
        migrate_env["WORKBENCH_DATABASE_URL"] = config.database_url
    steps.append(
        BootstrapStep(
            name="migrate_database",
            command=[str(venv_alembic), "upgrade", "head"],
            cwd=workbench_root,
            env=migrate_env,
        )
    )

    setup_command = [
        str(venv_python),
        str(workspace_setup_script),
        str(workspace_root),
        "--workbench-repo",
        str(workbench_root),
        "--workbench-url",
        config.workbench_url,
    ]
    if not config.enable_mcp:
        setup_command.append("--disable-mcp")

    steps.append(
        BootstrapStep(
            name="install_workspace_integration",
            command=setup_command,
            cwd=workbench_root,
        )
    )

    if config.install_opencode_dependencies:
        steps.append(
            BootstrapStep(
                name="install_opencode_dependencies",
                command=[config.package_manager, "install"],
                cwd=workspace_opencode,
            )
        )

    return steps


def execute_plan(steps: list[BootstrapStep]) -> None:
    for step in steps:
        subprocess.run(
            step.command,
            cwd=step.cwd,
            check=True,
            env={**os.environ, **step.env} if step.env else None,
        )
