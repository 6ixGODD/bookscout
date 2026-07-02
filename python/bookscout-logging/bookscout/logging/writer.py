"""Background writer thread that serializes ``LogRecord`` values to sinks."""

from __future__ import annotations

import atexit
import contextlib
import queue
import threading
import time
import typing as t

from .record import LogRecord

# pylint: disable-next=unused-import
from .sink import _LEVEL_NAMES  # noqa: F401
from .sink import Sink

# pylint: disable-next=unused-import
from .sink import _format_ts_iso  # noqa: F401 - exported for backward compat

_DEFAULT_MAX_QUEUE = 10_000


class _FlushToken(t.NamedTuple):
    done: threading.Event


_STOP = object()


class _Writer(threading.Thread):
    """Non-daemon background thread that serializes and writes log records.

    Key design choices:
    - Non-daemon + atexit: guarantees the queue is drained on interpreter exit.
    - Bounded queue (default 10 000 slots): prevents OOM under log bursts;
      dropped records are counted and reported on shutdown.
    - Flushes sinks whenever the queue is momentarily empty so records appear
      immediately in interactive terminals without hammering the OS per write.
    - Each sink write is individually guarded so one failing sink cannot
      silence the others.
    """

    def __init__(self, sinks: list[Sink], maxqueue: int = _DEFAULT_MAX_QUEUE) -> None:
        super().__init__(name="agentseed-log", daemon=False)
        self._queue: queue.Queue[LogRecord | _FlushToken | object] = queue.Queue(maxsize=maxqueue)
        self._sinks = sinks
        self._dropped = 0
        self._drop_lock = threading.Lock()
        self.start()
        atexit.register(self._atexit)

    def enqueue(self, record: LogRecord) -> None:
        """Non-blocking enqueue.  Drops the record and increments counter if full."""
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            with self._drop_lock:
                self._dropped += 1

    def flush(self, timeout: float = 5.0) -> None:
        """Block until all currently-queued records have been written."""
        done = threading.Event()
        self._queue.put(_FlushToken(done))
        done.wait(timeout=timeout)

    def stop(self, timeout: float = 10.0) -> None:
        """Drain the queue, close all sinks, and stop the thread."""
        try:
            self._queue.put(_STOP, timeout=timeout)
        except queue.Full:
            return
        self.join(timeout=timeout)

    def _atexit(self) -> None:
        if self.is_alive():
            self.stop(timeout=10.0)

    # Writer loop -----------------------------------------------------------

    def run(self) -> None:
        q = self._queue
        sinks = self._sinks

        while True:
            item = q.get()

            if item is _STOP:
                self._shutdown(sinks)
                break

            if isinstance(item, _FlushToken):
                for sink in sinks:
                    with contextlib.suppress(Exception):
                        sink.flush()
                item.done.set()
                continue

            record: LogRecord = item  # type: ignore[assignment]
            for sink in sinks:
                if record.level >= sink.level:
                    with contextlib.suppress(Exception):
                        sink.write(record)

            # Flush when the queue is momentarily empty so log lines appear
            # immediately in interactive terminals (one syscall per quiet period,
            # not one per record).
            if q.empty():
                for sink in sinks:
                    with contextlib.suppress(Exception):
                        sink.flush()

    def _shutdown(self, sinks: list[Sink]) -> None:
        """Report dropped records, flush and close all sinks."""
        with self._drop_lock:
            dropped = self._dropped

        if dropped:
            warning = LogRecord(
                ts=time.time(),
                level=30,
                name="agentseed.infra.logging",
                message=f"Logger dropped {dropped} records due to queue overflow",
                fields={},
            )
            for sink in sinks:
                with contextlib.suppress(Exception):
                    sink.write(warning)

        for sink in sinks:
            try:
                sink.flush()
                sink.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
