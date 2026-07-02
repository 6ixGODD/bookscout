"""Chunk Index — node content chunking by token budget + embedding + FTS (spec §11.2).

Splits each node's own body text into token-budget chunks, generates
embeddings via :mod:`bookscout.embedding`, stores vectors in LanceDB,
and writes chunk text + metadata to SQLite with FTS5.

Token budget is enforced via :meth:`ChatModel.estimate_token` when a
ChatModel is provided. Falls back to character-based estimation (~4
chars/token) when no model is available.
"""

from __future__ import annotations

import dataclasses
import pathlib
import typing as t

from sqlmodel import col
from sqlmodel import select

from bookscout.core.lib.utils import gen_id
from bookscout.doccompiler.indexer import IndexResult
from bookscout.doccompiler.indexer import Indexer
from bookscout.logging.mixin import LoggingMixin
from bookscout.sqlite import SQLite
from bookscout.sqlite import SQLiteConfig

from .models import CHUNK_FTS_CREATE
from .models import CHUNK_FTS_TRIGGERS
from .models import ChunkModel

if t.TYPE_CHECKING:
    from bookscout.books import BooksStore
    from bookscout.doccompiler.workspace import BookWorkspace
    from bookscout.embedding import EmbeddingSystem
    from bookscout.logging import Logger
    from bookscout.vectorstore.lancedb import LanceDBStore

# Default token budget per chunk.
DEFAULT_CHUNK_TOKEN_BUDGET = 500
DEFAULT_CHUNK_OVERLAP_TOKENS = 50
# Fallback chars-per-token when no ChatModel is available for estimate_token.
_CHARS_PER_TOKEN = 4


@dataclasses.dataclass(frozen=True, slots=True)
class ChunkEntry:
    """A chunk record for vector/FTS retrieval.

    Attributes:
        id: Unique chunk id.
        book_id: Owning book id.
        node_id: Source node id.
        chunk_text: The chunk text.
        content_offset: Character offset in CONTENT.md.
        content_length: Character length in CONTENT.md.
        chunk_index: Index within the node.
    """

    id: str
    book_id: str
    node_id: str
    chunk_text: str
    content_offset: int
    content_length: int
    chunk_index: int


class ChunkStore(LoggingMixin):
    """SQLite-backed store for text chunks with FTS5."""

    def __init__(self, logger: Logger, db_path: pathlib.Path) -> None:
        super().__init__(logger=logger)
        self._db_path = db_path
        self._sqlite = SQLite(
            config=SQLiteConfig(uri=f"sqlite+aiosqlite:///{db_path.as_posix()}"),
            logger=logger,
        )

    async def startup(self) -> None:
        await self._sqlite.startup()
        await self._sqlite.create_all([ChunkModel])
        await self._sqlite.exec(CHUNK_FTS_CREATE, readonly=False)
        for stmt in CHUNK_FTS_TRIGGERS:
            await self._sqlite.exec(stmt, readonly=False)

    async def shutdown(self) -> None:
        await self._sqlite.shutdown()

    async def write_chunks(self, chunks: t.Sequence[ChunkEntry]) -> None:
        async with self._sqlite.session() as session:
            for chunk in chunks:
                session.add(
                    ChunkModel(
                        id=chunk.id,
                        book_id=chunk.book_id,
                        node_id=chunk.node_id,
                        chunk_text=chunk.chunk_text,
                        content_offset=chunk.content_offset,
                        content_length=chunk.content_length,
                        chunk_index=chunk.chunk_index,
                    )
                )
            await session.commit()

    async def get_chunks_by_node(self, node_id: str) -> list[ChunkEntry]:
        async with self._sqlite.session() as session:
            stmt = select(ChunkModel).where(ChunkModel.node_id == node_id).order_by(col(ChunkModel.chunk_index))
            rows = (await session.execute(stmt)).scalars().all()
            return [
                ChunkEntry(
                    id=r.id,
                    book_id=r.book_id,
                    node_id=r.node_id,
                    chunk_text=r.chunk_text,
                    content_offset=r.content_offset,
                    content_length=r.content_length,
                    chunk_index=r.chunk_index,
                )
                for r in rows
            ]

    async def search_fts(self, query: str, limit: int = 10) -> list[ChunkEntry]:
        """Full-text search over chunk text via FTS5."""
        from sqlalchemy import text

        async with self._sqlite.session() as session:
            fts_sql = text(
                "SELECT c.* FROM chunks c JOIN chunks_fts f ON c.rowid = f.rowid WHERE chunks_fts MATCH :q LIMIT :lim"
            )
            result = await session.execute(fts_sql, {"q": query, "lim": limit})
            rows = result.all()
            return [
                ChunkEntry(
                    id=r[0],
                    book_id=r[1],
                    node_id=r[2],
                    chunk_text=r[3],
                    content_offset=r[4],
                    content_length=r[5],
                    chunk_index=r[6],
                )
                for r in rows
            ]

    async def delete_all(self, book_id: str) -> None:
        async with self._sqlite.session() as session:
            stmt = select(ChunkModel).where(ChunkModel.book_id == book_id)
            for row in (await session.execute(stmt)).scalars().all():
                await session.delete(row)
            await session.commit()


