from __future__ import annotations

import pathlib
import subprocess

_ROOT = pathlib.Path(__file__).parent.parent.parent


def get_git_config(key: str) -> str:
    """Read a value from git config; return empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "config", "--get", key],
            capture_output=True,
            text=True,
            cwd=_ROOT,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""
