---
name: bookscout
description: >
  BookScout monorepo conventions and tooling. Use this skill whenever working
  in the BookScout project — any Python command (python, ruff, mypy,
  pre-commit), any package management task (create/remove/list modules), any
  version bump, or whenever the user mentions "bs", "bookscout", "uv run", or
  wants to scaffold/delete/bump a package in this repo.
---

# BookScout — project conventions

## Python tooling: always `uv run`

Every Python-related command in this repo goes through **`uv run`**. This
ensures the project's managed Python and lockfile are always used.

| Instead of …              | Use …                       |
| ------------------------- | --------------------------- |
| `python ...`              | `uv run python ...`         |
| `python -m ...`           | `uv run python -m ...`      |
| `ruff ...` / `ruff check` | `uv run ruff check ...`     |
| `mypy ...`                | `uv run mypy ...`           |
| `pre-commit ...`          | `uv run pre-commit ...`     |
| `pip install ...`         | `uv add ...` (or `uv sync`) |

All dev tools (`ruff`, `mypy`, `pre-commit`, `pydantic`) live in
`[dependency-groups].dev` in pyproject.toml. They are available via
`uv run` because the user runs `uv sync --all-groups`.

## `bs` — BookScout CLI

`bs` is the monorepo developer toolkit. Every invocation is:

```bash
uv run bs <command> [args...]
```

### Package management (`uv run bs package` or `uv run bs pkg`)

| Command / alias                               | What it does                         |
| --------------------------------------------- | ------------------------------------ |
| `bs package list` / `bs ls`                   | List all packages in `python/`       |
| `bs package list --tree` / `bs ls -t`         | Show as namespace tree               |
| `bs package new <name> -y` / `bs n <name> -y` | Create a new package, all defaults   |
| `bs package new` / `bs n`                     | Interactive wizard (with prompts)    |
| `bs package rm <name>` / `bs rm <name>`       | Remove a package (asks confirmation) |
| `bs package rm <name> -f`                     | Remove without confirmation          |

**Package name convention**: all packages live under `python/` and use the
`bookscout-` prefix (kebab-case). The `bs` CLI auto-prepends `bookscout-` —
typing `llm` automatically becomes `bookscout-llm`. You can also provide the
full name.

**Creating a package**: `bs n bookscout-foo -y` scaffolds:

- `python/bookscout-foo/pyproject.toml`
- `python/bookscout-foo/README.md`
- `python/bookscout-foo/bookscout/foo/__init__.py`
- Registers it in `[tool.uv.workspace.members]`
- Adds to root `[project.dependencies]` + `[tool.uv.sources]`
- Runs `uv sync`

After creation the user should run `uv sync` to install the new package.

**Removing a package**: `bs rm bookscout-foo`:

- Removes from `[project.dependencies]` and `[tool.uv.sources]`
- Removes from `[tool.uv.workspace.members]`
- Deletes the directory `python/bookscout-foo/`
- Runs `uv sync`

### Version bump (`uv run bs bump`)

```bash
uv run bs bump            # interactive: choose patch/minor/major/custom
uv run bs bump -b patch   # non-interactive patch bump
uv run bs bump -b minor   # non-interactive minor bump
uv run bs bump --version 1.2.3  # explicit version
```

Bump updates these canonical locations:

- `VERSION` (root)
- `pyproject.toml` → `[project].version`
- `python/bookscout-core/pyproject.toml` → `[project].version`
- `python/bookscout-core/bookscout/core/__init__.py` → `__version__`

Optional flags: `--tag` (create git tag), `--push` (push commits+tags),
`--no-commit` (only update files).

## Code conventions

- **Type hints**: always `from __future__ import annotations` at top
- **Linting**: `uv run ruff check .` before committing
- **Pre-commit**: `uv run pre-commit run --all-files`
- **Imports**: use `from scripts._helpers import console` for console output
- **Pydantic**: v2 (`pydantic>=2.13.4`), use `model_validate`, `model_dump`, `FieldInfo`
