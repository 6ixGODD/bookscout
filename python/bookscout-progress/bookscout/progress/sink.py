"""A ``bookscout.logging`` sink that forwards records to a renderer's log tail.

Wire it alongside a file sink so the CLI keeps a full JSON-line log on disk
*and* shows a rolling tail inside the progress TUI::

    sinks = [
        FileSink(path, level),
        MonitorSink(renderer, level),
    ]
    logger = Logger("svc", targets=[...])  # targets→sinks done by build_logger

The sink is non-blocking: :meth:`write` just appends to the renderer's
in-memory buffer under its lock. The actual drawing happens on the renderer's
refresh thread.
"""

from __future__ import annotations

import typing as t

from bookscout.logging.sink import Sink

from .renderers import Renderer


class MonitorSink(Sink):
    """Log sink forwarding :class:`LogRecord` into a renderer's rolling tail.

    Args:
        renderer: The renderer that will display the log tail.
        level: Minimum log level to forward (records below this are dropped).
    """

    __slots__ = ("_renderer", "level")  # pylint: disable=redefined-slots-in-subclass

    def __init__(self, renderer: Renderer, level: int) -> None:
        super().__init__(level)
        self._renderer = renderer

    def write(self, record: t.Any) -> None:
        if record.level < self.level:
            return
        self._renderer.push_log(
            ts=record.ts,
            level=record.level,
            name=record.name,
            message=record.message,
            fields=dict(record.fields) if record.fields else {},
        )

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


__all__ = ["MonitorSink"]
