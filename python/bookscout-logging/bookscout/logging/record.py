"""Internal log record structure."""

from __future__ import annotations

import typing as t


class LogRecord(t.NamedTuple):
    """A single structured log record, ready to be serialized.

    Exception info is pre-converted to a plain dict so the writer thread
    never holds references to live traceback objects.
    """

    ts: float  # time.time() — formatted by writer
    level: int  # LogLevel constant (10/20/30/40/50)
    name: str  # logger name
    message: str  # log message
    fields: dict[str, t.Any]  # merged context fields
    exc: dict[str, str] | None = None  # {type, msg, tb} or None
