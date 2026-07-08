"""Pure data types for the progress monitor — no I/O, no locking.

A :class:`TaskSnapshot` is an immutable view of one task at a point in time,
produced by :meth:`Monitor.snapshot`. The renderer turns it into pixels; GUI
clients / metrics exporters turn it into whatever they need.
"""

from __future__ import annotations

import typing as t


class TaskStatus:
    """Lifecycle states a task may be in."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class TaskSnapshot(t.NamedTuple):
    """An immutable view of one task in the monitor tree.

    Attributes:
        id: Stable task id (unique within the monitor).
        label: Human-readable label shown on the progress bar.
        total: Total units of work, or ``0`` for an indeterminate task.
        completed: Units completed so far (clamped to ``[0, total]``).
        status: One of :class:`TaskStatus` constants.
        parent_id: Parent task id, or ``None`` for a top-level task.
        depth: Tree depth (0 for roots) — used for indentation.
        started_at: ``time.perf_counter()`` when the task started, or ``None``.
        finished_at: ``time.perf_counter()`` when it finished, or ``None``.
        error: Failure message when ``status == FAILED``, else ``None``.
        eta_seconds: Estimated seconds remaining based on observed rate, or
            ``None`` when not estimable (no progress yet, or done).
    """

    id: str
    label: str
    total: float
    completed: float
    status: str
    parent_id: str | None
    depth: int
    started_at: float | None
    finished_at: float | None
    error: str | None
    eta_seconds: float | None


__all__ = ["TaskSnapshot", "TaskStatus"]
