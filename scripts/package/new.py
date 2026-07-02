"""``package new`` — create a new Python namespace package."""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from scripts._helpers import console
from scripts._helpers.git import get_git_config
from scripts._helpers.ns import KEBAB_RE
from scripts._helpers.ns import check_namespace_conflict
from scripts._helpers.ns import name_to_ns
from scripts._helpers.templates import create_project
from scripts._helpers.workspace import ROOT
from scripts._helpers.workspace import add_root_dependency
from scripts._helpers.workspace import get_existing_packages
from scripts._helpers.workspace import get_requires_python

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


def new(
    name: Annotated[
        str | None,
        typer.Argument(
            help="Package name (kebab-case suffix, 'bookscout-' prefix is automatic)",
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "-y",
            "--yes",
            help="Skip all prompts — accept every default",
        ),
    ] = False,
    version: Annotated[
        str | None,
        typer.Option(
            "--version",
            help="Package version",
        ),
    ] = None,
    author: Annotated[
        str | None,
        typer.Option(
            "-a",
            "--author",
            help="Author name (default: git config user.name)",
        ),
    ] = None,
    email: Annotated[
        str | None,
        typer.Option(
            "-e",
            "--email",
            help="Author email (default: git config user.email)",
        ),
    ] = None,
    description: Annotated[
        str | None,
        typer.Option(
            "-d",
            "--description",
            help="Short package description",
        ),
    ] = None,
    python_requires: Annotated[
        str | None,
        typer.Option(
            "--python-requires",
            help="Python version constraint",
        ),
    ] = None,
    no_workspace: Annotated[
        bool,
        typer.Option(
            "--no-workspace",
            help="Standalone package — not a workspace member, placed outside python/. Requires --base-dir.",
        ),
    ] = False,
    base_dir: Annotated[
        str | None,
        typer.Option(
            "-b",
            "--base-dir",
            help="Base directory relative to repo root (default: python/). Required when --no-workspace is set.",
        ),
    ] = None,
    no_root_dep: Annotated[
        bool,
        typer.Option(
            "--no-root-dep",
            help="Add to workspace members but skip adding to root [project.dependencies].",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Preview configuration without writing any files",
        ),
    ] = False,
) -> None:
    """Create a new Python namespace package in python/.

    Quick start::

        bs package new bookscout-llm -y    # all defaults, no prompts
        bs package new llm -y              # same — 'bookscout-' prefix is automatic
        bs package new                     # full interactive wizard
    """
    from scripts._helpers.workspace import get_version

    pkg_name = _resolve_name(name)

    # ── Fast path: positional name + --yes → skip all prompts ──────
    if pkg_name and yes:
        add_to_ws = not no_workspace
        base_dir_str = base_dir or "python"
        target_dir = ROOT / base_dir_str
        add_root_dep = not no_root_dep if add_to_ws else False

        pkg_version = version or "0.1.0"
        pkg_author = author or get_git_config("user.name")
        pkg_email = email or get_git_config("user.email")
        pkg_description = description or ""
        pkg_python_requires = python_requires or get_requires_python()
        dependencies: list[str] = []
    else:
        # ── Interactive wizard ─────────────────────────────────────
        console.banner(
            "BookScout Dev Tools",
            subtitle="New Package Wizard",
            version=get_version(),
        )

        # Step 1: Workspace membership
        add_to_ws = (
            False
            if no_workspace
            else console.confirm(
                "Add to uv workspace members?",
                default=True,
            )
        )

        # Step 2: Base directory
        base_dir_str = base_dir
        if add_to_ws:
            if not base_dir_str:
                base_dir_str = (
                    console.prompt_input(
                        "Base directory (relative to repo root)",
                        default="python",
                    )
                    or "python"
                )
            target_dir = ROOT / base_dir_str
        else:
            if not base_dir_str:
                console.info("Existing top-level directories:")
                top_dirs = sorted(
                    d.name
                    for d in ROOT.iterdir()
                    if d.is_dir() and not d.name.startswith(".") and d.name not in (".venv", "__pycache__")
                )
                console.list_items(top_dirs or ["(none yet)"])
                print()
                base_dir_str = console.prompt_input("Base directory (relative to repo root, e.g. mcp, tools, apps)")
            if not base_dir_str:
                console.error("Base directory is required.")
                sys.exit(1)
            target_dir = ROOT / base_dir_str

        # Step 3: Root dependency
        if add_to_ws:
            add_root_dep = (
                False
                if no_root_dep
                else console.confirm(
                    "Add as root workspace dependency?",
                    default=True,
                )
            )
        else:
            add_root_dep = False

        # Step 4: Package name (with bookscout- prefix)
        existing_in_dir = (
            [
                d.name
                for d in sorted(target_dir.iterdir())
                if d.is_dir() and not d.name.startswith(".") and (d / "pyproject.toml").exists()
            ]
            if target_dir.exists()
            else []
        )
        all_known_names = list(set(get_existing_packages()) | set(existing_in_dir))

        print()
        pkg_name = _resolve_name(name)
        while True:
            if not pkg_name:
                pkg_name = console.prompt_input(
                    "Package name (e.g. bookscout-foo-bar)",
                    prefix=_PREFIX,
                )
            if not pkg_name:
                console.error("Package name is required.")
                continue
            if not KEBAB_RE.match(pkg_name):
                console.error(f"'{pkg_name}' is not valid kebab-case.")
                pkg_name = None
                continue
            if (target_dir / pkg_name).exists():
                console.error(f"'{(target_dir / pkg_name).relative_to(ROOT)}' already exists.")
                pkg_name = None
                continue
            conflict, reason = check_namespace_conflict(pkg_name, all_known_names)
            if conflict:
                console.error(f"Namespace conflict: {reason}")
                pkg_name = None
                continue
            break

        # Step 5: Metadata
        pkg_version = version or console.prompt_input("Version", default="0.1.0") or "0.1.0"
        default_author = get_git_config("user.name")
        pkg_author = author or console.prompt_input("Author name", default=default_author)
        default_email = get_git_config("user.email")
        pkg_email = email or console.prompt_input("Author email", default=default_email)
        pkg_description = description if description is not None else console.prompt_input("Description", default="")
        default_requires_python = get_requires_python()
        pkg_python_requires = (
            python_requires
            or console.prompt_input("Python requires", default=default_requires_python)
            or default_requires_python
        )

        # Step 6: Dependencies
        print()
        available = get_existing_packages()
        console.info("Available workspace packages (python/):")
        console.list_items(sorted(available) or ["(none)"])
        print()
        deps_raw = console.prompt_input("Dependencies (comma-separated, or leave blank)", default="")
        dependencies = [d.strip() for d in deps_raw.split(",") if d.strip()] if deps_raw else []

    # ── Preview ───────────────────────────────────────────────────
    location = str((target_dir / pkg_name).relative_to(ROOT))
    print()
    console.header("Package Configuration")
    console.key_value({
        "Name": pkg_name,
        "Namespace": name_to_ns(pkg_name),
        "Location": location,
        "Version": pkg_version,
        "Author": f"{pkg_author} <{pkg_email}>" if pkg_email else (pkg_author or "(none)"),
        "Description": pkg_description or "(none)",
        "Python": pkg_python_requires,
        "Dependencies": ", ".join(dependencies) or "(none)",
        "Workspace member": "yes" if add_to_ws else "no",
        "Root dependency": "yes" if add_root_dep else ("— (not a member)" if not add_to_ws else "no (member only)"),
    })
    print()

    if dry_run:
        console.warning("Dry-run mode — no files were written.")
        return

    if not yes and not console.confirm("Create package?", default=True):
        console.info("Aborted.")
        sys.exit(0)

    print()
    create_project(
        name=pkg_name,
        version=pkg_version,
        author_name=pkg_author,
        author_email=pkg_email,
        description=pkg_description or "",
        python_requires=pkg_python_requires,
        dependencies=dependencies,
        do_add_to_workspace=add_to_ws,
        base_dir=target_dir,
    )

    if add_root_dep:
        console.step("Adding to root dependencies", step=4)
        add_root_dependency(pkg_name)

    console.separator()
    console.success("New package created. Run `uv sync` to update the workspace.")
