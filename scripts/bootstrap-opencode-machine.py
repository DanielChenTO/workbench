#!/usr/bin/env python3
"""One-shot bootstrap for preparing workbench on a machine and wiring a workspace."""

from __future__ import annotations

import argparse
from pathlib import Path

from workbench.bootstrap_machine import (
    BootstrapConfig,
    build_bootstrap_plan,
    execute_plan,
    missing_commands,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap workbench on this machine and wire it into an OpenCode workspace."
    )
    parser.add_argument("workspace", help="Path to the target OpenCode workspace")
    parser.add_argument(
        "--workbench-root",
        default=str(Path.home() / "workbench"),
        help="Directory where workbench should live or already exists",
    )
    parser.add_argument(
        "--workbench-repo-url",
        default="https://github.com/DanielChenTO/workbench.git",
        help="Git URL to clone workbench from if not already present",
    )
    parser.add_argument(
        "--workbench-url",
        default="http://127.0.0.1:8420",
        help="Workbench API URL to record in workspace scripts",
    )
    parser.add_argument(
        "--package-manager",
        default="npm",
        choices=["npm", "pnpm", "bun"],
        help="Package manager to use for .opencode dependency install",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Use an external Postgres URL instead of docker compose",
    )
    parser.add_argument(
        "--skip-opencode-install",
        action="store_true",
        help="Do not run package-manager install inside workspace .opencode",
    )
    parser.add_argument(
        "--disable-mcp",
        action="store_true",
        help="Do not enable workbench MCP in opencode.json",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Print the bootstrap plan without executing it",
    )
    args = parser.parse_args()

    config = BootstrapConfig(
        workspace_root=Path(args.workspace),
        workbench_root=Path(args.workbench_root),
        workbench_repo_url=args.workbench_repo_url,
        workbench_url=args.workbench_url,
        package_manager=args.package_manager,
        install_opencode_dependencies=not args.skip_opencode_install,
        use_docker_db=args.database_url is None,
        database_url=args.database_url,
        enable_mcp=not args.disable_mcp,
    )

    missing = missing_commands(config)
    if missing:
        print("Missing required commands:")
        for cmd in missing:
            print(f"  - {cmd}")
        print("Install the missing prerequisites and re-run bootstrap.")
        return 1

    steps = build_bootstrap_plan(config)
    print("Bootstrap plan:")
    for idx, step in enumerate(steps, start=1):
        rendered = " ".join(step.command)
        print(f"  {idx}. {step.name}: (cd {step.cwd} && {rendered})")

    if args.plan_only:
        return 0

    execute_plan(steps)
    print("")
    print("Bootstrap completed.")
    print(
        f"Start workbench with: {config.workspace_root / '.opencode' / 'scripts' / 'workbench-serve.sh'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
