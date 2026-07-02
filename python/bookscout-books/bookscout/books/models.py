"""Internal SQLModel tables and index DDL for the bookscout-books ontology DB.

These models are **private** to :mod:`bookscout.books` — the public API
exposes only the frozen :class:`~bookscout.books.Book` /
:class:`~bookscout.books.BookNode` dataclasses. Mapping between the two
representations happens inside :class:`bookscout.books.BooksStore`.

The ``books`` table holds book identity/metadata; ``book_nodes`` holds the
adjacency-list node tree. Non-declarative indexes (composite, for efficient
tree traversal) are created via raw SQL at startup, mirroring the FTS pattern
in :mod:`bookscout.filestore.models`.
"""

from __future__ import annotations

import typing as t

from sqlalchemy import JSON
from sqlalchemy import Column
from sqlmodel import Field
from sqlmodel import SQLModel

from bookscout.core.lib.utils import utcnow_ts

# Raw index DDL executed after create_all. SQLModel cannot express composite
# indexes declaratively with the desired column order, so we use raw SQL.
# Each statement runs in a single ``SQLite.exec`` call.
NODE_INDEX_SQL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_book_nodes_book_parent ON book_nodes (book_id, parent_id)",
    "CREATE INDEX IF NOT EXISTS idx_book_nodes_book_level_order ON book_nodes (book_id, level, order_index)",
    "CREATE INDEX IF NOT EXISTS idx_book_nodes_book_id ON book_nodes (book_id)",
)


class BookModel(SQLModel, table=True):
    """Persistent row for a book's core metadata.

    Attributes:
        id: Primary key; matches :attr:`bookscout.books.Book.id`.
        title/author/isbn/publisher/language: Core metadata (never NULL).
        extras: JSON column for free-form metadata.
        content_path: Path to ``CONTENT.md``.
        source_path: Path to the original source file.
        checksum: Source-file checksum.
        created_at/updated_at: Epoch-seconds timestamps.
    """

    __tablename__ = "books"

    id: str = Field(primary_key=True)
    title: str = Field(default="", nullable=False)
    author: str = Field(default="", nullable=False)
    isbn: str = Field(default="", nullable=False)
    publisher: str = Field(default="", nullable=False)
    language: str = Field(default="", nullable=False)
    # ``metadata`` is reserved on declarative models; alias the column name.
    extras: dict[str, t.Any] | None = Field(
        default=None,
        sa_column=Column("metadata", JSON, nullable=False),
    )
    content_path: str = Field(default="", nullable=False)
    source_path: str = Field(default="", nullable=False)
    checksum: str = Field(default="", nullable=False)
    created_at: float = Field(default_factory=utcnow_ts, nullable=False)
    updated_at: float = Field(default_factory=utcnow_ts, nullable=False)


class BookNodeModel(SQLModel, table=True):
    """Persistent row for a single node in a book's tree.

    Attributes:
        id: Primary key; matches :attr:`bookscout.books.BookNode.id`.
        book_id: Foreign book id (logical FK, not enforced by a constraint).
        parent_id: Parent node id; ``""`` for the root.
        level: Tree depth (``0`` for root).
        order_index: Sibling order under the parent.
        title: Heading text.
        title_offset/title_length: Heading range in ``CONTENT.md``.
        content_offset/content_length: Own-body range in ``CONTENT.md``.
        created_at/updated_at: Epoch-seconds timestamps.
    """

    __tablename__ = "book_nodes"

    id: str = Field(primary_key=True)
    book_id: str = Field(nullable=False, index=True)
    parent_id: str = Field(default="", nullable=False, index=True)
    level: int = Field(default=0, nullable=False)
    order_index: int = Field(default=0, nullable=False)
    title: str = Field(default="", nullable=False)
    title_offset: int = Field(default=0, nullable=False)
    title_length: int = Field(default=0, nullable=False)
    content_offset: int = Field(default=0, nullable=False)
    content_length: int = Field(default=0, nullable=False)
    created_at: float = Field(default_factory=utcnow_ts, nullable=False)
    updated_at: float = Field(default_factory=utcnow_ts, nullable=False)
