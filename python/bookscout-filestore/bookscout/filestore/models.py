"""SQLModel index table and FTS5 DDL for the FileStore content index.

The physical blobs live under ``<base_path>/blobs/<hh>/<sha256>`` (content
addressed). This module only describes the *index*: the virtual ``key`` →
``content_hash`` mapping plus user metadata. FTS5 (over ``key``) and the
keeping-in-sync triggers are raw SQL because they are not declarative in
SQLModel.
"""

from __future__ import annotations

import typing as t

from sqlalchemy import JSON
from sqlalchemy import Column
from sqlmodel import Field
from sqlmodel import SQLModel

from bookscout.core.lib.utils import utcnow_ts

# Raw SQL run at startup (after create_all). FTS5 is an external-content table
# keyed off ``file_index.rowid``; triggers keep it in sync on insert/update/
# delete. ``key`` is the only indexed column. Each statement is executed
# separately because ``SQLite.exec`` runs a single statement per call.
FTS_CREATE_SQL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS file_index_fts USING fts5(key, content='file_index', content_rowid='rowid')"
)

FTS_TRIGGER_SQL: tuple[str, ...] = (
    "CREATE TRIGGER IF NOT EXISTS file_index_ai AFTER INSERT ON file_index "
    "BEGIN INSERT INTO file_index_fts(rowid, key) VALUES (new.rowid, new.key); END",
    "CREATE TRIGGER IF NOT EXISTS file_index_ad AFTER DELETE ON file_index "
    "BEGIN INSERT INTO file_index_fts(file_index_fts, rowid, key) "
    "VALUES ('delete', old.rowid, old.key); END",
    "CREATE TRIGGER IF NOT EXISTS file_index_au AFTER UPDATE ON file_index "
    "BEGIN INSERT INTO file_index_fts(file_index_fts, rowid, key) "
    "VALUES ('delete', old.rowid, old.key); "
    "INSERT INTO file_index_fts(rowid, key) VALUES (new.rowid, new.key); END",
)

# Drop order (FTS + triggers first, then the table) — kept for tests/cleanup.
FTS_DROP_SQL: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS file_index_ai",
    "DROP TRIGGER IF EXISTS file_index_ad",
    "DROP TRIGGER IF EXISTS file_index_au",
    "DROP TABLE IF EXISTS file_index_fts",
)


class FileIndex(SQLModel, table=True):
    """Index row mapping a virtual storage key to its content-addressed blob.

    Attributes:
        key: Virtual storage path (e.g. ``books/epub/foo.epub``). Primary key.
        content_hash: sha256 hex of the blob content; locates the physical file
            under ``blobs/<hash[:shard_depth]>/<hash>``.
        size: Blob size in bytes.
        created_at: Epoch seconds the key was first indexed.
        modified_at: Epoch seconds the key row was last updated.
        meta: User metadata (JSON), replacing the legacy ``.metadata.json``
            sidecar. Stored in the ``metadata`` column; the attribute is named
            ``meta`` because ``metadata`` is reserved by SQLAlchemy/SQLModel.
    """

    __tablename__ = "file_index"

    key: str = Field(primary_key=True)
    content_hash: str = Field(index=True)
    size: int = Field(default=0)
    created_at: float = Field(default_factory=utcnow_ts)
    modified_at: float = Field(default_factory=utcnow_ts)
    # ``metadata`` is reserved on declarative models; alias the column name.
    meta: dict[str, t.Any] | None = Field(
        default=None,
        sa_column=Column("metadata", JSON),
    )
