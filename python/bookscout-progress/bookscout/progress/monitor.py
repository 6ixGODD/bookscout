"""The :class:`Monitor` — a thread-safe task tree.

Pipeline code (graphrag, cases, kernel) drives it from concurrent
``asyncio.gather`` tasks and plain sequential code alike. The monitor owns
no rendering state; a :class:`~bookscout.progress.Renderer` polls
:meth:`snapshot` on its own cadence and turns the snapshot tree into pixels.

Thread safety:
    Every mutating call takes a single ``threading.Lock``. ``asyncio`` tasks
    run on one thread, but embeddings / completion clients may use worker
    threads, and a GUI renderer runs on yet another. The lock is held for the
    minimum span (no I/O under it), so contention is negligible.

ETA:
    Each running task tracks ``completed`` and wall-clock elapsed. ETA is
    ``remaining / rate`` where ``rate = completed / elapsed``. ``None`` when
    not yet estimable (no progress, or done/failed). The rate is recomputed
    per snapshot from the running totals — no rolling window — so it reflects
    the whole-task average. This is robust against jitter and avoids state
    that has to be aged.

Parent aggregation:
    A parent task with its own ``total`` reports that total verbatim; one
    created with ``total=0`` auto-sums its children's totals and completeds
    so a phase like "merge" can show overall progress without the caller
    manually computing it.
"""

from __future__ import annotations

import contextlib
import threading
import time
import typing as t

from bookscout.core.lib.utils import gen_id

from .schema import TaskSnapshot
from .schema import TaskStatus


class _Task:
    """Mutable task record held by the monitor. Guarded by the monitor lock."""

    __slots__ = (
        "_auto_total",
        "child_ids",
        "completed",
        "depth",
        "error",
        "finished_at",
        "id",
        "label",
        "parent_id",
        "started_at",
        "status",
        "total",
    )

    def __init__(self, task_id: str, label: str, parent_id: str | None, depth: int, total: float) -> None:
        self.id = task_id
        self.label = label
        self.total = float(total)
        self.completed = 0.0
        self.status = TaskStatus.PENDING
        self.parent_id = parent_id
        self.depth = depth
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.error: str | None = None
        self.child_ids: list[str] = []
        # When True, `total` is derived from children (auto-aggregate).
        self._auto_total = self.total == 0.0


