"""IndexProvider descriptor for the Summary index."""

from __future__ import annotations

from bookscout.doccompiler.index_provider import IndexProvider


def _indexer_factory(logger, books_store, **kw):
    from .__init__ import SummaryIndexer

    return SummaryIndexer(
        logger=logger,
        books_store=books_store,
        model=kw["llm"],
    )


def _store_factory(db_path, logger, **kw):  # noqa: ARG001
    from .__init__ import SummaryStore

    return SummaryStore(logger=logger, db_path=db_path)


def _tool_factory(indexer, store, **kw):  # noqa: ARG001
    # create_summary_tools takes (logger, db_path) and internally builds its own
    # SummaryStore per tool; the toolset's _startup_hidden_summary_stores starts them.
    from .tools import create_summary_tools

    return create_summary_tools(kw["logger"], kw["db_path"])


INDEX_PROVIDER = IndexProvider(
    index_type="summary",
    display_name="Summary",
    short_letter="s",
    requires_vector_store=False,
    default_enabled=True,
    indexer_factory=_indexer_factory,
    tool_factory=_tool_factory,
    store_factory=_store_factory,
    db_path_name="summary",
    description="Book-level digest; cheap, good for high-level questions",
)
