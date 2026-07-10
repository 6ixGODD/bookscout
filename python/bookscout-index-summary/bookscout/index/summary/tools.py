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
    description=(
        "List summaries for a book, optionally filtered to specific nodes. "
        "Prefer passing node_ids to avoid returning all summaries at once — "
        "use get_children or list_nodes_by_level first to discover node IDs, "
        "then pass them here. Returns an array of {node_id, node_title, level, summary}."
    ),
):
    """Tool: list_summaries."""

    def __init__(self, store: SummaryStore) -> None:
        self._store = store

    async def __call__(
        self,
        book_id: Annotated[str, Property(description="The book ID")],
        node_ids: Annotated[
            list[str] | None,
            Property(
                description="Optional list of node IDs to filter summaries. If omitted, returns ALL summaries (expensive)."
            ),
        ] = None,
    ) -> str:
        entries = await self._store.list_summaries(book_id, node_ids=node_ids)
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
