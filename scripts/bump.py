"""``bump`` — bump the core/workspace version.

Updates all four canonical locations that must stay in sync:
  • VERSION                            (repo root, plain text)
  • pyproject.toml                     (workspace root [project].version)
  • python/bookscout-core/pyproject.toml  ([project].version)
  • python/bookscout-core/bookscout/core/__init__.py  (__version__)

Optionally creates a git commit + tag and pushes.
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Annotated

import typer

from scripts._helpers import console
from scripts._helpers.workspace import ROOT
from scripts._helpers.workspace import get_version

# ── Canonical file paths ──────────────────────────────────────────────────────

VERSION_FILE = ROOT / "VERSION"
WORKSPACE_TOML = ROOT / "pyproject.toml"
CORE_DIR = ROOT / "python" / "bookscout-core"
CORE_TOML = CORE_DIR / "pyproject.toml"
CORE_INIT = CORE_DIR / "bookscout" / "core" / "__init__.py"


_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _parse(version: str) -> tuple[int, int, int]:
    m = _SEMVER_RE.match(version.strip())
    if not m:
        console.error(f"'{version}' is not a valid semver (MAJOR.MINOR.PATCH).")
        sys.exit(1)
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _bump(version: str, part: str) -> str:
    major, minor, patch = _parse(version)
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _read_current() -> str:
    """Read version from VERSION file (canonical source of truth)."""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        # Fall back to workspace pyproject
        return get_version()


def _update_version_file(new: str) -> None:
    VERSION_FILE.write_text(f"{new}\n", encoding="utf-8")
    console.success(f"VERSION  →  {new}")


def _update_toml_version(path, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, n = re.subn(
        r'^(version\s*=\s*")[^"]*(")',
        rf"\g<1>{new}\g<2>",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n == 0:
        console.warning(f"Could not find version field in {path.relative_to(ROOT)}")
        return
    path.write_text(updated, encoding="utf-8")
    console.success(f"{path.relative_to(ROOT)}  →  {new}")


def _update_core_init(new: str) -> None:
    text = CORE_INIT.read_text(encoding="utf-8")
    updated, n = re.subn(
        r'^(__version__\s*=\s*")[^"]*(")',
        rf"\g<1>{new}\g<2>",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n == 0:
        console.warning(f"Could not find __version__ in {CORE_INIT.relative_to(ROOT)}")
        return
    CORE_INIT.write_text(updated, encoding="utf-8")
    console.success(f"{CORE_INIT.relative_to(ROOT)}  →  {new}")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def _git_commit_and_tag(new: str, tag: bool, push: bool) -> None:
    files = [
        str(VERSION_FILE.relative_to(ROOT)),
        str(WORKSPACE_TOML.relative_to(ROOT)),
        str(CORE_TOML.relative_to(ROOT)),
        str(CORE_INIT.relative_to(ROOT)),
    ]

    console.step("Staging changed files", step=2)
    result = _git("add", *files)
    if result.returncode != 0:
        console.warning(f"git add failed: {result.stderr.strip()}")
        return

    console.step(f"Committing  chore: bump version to {new}", step=3)
    msg = f"chore: bump version to {new}\n\nCo-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
    result = _git("commit", "-m", msg)
    if result.returncode != 0:
        console.warning(f"git commit failed: {result.stderr.strip()}")
        return
    console.success("Committed.")

    if tag:
        tag_name = f"v{new}"
        console.step(f"Creating tag  {tag_name}", step=4)
        result = _git("tag", tag_name)
        if result.returncode != 0:
            console.warning(f"git tag failed: {result.stderr.strip()}")
        else:
            console.success(f"Tag {tag_name} created.")

        if push:
            console.step("Pushing commits + tags", step=5)
            r1 = _git("push")
            r2 = _git("push", "--tags")
            if r1.returncode != 0 or r2.returncode != 0:
                console.warning(f"git push failed: {(r1.stderr + r2.stderr).strip()}")
            else:
                console.success("Pushed.")
    elif push:
        console.step("Pushing commits", step=4)
        result = _git("push")
        if result.returncode != 0:
            console.warning(f"git push failed: {result.stderr.strip()}")
        else:
            console.success("Pushed.")


_BUMP_PARTS = ("patch", "minor", "major")


def bump(
    bump_part: Annotated[
        str | None,
        typer.Option("-b", "--bump", help="Version part to increment: patch | minor | major"),
    ] = None,
    version: Annotated[
        str | None,
        typer.Option("--version", help="Explicit new version (e.g. 1.2.3). Overrides --bump."),
    ] = None,
    tag: Annotated[
        bool,
        typer.Option("--tag", help="Create a git tag vX.Y.Z after updating files"),
    ] = False,
    no_tag: Annotated[
        bool,
        typer.Option("--no-tag", help="Skip git tag creation"),
    ] = False,
    push: Annotated[
        bool,
        typer.Option("--push", help="Push commits (and tags if --tag) to remote"),
    ] = False,
    no_commit: Annotated[
        bool,
        typer.Option("--no-commit", help="Only update files; skip git commit, tag, and push"),
    ] = False,
) -> None:
    """Bump the core/workspace version across all canonical locations."""
    console.banner(
        "BookScout Dev Tools",
        subtitle="Version Bump",
        version=get_version(),
    )

    current = _read_current()
    console.info(f"Current version:  {current}")
    print()

    # ── Determine new version ─────────────────────────────────────────
    new_version: str

    if version:
        _parse(version)  # validate
        new_version = version
    elif bump_part:
        if bump_part not in _BUMP_PARTS:
            console.error(f"--bump must be one of: {', '.join(_BUMP_PARTS)}")
            sys.exit(1)
        new_version = _bump(current, bump_part)
    else:
        # Interactive
        choices = [
            f"patch  →  {_bump(current, 'patch')}",
            f"minor  →  {_bump(current, 'minor')}",
            f"major  →  {_bump(current, 'major')}",
            "custom …",
        ]
        idx = console.choose("Bump type", choices)
        if idx == 3:  # custom
            raw = console.prompt_input("New version (e.g. 1.2.3)")
            if not raw:
                console.error("Version is required.")
                sys.exit(1)
            _parse(raw)  # validate
            new_version = raw.strip()
        else:
            new_version = _bump(current, _BUMP_PARTS[idx])

    print()
    console.header(f"  {current}  →  {new_version}")
    print()

    if not console.confirm(f"Apply version bump to {new_version}?", default=True):
        console.info("Aborted.")
        sys.exit(0)

    # ── Step 1: Update files ──────────────────────────────────────────
    print()
    console.step("Updating version in all canonical locations", step=1)
    _update_version_file(new_version)
    _update_toml_version(WORKSPACE_TOML, new_version)
    _update_toml_version(CORE_TOML, new_version)
    _update_core_init(new_version)

    if no_commit:
        print()
        console.success(f"Version bumped to {new_version}.  (no commit)")
        return

    # ── Git ───────────────────────────────────────────────────────────
    print()
    do_commit = console.confirm("Commit changed files?", default=True)
    if not do_commit:
        console.success(f"Version bumped to {new_version}.  (no commit)")
        return

    do_tag: bool
    if no_tag:
        do_tag = False
    elif tag:
        do_tag = True
    else:
        do_tag = console.confirm(f"Create git tag  v{new_version}?", default=True)

    do_push = True if push else console.confirm("Push to remote?", default=False)

    print()
    _git_commit_and_tag(new_version, tag=do_tag, push=do_push)

    print()
    console.success(f"Version bumped to {new_version}.")
