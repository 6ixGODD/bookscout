"""``docs gen`` — Generate API reference documentation stubs.

Walks Python source trees (python/) and emits mkdocstrings stub pages
for Python modules.  A ``docs/reference/SUMMARY.md`` file is written for
the mkdocs-literate-nav plugin, mirroring the source file-system hierarchy.

Typical workflow::

    bs docs gen          # generate / refresh all reference docs
    bs docs build        # build the static site
    bs docs serve        # preview with mkdocs serve
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
import shutil
import sys
from typing import Annotated

import typer

from scripts._helpers import console
from scripts._helpers.workspace import ROOT

DOCS_DIR = ROOT / "docs"
DOCS_REF = DOCS_DIR / "reference"
MKDOCS_YML = ROOT / "mkdocs.yml"

# Directories that must not be traversed during Python module discovery.
_SKIP_DIRS: frozenset[str] = frozenset({
    "__pycache__",
    "tests",
    "test",
    "migrations",
    "exp",
    "build",
    "dist",
    ".benchmarks",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
})

# File names that are excluded even when they exist in the source tree.
_SKIP_FILES: frozenset[str] = frozenset({
    "conftest.py",
    "__main__.py",
    "cli.py",
    "setup.py",
})

# (section_label, root_path) pairs — order controls nav order.
_PY_ROOTS: list[tuple[str, Path]] = [
    ("python", ROOT / "python"),
]

# Item = (module_path, rel_from_pkg_root, section, pkg_name, pkg_root)
_Item = tuple[str, Path, str, str, Path]


def _should_skip_file(rel: Path) -> bool:
    """Return True when *rel* (relative to a package root) should be excluded."""
    for part in rel.parts:
        if part in _SKIP_DIRS or (part.startswith(".") and part != "."):
            return True
        if part.endswith((".egg-info", ".dist-info")):
            return True
    name = rel.name
    if name in _SKIP_FILES:
        return True
    return name.startswith("_") and name != "__init__.py"


def _module_path(rel: Path) -> str:
    """Convert a relative .py path to a dotted module string."""
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _doc_path(rel: Path, section: str, pkg_name: str) -> Path:
    """Compute the output .md path for a Python source file."""
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts[-1] = "index.md"
    else:
        parts[-1] = parts[-1][:-3] + ".md"
    return DOCS_REF / section / pkg_name / Path(*parts)


def _collect_py(section: str, root: Path) -> list[_Item]:
    """Collect Python module items from all packages under *root*."""
    items: list[_Item] = []
    if not root.exists():
        return items

    def _scan(parent: Path) -> None:
        for pkg_dir in sorted(parent.iterdir()):
            if not pkg_dir.is_dir() or pkg_dir.name.startswith("."):
                continue
            if not (pkg_dir / "pyproject.toml").exists():
                continue
            for py_file in sorted(pkg_dir.rglob("*.py")):
                rel = py_file.relative_to(pkg_dir)
                if _should_skip_file(rel):
                    continue
                module = _module_path(rel)
                if not module:
                    continue
                items.append((module, rel, section, pkg_dir.name, pkg_dir))

    _scan(root)
    return items


def _collect_pkg_roots(py_items: list[_Item]) -> list[str]:
    """Return sorted POSIX-relative paths of all unique package root dirs."""
    seen: set[str] = set()
    for _, _, _, _, pkg_root in py_items:
        rel = pkg_root.relative_to(ROOT).as_posix()
        seen.add(rel)
    return sorted(seen)


def _update_mkdocs_paths(pkg_roots: list[str]) -> None:
    """Inject the ``paths:`` list into mkdocs.yml under mkdocstrings.handlers.python."""
    if not MKDOCS_YML.exists():
        return

    text = MKDOCS_YML.read_text(encoding="utf-8")
    paths_block = "".join(f"          - {r}\n" for r in pkg_roots)
    # Replace existing paths block if present, otherwise insert before `options:`
    if re.search(r"^\s+paths:\s*\n(\s+-[^\n]+\n)+", text, re.MULTILINE):
        text = re.sub(
            r"(\s+paths:\s*\n)(\s+-[^\n]+\n)+",
            lambda m: f"{m.group(1)}{paths_block}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        text = re.sub(
            r"( {10}options:)",
            f"          paths:\n{paths_block}\\1",
            text,
            count=1,
        )
    MKDOCS_YML.write_text(text, encoding="utf-8")


def _generate_py_stubs(items: list[_Item]) -> int:
    """Write mkdocstrings ``:::`` stub pages. Returns count of files written."""
    count = 0
    for module, rel, section, pkg_name, _root in items:
        out = _doc_path(rel, section, pkg_name)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"# `{module}`\n\n::: {module}\n", encoding="utf-8")
        count += 1
    return count


def _base_depth(items: list[_Item], pkg_name: str) -> int:
    """Return the minimum module depth (parts) for a given package."""
    depths = [len(m.split(".")) for m, _, _, p, _ in items if p == pkg_name]
    return min(depths) if depths else 2


def _build_summary(py_items: list[_Item]) -> str:
    """Build the SUMMARY.md content for mkdocs-literate-nav.

    Each package header is linked to its root ``__init__`` page so Material
    theme renders it as a proper expandable nav item (not a dead section label).
    """
    lines: list[str] = ["* [Overview](index.md)"]
    section_labels = {"python": "Python"}
    by_section: dict[str, dict[str, list[_Item]]] = defaultdict(lambda: defaultdict(list))
    for item in py_items:
        _, _, section, pkg_name, _ = item
        by_section[section][pkg_name].append(item)

    for section, label in section_labels.items():
        pkgs = by_section.get(section)
        if not pkgs:
            continue
        lines.append(f"* {label}")
        for pkg_name in sorted(pkgs):
            base = _base_depth(py_items, pkg_name)
            sorted_items = sorted(pkgs[pkg_name], key=lambda x: x[0])

            # Find the root module (__init__ at base_depth) — it becomes the
            # linked section header so Material renders it as an expandable item.
            root = next((it for it in sorted_items if len(it[0].split(".")) == base), None)
            if root:
                root_doc = _doc_path(root[1], root[2], pkg_name)
                try:
                    root_rel = str(root_doc.relative_to(DOCS_REF)).replace("\\", "/")
                except ValueError:
                    root_rel = None
            else:
                root_rel = None

            if root_rel:
                lines.append(f"    * [{pkg_name}]({root_rel})")
            else:
                lines.append(f"    * {pkg_name}")

            # All non-root modules become children of the linked section header.
            children = [it for it in sorted_items if it is not root]
            for module, rel, sec, _, _ in children:
                doc = _doc_path(rel, sec, pkg_name)
                try:
                    doc_rel = str(doc.relative_to(DOCS_REF)).replace("\\", "/")
                except ValueError:
                    continue
                # depth relative to root (base_depth), rendered one level under pkg.
                depth = len(module.split(".")) - base
                indent = "    " * (1 + depth)
                lines.append(f"{indent}* [`{module}`]({doc_rel})")

    lines.append("")
    return "\n".join(lines)


def _generate_index(py_count: int) -> None:
    """Write docs/reference/index.md landing page."""
    parts: list[str] = [
        "# API Reference\n",
        "Auto-generated API reference for all BookScout packages.\n",
        f"> Generated from source: **{py_count}** Python modules.\n",
        "\n## Sections\n",
        "| Section | Description |",
        "| ------- | ----------- |",
        "| **Python** | Shared libraries under `python/` |",
        "\nUse the navigation panel to browse modules.\n",
    ]

    out = DOCS_REF / "index.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")


def gen(
    clean: Annotated[
        bool,
        typer.Option(
            "--clean",
            help="Remove existing docs/reference/ before generating",
        ),
    ] = False,
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help="Check only — exit 1 if SUMMARY.md would change, without writing",
        ),
    ] = False,
) -> None:
    """Generate API reference documentation in docs/reference/."""
    if clean and not check and DOCS_REF.exists():
        shutil.rmtree(DOCS_REF)
        console.info("Removed existing docs/reference/")

    # Collect Python modules
    py_items: list[_Item] = []
    for section, root in _PY_ROOTS:
        batch = _collect_py(section, root)
        py_items.extend(batch)
        console.info(f"Found {len(batch)} Python modules in {section}/")

    # Generate Python stubs
    if not check:
        py_count = _generate_py_stubs(py_items)
        console.success(f"Generated {py_count} Python reference pages")
        # Keep mkdocs.yml paths in sync with discovered package roots
        pkg_roots = _collect_pkg_roots(py_items)
        _update_mkdocs_paths(pkg_roots)
    else:
        py_count = len(py_items)

    # Build SUMMARY.md
    summary_content = _build_summary(py_items)
    summary_path = DOCS_REF / "SUMMARY.md"

    if check:
        if not summary_path.exists():
            console.error("docs/reference/SUMMARY.md does not exist (run without --check to generate).")
            sys.exit(1)
        existing = summary_path.read_text(encoding="utf-8")
        if existing != summary_content:
            console.error("docs/reference/SUMMARY.md is stale — re-run `bs docs gen`.")
            sys.exit(1)
        console.success("docs/reference/SUMMARY.md is up-to-date.")
        return

    DOCS_REF.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(summary_content, encoding="utf-8")
    console.success("Written docs/reference/SUMMARY.md")

    # Landing page
    _generate_index(py_count)
    console.success("Written docs/reference/index.md")
