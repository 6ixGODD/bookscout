"""Compiler tools — compile/index/progress tools for MCP exposure.

These tools wrap the TaskManager, enabling async compile and index
operations with progress polling. Tools return task_id immediately
(non-blocking); the LLM polls get_task_progress for status.
"""

from __future__ import annotations

import json
from typing import Annotated

from bookscout.tools import BaseTool
from bookscout.tools import Property

from .task_manager import TaskManager


class CompileTool(  # type: ignore[call-arg]
    BaseTool,
    name="compile",
    description="Start compiling a source document (EPUB or PDF) into a BookScout ontology. Returns a task_id immediately — use get_task_progress to poll for completion. This is a non-blocking call.",
):
    """Tool: compile."""

    def __init__(self, task_manager: TaskManager) -> None:
        self._tm = task_manager

    async def __call__(
        self,
        source_path: Annotated[str, Property(description="Absolute path to the source file (EPUB or PDF)")],
        book_id: Annotated[
            str | None, Property(description="Optional book ID; auto-generated if not provided", default=None)
        ] = None,
    ) -> str:
        task_id = await self._tm.start_compile(source_path, book_id)
        return json.dumps({
            "task_id": task_id,
            "status": "started",
            "message": "Use get_task_progress to poll for completion.",
        })


class BuildIndexesTool(  # type: ignore[call-arg]
    BaseTool,
    name="build_indexes",
    description="Start building derived indexes (summary, chunk, graph) for a book. Returns a task_id for progress polling. Non-blocking.",
):
    """Tool: build_indexes."""

    def __init__(self, task_manager: TaskManager) -> None:
        self._tm = task_manager

    async def __call__(
        self,
        book_id: Annotated[str, Property(description="The book ID to build indexes for")],
        index_types: Annotated[
            list[str] | None,
            Property(
                description="Which indexes to build: 'summary', 'chunk', 'graph'. If not specified, builds all.",
                default=None,
            ),
        ] = None,
    ) -> str:
        task_id = await self._tm.start_index(book_id, index_types)
        return json.dumps({
            "task_id": task_id,
            "status": "started",
            "message": "Use get_task_progress to poll for completion.",
        })


class GetTaskProgressTool(  # type: ignore[call-arg]
    BaseTool,
    name="get_task_progress",
    description="Poll the progress of a compile or index task. Returns status, stage, percentage, ETA, and result data. Poll periodically until status is 'succeeded' or 'failed'.",
):
    """Tool: get_task_progress."""

    def __init__(self, task_manager: TaskManager) -> None:
        self._tm = task_manager

    async def __call__(
        self,
        task_id: Annotated[str, Property(description="The task ID returned by compile or build_indexes")],
    ) -> str:
        progress = self._tm.get_progress(task_id)
        if progress is None:
            return json.dumps({"error": "Task not found", "task_id": task_id})
        return json.dumps(
            {
                "task_id": progress.task_id,
                "task_type": progress.task_type,
                "status": progress.status,
                "stage": progress.stage,
                "percentage": progress.percentage,
                "processed": progress.processed,
                "total": progress.total,
                "eta_seconds": progress.eta_seconds,
                "elapsed_seconds": progress.elapsed_seconds,
                "error": progress.error,
                "result": progress.result,
            },
            ensure_ascii=False,
        )


class ListTasksTool(  # type: ignore[call-arg]
    BaseTool,
    name="list_tasks",
    description="List all tasks (compile and index) with their current progress. Useful for checking what's running.",
):
    """Tool: list_tasks."""

    def __init__(self, task_manager: TaskManager) -> None:
        self._tm = task_manager

    async def __call__(self) -> str:
        tasks = self._tm.list_tasks()
        return json.dumps(
            [
                {
                    "task_id": p.task_id,
                    "task_type": p.task_type,
                    "status": p.status,
                    "stage": p.stage,
                    "percentage": p.percentage,
                    "eta_seconds": p.eta_seconds,
                }
                for p in tasks
            ],
            ensure_ascii=False,
        )


def create_compiler_tools(task_manager: TaskManager) -> list[BaseTool]:
    """Create compiler tools bound to a TaskManager.

    Args:
        task_manager: An initialized TaskManager.

    Returns:
        List of BaseTool instances for compiler operations.
    """
    return [
        CompileTool(task_manager),
        BuildIndexesTool(task_manager),
        GetTaskProgressTool(task_manager),
        ListTasksTool(task_manager),
    ]


__all__ = [
    "BuildIndexesTool",
    "CompileTool",
    "GetTaskProgressTool",
    "ListTasksTool",
    "create_compiler_tools",
]
