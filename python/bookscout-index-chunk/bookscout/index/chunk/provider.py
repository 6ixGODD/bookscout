"""IndexProvider descriptor for the Chunk index."""

from __future__ import annotations

import typing as t

from bookscout.doccompiler.index_provider import IndexContext
from bookscout.doccompiler.index_provider import IndexProvider

if t.TYPE_CHECKING:
    from bookscout.doccompiler import Indexer
    from bookscout.index.chunk import ChunkIndexer
    from bookscout.index.chunk import ChunkStore
    from bookscout.tools import BaseTool


def _indexer_factory(ctx: IndexContext) -> Indexer:
    from bookscout.llm import ChatModel

    from . import ChunkIndexer

    return ChunkIndexer(
        logger=ctx.logger,
        books_store=ctx.books_store,
        embedding=ctx.embedding,
        vector_store=ctx.vector_store,
        estimate_token_fn=ChatModel.estimate_token,
    )


def _store_factory(ctx: IndexContext) -> ChunkStore:
    from . import ChunkStore

    return ChunkStore(logger=ctx.logger, db_path=ctx.db_path)


def _tool_factory(indexer: ChunkIndexer, store: ChunkStore, ctx: IndexContext) -> list[BaseTool]:  # noqa: ARG001
    from .tools import create_chunk_tools

    return create_chunk_tools(indexer, store)


INDEX_PROVIDER = IndexProvider(
    index_type="chunk",
    display_name="Chunk",
    short_letter="c",
    requires_vector_store=True,
    default_enabled=True,
    indexer_factory=_indexer_factory,
    tool_factory=_tool_factory,
    store_factory=_store_factory,
    db_path_name="chunks",
    description="Passage-level chunks for precise citation and semantic search",
)


# NOTE: db_path_name is "chunks" because BookWorkspace.index_db_path("chunks")
# resolves to indexes/chunks.sqlite, matching the existing conventions in
# ReadingModeConfig.resolved_chunk_db_path (filename "chunks.sqlite").
