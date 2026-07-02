"""Chunk Index retrieval tools — BaseTool implementations for MCP exposure."""

from __future__ import annotations

import json
import typing as t
from typing import Annotated

from bookscout.tools import BaseTool
from bookscout.tools import Property

from . import ChunkIndexer
from . import ChunkStore

if t.TYPE_CHECKING:
    pass


class ChunkVectorSearchTool(  # type: ignore[call-arg]
    BaseTool,
    name="chunk_vector_search",
    description="Search chunks by semantic similarity (embedding-based). Returns matching chunks with their node_id for source node lookup.",
):
    """Tool: chunk_vector_search."""

    def __init__(self, indexer: ChunkIndexer) -> None:
        self._indexer = indexer

    async def __call__(
        self,
        query: Annotated[str, Property(description="The search query text")],
        book_id: Annotated[str, Property(description="The book ID to search within")],
        top_k: Annotated[int, Property(description="Maximum number of results", default=10)] = 10,
    ) -> str:
        results = await self._indexer.search_vector(query, book_id, top_k=top_k)
        return json.dumps(results, ensure_ascii=False)


class ChunkFtsSearchTool(  # type: ignore[call-arg]
    BaseTool,
    name="chunk_fts_search",
    description="Search chunks by full-text search (FTS5). Returns matching chunks with their node_id. Use for keyword-based search.",
):
    """Tool: chunk_fts_search."""

    def __init__(self, store: ChunkStore) -> None:
        self._store = store

    async def __call__(
        self,
        query: Annotated[str, Property(description="The FTS5 search query")],
        book_id: Annotated[str, Property(description="The book ID to filter results")],
        limit: Annotated[int, Property(description="Maximum number of results", default=10)] = 10,
    ) -> str:
        entries = await self._store.search_fts(query, limit=limit)
        results = [
            {
                "chunk_id": e.id,
                "node_id": e.node_id,
                "chunk_text": e.chunk_text,
                "content_offset": e.content_offset,
                "content_length": e.content_length,
            }
            for e in entries
            if e.book_id == book_id
        ]
        return json.dumps(results, ensure_ascii=False)


class GetChunksByNodeTool(  # type: ignore[call-arg]
    BaseTool,
    name="get_chunks_by_node",
    description="Get all chunks that belong to a specific node. Returns chunks ordered by their index within the node.",
):
    """Tool: get_chunks_by_node."""

    def __init__(self, store: ChunkStore) -> None:
        self._store = store

    async def __call__(
        self,
        node_id: Annotated[str, Property(description="The node ID")],
    ) -> str:
        entries = await self._store.get_chunks_by_node(node_id)
        return json.dumps(
            [
                {
                    "chunk_id": e.id,
                    "node_id": e.node_id,
                    "chunk_text": e.chunk_text,
                    "content_offset": e.content_offset,
                    "content_length": e.content_length,
                    "chunk_index": e.chunk_index,
                }
                for e in entries
            ],
            ensure_ascii=False,
        )


def create_chunk_tools(
    indexer: ChunkIndexer,
    store: ChunkStore,
) -> list[BaseTool]:
    """Create chunk retrieval tools.

    Args:
        indexer: A ChunkIndexer (for vector search).
        store: An open ChunkStore (for FTS and node-based lookup).

    Returns:
        List of BaseTool instances.
    """
    return [
        ChunkVectorSearchTool(indexer),
        ChunkFtsSearchTool(store),
        GetChunksByNodeTool(store),
    ]


__all__ = [
    "ChunkFtsSearchTool",
    "ChunkVectorSearchTool",
    "GetChunksByNodeTool",
    "create_chunk_tools",
]
