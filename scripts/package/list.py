"""``package list`` — list all Python namespace packages in the workspace."""

from __future__ import annotations

from typing import Annotated

import typer

from scripts._helpers import console
from scripts._helpers.ns import build_ns_tree
from scripts._helpers.ns import name_to_ns
from scripts._helpers.ns import print_ns_tree
from scripts._helpers.workspace import PYTHON_DIR
from scripts._helpers.workspace import get_existing_packages
from scripts._helpers.workspace import get_workspace_members


def list_packages(
    tree: Annotated[
        bool,
        typer.Option("-t", "--tree", help="Display as namespace tree"),
    ] = False,
) -> None:
    """List all Python packages in the workspace."""
    packages = get_existing_packages()
    workspace_members = get_workspace_members()

    console.header(f"Python Packages ({len(packages)})")

    if not packages:
        console.info("No packages found in python/")
        return

    if tree:
        print()
        console.info("Namespace tree:")
        print()
        print_ns_tree(build_ns_tree(packages))
        print()
    else:
        print()
        name_w = max(len(p) for p in packages) + 2
        for package_name in packages:
            ns = name_to_ns(package_name)
            in_ws = "✓" if package_name in workspace_members else " "
            ws_marker = console.format_dim(f"[{in_ws}]")
            padding = " " * (name_w - len(package_name))
            print(f"  {ws_marker} {console.format_cyan(package_name)}{padding}  {console.format_dim(ns)}")
        print()
        console.info(f"{console.format_dim('[✓]')} = in workspace   Location: {console.format_path(PYTHON_DIR)}")

    print()
