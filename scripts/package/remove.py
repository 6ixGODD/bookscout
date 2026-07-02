"""``package remove`` — remove a Python namespace package from the workspace."""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Annotated

import typer

from scripts._helpers import console
from scripts._helpers.workspace import PYTHON_DIR
from scripts._helpers.workspace import ROOT
from scripts._helpers.workspace import get_existing_packages
from scripts._helpers.workspace import get_package_dependents
from scripts._helpers.workspace import is_root_dependent
from scripts._helpers.workspace import remove_from_workspace
from scripts._helpers.workspace import remove_root_dependency

_PREFIX = "bookscout-"


def _resolve_name(raw: str | None) -> str | None:
    """Resolve a package name, auto-prepending ``bookscout-`` if needed."""
    if raw is None:
        return None
    name = raw.strip()
    if not name:
        return None
    if name.startswith(_PREFIX):
        return name
    return _PREFIX + name


def remove(
    name: Annotated[
        str | None,
        typer.Argument(help="Package name (suffix only — 'bookscout-' prefix is automatic)"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("-f", "--force", help="Skip confirmation prompt"),
    ] = False,
) -> None:
    """Remove a Python namespace package from the workspace.

    Examples::

        bs package rm bar                  # removes bookscout-bar
        bs package rm bookscout-bar -f     # full name, skip confirmation
        bs package rm                      # interactive with prefix
    """
    pkg_name = _resolve_name(name)
    if not pkg_name:
        existing = get_existing_packages()
        if not existing:
            console.info("No packages in python/")
            sys.exit(0)
        console.info("Existing packages:")
        console.list_items(sorted(existing))
        print()
        raw = console.prompt_input(
            "Package name (e.g. bookscout-foo-bar)",
            prefix=_PREFIX,
        )
        pkg_name = _resolve_name(raw)
    if not pkg_name:
        console.error("Package name is required.")
        sys.exit(1)

    package_dir = PYTHON_DIR / pkg_name
    if not package_dir.exists():
        console.error(f"Package '{pkg_name}' not found in python/.")
        sys.exit(1)

    # ── Dependency check ──────────────────────────────────────────────
    dependents = get_package_dependents(pkg_name)
    if dependents:
        console.error(f"Cannot remove '{pkg_name}' — the following packages still depend on it:")
        console.list_items(dependents)
        console.info("Remove those dependencies first, then retry.")
        sys.exit(1)

    root_dep = is_root_dependent(pkg_name)
    if root_dep:
        console.warning(f"Workspace root depends on '{pkg_name}' — will be removed automatically.")

    # ── Confirmation ──────────────────────────────────────────────────
    if not force:
        print()
        console.warning(f"This will permanently delete  python/{pkg_name}/")
        if not console.confirm(f"Remove '{pkg_name}'?", default=False):
            console.info("Aborted.")
            sys.exit(0)

    print()
    step = 1

    # ── Step 1: Remove from root dependencies ─────────────────────────
    if root_dep:
        console.step("Removing from root dependencies", step=step)
        remove_root_dependency(pkg_name)
        step += 1

    # ── Step 2: Remove from workspace members ─────────────────────────
    console.step("Updating workspace members", step=step)
    remove_from_workspace(pkg_name)
    step += 1

    # ── Step 3: Delete package directory ──────────────────────────────
    console.step(f"Deleting  python/{pkg_name}/", step=step)
    shutil.rmtree(package_dir)
    console.success(f"Deleted python/{pkg_name}/")

    # ── Step 4: Sync ──────────────────────────────────────────────────
    console.step("Syncing workspace", step=step + 1)
    result = subprocess.run(
        ["uv", "sync"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        console.success("Workspace synced.")
    else:
        console.info("Run  uv sync  to install.")

    print()
    console.success(f"Package '{pkg_name}' removed successfully.")