class Monitor:
    """A thread-safe task tree that pipelines update and renderers poll.

    Example::

        mon = Monitor()
        root = mon.start("build", total=0)  # auto-aggregates children
        with mon.scope(root):
            extract = mon.start("extract", total=36)
            async for chunk in ...:
                ...  # do work
                mon.advance(extract, 1)
            mon.finish(extract)
        mon.finish(root)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, _Task] = {}
        # Stable insertion order so the tree renders top-down deterministically.
        self._order: list[str] = []

    def start(
        self,
        label: str,
        *,
        total: float = 0,
        parent_id: str | None = None,
        task_id: str | None = None,
    ) -> str:
        """Register a task, mark it RUNNING, and return its id.

        Args:
            label: Human-readable label for the progress bar.
            total: Total units of work. ``0`` means the task auto-aggregates
                its children's totals (a phase container); use a non-zero
                total for a leaf task with a known unit count.
            parent_id: Parent task id (creates a sub-task), or ``None`` for a
                root.
            task_id: Explicit id (must be unique). Auto-generated when
                omitted.
        """
        tid = task_id or gen_id(prefix="task_")
        depth = 0
        if parent_id is not None:
            parent = self._tasks.get(parent_id)
            if parent is None:
                raise KeyError(f"parent task {parent_id!r} does not exist")
            depth = parent.depth + 1
        task = _Task(tid, label, parent_id, depth, total)
        with self._lock:
            if tid in self._tasks:
                raise ValueError(f"task id {tid!r} already exists")
            task.status = TaskStatus.RUNNING
            task.started_at = time.perf_counter()
            self._tasks[tid] = task
            self._order.append(tid)
            if parent_id is not None:
                self._tasks[parent_id].child_ids.append(tid)
        return tid

    def advance(self, task_id: str, by: float = 1) -> None:
        """Add ``by`` units to the task's completed count (clamped to total)."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.completed = (
                min(task.completed + float(by), task.total) if task.total > 0 else task.completed + float(by)
            )

    def set_total(self, task_id: str, total: float) -> None:
        """Set or update a task's total (useful when the count is learnt late)."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.total = float(total)
            task._auto_total = False
            task.completed = min(task.completed, task.total)

    def finish(self, task_id: str) -> None:
        """Mark a task DONE and clamp completed to total."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = TaskStatus.DONE
            task.finished_at = time.perf_counter()
            if task.total > 0:
                task.completed = task.total

    def fail(self, task_id: str, error: str | None = None) -> None:
        """Mark a task FAILED with an optional error message."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = TaskStatus.FAILED
            task.finished_at = time.perf_counter()
            task.error = error

    def update_label(self, task_id: str, label: str) -> None:
        """Replace a task's label (e.g. "extract" → "extract (36 chunks)")."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.label = label

    @contextlib.contextmanager
    def scope(self, task_id: str) -> t.Generator[str]:
        """Context manager that finishes the task on exit (success or fail).

        On normal exit the task is marked DONE; on an exception it is marked
        FAILED with the exception's repr, then the exception re-raises.
        """
        try:
            yield task_id
        except BaseException as exc:
            self.fail(task_id, error=repr(exc))
            raise
        self.finish(task_id)

    def snapshot(self) -> list[TaskSnapshot]:
        """Return a list of :class:`TaskSnapshot` in depth-first tree order.

        Roots come first, then each root's subtree (children in insertion
        order, recursively). Auto-aggregate parents report ``total`` /
        ``completed`` summed from their children (overriding their own
        counters). Renderers can indent by ``depth`` and draw sequentially.
        """
        now = time.perf_counter()
        with self._lock:
            # Pre-compute child sums for auto-aggregate parents.
            agg_total: dict[str, float] = {}
            agg_done: dict[str, float] = {}
            for tid in self._order:
                task = self._tasks[tid]
                if task.parent_id is not None and self._tasks[task.parent_id]._auto_total:
                    agg_total[task.parent_id] = agg_total.get(task.parent_id, 0.0) + (
                        task.total if not task._auto_total else agg_total.get(tid, 0.0)
                    )
                    agg_done[task.parent_id] = agg_done.get(task.parent_id, 0.0) + (
                        task.completed if not task._auto_total else agg_done.get(tid, 0.0)
                    )

            # Depth-first traversal rooted at the top-level tasks, preserving
            # child insertion order. ``_order`` is already insertion-ordered;
            # we just re-walk it as a tree so children sit under their parent.
            roots = [tid for tid in self._order if self._tasks[tid].parent_id is None]

            out: list[TaskSnapshot] = []

            def _emit(tid: str) -> None:
                task = self._tasks[tid]
                if task._auto_total and task.child_ids:
                    total = agg_total.get(tid, 0.0)
                    completed = agg_done.get(tid, 0.0)
                else:
                    total = task.total
                    completed = task.completed
                eta = _eta(task, total, completed, now)
                out.append(
                    TaskSnapshot(
                        id=task.id,
                        label=task.label,
                        total=total,
                        completed=completed,
                        status=task.status,
                        parent_id=task.parent_id,
                        depth=task.depth,
                        started_at=task.started_at,
                        finished_at=task.finished_at,
                        error=task.error,
                        eta_seconds=eta,
                    )
                )
                for cid in task.child_ids:
                    _emit(cid)

            for rid in roots:
                _emit(rid)
            return out


def _eta(task: _Task, total: float, completed: float, now: float) -> float | None:
    """Estimate seconds remaining for a running task, or None if not estimable."""
    if task.status != TaskStatus.RUNNING or total <= 0 or completed <= 0:
        return None
    elapsed = now - (task.started_at or now)
    if elapsed <= 0:
        return None
    rate = completed / elapsed
    if rate <= 0:
        return None
    return max(0.0, (total - completed) / rate)


__all__ = ["Monitor", "MonitorScope"]


class MonitorScope:
    """Standalone context manager that creates a task and finishes it on exit.

    Cleaner than ``mon.scope(tid)`` when you want to create + scope a task in
    one expression::

        with MonitorScope(mon, "extract", total=36) as extract_id:
            ...
            mon.advance(extract_id, 1)

    On an exception the task is marked FAILED with the exception's repr, then
    the exception re-raises.
    """

    def __init__(
        self,
        monitor: Monitor,
        label: str,
        *,
        total: float = 0,
        parent_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        self._monitor = monitor
        self._label = label
        self._total = total
        self._parent_id = parent_id
        self._task_id: str | None = task_id
        self._owned_id: str | None = None

    def __enter__(self) -> str:
        self._owned_id = self._monitor.start(
            self._label,
            total=self._total,
            parent_id=self._parent_id,
            task_id=self._task_id,
        )
        return self._owned_id

    @property
    def id(self) -> str | None:
        """The owned task id, or ``None`` before ``__enter__`` / after
        ``__exit__``."""
        return self._owned_id

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: t.Any,
    ) -> None:
        if self._owned_id is None:
            return
        if exc_type is not None and exc_val is not None:
            self._monitor.fail(self._owned_id, error=repr(exc_val))
        else:
            self._monitor.finish(self._owned_id)
