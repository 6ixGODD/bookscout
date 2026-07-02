"""SQLModel table and FTS5 DDL for ChunkStore."""

from __future__ import annotations

from sqlmodel import Field
from sqlmodel import SQLModel

CHUNK_FTS_CREATE = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_text, content='chunks', content_rowid='rowid')"
)

CHUNK_FTS_TRIGGERS: tuple[str, ...] = (
    "CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks "
    "BEGIN INSERT INTO chunks_fts(rowid, chunk_text) VALUES (new.rowid, new.chunk_text); END",
    "CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks "
    "BEGIN INSERT INTO chunks_fts(chunks_fts, rowid, chunk_text) "
    "VALUES ('delete', old.rowid, old.chunk_text); END",
    "CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks "
    "BEGIN INSERT INTO chunks_fts(chunks_fts, rowid, chunk_text) "
    "VALUES ('delete', old.rowid, old.chunk_text); "
    "INSERT INTO chunks_fts(rowid, chunk_text) VALUES (new.rowid, new.chunk_text); END",
)


class ChunkModel(SQLModel, table=True):
    """Chunk record for vector/FTS retrieval."""

    __tablename__ = "chunks"

    id: str = Field(primary_key=True)
    book_id: str = Field(index=True, nullable=False)
    node_id: str = Field(index=True, nullable=False)
    chunk_text: str = Field(default="", nullable=False)
    content_offset: int = Field(default=0, nullable=False)
    content_length: int = Field(default=0, nullable=False)
    chunk_index: int = Field(default=0, nullable=False)
