from __future__ import annotations

import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).parent.parent.parent
PYTHON_DIR = ROOT / "python"
WORKSPACE_TOML = ROOT / "pyproject.toml"


def get_version() -> str:
    """Read the workspace version from the root pyproject.toml."""
    try:
        with WORKSPACE_TOML.open("rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


def get_requires_python() -> str:
    """Read the workspace requires-python from the root pyproject.toml."""
    try:
        with WORKSPACE_TOML.open("rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("requires-python", ">=3.12")
    except Exception:
        return ">=3.12"


def get_existing_packages() -> list[str]:
    """Return sorted package directory names inside python/.

    Only directories that contain a pyproject.toml are considered packages.
    """
    if not PYTHON_DIR.exists():
        return []
    return [
        d.name
        for d in sorted(PYTHON_DIR.iterdir())
        if d.is_dir() and not d.name.startswith(".") and (d / "pyproject.toml").exists()
    ]


def get_workspace_members() -> set[str]:
    """Return package names listed in [tool.uv.workspace.members]."""
    try:
        with WORKSPACE_TOML.open("rb") as f:
            data = tomllib.load(f)
        members = data.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", [])
        return {m.removeprefix("python/").strip("/") for m in members}
    except Exception:
        return set()


def add_to_workspace(name: str, rel_dir: str = "python") -> None:
    """Insert *name* into [tool.uv.workspace.members] in the root pyproject.toml.

    Args:
        name:    Package directory name (e.g. ``bookscout-foo``).
        rel_dir: Directory containing the package, relative to repo root
                 (e.g. ``python``, ``mcp``).  Defaults to ``python``.
    """
    from scripts._helpers import console

    try:
        content = WORKSPACE_TOML.read_text(encoding="utf-8")
        member_entry = f'"{rel_dir}/{name}"'
        if member_entry in content:
            console.warning(f"'{name}' is already listed in workspace members.")
            return

        pattern = re.compile(r"(members\s*=\s*\[)(.*?)(])", re.DOTALL)
        match = pattern.search(content)
        if match:
            prefix, body, suffix = match.group(1), match.group(2), match.group(3)
            stripped = body.rstrip().rstrip(",")
            new_body = stripped + f",\n  {member_entry},\n" if stripped.strip() else f"\n  {member_entry},\n"
            new_content = content[: match.start()] + prefix + new_body + suffix + content[match.end() :]
            WORKSPACE_TOML.write_text(new_content, encoding="utf-8")
            console.success(f"Added '{rel_dir}/{name}' to workspace members.")
        else:
            # Section doesn't exist yet — create it at the end of the file
            new_section = f"\n[tool.uv.workspace]\nmembers = [\n  {member_entry},\n]\n"
            # Also add [tool.uv.sources] if not present
            if "[tool.uv.sources]" not in content:
                new_section += "\n[tool.uv.sources]\n"
            WORKSPACE_TOML.write_text(content.rstrip() + new_section)
            console.success(f"Created [tool.uv.workspace.members] and added '{rel_dir}/{name}'.")
    except Exception as exc:
        console.warning(f"Failed to update workspace pyproject.toml: {exc}")


def add_root_dependency(name: str) -> None:
    """Add *name* to root [project.dependencies] and [tool.uv.sources].

    Does NOT run ``uv add`` — instead edits pyproject.toml directly so the
    root project is never rebuilt while the CLI itself is running.
    """
    from scripts._helpers import console

    try:
        content = WORKSPACE_TOML.read_text(encoding="utf-8")

        # ── Add to [project.dependencies] ──────────────────────────
        dep_entry = f'"{name}"'
        dep_pattern = re.compile(r"(dependencies\s*=\s*\[)(.*?)(])", re.DOTALL)
        dep_match = dep_pattern.search(content)
        if dep_match:
            prefix, body, suffix = dep_match.group(1), dep_match.group(2), dep_match.group(3)
            stripped = body.rstrip().rstrip(",")
            new_body = stripped + f",\n  {dep_entry},\n" if stripped.strip() else f"\n  {dep_entry},\n"
            content = content[: dep_match.start()] + prefix + new_body + suffix + content[dep_match.end() :]
        else:
            console.warning("Could not locate [project].dependencies in pyproject.toml.")
            return

        # ── Add to [tool.uv.sources] ───────────────────────────────
        source_entry = f"{name} = {{ workspace = true }}"
        if source_entry not in content:
            sources_pattern = re.compile(r"(\[tool\.uv\.sources\]\s*\n)")
            sources_match = sources_pattern.search(content)
            if sources_match:
                insert_pos = sources_match.end()
                content = content[:insert_pos] + f"{source_entry}\n" + content[insert_pos:]

        WORKSPACE_TOML.write_text(content, encoding="utf-8")
        console.success(f"'{name}' added to root dependencies.")
    except Exception as exc:
        console.warning(f"Failed to add root dependency: {exc}")


def remove_root_dependency(name: str) -> None:
    """Remove *name* from root [project.dependencies] and [tool.uv.sources].

    Does NOT run ``uv remove`` — edits pyproject.toml directly.
    """
    from scripts._helpers import console

    try:
        content = WORKSPACE_TOML.read_text(encoding="utf-8")
        changed = False

        # ── Remove from [project.dependencies] ──────────────────────
        dep_entry = f'"{name}"'
        dep_pattern = re.compile(r"(dependencies\s*=\s*\[)(.*?)(])", re.DOTALL)
        dep_match = dep_pattern.search(content)
        if dep_match:
            prefix, body, suffix = dep_match.group(1), dep_match.group(2), dep_match.group(3)
            new_body = re.sub(
                rf"\s*{re.escape(dep_entry)},?\s*\n?",
                "\n",
                body,
            )
            if new_body != body:
                content = content[: dep_match.start()] + prefix + new_body + suffix + content[dep_match.end() :]
                changed = True

        # ── Remove from [tool.uv.sources] ───────────────────────────
        source_pattern = re.compile(rf"^\s*{re.escape(name)}\s*=\s*\{{.*?\}}\s*\n?", re.MULTILINE)
        if source_pattern.search(content):
            content = source_pattern.sub("", content)
            changed = True

        if changed:
            WORKSPACE_TOML.write_text(content, encoding="utf-8")
            console.success(f"'{name}' removed from root dependencies.")
        else:
            console.warning(f"'{name}' was not found in root dependencies.")
    except Exception as exc:
        console.warning(f"Failed to remove root dependency: {exc}")


def remove_from_workspace(name: str) -> None:
    """Remove *name* from [tool.uv.workspace.members] and [tool.uv.sources] in root pyproject.toml."""
    from scripts._helpers import console

    try:
        content = WORKSPACE_TOML.read_text(encoding="utf-8")
        member_entry = f'"python/{name}"'
        if member_entry not in content:
            console.warning(f"'{name}' was not found in workspace members.")
            return

        lines = content.splitlines(keepends=True)
        # Remove the members line (handles trailing comma)
        new_lines = [ln for ln in lines if member_entry not in ln]
        # Also strip a now-dangling [tool.uv.sources] entry if present
        source_pattern = re.compile(rf"^\s*{re.escape(name)}\s*=\s*\{{.*workspace.*\}}\s*\n?")
        new_lines = [ln for ln in new_lines if not source_pattern.match(ln)]

        WORKSPACE_TOML.write_text("".join(new_lines), encoding="utf-8")
        console.success(f"Removed '{name}' from workspace members.")
    except Exception as exc:
        console.warning(f"Failed to update workspace pyproject.toml: {exc}")


def get_package_dependents(name: str) -> list[str]:
    """Return names of workspace members that declare *name* as a dependency."""
    _dep_re = re.compile(rf"^{re.escape(name)}($|[><=!;\[])", re.IGNORECASE)
    dependents: list[str] = []
    for pkg in get_existing_packages():
        if pkg == name:
            continue
        toml_path = PYTHON_DIR / pkg / "pyproject.toml"
        try:
            with toml_path.open("rb") as f:
                data = tomllib.load(f)
            project = data.get("project", {})
            all_deps: list[str] = list(project.get("dependencies", []))
            for group_deps in data.get("dependency-groups", {}).values():
                all_deps.extend(d for d in group_deps if isinstance(d, str))
            if any(_dep_re.match(d) for d in all_deps):
                dependents.append(pkg)
        except Exception:
            pass
    return dependents


def is_root_dependent(name: str) -> bool:
    """Return True if the workspace root pyproject.toml depends on *name*."""
    _dep_re = re.compile(rf"^{re.escape(name)}($|[><=!;\[])", re.IGNORECASE)
    try:
        with WORKSPACE_TOML.open("rb") as f:
            data = tomllib.load(f)
        deps: list[str] = data.get("project", {}).get("dependencies", [])
        return any(_dep_re.match(d) for d in deps)
    except Exception:
        return False
