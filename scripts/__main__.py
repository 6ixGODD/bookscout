"""BookScout CLI entry point dispatcher.

- ``bookscout`` → launches the Textual TUI (in-process, no subprocess).
- ``bookscout-dev`` / ``bs`` → developer toolkit (package/docs/bump).

The TUI is a Textual app that runs :class:`ReplContext` directly — no
Node.js, no IPC, no daemon. Running ``bookscout`` with no subcommand (or
with options only) defaults to the ``tui`` subcommand; pass ``serve``
explicitly to launch the stdio REPL server instead.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> None:
    """Entry point for ``bookscout`` — launches the TUI by default.

    All command-line args are forwarded to ``bookscout-repl``. If the
    first arg is not ``tui`` or ``serve``, ``tui`` is prepended so
    ``bookscout --config x.yaml`` behaves as ``bookscout tui --config x.yaml``.
    """
    repo_root = Path(__file__).resolve().parent.parent
    os.environ.setdefault("BOOKSCOUT_DATA_DIR", str(repo_root / "data"))

    args = list(sys.argv[1:])
    if not args or args[0] not in ("tui", "serve"):
        args.insert(0, "tui")

    from bookscout.repl.__main__ import app as repl_app

    repl_app(args=args, prog_name="bookscout")


if __name__ == "__main__":
    main()
