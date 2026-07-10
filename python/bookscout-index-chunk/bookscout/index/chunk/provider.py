"""IndexProvider descriptor for the Chunk index."""

from __future__ import annotations

from bookscout.doccompiler.index_provider import IndexProvider


def _indexer_factory(logger, books_store, **kw):
    from bookscout.llm import ChatModel

    from .__init__ import ChunkIndexer

    return ChunkIndexer(
        logger=logger,
        books_store=books_store,
        embedding=kw["embedding"],
        vector_store=kw["vector_store"],
        estimate_token_fn=ChatModel.estimate_token,
    )


# pylint: disable-next=unused-argument
def _store_factory(db_path, logger, **kw):  # noqa: ARG001
    from .__init__ import ChunkStore

    return ChunkStore(logger=logger, db_path=db_path)


# pylint: disable-next=unused-argument
def _tool_factory(indexer, store, **kw):  # noqa: ARG001
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
