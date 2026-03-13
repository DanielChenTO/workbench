#!/usr/bin/env python3
"""Portable setup helper for installing workbench into any OpenCode workspace."""

from __future__ import annotations

import argparse
from pathlib import Path

from workbench.workspace_setup import TOOL_FILES, install_workspace


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install workbench integration into an arbitrary OpenCode workspace."
    )
    parser.add_argument("workspace", help="Path to the target OpenCode workspace")
    parser.add_argument(
        "--workbench-repo",
        default=str(Path(__file__).resolve().parents[1]),
        help="Path to the workbench repository (defaults to this checkout)",
    )
    parser.add_argument(
        "--workbench-url",
        default="http://127.0.0.1:8420",
        help="Workbench API base URL to record in workspace scripts",
    )
    parser.add_argument(
        "--disable-mcp",
        action="store_true",
        help="Install tools and scripts but do not enable workbench MCP in opencode.json",
    )
    args = parser.parse_args()

    workbench_repo = Path(args.workbench_repo).resolve()
    workspace_root = Path(args.workspace).resolve()
    result = install_workspace(
        workspace_root=workspace_root,
        workbench_repo=workbench_repo,
        package_tools_dir=workbench_repo / "opencode-tools",
        workbench_url=args.workbench_url,
        enable_mcp=not args.disable_mcp,
    )

    print(f"Workspace prepared: {workspace_root}")
    print("Installed tools:")
    for name in TOOL_FILES:
        path = result.tools_dir / name
        if path.exists():
            print(f"  - {path}")
    print(f"Updated OpenCode package: {result.package_json_path}")
    print(f"Updated OpenCode config:   {result.opencode_json_path}")
    print(f"Workbench env file:       {result.env_path}")
    print(f"Serve helper:             {result.serve_script_path}")
    print(f"MCP helper:               {result.mcp_script_path}")
    print("")
    print("Next steps:")
    print("  1. Ensure workbench itself is installed and migrated on this machine")
    print(f"  2. Start workbench: {result.serve_script_path}")
    print(
        f"  3. If needed, install .opencode dependencies: cd {workspace_root / '.opencode'} && npm install"
    )
    print("  4. Open a new OpenCode session in the workspace")
    if not args.disable_mcp:
        print("  5. MCP integration is enabled in opencode.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
