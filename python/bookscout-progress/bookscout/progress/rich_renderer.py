"""Rich ``Live`` renderer — task bars + a rolling log tail.

Layout (top to bottom inside one ``Live`` region)::

    ┌ bookscout-graphrag build ────────────────────────────────┐
    │ build      ━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:42 done   │
    │   chunk    ━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:02 done   │
    │   extract  ━━━━━━━━━━━━━━━━━━━━━━━  67% 0:03:12 12/36  │
    │   merge    ░░░░░░░░░░░░░░░░░░░░░░░   0% waiting        │
    └────────────────────────────────────────────────────────┘
    14:33:18 INFO  graphrag.PIPELINE  process_texts: text extracted ...
    14:33:20 INFO  graphrag.EXTRACT   chunk 18/36 done (12 entities)

Refresh cadence: a daemon thread polls :meth:`Monitor.snapshot` every
``refresh_per_second`` and swaps the renderable via ``Live.update``, so the
TUI updates even when the asyncio loop is busy inside a long LLM call (the
merge pass can sit in one ``await`` for tens of seconds). Rich's own
``Live`` timer is set to the same rate; our thread is the one that advances
the visible state, keeping renders monotonic.
"""

from __future__ import annotations

import threading
import time
import typing as t

try:
    from rich.console import Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import BarColumn
    from rich.progress import Progress
    from rich.progress import TaskProgressColumn
    from rich.progress import TextColumn
    from rich.progress import TimeElapsedColumn
    from rich.progress import TimeRemainingColumn
    from rich.text import Text

    _RICH_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised only without the tui extra
    _RICH_AVAILABLE = False

from .monitor import Monitor
from .renderers import Renderer
from .schema import TaskSnapshot
from .schema import TaskStatus

_LOG_TAIL_LINES = 8
_LEVEL_NAME: dict[int, str] = {10: "DEBUG", 20: "INFO", 30: "WARN", 40: "ERROR", 50: "CRIT"}
_LEVEL_STYLE: dict[int, str] = {10: "dim", 20: "cyan", 30: "yellow", 40: "red", 50: "bold red"}


