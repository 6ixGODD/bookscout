"""A no-op scope / monitor helpers for the ``monitor=None`` path.

Keeps pipeline code branch-light: instead of ``if mon: with mon.scope(...)``
everywhere, the pipeline grabs ``mon.scope(...) if mon else NullScope()`` and
the ``NullScope`` swallows the no-op case. Its ``id`` is ``None``, which the
monitor methods already treat as "no task" (``start``/``advance`` with a
``None`` parent are harmless; ``advance(None)`` is a no-op).
"""

from __future__ import annotations

import contextlib
import typing as t


class NullScope:
    """A context manager that owns no task. ``id`` is always ``None``.

    Pair with :class:`~bookscout.progress.Monitor` methods that accept
    ``parent_id=None`` / ``task_id=None`` so the no-monitor path needs no
    special-casing at call sites.
    """

    __slots__ = ()

    @property
    def id(self) -> None:
        return None

    def __enter__(self) -> NullScope:
        return self

    @contextlib.contextmanager
    def _cm(self) -> t.Generator[NullScope]:
        yield self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: t.Any,
    ) -> None:
        pass


__all__ = ["NullScope"]
