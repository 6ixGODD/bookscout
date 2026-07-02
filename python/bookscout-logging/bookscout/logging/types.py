"""Core types: LogLevel constants and LogTarget descriptor.

Kept in a separate module so that both ``__init__`` and ``config`` can import
from here without creating a circular dependency.
"""

from __future__ import annotations

import dataclasses
import enum
import os
import pathlib
import typing as t


class LogLevel(enum.IntEnum):
    """Log level integer constants, compatible with stdlib logging values."""

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


@dataclasses.dataclass(frozen=True)
class LogTarget:
    """A single log output destination.

    Prefer the class-method constructors for clarity::

        LogTarget.stdout()
        LogTarget.stderr(level=LogLevel.WARNING)
        LogTarget.file("app.log", max_bytes=50 * 1024 * 1024)
        LogTarget.stdout(pretty=True)  # human-readable colored output
    """

    dest: t.Literal["stdout", "stderr"] | pathlib.Path
    level: int = LogLevel.DEBUG
    max_bytes: int = 0  # 0 = no rotation
    backup_count: int = 5
    pretty: bool = False  # colored human-readable format (stream targets only)

    @classmethod
    def stdout(cls, level: int = LogLevel.DEBUG, *, pretty: bool = False) -> LogTarget:
        return cls(dest="stdout", level=level, pretty=pretty)

    @classmethod
    def stderr(cls, level: int = LogLevel.WARNING, *, pretty: bool = False) -> LogTarget:
        return cls(dest="stderr", level=level, pretty=pretty)

    @classmethod
    def file(
        cls,
        path: str | os.PathLike[str],
        level: int = LogLevel.DEBUG,
        max_bytes: int = 0,
        backup_count: int = 5,
    ) -> LogTarget:
        return cls(
            dest=pathlib.Path(path),
            level=level,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )
