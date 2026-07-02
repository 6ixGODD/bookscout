"""TaskManager — async compile/index task management with progress tracking.

Manages long-running compilation and index-building tasks in memory.
Each task runs as an asyncio.Task in the background and can be polled
for progress (percentage, ETA, current stage).
"""

from __future__ import annotations

import asyncio
import dataclasses
import pathlib
import time
import typing as t

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin

if t.TYPE_CHECKING:
    from bookscout.books import BooksStore
    from bookscout.doccompiler import Builder
    from bookscout.doccompiler import DocParser
    from bookscout.doccompiler.indexer import Indexer
    from bookscout.llm import ChatModel
    from bookscout.logging import Logger
    from bookscout.vectorstore.lancedb import LanceDBStore


@dataclasses.dataclass(slots=True)
class TaskProgress:
    """Progress snapshot for a background task.

    Attributes:
        task_id: Unique task identifier.
        task_type: "compile" or "index".
        status: "pending", "running", "succeeded", "failed".
        stage: Current stage (e.g. "parse_source", "build_ontology").
        percentage: 0-100 progress estimate.
        processed: Items processed in current stage.
        total: Total items in current stage.
        eta_seconds: Estimated time remaining (None if unknown).
        elapsed_seconds: Time elapsed since task start.
        error: Error message if failed.
        result: Result data if succeeded (e.g. book_id).
    """

    task_id: str
    task_type: str
    status: str
    stage: str
    percentage: float
    processed: int
    total: int
    eta_seconds: float | None
    elapsed_seconds: float
    error: str
    result: dict[str, t.Any]


@dataclasses.dataclass(slots=True)
class _TaskState:
    """Internal mutable state for a running task."""

    task_id: str
    task_type: str
    status: str
    stage: str
    processed: int
    total: int
    started_at: float
    error: str
    result: dict[str, t.Any]
    asyncio_task: asyncio.Task[t.Any] | None


class TaskManager(LoggingMixin, AsyncResourceMixin):
    """Manages async compile/index tasks with progress tracking.

    Tasks run as background asyncio.Tasks. Progress is tracked in-memory
    and can be polled via :meth:`get_progress`.

    Args:
        logger: Logger instance.
        books_store: BooksStore for ontology persistence.
        parser: Document parser.
        builder: Ontology builder.
        llm_model: Optional ChatModel (stateless) for LLM features.
        indexers: Optional list of Indexers for derived layer building.
        vector_store: Optional LanceDB store (for indexers that need it).
        workspace_base: Base directory for book workspaces.
    """

    def __init__(
        self,
        logger: Logger,
        books_store: BooksStore,
        parser: DocParser,
        builder: Builder,
        llm_model: ChatModel | None = None,
        indexers: t.Sequence[Indexer] | None = None,
        vector_store: LanceDBStore | None = None,
        workspace_base: pathlib.Path | str = "output",
    ) -> None:
        super().__init__(logger=logger)
        self._books_store = books_store
        self._parser = parser
        self._builder = builder
        self._llm_model = llm_model
        self._indexers = list(indexers) if indexers else []
        self._vector_store = vector_store
        self._workspace_base = pathlib.Path(workspace_base).resolve()
        self._tasks: dict[str, _TaskState] = {}

    async def startup(self) -> None:
        """Start the underlying stores."""
        await self._books_store.startup()
        await self._parser.startup()
        await self._builder.startup()
        if self._llm_model is not None:
            await self._llm_model.startup()
        for indexer in self._indexers:
            await indexer.startup()
        if self._vector_store is not None:
            await self._vector_store.init()
        await super().startup()
        self.logger.info("task manager started")

    async def shutdown(self) -> None:
        """Cancel all running tasks and shut down stores."""
        for state in self._tasks.values():
            if state.asyncio_task is not None and not state.asyncio_task.done():
                state.asyncio_task.cancel()
        if self._vector_store is not None:
            await self._vector_store.close()
        for indexer in self._indexers:
            await indexer.shutdown()
        if self._llm_model is not None:
            await self._llm_model.shutdown()
        await self._builder.shutdown()
        await self._parser.shutdown()
        await self._books_store.shutdown()
        await super().shutdown()

    async def start_compile(
        self,
        source_path: str,
        book_id: str | None = None,
    ) -> str:
        """Start a compilation task in the background.

        Args:
            source_path: Absolute path to the source file (EPUB, PDF).
            book_id: Optional book ID; auto-generated if None.

        Returns:
            task_id for progress polling.
        """
        from bookscout.core.lib.utils import gen_id

        task_id = gen_id(prefix="task_")
        state = _TaskState(
            task_id=task_id,
            task_type="compile",
            status="pending",
            stage="load_source",
            processed=0,
            total=0,
            started_at=time.monotonic(),
            error="",
            result={},
            asyncio_task=None,
        )
        self._tasks[task_id] = state

        state.asyncio_task = asyncio.create_task(
            self._run_compile(task_id, state, pathlib.Path(source_path), book_id),
        )
        self.logger.info("compile task started", task_id=task_id, source=source_path)
        return task_id  # type: ignore[no-any-return]

    async def start_index(
        self,
        book_id: str,
        index_types: list[str] | None = None,
    ) -> str:
        """Start an index-building task in the background.

        Args:
            book_id: The book to build indexes for.
            index_types: Which indexes to build (e.g. ["summary", "chunk", "graph"]).
                If None, builds all configured indexers.

        Returns:
            task_id for progress polling.
        """
        from bookscout.core.lib.utils import gen_id

        task_id = gen_id(prefix="task_")
        state = _TaskState(
            task_id=task_id,
            task_type="index",
            status="pending",
            stage="build_indexes",
            processed=0,
            total=0,
            started_at=time.monotonic(),
            error="",
            result={},
            asyncio_task=None,
        )
        self._tasks[task_id] = state

        state.asyncio_task = asyncio.create_task(
            self._run_index(task_id, state, book_id, index_types),
        )
        self.logger.info("index task started", task_id=task_id, book_id=book_id)
        return task_id  # type: ignore[no-any-return]

    def get_progress(self, task_id: str) -> TaskProgress | None:
        """Get the current progress of a task.

        Args:
            task_id: The task ID.

        Returns:
            A :class:`TaskProgress` snapshot, or None if task not found.
        """
        state = self._tasks.get(task_id)
        if state is None:
            return None

        elapsed = time.monotonic() - state.started_at
        percentage = 0.0
        eta: float | None = None

        if state.total > 0:
            percentage = (state.processed / state.total) * 100.0
            if state.processed > 0 and state.status == "running":
                rate = state.processed / elapsed
                remaining = state.total - state.processed
                if rate > 0:
                    eta = remaining / rate

        # If task is done, percentage = 100.
        if state.status in ("succeeded", "failed"):
            percentage = 100.0
            eta = 0.0

        return TaskProgress(
            task_id=state.task_id,
            task_type=state.task_type,
            status=state.status,
            stage=state.stage,
            percentage=round(percentage, 1),
            processed=state.processed,
            total=state.total,
            eta_seconds=round(eta, 1) if eta is not None else None,
            elapsed_seconds=round(elapsed, 1),
            error=state.error,
            result=state.result,
        )

    def list_tasks(self) -> list[TaskProgress]:
        """List progress for all tasks.

        Returns:
            List of TaskProgress snapshots.
        """
        return [self.get_progress(tid) for tid in self._tasks if self.get_progress(tid) is not None]  # type: ignore[misc]

    async def _run_compile(
        self,
        task_id: str,
        state: _TaskState,
        source_path: pathlib.Path,
        book_id: str | None,
    ) -> None:
        """Run a compilation task."""
        from .compiler import Compiler

        try:
            state.status = "running"
            state.stage = "load_source"

            compiler = Compiler(
                logger=self.logger,
                parser=self._parser,
                books_store=self._books_store,
                builder=self._builder,
                llm_model=self._llm_model,
                indexers=self._indexers if self._indexers else None,
                workspace_base=self._workspace_base,
            )

            # Poll metrics while compiling.
            result = await compiler.compile(source_path, book_id)

            state.status = "succeeded"
            state.stage = "finished"
            state.result = {
                "book_id": result.book.id,
                "title": result.book.title,
                "node_count": len(result.nodes),
                "workspace": str(result.workspace.root),
            }
            self.logger.info("compile task succeeded", task_id=task_id, book_id=result.book.id)

        except Exception as e:  # pylint: disable=broad-exception-caught
            state.status = "failed"
            state.error = str(e)
            self.logger.error("compile task failed", task_id=task_id, error=str(e))

    async def _run_index(
        self,
        task_id: str,
        state: _TaskState,
        book_id: str,
        index_types: list[str] | None,
    ) -> None:
        """Run an index-building task."""
        from .workspace import BookWorkspace

        try:
            state.status = "running"
            state.stage = "build_indexes"

            # Reconstruct the workspace from the book_id.
            # The workspace base is the parent of the book directory.
            book_dir = self._workspace_base / book_id
            if not book_dir.exists():
                # Try to find it — the workspace_base might be the book's parent.
                raise RuntimeError(f"Book workspace not found: {book_dir}")

            workspace = BookWorkspace.create(self._workspace_base, book_id)

            # Select which indexers to run.
            indexers_to_run = self._indexers
            if index_types:
                indexers_to_run = [i for i in self._indexers if i.index_type in index_types]

            state.total = len(indexers_to_run)
            state.processed = 0

            results: dict[str, t.Any] = {}
            for indexer in indexers_to_run:
                state.stage = f"build_{indexer.index_type}_index"
                self.logger.info(
                    "building index",
                    task_id=task_id,
                    index_type=indexer.index_type,
                )
                result = await indexer.build_index(book_id, workspace)
                results[indexer.index_type] = result.count
                state.processed += 1

            state.status = "succeeded"
            state.stage = "finished"
            state.result = results
            self.logger.info("index task succeeded", task_id=task_id, results=results)

        except Exception as e:  # pylint: disable=broad-exception-caught
            state.status = "failed"
            state.error = str(e)
            self.logger.error("index task failed", task_id=task_id, error=str(e))


__all__ = [
    "TaskManager",
    "TaskProgress",
]
