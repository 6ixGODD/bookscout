"""bookscout.progress — render-agnostic progress / task monitor.

Public surface::

    from bookscout.progress import (
        Monitor,
        MonitorScope,
        NullRenderer,
        Renderer,
        RichLiveRenderer,
        TaskSnapshot,
        MonitorSink,
    )

The :class:`Monitor` is a thread-safe task tree updated by pipeline code with
``start`` / ``advance`` / ``finish`` / ``fail``. It carries no rendering state
and is safe to drive from concurrent ``asyncio.gather`` tasks. A
:class:`Renderer` consumes snapshots and draws them (Rich ``Live`` TUI,
nothing, or a future GUI client). :class:`MonitorSink` is a
``bookscout.logging`` sink forwarding records into the active renderer's
rolling log tail.
"""

from __future__ import annotations

from .monitor import Monitor
from .monitor import MonitorScope
from .null_scope import NullScope
from .renderers import NullRenderer
from .renderers import Renderer
from .renderers import RichLiveRenderer
from .schema import TaskSnapshot
from .sink import MonitorSink

__all__ = [
    "Monitor",
    "MonitorScope",
    "MonitorSink",
    "NullRenderer",
    "NullScope",
    "Renderer",
    "RichLiveRenderer",
    "TaskSnapshot",
]
