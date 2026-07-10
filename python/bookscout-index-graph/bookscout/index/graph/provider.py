"""IndexProvider descriptor for the Graph index."""

from __future__ import annotations

import typing as t

from bookscout.doccompiler.index_provider import IndexProvider

if t.TYPE_CHECKING:
    import pathlib

    from bookscout.books import BooksStore
    from bookscout.index.graph import GraphIndexer
    from bookscout.index.graph import GraphStore
    from bookscout.logging import Logger


def _indexer_factory(logger: Logger, books_store: BooksStore, **kw: t.Any):
    from bookscout.llm import ChatModel

    from .__init__ import GraphIndexer

    return GraphIndexer(
        logger=logger,
        books_store=books_store,
        model=kw["llm"],
        embedding=kw["embedding"],
        vector_store=kw["vector_store"],
        estimate_token_fn=ChatModel.estimate_token,
    )


# pylint: disable-next=unused-argument
def _store_factory(db_path: pathlib.Path, logger: Logger, **kw: t.Any):  # noqa: ARG001
    from .__init__ import GraphStore

    return GraphStore(logger=logger, db_path=db_path)


# pylint: disable-next=unused-argument
def _tool_factory(indexer: GraphIndexer, store: GraphStore, **kw: t.Any):  # noqa: ARG001
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
