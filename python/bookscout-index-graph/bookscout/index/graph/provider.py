"""IndexProvider descriptor for the Graph index."""

from __future__ import annotations

import typing as t

from bookscout.doccompiler.index_provider import IndexContext
from bookscout.doccompiler.index_provider import IndexProvider

if t.TYPE_CHECKING:
    from bookscout.doccompiler import Indexer
    from bookscout.index.graph import GraphIndexer
    from bookscout.index.graph import GraphStore
    from bookscout.tools import BaseTool


def _indexer_factory(ctx: IndexContext) -> Indexer:
    from bookscout.llm import ChatModel

    from . import GraphIndexer

    return GraphIndexer(
        logger=ctx.logger,
        books_store=ctx.books_store,
        model=ctx.llm,
        embedding=ctx.embedding,
        vector_store=ctx.vector_store,
        estimate_token_fn=ChatModel.estimate_token,
    )


def _store_factory(ctx: IndexContext) -> GraphStore:
    from . import GraphStore

    return GraphStore(logger=ctx.logger, db_path=ctx.db_path)


def _tool_factory(indexer: GraphIndexer, store: GraphStore, ctx: IndexContext) -> list[BaseTool]:  # noqa: ARG001
    from .tools import create_graph_tools

    return create_graph_tools(indexer, store)


INDEX_PROVIDER = IndexProvider(
    index_type="graph",
    display_name="Graph",
    short_letter="g",
    requires_vector_store=True,
    default_enabled=False,
    indexer_factory=_indexer_factory,
    tool_factory=_tool_factory,
    store_factory=_store_factory,
    db_path_name="graph",
    description="Relationship map between entities; slow and expensive",
)
