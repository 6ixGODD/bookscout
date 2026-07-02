"""Summary Index retrieval tools — BaseTool implementations for MCP exposure."""

from __future__ import annotations

import json
import pathlib
import typing as t
from typing import Annotated

from bookscout.tools import BaseTool
from bookscout.tools import Property

from . import SummaryStore

if t.TYPE_CHECKING:
    from bookscout.logging import Logger


class GetSummaryTool(  # type: ignore[call-arg]
    BaseTool,
    name="get_summary",
    description="Get the summary of a specific node. Returns the LLM-generated summary text for that node.",
):
    """Tool: get_summary."""

    def __init__(self, store: SummaryStore) -> None:
        self._store = store

    async def __call__(
        self,
        book_id: Annotated[str, Property(description="The book ID")],
        node_id: Annotated[str, Property(description="The node ID to get the summary for")],
    ) -> str:
        entry = await self._store.get_summary(book_id, node_id)
        if entry is None:
            return "No summary found for this node."
        return json.dumps(
            {
                "node_id": entry.node_id,
                "node_title": entry.node_title,
                "level": entry.level,
                "summary": entry.summary_text,
            },
            ensure_ascii=False,
        )


class ListSummariesTool(  # type: ignore[call-arg]
    BaseTool,
    name="list_summaries",
    description="List all summaries for a book. Returns an array of node summaries with their node IDs, titles, levels, and summary text.",
):
    """Tool: list_summaries."""

    def __init__(self, store: SummaryStore) -> None:
        self._store = store

    async def __call__(
        self,
        book_id: Annotated[str, Property(description="The book ID")],
    ) -> str:
        entries = await self._store.list_summaries(book_id)
        return json.dumps(
            [
                {
                    "node_id": e.node_id,
                    "node_title": e.node_title,
                    "level": e.level,
                    "summary": e.summary_text,
                }
                for e in entries
            ],
            ensure_ascii=False,
        )


def create_summary_tools(
    logger: Logger,
    db_path: pathlib.Path,
) -> list[BaseTool]:
    """Create summary retrieval tools.

    Args:
        logger: Logger instance.
        db_path: Path to the summary SQLite database.

    Returns:
        List of BaseTool instances. The SummaryStore is created but
        startup() must be called separately before use.
    """
    store = SummaryStore(logger=logger, db_path=db_path)
    return [GetSummaryTool(store), ListSummariesTool(store)]


__all__ = [
    "GetSummaryTool",
    "ListSummariesTool",
    "create_summary_tools",
]