class RichLiveRenderer(Renderer):
    """A Rich ``Live`` renderer drawing task bars + a rolling log tail.

    Args:
        monitor: The :class:`Monitor` to poll.
        title: Panel title (e.g. ``"bookscout-graphrag build"``).
        refresh_per_second: How often the refresh thread re-renders.
        log_tail_lines: How many recent log lines to show at the bottom.
    """

    def __init__(
        self,
        monitor: Monitor,
        *,
        title: str = "bookscout",
        refresh_per_second: float = 10.0,
        log_tail_lines: int = _LOG_TAIL_LINES,
    ) -> None:
        if not _RICH_AVAILABLE:
            raise RuntimeError("Install the tui extra: pip install 'bookscout-progress[tui]' (rich>=13)")
        self._monitor = monitor
        self._title = title
        self._refresh_per_second = refresh_per_second
        self._log_tail_lines = log_tail_lines
        self._live: Live | None = None
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        # Log tail buffer (newest last). Guarded by its own lock so the logging
        # writer thread can append while the render thread reads.
        self._log_lock = threading.Lock()
        self._log_buf: list[tuple[float, int, str, str, dict[str, t.Any]]] = []
        # Persistent Progress + monitor-id → rich-task-id mapping so elapsed
        # time accumulates across frames instead of resetting each render.
        self._progress: Progress | None = None
        self._rich_ids: dict[str, t.Any] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _start(self) -> None:
        # Fresh Progress + id map for this run (renderer is reusable).
        self._progress = self._build_progress()
        self._rich_ids = {}
        # Rich's Live asserts refresh_per_second > 0, so we feed it our refresh
        # rate. Our refresh thread is still the one that swaps the renderable
        # via update() (Rich's own timer just re-paints whatever is current),
        # which keeps the visible state monotonic across renders.
        self._live = Live(self._render(), refresh_per_second=self._refresh_per_second, transient=False)
        self._live.start()
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._refresh_loop, name="bookscout-progress", daemon=True)
        self._thread.start()

    def _stop(self) -> None:
        self._stop_evt.set()
        thr = self._thread
        if thr is not None:
            thr.join(timeout=2.0)
        # Final render so the terminal shows the completed state.
        if self._live is not None:
            self._live.update(self._render())
            self._live.stop()
            self._live = None
        self._thread = None
        self._progress = None
        self._rich_ids = {}

    # ------------------------------------------------------------------
    # Log tail
    # ------------------------------------------------------------------

    def push_log(self, ts: float, level: int, name: str, message: str, fields: dict[str, t.Any]) -> None:
        with self._log_lock:
            self._log_buf.append((ts, level, name, message, fields))
            if len(self._log_buf) > self._log_tail_lines * 4:
                # Trim to ~4x the visible tail so we keep some history without
                # unbounded growth between renders.
                del self._log_buf[: len(self._log_buf) - self._log_tail_lines * 4]

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def _refresh_loop(self) -> None:
        interval = 1.0 / self._refresh_per_second
        while not self._stop_evt.is_set():
            if self._live is not None:
                self._live.update(self._render())
            self._stop_evt.wait(interval)

    def _render(self) -> t.Any:
        snapshots = self._monitor.snapshot()
        if self._progress is None:
            self._progress = self._build_progress()
        progress = self._progress
        for snap in snapshots:
            rid = self._rich_ids.get(snap.id)
            if rid is None:
                rid = self._add_task(progress, snap)
                self._rich_ids[snap.id] = rid
            progress.update(
                rid,
                completed=snap.completed if snap.total > 0 else None,
                total=snap.total if snap.total > 0 else None,
                description=self._description(snap),
            )
        log_panel = self._render_log_tail()
        return Panel(Group(progress, log_panel), title=self._title, border_style="cyan")

    def _build_progress(self) -> Progress:
        return Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=None),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            expand=True,
        )

    def _add_task(self, progress: Progress, snap: TaskSnapshot) -> t.Any:
        indent = "  " * snap.depth
        return progress.add_task(
            f"{indent}{snap.label}",
            total=snap.total if snap.total > 0 else None,
            completed=snap.completed if snap.total > 0 else 0,
        )

    def _description(self, snap: TaskSnapshot) -> str:
        indent = "  " * snap.depth
        suffix = _status_suffix(snap)
        return f"{indent}{snap.label}{suffix}"

    def _render_log_tail(self) -> Text:
        with self._log_lock:
            tail = self._log_buf[-self._log_tail_lines :]
        text = Text()
        if not tail:
            text.append("(no logs yet)", style="dim")
            return text
        for ts, level, name, message, fields in tail:
            hh_mm_ss = _hh_mm_ss(ts)
            level_name = _LEVEL_NAME.get(level, str(level))
            style = _LEVEL_STYLE.get(level, "")
            text.append(f"{hh_mm_ss} ", style="dim")
            text.append(f"{level_name:<5} ", style=style)
            text.append(f"{name}  ", style="cyan")
            text.append(message)
            if fields:
                text.append(f"  {fields}", style="dim magenta")
            text.append("\n")
        return text


def _status_suffix(snap: TaskSnapshot) -> str:
    if snap.status == TaskStatus.DONE:
        return " [green]done[/green]"
    if snap.status == TaskStatus.FAILED:
        return f" [red]failed[/red]{f' ({snap.error})' if snap.error else ''}"
    if snap.status == TaskStatus.PENDING:
        return " [dim]waiting[/dim]"
    if 0 < snap.total <= snap.completed:
        return " [green]done[/green]"
    return ""


def _hh_mm_ss(ts: float) -> str:
    s = time.localtime(ts)
    return f"{s.tm_hour:02d}:{s.tm_min:02d}:{s.tm_sec:02d}"


__all__ = ["RichLiveRenderer"]