class ChunkIndexer(Indexer):
    """Builds a Chunk Index with token-budget chunking + embeddings + FTS (spec §11.2).

    Args:
        logger: Logger instance.
        books_store: The BooksStore to read node content from.
        embedding: The embedding system for vector generation.
        vector_store: The LanceDB vector store.
        token_budget: Max tokens per chunk.
        token_overlap: Token overlap between chunks.
        estimate_token_fn: Optional callable for token estimation.
            If provided, used for precise token counting. Otherwise
            falls back to ~4 chars/token heuristic.
    """

    def __init__(
        self,
        logger: Logger,
        books_store: BooksStore,
        embedding: EmbeddingSystem,
        vector_store: LanceDBStore,
        token_budget: int = DEFAULT_CHUNK_TOKEN_BUDGET,
        token_overlap: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
        estimate_token_fn: t.Callable[[str], int] | None = None,
    ) -> None:
        super().__init__(logger=logger, books_store=books_store)
        self._embedding = embedding
        self._vector_store = vector_store
        self._token_budget = token_budget
        self._token_overlap = token_overlap
        self._estimate_token = estimate_token_fn

    @property
    def index_type(self) -> str:
        return "chunk"

    async def build_index(self, book_id: str, workspace: BookWorkspace) -> IndexResult:
        """Build chunk index for a book.

        Iterates all nodes with content_length > 0, reads each node's own
        body text from CONTENT.md via BooksStore, splits by token budget,
        generates embeddings, and writes to LanceDB + SQLite FTS5.
        """
        db_path = workspace.index_db_path("chunks")
        store = ChunkStore(logger=self.logger, db_path=db_path)
        await store.startup()
        try:
            tree = await self._books_store.get_tree(book_id)
            self.logger.info("chunk build starting", book_id=book_id, nodes=len(tree))
            self._update_progress(total=0, processed=0, status="running", error="")

            all_chunks: list[ChunkEntry] = []
            for node in tree:
                if node.content_length <= 0:
                    continue
                # Read the node's own body text from CONTENT.md.
                content = await self._books_store.read_node_content(node.id)
                if not content.strip():
                    continue

                # Split by token budget.
                node_chunks = self._split_by_tokens(content, node.content_offset)
                for idx, (chunk_text, rel_offset, chunk_len) in enumerate(node_chunks):
                    all_chunks.append(
                        ChunkEntry(
                            id=gen_id(prefix="chunk_"),
                            book_id=book_id,
                            node_id=node.id,
                            chunk_text=chunk_text,
                            content_offset=rel_offset,
                            content_length=chunk_len,
                            chunk_index=idx,
                        )
                    )

            self._update_progress(total=len(all_chunks), processed=0)
            self.logger.info("chunks split", total=len(all_chunks))

            # Write to SQLite (for FTS + metadata).
            await store.write_chunks(all_chunks)

            # Generate embeddings and write to LanceDB.
            if all_chunks:
                texts = [c.chunk_text for c in all_chunks]
                self.logger.info("generating embeddings", count=len(texts))
                vectors = await self._embedding.embed_batch(texts)
                ids = [c.id for c in all_chunks]
                payloads = [
                    {
                        "book_id": c.book_id,
                        "node_id": c.node_id,
                        "chunk_text": c.chunk_text,
                        "content_offset": c.content_offset,
                        "content_length": c.content_length,
                        "chunk_index": c.chunk_index,
                    }
                    for c in all_chunks
                ]
                await self._vector_store.upsert(ids, vectors, payloads)
                self.logger.info("vectors stored in lancedb", count=len(ids))

            self._update_progress(processed=len(all_chunks), status="done")
            self.logger.info("chunk build finished", book_id=book_id, chunks=len(all_chunks))
            return IndexResult(index_type="chunk", count=len(all_chunks), progress=self.progress)
        finally:
            await store.shutdown()

    # ------------------------------------------------------------------ retrieval

    async def search_vector(
        self,
        query: str,
        book_id: str,
        top_k: int = 10,
    ) -> list[dict[str, t.Any]]:
        """Vector (embedding) search over chunks.

        Embeds the query, searches LanceDB, and returns chunk results
        with node_id linkage for source node lookup.

        Args:
            query: The search query text.
            book_id: Book id to filter results.
            top_k: Max results.

        Returns:
            List of result dicts with chunk_id, node_id, score, chunk_text,
            content_offset, content_length.
        """
        query_vec = await self._embedding.embed(query)
        results = await self._vector_store.search(query_vec, top_k=top_k * 2)
        hits = [r for r in results if r.payload.get("book_id") == book_id][:top_k]
        return [
            {
                "chunk_id": r.id,
                "node_id": r.payload.get("node_id", ""),
                "score": r.score,
                "chunk_text": r.payload.get("chunk_text", ""),
                "content_offset": r.payload.get("content_offset", 0),
                "content_length": r.payload.get("content_length", 0),
            }
            for r in hits
        ]

    async def search_fts(
        self,
        query: str,
        book_id: str,
        chunk_store: ChunkStore,
        limit: int = 10,
    ) -> list[dict[str, t.Any]]:
        """Full-text search over chunks via FTS5.

        Args:
            query: FTS5 query string.
            book_id: Book id to filter results.
            chunk_store: An open ChunkStore for FTS search.
            limit: Max results.

        Returns:
            List of result dicts with chunk_id, node_id, chunk_text.
        """
        entries = await chunk_store.search_fts(query, limit=limit)
        return [
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

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for text.

        Uses the provided estimate_token_fn if available, otherwise
        falls back to ~4 chars/token.

        Args:
            text: The text to estimate.

        Returns:
            Estimated token count (minimum 1).
        """
        if self._estimate_token is not None:
            return self._estimate_token(text)
        return max(1, len(text) // _CHARS_PER_TOKEN)

    def _split_by_tokens(
        self,
        content: str,
        base_offset: int,
    ) -> list[tuple[str, int, int]]:
        """Split content into token-budget chunks with overlap.

        Splits at paragraph boundaries (double newlines) when possible,
        falling back to sentence boundaries, then to character boundaries.

        Args:
            content: The text to split.
            base_offset: Absolute offset of this content in CONTENT.md.

        Returns:
            List of (chunk_text, absolute_offset, chunk_length) tuples.
        """
        if not content:
            return []

        chunks: list[tuple[str, int, int]] = []
        pos = 0

        while pos < len(content):
            # Estimate how many characters correspond to the token budget.
            remaining = content[pos:]
            remaining_tokens = self._estimate_tokens(remaining)

            if remaining_tokens <= self._token_budget:
                # Rest fits in one chunk.
                chunk_text = remaining
                chunks.append((chunk_text, base_offset + pos, len(chunk_text)))
                break

            # Need to cut. Estimate character position for the budget.
            # Use ratio: budget/remaining_tokens * len(remaining).
            char_estimate = int(len(remaining) * self._token_budget / remaining_tokens)
            # Clamp to reasonable bounds.
            char_estimate = max(100, min(char_estimate, len(remaining)))

            # Try to find a paragraph break near the estimate.
            cut_pos = self._find_cut_point(remaining, char_estimate)

            chunk_text = remaining[:cut_pos]
            chunks.append((chunk_text, base_offset + pos, len(chunk_text)))

            # Move forward with overlap.
            step = max(1, cut_pos - self._token_overlap * _CHARS_PER_TOKEN)
            pos += step

        return chunks

    @staticmethod
    def _find_cut_point(text: str, target_pos: int) -> int:
        """Find a good cut point near target_pos.

        Tries paragraph break → sentence break → character.

        Args:
            text: The text to search in.
            target_pos: Target character position.

        Returns:
            The cut position (character index).
        """
        # Try paragraph break (double newline) near target.
        search_start = max(0, target_pos - 200)
        search_end = min(len(text), target_pos + 200)
        para_break = text.rfind("\n\n", search_start, search_end)
        if para_break > 0:
            return para_break + 2

        # Try sentence break.
        sent_break = text.rfind("。", search_start, search_end)
        if sent_break < 0:
            sent_break = text.rfind(".", search_start, search_end)
        if sent_break > 0:
            return sent_break + 1

        # Try newline.
        nl_break = text.rfind("\n", search_start, search_end)
        if nl_break > 0:
            return nl_break + 1

        # Fall back to exact position.
        return target_pos


__all__ = [
    "ChunkEntry",
    "ChunkIndexer",
    "ChunkStore",
]
