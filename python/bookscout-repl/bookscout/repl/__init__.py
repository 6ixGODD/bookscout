"""``bookscout.repl`` package — interactive agent runtime with streaming transport.

Public API:
    * :class:`BookScoutConfig` — configuration model (YAML + env + CLI).
    * :class:`ReplContext` — shared runtime resources (BooksStore, LLM,
      embedding, vector store, TaskManager, per-book ReadingMode cache).
    * :class:`Transport` — abstract transport for REPL communication.
    * :class:`StdioTransport` — stdio-based transport with length-prefixed JSON.
    * :class:`ReplServer` — the stdio REPL server (transport over ReplContext).
    * :class:`BookScoutTui` — the in-process Textual TUI.

CLI entry::

    bookscout-repl tui  --config config.yaml --set chatmodel.model=deepseek-v4-pro
    bookscout-repl serve --config config.yaml --daemon
"""

from __future__ import annotations

from .config import BookScoutConfig
from .context import ReplContext
from .transport import StdioTransport
from .transport import Transport

__all__ = [
    "BookScoutConfig",
    "ReplContext",
    "StdioTransport",
    "Transport",
]
