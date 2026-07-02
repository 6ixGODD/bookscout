"""Pydantic-based logging configuration."""

from __future__ import annotations

import os
import pathlib
import typing as t

from pydantic import BaseModel
from pydantic.fields import Field

from bookscout.core import __app__

from .types import LogLevel
from .types import LogTarget

LEVEL_MAP: dict[str, int] = {
    "DEBUG": LogLevel.DEBUG,
    "INFO": LogLevel.INFO,
    "WARNING": LogLevel.WARNING,
    "ERROR": LogLevel.ERROR,
    "CRITICAL": LogLevel.CRITICAL,
}

type _LevelLiteral = t.Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class SizeBasedRotation(BaseModel):
    max_size: int = Field(
        default=10,
        ge=1,
        le=1024 * 1024,
        description="Maximum log file size in MB before rotation",
    )

    backup_count: int = Field(
        default=5,
        ge=0,
        le=100,
        description="Number of backup files to keep after rotation",
    )


class TargetConfig(BaseModel):
    """Configuration for a single log output target."""

    dest: t.Literal["stdout", "stderr"] | os.PathLike[str] = Field(
        default="stdout",
        description="Output destination: 'stdout', 'stderr', or a file path",
    )

    level: _LevelLiteral = Field(
        default="INFO",
        description="Minimum log level for this target",
    )

    pretty: bool = Field(
        default=False,
        description=(
            "When True, emit human-readable colored output instead of JSON-lines. "
            "Only applies to stream targets (stdout/stderr). Ignored for file targets."
        ),
    )
    rotation: SizeBasedRotation | None = Field(
        default=None,
        description="Size-based rotation config (file targets only)",
    )

    def to_log_target(self) -> LogTarget:
        level_int = LEVEL_MAP[self.level.upper()]  # pylint: disable=no-member
        if self.dest in ("stdout", "stderr"):
            return LogTarget(dest=self.dest, level=level_int, pretty=self.pretty)  # type: ignore[arg-type]
        max_bytes = (self.rotation.max_size * 1024 * 1024) if self.rotation else 0  # pylint: disable=no-member
        backup_count = self.rotation.backup_count if self.rotation else 5  # pylint: disable=no-member
        return LogTarget(
            dest=pathlib.Path(self.dest),
            level=level_int,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )


class LoggingConfig(BaseModel):
    """Top-level logging configuration."""

    name: str = Field(
        default=__app__,
        description="Logger name / service identifier",
    )

    level: _LevelLiteral = Field(
        default="INFO",
        description="Global minimum log level (applied before per-target levels)",
    )

    targets: list[TargetConfig] = Field(
        default_factory=lambda: [
            TargetConfig(dest="stdout", level="DEBUG"),
            TargetConfig(dest="stderr", level="ERROR"),
        ],
        description="List of output targets",
    )

    context_keys: list[str] | None = Field(
        default=None,
        description="Keys automatically extracted from agentseed.core.Context per log call",
    )

    suppress: list[str] | None = Field(
        default=None,
        description=(
            "Python logger names to silence (set to CRITICAL+1). "
            "Useful for silencing noisy third-party libraries, e.g. ['mcp', 'uvicorn.access']."
        ),
    )
