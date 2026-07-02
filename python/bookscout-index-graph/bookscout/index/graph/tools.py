"""Graph Index retrieval tools — BaseTool implementations for MCP exposure."""

from __future__ import annotations

import json
from typing import Annotated

from bookscout.tools import BaseTool
from bookscout.tools import Property

from . import GraphIndexer
from . import GraphStore


class GraphEntityFirstTool(  # type: ignore[call-arg]
    BaseTool,
    name="graph_entity_first_retrieval",
    description="Entity-first graph retrieval: vector-search entities matching the query, then expand to their relationships via multi-hop. Returns entities with their relationships and source chunk info.",
):
    """Tool: graph_entity_first_retrieval."""

    def __init__(self, indexer: GraphIndexer, store: GraphStore) -> None:
        self._indexer = indexer
        self._store = store

    async def __call__(
        self,
        query: Annotated[str, Property(description="The search query")],
        book_id: Annotated[str, Property(description="The book ID")],
        top_k: Annotated[int, Property(description="Number of seed entities", default=5)] = 5,
        max_hop: Annotated[int, Property(description="Max multi-hop depth", default=2)] = 2,
    ) -> str:
        results = await self._indexer.entity_first_retrieval(
            query,
            book_id,
            self._store,
            top_k=top_k,
            max_hop=max_hop,
        )
        return json.dumps(results, ensure_ascii=False)


class GraphRelFirstTool(  # type: ignore[call-arg]
    BaseTool,
    name="graph_relationship_first_retrieval",
    description="Relationship-first graph retrieval: vector-search relationships matching the query, then resolve source/target entities via multi-hop. Returns relationships with connected entities.",
):
    """Tool: graph_relationship_first_retrieval."""

    def __init__(self, indexer: GraphIndexer, store: GraphStore) -> None:
        self._indexer = indexer
        self._store = store

    async def __call__(
        self,
        query: Annotated[str, Property(description="The search query")],
        book_id: Annotated[str, Property(description="The book ID")],
        top_k: Annotated[int, Property(description="Number of seed relationships", default=5)] = 5,
        max_hop: Annotated[int, Property(description="Max multi-hop depth", default=2)] = 2,
    ) -> str:
        results = await self._indexer.relationship_first_retrieval(
            query,
            book_id,
            self._store,
            top_k=top_k,
            max_hop=max_hop,
        )
        return json.dumps(results, ensure_ascii=False)


class GraphFtsEntityTool(  # type: ignore[call-arg]
    BaseTool,
    name="graph_fts_entity_retrieval",
    description="FTS-based graph retrieval: full-text search entities, then expand relationships via multi-hop. Use for keyword-based entity search.",
):
    """Tool: graph_fts_entity_retrieval."""

    def __init__(self, indexer: GraphIndexer, store: GraphStore) -> None:
        self._indexer = indexer
        self._store = store

    async def __call__(
        self,
        query: Annotated[str, Property(description="The FTS search query")],
        book_id: Annotated[str, Property(description="The book ID")],
        limit: Annotated[int, Property(description="Max entities from FTS", default=10)] = 10,
        max_hop: Annotated[int, Property(description="Max multi-hop depth", default=1)] = 1,
    ) -> str:
        results = await self._indexer.fts_entity_retrieval(
            query,
            book_id,
            self._store,
            limit=limit,
            max_hop=max_hop,
        )
        return json.dumps(results, ensure_ascii=False)


class GetEntitiesTool(  # type: ignore[call-arg]
    BaseTool,
    name="get_entities",
    description="List all entities in a book's knowledge graph. Returns entity names, types, tags, summaries.",
):
    """Tool: get_entities."""

    def __init__(self, store: GraphStore) -> None:
        self._store = store

    async def __call__(
        self,
        book_id: Annotated[str, Property(description="The book ID")],
    ) -> str:
        entities = await self._store.get_all_entities(book_id)
        return json.dumps(
            [
                {
                    "id": e.id,
                    "name": e.name,
                    "type": e.entity_type.value,
                    "tags": e.tags,
                    "summary": e.summary,
                    "source_chunk_id": e.source_chunk_id,
                    "source_node_id": e.source_node_id,
                }
                for e in entities
            ],
            ensure_ascii=False,
        )


class GetRelationshipsTool(  # type: ignore[call-arg]
    BaseTool,
    name="get_relationships",
    description="List all relationships in a book's knowledge graph. Returns relationship types, source/target entity IDs, summaries.",
):
    """Tool: get_relationships."""

    def __init__(self, store: GraphStore) -> None:
        self._store = store

    async def __call__(
        self,
        book_id: Annotated[str, Property(description="The book ID")],
    ) -> str:
        relationships = await self._store.get_all_relationships(book_id)
        return json.dumps(
            [
                {
                    "id": r.id,
                    "source_entity_id": r.source_entity_id,
                    "target_entity_id": r.target_entity_id,
                    "relation_type": r.relation_type,
                    "summary": r.summary,
                    "claims": r.claims,
                    "source_chunk_id": r.source_chunk_id,
                    "source_node_id": r.source_node_id,
                }
                for r in relationships
            ],
            ensure_ascii=False,
        )


def create_graph_tools(
    indexer: GraphIndexer,
    store: GraphStore,
) -> list[BaseTool]:
    """Create graph retrieval tools.

    Args:
        indexer: A GraphIndexer (for vector-based retrieval).
        store: An open GraphStore (for FTS and listing).

    Returns:
        List of BaseTool instances.
    """
    return [
        GraphEntityFirstTool(indexer, store),
        GraphRelFirstTool(indexer, store),
        GraphFtsEntityTool(indexer, store),
        GetEntitiesTool(store),
        GetRelationshipsTool(store),
    ]


__all__ = [
    "GetEntitiesTool",
    "GetRelationshipsTool",
    "GraphEntityFirstTool",
    "GraphFtsEntityTool",
    "GraphRelFirstTool",
    "create_graph_tools",
]
