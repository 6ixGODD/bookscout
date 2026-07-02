"""Structured async-safe logger for agentseed.

Public surface::

    from agentseed.infra.logging import Logger, LogLevel, LogTarget, build_logger

All log calls are non-blocking: the caller enqueues a record and returns
immediately.  A background thread handles JSON serialization and I/O.

Wire format (JSON-lines, one object per line)::

    {
        "ts": "2026-01-01T12:00:00.123Z",
        "level": "INFO",
        "name": "svc",
        "msg": "...",
        "key": "val",
    }

Exception info (when exc_info=True)::

    {"ts":..., ..., "exc":{"type":"ValueError","msg":"bad","tb":"Traceback ..."}}
"""

from __future__ import annotations

from collections.abc import Generator
from collections.abc import Sequence
import contextlib
import os
import sys
import time
import traceback
import typing as t

from bookscout.core.lib.context import Context as _CoreContext
from bookscout.core.mixins import SyncResourceMixin

from .config import LEVEL_MAP
from .config import LoggingConfig
from .record import LogRecord
from .sink import FileSink
from .sink import PrettyStreamSink
from .sink import Sink
from .sink import StreamSink
from .types import LogLevel
from .types import LogTarget
from .writer import _Writer


def _make_sink(target: LogTarget) -> Sink:
    if target.dest == "stdout":
        if target.pretty:
            return PrettyStreamSink(sys.stdout, target.level)
        return StreamSink(sys.stdout, target.level)
    if target.dest == "stderr":
        if target.pretty:
            return PrettyStreamSink(sys.stderr, target.level)
        return StreamSink(sys.stderr, target.level)
    assert isinstance(target.dest, os.PathLike)
    return FileSink(
        target.dest,
        target.level,
        max_bytes=target.max_bytes,
        backup_count=target.backup_count,
    )


def _capture_exc() -> dict[str, str] | None:
    """Capture the current exception as a plain dict (no live traceback refs)."""
    ei = sys.exc_info()
    if ei[0] is None:
        return None
    exc_type, exc_val, exc_tb = ei
    return {
        "type": exc_type.__name__,
        "msg": str(exc_val),
        "tb": "".join(traceback.format_exception(exc_type, exc_val, exc_tb)),
    }


