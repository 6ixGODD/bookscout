"""IndexProvider descriptor for the Chunk index."""

from __future__ import annotations

import typing as t

from bookscout.doccompiler.index_provider import IndexProvider

if t.TYPE_CHECKING:
    import pathlib

    from bookscout.books import BooksStore
    from bookscout.doccompiler import Indexer
    from bookscout.index.chunk import ChunkIndexer
    from bookscout.index.chunk import ChunkStore
    from bookscout.logging import Logger
    from bookscout.tools import BaseTool


def _indexer_factory(logger: Logger, books_store: BooksStore, **kw: t.Any) -> Indexer:
    from bookscout.llm import ChatModel

    from . import ChunkIndexer

    return ChunkIndexer(
        logger=logger,
        books_store=books_store,
        embedding=kw["embedding"],
        vector_store=kw["vector_store"],
        estimate_token_fn=ChatModel.estimate_token,
    )


# pylint: disable-next=unused-argument
def _store_factory(db_path: pathlib.Path, logger: Logger, **_kw: t.Any) -> ChunkStore:
    from . import ChunkStore

    return ChunkStore(logger=logger, db_path=db_path)


# pylint: disable-next=unused-argument
def _tool_factory(indexer: ChunkIndexer, store: ChunkStore, **_kw: t.Any) -> list[BaseTool]:
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
