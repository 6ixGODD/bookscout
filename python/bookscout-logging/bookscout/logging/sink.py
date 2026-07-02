"""Log output sinks — stream and file targets."""

from __future__ import annotations

import contextlib
import os
import pathlib
import time
import typing as t

import orjson

if t.TYPE_CHECKING:
    from .record import LogRecord


def _dumps(obj: dict[str, t.Any]) -> bytes:
    return orjson.dumps(obj)  # pylint: disable=no-member


_LEVEL_NAMES: dict[int, str] = {
    10: "DEBUG",
    20: "INFO",
    30: "WARNING",
    40: "ERROR",
    50: "CRITICAL",
}


def _format_ts_iso(ts: float) -> str:
    s = time.gmtime(ts)
    ms = int((ts % 1.0) * 1000)
    return f"{s.tm_year:04d}-{s.tm_mon:02d}-{s.tm_mday:02d}T{s.tm_hour:02d}:{s.tm_min:02d}:{s.tm_sec:02d}.{ms:03d}Z"


def _format_ts_pretty(ts: float) -> str:
    s = time.localtime(ts)
    ms = int((ts % 1.0) * 1000)
    return f"{s.tm_hour:02d}:{s.tm_min:02d}:{s.tm_sec:02d}.{ms:03d}"


def serialize_json(record: LogRecord) -> bytes:
    data: dict[str, t.Any] = {
        "ts": _format_ts_iso(record.ts),
        "level": _LEVEL_NAMES.get(record.level, str(record.level)),
        "name": record.name,
        "msg": record.message,
    }
    if record.fields:
        data.update(record.fields)
    if record.exc:
        data["exc"] = record.exc
    try:
        return _dumps(data) + b"\n"
    except Exception:  # pylint: disable=broad-exception-caught
        safe: dict[str, t.Any] = {k: str(v) for k, v in data.items()}
        try:
            return _dumps(safe) + b"\n"
        except Exception:  # pylint: disable=broad-exception-caught
            level = data.get("level", "?")
            return f'{{"level":"{level}","msg":"{record.message}","_err":"serialization_failed"}}\n'.encode()


# ── ANSI helpers (colorama-aware) ─────────────────────────────────────────────

try:
    import colorama

    colorama.init(autoreset=False)
    _COLORAMA = True
except ImportError:
    _COLORAMA = False

_C_RESET = "\033[0m"
_C_DIM = "\033[2m"
_C_CYAN = "\033[36m"
_C_YELLOW = "\033[33m"
_C_MAGENTA = "\033[35m"

_LEVEL_COLOR: dict[int, str] = {
    10: "\033[34m",  # blue  — DEBUG
    20: "\033[32m",  # green — INFO
    30: "\033[33m",  # yellow — WARNING
    40: "\033[31m",  # red   — ERROR
    50: "\033[1;31m",  # bold red — CRITICAL
}


def _format_field_value(v: t.Any) -> str:
    """Render a field value for the pretty sink.

    Strings are rendered bare; other types use ``repr`` so booleans, numbers,
    None and containers stay unambiguous.
    """
    if isinstance(v, str):
        return v
    return repr(v)


def _format_fields(fields: dict[str, t.Any]) -> str:
    """Render merged context fields as a colored ``[k=v, k=v]`` suffix.

    Returns an empty string when there are no fields.
    """
    if not fields:
        return ""
    parts = [f"{k}={_format_field_value(v)}" for k, v in fields.items()]
    return f"  {_C_MAGENTA}[{', '.join(parts)}]{_C_RESET}"


# ── Base sink ─────────────────────────────────────────────────────────────────


class Sink:
    """Base class for log output targets."""

    __slots__ = ("level",)

    def __init__(self, level: int) -> None:
        self.level = level

    def write(self, record: LogRecord) -> None:
        raise NotImplementedError

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


# ── Stream sinks ──────────────────────────────────────────────────────────────


class StreamSink(Sink):
    """Writes JSON-lines to stdout or stderr."""

    __slots__ = ("_buf", "level")  # pylint: disable=redefined-slots-in-subclass

    def __init__(self, stream: t.IO[str], level: int) -> None:
        super().__init__(level)
        self._buf: t.IO[bytes] = getattr(stream, "buffer", stream)  # type: ignore[assignment, arg-type]

    def write(self, record: LogRecord) -> None:
        self._buf.write(serialize_json(record))

    def flush(self) -> None:
        self._buf.flush()


class PrettyStreamSink(Sink):
    """Colorized, human-readable single-line output to stdout or stderr.

    Format::

        19:16:06.123  INFO      agentseed-mcp-cbex  incoming request  path=/mcp method=POST
    """

    __slots__ = ("_stream", "level")  # pylint: disable=redefined-slots-in-subclass

    def __init__(self, stream: t.IO[str], level: int) -> None:
        super().__init__(level)
        self._stream = stream

    def write(self, record: LogRecord) -> None:
        ts = _format_ts_pretty(record.ts)
        level_name = _LEVEL_NAMES.get(record.level, str(record.level))
        level_color = _LEVEL_COLOR.get(record.level, "")

        line = (
            f"{_C_DIM}{ts}{_C_RESET}  "
            f"{level_color}{level_name:<8}{_C_RESET}  "
            f"{_C_CYAN}{record.name}{_C_RESET}  "
            f"{record.message}"
            f"{_format_fields(record.fields)}"
        )
        self._stream.write(line + "\n")

        if record.exc:
            tb = record.exc.get("tb", "")
            exc_type = record.exc.get("type", "")
            exc_msg = record.exc.get("msg", "")
            self._stream.write(f"{_C_DIM}  {exc_type}: {exc_msg}\n{tb.rstrip()}{_C_RESET}\n")

    def flush(self) -> None:
        self._stream.flush()


# ── File sink ─────────────────────────────────────────────────────────────────


class FileSink(Sink):
    """Writes JSON-lines to a file with optional size-based rotation."""

    __slots__ = ("_backup_count", "_file", "_max_bytes", "_path", "_pos", "level")  # pylint: disable=redefined-slots-in-subclass

    def __init__(
        self,
        path: os.PathLike[str],
        level: int,
        *,
        max_bytes: int = 0,
        backup_count: int = 5,
    ):
        super().__init__(level)
        self._path = pathlib.Path(path)
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("ab")  # pylint: disable=consider-using-with
        self._pos: int = self._path.stat().st_size if self._path.exists() else 0

    def write(self, record: LogRecord) -> None:
        line = serialize_json(record)
        if 0 < self._max_bytes < self._pos + len(line):
            self._rotate()
        self._file.write(line)
        self._pos += len(line)

    def _rotate(self) -> None:
        self._file.flush()
        self._file.close()
        for i in range(self._backup_count - 1, 0, -1):
            src = pathlib.Path(f"{self._path}.{i}")
            dst = pathlib.Path(f"{self._path}.{i + 1}")
            if src.exists():
                with contextlib.suppress(OSError):
                    src.replace(dst)
        with contextlib.suppress(OSError):
            self._path.replace(pathlib.Path(f"{self._path}.1"))
        self._file = self._path.open("ab")  # pylint: disable=consider-using-with
        self._pos = 0

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        try:
            self._file.flush()
            self._file.close()
        except OSError:
            pass
