"""IndexProvider descriptor for the Graph index."""

from __future__ import annotations

from bookscout.doccompiler.index_provider import IndexProvider


def _indexer_factory(logger, books_store, **kw):
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


def _store_factory(db_path, logger, **kw):  # noqa: ARG001
    from .__init__ import GraphStore

    return GraphStore(logger=logger, db_path=db_path)


def _tool_factory(indexer, store, **kw):  # noqa: ARG001
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
