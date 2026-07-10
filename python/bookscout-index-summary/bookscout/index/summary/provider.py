"""IndexProvider descriptor for the Summary index."""

from __future__ import annotations

import typing as t

from bookscout.doccompiler.index_provider import IndexContext
from bookscout.doccompiler.index_provider import IndexProvider

if t.TYPE_CHECKING:
    from bookscout.doccompiler import Indexer
    from bookscout.index.summary import SummaryStore
    from bookscout.tools import BaseTool


def _indexer_factory(ctx: IndexContext) -> Indexer:
    from . import SummaryIndexer

    return SummaryIndexer(
        logger=ctx.logger,
        books_store=ctx.books_store,
        model=ctx.llm,
    )


def _store_factory(ctx: IndexContext) -> SummaryStore:
    from . import SummaryStore

    return SummaryStore(logger=ctx.logger, db_path=ctx.db_path)


def _tool_factory(indexer: t.Any, store: t.Any, ctx: IndexContext) -> list[BaseTool]:  # noqa: ARG001
    # create_summary_tools takes (logger, db_path) and internally builds its own
    # SummaryStore per tool; the toolset's _startup_hidden_summary_stores starts them.
    from .tools import create_summary_tools

    return create_summary_tools(ctx.logger, ctx.db_path)


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
