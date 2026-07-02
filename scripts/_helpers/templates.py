from __future__ import annotations

import pathlib

from bookscout.core import __app__

from .ns import name_to_ns
from .ns import name_to_rel_path
from .workspace import PYTHON_DIR
from .workspace import WORKSPACE_TOML
from .workspace import add_to_workspace
from .workspace import get_existing_packages

_ROOT = WORKSPACE_TOML.parent


def gen_pyproject_toml(
    name: str,
    version: str,
    description: str,
    author_name: str,
    author_email: str,
    python_requires: str,
    dependencies: list[str],
    workspace_deps: list[str],
) -> str:
    ns = name_to_ns(name)

    if author_name and author_email:
        authors_block = f'authors = [\n  {{ name = "{author_name}", email = "{author_email}" }},\n]\n'
    elif author_name:
        authors_block = f'authors = [\n  {{ name = "{author_name}" }},\n]\n'
    else:
        authors_block = ""

    classifiers_lines: list[str] = ['  "Programming Language :: Python :: 3 :: Only",']
    try:
        min_ver = next(
            (p.strip().lstrip(">=") for p in python_requires.split(",") if p.strip().startswith(">=")),
            "3.12",
        )
        max_ver = next(
            (p.strip().lstrip("<=") for p in python_requires.split(",") if p.strip().startswith("<=")),
            "3.14",
        )
        for minor in range(int(min_ver.split(".")[1]), int(max_ver.split(".")[1]) + 1):
            classifiers_lines.append(f'  "Programming Language :: Python :: 3.{minor}",')
    except (ValueError, StopIteration, IndexError):
        pass
    classifiers_str = "\n".join(classifiers_lines)

    deps_str = "[\n" + "\n".join(f'  "{d}",' for d in dependencies) + "\n]" if dependencies else "[]"

    toml = f"""\
[build-system]
build-backend = "setuptools.build_meta"
requires = [
  "setuptools>=68",
  "wheel",
]

[project]
name = "{name}"
version = "{version}"
description = "{description}"
readme = "README.md"
{authors_block}requires-python = "{python_requires}"
classifiers = [
{classifiers_str}
]
dependencies = {deps_str}

[tool.setuptools]
package-dir = {{ "" = "." }}

[tool.setuptools.packages.find]
where = [ "." ]
include = [ "{ns}*" ]

[tool.setuptools.exclude-package-data]
"*" = [
  "**/.ruff_cache/**",
  "**/__pycache__/**",
  "**/*.pyc",
  "**/.mypy_cache/**",
  "**/.pytest_cache/**",
  "**/*.egg-info/**",
  "**/build/**",
  "**/dist/**",
]
"""

    if workspace_deps:
        sources = "\n".join(f"{d} = {{ workspace = true }}" for d in workspace_deps)
        toml += f"\n[tool.uv.sources]\n{sources}\n"

    return toml


def gen_readme(name: str, description: str) -> str:
    ns = name_to_ns(name)
    desc_section = f"\n{description}\n" if description else ""
    return f"""\
# {name}
{desc_section}
Part of the [{__app__}](../../README.md) Python namespace packages.

## Namespace

`{ns}`

## Installation

```bash
pip install {name}
```
"""


def gen_init(name: str) -> str:
    ns = name_to_ns(name)
    return f'"""`{ns}` package."""\n\nfrom __future__ import annotations\n'


def create_project(
    *,
    name: str,
    version: str,
    author_name: str,
    author_email: str,
    description: str,
    python_requires: str,
    dependencies: list[str],
    do_add_to_workspace: bool,
    base_dir: pathlib.Path | None = None,
) -> None:
    """Create the full project scaffold for a new namespace package."""
    from scripts._helpers import console

    project_dir = (base_dir if base_dir is not None else PYTHON_DIR) / name
    src_dir = project_dir / name_to_rel_path(name)

    console.step("Creating directory structure", step=1)
    src_dir.mkdir(parents=True, exist_ok=True)
    console.path(project_dir.relative_to(_ROOT), label="Project dir", exists=True)
    console.path(src_dir.relative_to(_ROOT), label="  Source dir", exists=True)

    console.step("Generating project files", step=2)

    existing_set = set(get_existing_packages())
    workspace_deps = [d for d in dependencies if d in existing_set]

    files: dict[pathlib.Path, str] = {
        project_dir / "pyproject.toml": gen_pyproject_toml(
            name=name,
            version=version,
            description=description,
            author_name=author_name,
            author_email=author_email,
            python_requires=python_requires,
            dependencies=dependencies,
            workspace_deps=workspace_deps,
        ),
        project_dir / "README.md": gen_readme(name, description),
        src_dir / "__init__.py": gen_init(name),
    }

    for file_path, content in files.items():
        file_path.write_text(content, encoding="utf-8")
        console.path(file_path.relative_to(_ROOT), label="  Created")

    if do_add_to_workspace:
        console.step("Updating workspace", step=3)
        rel_dir = project_dir.parent.relative_to(_ROOT).as_posix()
        add_to_workspace(name, rel_dir=rel_dir)

    print()
    console.success(f"Package '{name}' created successfully!")
    print()
    console.info(f"  Namespace : {console.format_cyan(name_to_ns(name))}")
    console.info(f"  Location  : {console.format_cyan(str(project_dir.relative_to(_ROOT)))}")
    print()
    console.info("Next steps:")
    console.list_items([
        f"cd {project_dir.relative_to(_ROOT)}",
        "uv sync                    # sync workspace dependencies",
        f"# Start coding in {name_to_rel_path(name) / '__init__.py'}",
    ])
