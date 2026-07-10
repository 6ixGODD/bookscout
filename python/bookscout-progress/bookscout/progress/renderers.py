"""Renderers — consume :class:`Monitor` snapshots and draw them somewhere.

A :class:`Renderer` is started once, polled at its own cadence (an internal
thread or an ``asyncio`` loop callback), and stopped at the end of the run.
The base contract is intentionally tiny so a future GUI client can implement
it without pulling in the TUI stack.

Two concrete renderers ship here:

- :class:`NullRenderer` — does nothing. The monitor is still pollable via
  :meth:`Monitor.snapshot`, so this is what GUI / metrics / test code uses.
- :class:`RichLiveRenderer` — a Rich ``Live`` region showing one progress bar
  per task (with ETA) and a rolling tail of recent log lines at the bottom.
  Used by the CLIs.
"""

from __future__ import annotations

import abc
import types
import typing as t

from .monitor import Monitor


class Renderer(abc.ABC):
    """Abstract renderer. Subclasses implement :meth:`_start` / :meth:`_stop`.

    The log tail is part of the contract: :class:`MonitorSink` calls
    :meth:`push_log` from the logging writer thread, and renderers that show
    a log tail (the TUI) buffer it; renderers that don't (Null) ignore it.
    """

    @abc.abstractmethod
    def _start(self) -> None:
        """Begin rendering (start the Live region / refresh thread)."""

    @abc.abstractmethod
    def _stop(self) -> None:
        """Stop rendering and leave the terminal in a clean state."""

    @abc.abstractmethod
    def push_log(self, ts: float, level: int, name: str, message: str, fields: dict[str, t.Any]) -> None:
        """Feed one log record into the renderer's rolling tail (non-blocking)."""

    def __enter__(self) -> Renderer:
        self._start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        self._stop()


class NullRenderer(Renderer):
    """A renderer that draws nothing. For GUI / metrics / test code.

    The monitor is still pollable via :meth:`Monitor.snapshot`; this just
    declines to draw anything itself and discards log records.
    """

    def __init__(self, monitor: Monitor) -> None:
        self._monitor = monitor

    def _start(self) -> None:
        pass

    def _stop(self) -> None:
        pass

    def push_log(self, ts: float, level: int, name: str, message: str, fields: dict[str, t.Any]) -> None:
        pass

    @property
    def monitor(self) -> Monitor:
        return self._monitor


# RichLiveRenderer imports ``rich`` lazily so the package stays importable
# without the ``tui`` extra installed (e.g. on a GUI client).
# pylint: disable-next=wrong-import-position
from .rich_renderer import RichLiveRenderer  # noqa: E402

__all__ = ["NullRenderer", "Renderer", "RichLiveRenderer"]
