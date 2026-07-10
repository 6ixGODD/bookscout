"""BookScout CLI entry point dispatcher.

- ``bookscout`` → launches the Textual TUI (in-process, no subprocess).
- ``bookscout-dev`` / ``bs`` → developer toolkit (package/docs/bump).

The TUI is a Textual app that runs :class:`ReplContext` directly — no
Node.js, no IPC, no daemon. Running ``bookscout`` with no subcommand (or
with options only) defaults to the ``tui`` subcommand; pass ``serve``
explicitly to launch the stdio REPL server instead.

The workspace defaults to ``~/.bookscout``.  For development you can
override it::

    bookscout --data-dir ./data       # per-invocation
    export BOOKSCOUT_DATA_DIR=./data  # via environment
"""

from __future__ import annotations

import sys


def main() -> None:
    """Entry point for ``bookscout`` — launches the TUI by default."""
    args = list(sys.argv[1:])
    if not args or args[0] not in ("tui", "serve"):
        args.insert(0, "tui")

    from bookscout.repl.__main__ import app as repl_app

    repl_app(args=args, prog_name="bookscout")


if __name__ == "__main__":
    main()