class Logger(SyncResourceMixin):
    """Structured, async-safe logger.

    All log methods are non-blocking.  A single background writer thread shared
    across all loggers derived via ``with_context()`` handles serialization
    and I/O.

    Async safety:
        - ``agentseed.core.context.Context`` uses ``contextvars.ContextVar``,
          so each asyncio Task sees its own copy automatically.
        - ``queue.Queue.put_nowait()`` is thread-safe.
        - The writer thread never touches asyncio internals.

    Example::

        logger = Logger("my-service", targets=[LogTarget.stdout()])
        logger.info("started", port=8080)

        child = logger.with_context(component="auth")
        child.warning("token expired", user_id="u42")

        # Auto-extract fields from agentseed.core.Context:
        logger = Logger("svc", targets=[...], context_keys=["request_id"])
        async with Context({"request_id": "abc"}):
            logger.info("handled")  # → {"request_id": "abc", ...}
    """

    __slots__ = ("_bound", "_context_keys", "_level", "_name", "_writer")

    def __init__(
        self,
        name: str,
        *,
        targets: list[LogTarget],
        level: int = LogLevel.DEBUG,
        context_keys: Sequence[str] = (),
        _bound: dict[str, t.Any] | None = None,
        _writer: _Writer | None = None,
    ):
        super().__init__()
        self._name = name
        self._level = int(level)
        self._bound: dict[str, t.Any] = dict(_bound) if _bound else {}
        self._context_keys: frozenset[str] = frozenset(context_keys)
        if _writer is not None:
            self._writer = _writer
        else:
            sinks = [_make_sink(tgt) for tgt in targets]
            self._writer = _Writer(sinks)

    def with_context_keys(self, *keys: str) -> Logger:
        """Return a new Logger with additional context keys."""
        new = self.with_context()
        new._context_keys = frozenset(set(self._context_keys) | set(keys))
        return new

    # ── Context ───────────────────────────────────────────────────────────
    def with_context(self, /, **kwargs: t.Any) -> Logger:
        """Return a new Logger with additional bound fields.

        The new logger shares the same background writer.  Binding is eager
        (dict copy at call-time) so log calls pay no extra cost.
        """
        new: Logger = Logger.__new__(Logger)  # pylint: disable=no-value-for-parameter
        new._name = self._name
        new._level = self._level
        new._bound = {**self._bound, **kwargs}
        new._context_keys = self._context_keys
        new._writer = self._writer  # shared
        return new

    # ── Hot path ──────────────────────────────────────────────────────────
    def _build_fields(self, extra: dict[str, t.Any]) -> dict[str, t.Any]:
        fields = dict(self._bound) if self._bound else {}
        if self._context_keys:
            ctx = _CoreContext.try_current()  # type: ignore[union-attr]
            if ctx is not None:
                for k in self._context_keys:
                    v = ctx.get(k)
                    if v is not None:
                        fields[k] = v
        if extra:
            fields.update(extra)
        return fields

    def _emit(
        self,
        level: int,
        msg: str,
        extra: dict[str, t.Any],
        exc_info: bool = False,
    ) -> None:
        exc = _capture_exc() if exc_info else None
        self._writer.enqueue(
            LogRecord(
                ts=time.time(),
                level=level,
                name=self._name,
                message=msg,
                fields=self._build_fields(extra),
                exc=exc,
            )
        )

    # ── Log methods ───────────────────────────────────────────────────────
    def debug(self, msg: str, /, **fields: t.Any) -> None:
        if self._level <= 10:
            self._emit(10, msg, fields)

    def info(self, msg: str, /, **fields: t.Any) -> None:
        if self._level <= 20:
            self._emit(20, msg, fields)

    def warning(self, msg: str, /, **fields: t.Any) -> None:
        if self._level <= 30:
            self._emit(30, msg, fields)

    def error(self, msg: str, /, exc_info: bool = False, **fields: t.Any) -> None:
        if self._level <= 40:
            self._emit(40, msg, fields, exc_info=exc_info)

    def exception(self, msg: str, /, **fields: t.Any) -> None:
        self.error(msg, exc_info=True, **fields)

    def critical(self, msg: str, /, **fields: t.Any) -> None:
        if self._level <= 50:
            self._emit(50, msg, fields)

    @contextlib.contextmanager
    def catch(
        self,
        msg: str,
        *,
        exc: type[BaseException] | tuple[type[BaseException], ...] = Exception,
        reraise: bool = True,
        **kwargs: t.Any,
    ) -> Generator[None]:
        """Context manager that logs and optionally re-raises exceptions.

        Example::

            with logger.catch("failed to connect", reraise=False):
                connect()  # exception → logged at ERROR, then swallowed
        """
        try:
            yield
        except exc as e:  # pylint: disable=broad-exception-caught
            self.error(f"{msg}: {e}", exc_info=True, **kwargs)
            if reraise:
                raise

    # ── Lifecycle ─────────────────────────────────────────────────────────
    def flush(self, timeout: float = 5.0) -> None:
        """Block until all currently-queued records have been written."""
        self._writer.flush(timeout=timeout)

    def close(self) -> None:
        """Drain the queue, close all sinks, and stop the background thread."""
        self._writer.stop()

    def shutdown(self) -> None:
        """Alias for :meth:`close()`, for adapting to lifecycle protocols."""
        self.close()

    def __repr__(self) -> str:
        return f"Logger(name={self._name!r}, level={self._level})"


# ── Factory ────────────────────────────────────────────────────────────────
def build_logger(config: LoggingConfig) -> Logger:
    """Build a :class:`Logger` from a :class:`~agentseed.infra.logging.config.LoggingConfig`."""
    import logging as _stdlib_logging

    level_int = LEVEL_MAP[config.level.upper()]

    # Silence noisy third-party loggers before any I/O happens.
    for name in config.suppress or []:
        _stdlib_logging.getLogger(name).setLevel(_stdlib_logging.CRITICAL + 1)  # type: ignore[attr-defined]

    return Logger(
        config.name,
        targets=[tgt.to_log_target() for tgt in config.targets],
        level=level_int,
        context_keys=list(set(config.context_keys or [])),
    )
