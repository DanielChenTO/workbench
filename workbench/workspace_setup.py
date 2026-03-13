"""Helpers for installing workbench into arbitrary OpenCode workspaces."""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from pathlib import Path
from shutil import copy2


PLUGIN_DEP = "@opencode-ai/plugin"
PLUGIN_VERSION = "^1.2.15"
TOOL_FILES = [
    "dispatch-task.ts",
    "dispatch-pipeline.ts",
    "dispatch-code-change.ts",
    "dispatch-local-autopilot.ts",
    "create-autopilot-schedule.ts",
    "manage-autopilot-backlog.ts",
    "check-task.ts",
]


@dataclass
class WorkspaceSetupResult:
    tools_dir: Path
    package_json_path: Path
    opencode_json_path: Path
    env_path: Path
    serve_script_path: Path
    mcp_script_path: Path


def install_workspace(
    *,
    workspace_root: Path,
    workbench_repo: Path,
    package_tools_dir: Path,
    workbench_url: str = "http://127.0.0.1:8420",
    enable_mcp: bool = True,
) -> WorkspaceSetupResult:
    workspace_root = workspace_root.resolve()
    workbench_repo = workbench_repo.resolve()
    package_tools_dir = package_tools_dir.resolve()

    tools_dir = workspace_root / ".opencode" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    for name in TOOL_FILES:
        src = package_tools_dir / name
        if src.is_file():
            copy2(src, tools_dir / name)

    package_json_path = ensure_opencode_package_json(workspace_root)
    env_path, serve_script_path, mcp_script_path = write_workbench_scripts(
        workspace_root=workspace_root,
        workbench_repo=workbench_repo,
        workbench_url=workbench_url,
    )
    opencode_json_path = ensure_opencode_json(
        workspace_root=workspace_root,
        mcp_script_path=mcp_script_path,
        enable_mcp=enable_mcp,
    )
    ensure_workspace_scaffolding(workspace_root)

    return WorkspaceSetupResult(
        tools_dir=tools_dir,
        package_json_path=package_json_path,
        opencode_json_path=opencode_json_path,
        env_path=env_path,
        serve_script_path=serve_script_path,
        mcp_script_path=mcp_script_path,
    )


def ensure_opencode_package_json(workspace_root: Path) -> Path:
    pkg_json_path = workspace_root / ".opencode" / "package.json"
    pkg_json_path.parent.mkdir(parents=True, exist_ok=True)

    if pkg_json_path.is_file():
        pkg_data = json.loads(pkg_json_path.read_text(encoding="utf-8"))
    else:
        pkg_data = {"private": True, "dependencies": {}}

    pkg_data.setdefault("private", True)
    deps = pkg_data.setdefault("dependencies", {})
    if PLUGIN_DEP not in deps:
        deps[PLUGIN_DEP] = PLUGIN_VERSION

    pkg_json_path.write_text(json.dumps(pkg_data, indent=2) + "\n", encoding="utf-8")
    return pkg_json_path


def ensure_opencode_json(
    *,
    workspace_root: Path,
    mcp_script_path: Path,
    enable_mcp: bool,
) -> Path:
    path = workspace_root / "opencode.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"$schema": "https://opencode.ai/config.json"}

    mcp = data.setdefault("mcp", {})
    mcp["workbench"] = {
        "type": "local",
        "command": [str(mcp_script_path)],
        "enabled": enable_mcp,
    }

    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def write_workbench_scripts(
    *,
    workspace_root: Path,
    workbench_repo: Path,
    workbench_url: str,
) -> tuple[Path, Path, Path]:
    opencode_dir = workspace_root / ".opencode"
    scripts_dir = opencode_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    env_path = opencode_dir / "workbench.env"
    env_path.write_text(
        "\n".join(
            [
                f'WORKBENCH_REPO="{workbench_repo}"',
                f'WORKBENCH_WORKSPACE_ROOT="{workspace_root}"',
                f'WORKBENCH_URL="{workbench_url}"',
                'WORKBENCH_BIN=".venv/bin/workbench"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    serve_script_path = scripts_dir / "workbench-serve.sh"
    serve_script_path.write_text(
        _script_template(command="serve"),
        encoding="utf-8",
    )
    _make_executable(serve_script_path)

    mcp_script_path = scripts_dir / "workbench-mcp.sh"
    mcp_script_path.write_text(
        _script_template(command="mcp"),
        encoding="utf-8",
    )
    _make_executable(mcp_script_path)

    return env_path, serve_script_path, mcp_script_path


def ensure_workspace_scaffolding(workspace_root: Path) -> None:
    refs_dir = workspace_root / "work-directory" / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)

    index_path = refs_dir / "INDEX.md"
    if not index_path.exists():
        index_path.write_text(
            "# Reference Index\n\nAdd reference documents here for focused context injection.\n",
            encoding="utf-8",
        )

    log_path = workspace_root / "work-directory" / "log.md"
    if not log_path.exists():
        log_path.write_text(
            "# Work Log\n\n## YYYY-MM-DD — Session N: <title>\n\n### Context\n### What was asked\n### Decisions and reasoning\n### What was done\n### Key discoveries\n### What's still open\n### Patterns and automatable tasks\n### Documents referenced\n",
            encoding="utf-8",
        )

    backlog_path = workspace_root / "work-directory" / "backlog.md"
    if not backlog_path.exists():
        backlog_path.write_text(
            "# Local Backlog\n\nUse this file as the persistent queue for local autopilot work.\n\n## Active\n\n- [ ] No items\n\n## Review Queue\n\n- [ ] No items\n\n## Notes\n\n- Local autopilot should only work on items that can be completed with local changes.\n- Failed validation should move the item into `Review Queue` with the failure report.\n- When an item is completed, move it into the session log instead of keeping long history here.\n",
            encoding="utf-8",
        )


def _script_template(*, command: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)
ENV_FILE="${{SCRIPT_DIR}}/../workbench.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if [[ -z "${{WORKBENCH_REPO:-}}" || -z "${{WORKBENCH_WORKSPACE_ROOT:-}}" ]]; then
  echo "workbench.env is missing required configuration" >&2
  exit 1
fi

cd "$WORKBENCH_REPO"
exec "${{WORKBENCH_BIN:-.venv/bin/workbench}}" {command} "$@"
"""


def _make_executable(path: Path) -> None:
    current = path.stat().st_mode
    path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
