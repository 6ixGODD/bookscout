"""Public domain models for the bookscout-books ontology layer.

These are pure, immutable value objects. They deliberately carry **no**
persistence concerns: the :class:`bookscout.books.BooksStore` is responsible for
mapping between these dataclasses and its internal SQLite/SQLModel tables, so
callers never see SQLite, SQLAlchemy or SQLModel types.
"""

from __future__ import annotations

import dataclasses
import typing as t

from bookscout.core.lib.utils import gen_id

if t.TYPE_CHECKING:
    from collections.abc import Iterator


@dataclasses.dataclass(frozen=True, slots=True)
class Book:
    """A managed book (ontology entry).

    Holds only the book's core identity/metadata plus entry paths into the
    filestore. It intentionally stores **no** derived index data (summary,
    chunk, embedding, graph) and **no** parser source-mapping (bbox, XML
    location) — those live in their own layers.

    Attributes:
        id: Stable unique identifier (generated when ``Book.new`` is used).
        title: Book title; ``""`` when unrecognised (never ``None``).
        author: Author string; ``""`` when unrecognised.
        isbn: ISBN string; ``""`` when unrecognised.
        publisher: Publisher string; ``""`` when unrecognised.
        language: Language tag; ``""`` when unrecognised.
        extras: Free-form metadata outside the fixed fields above.
        content_path: Path to this book's normalised ``CONTENT.md``.
        source_path: Path to the original source file.
        checksum: Source-file checksum (e.g. sha256 hex).

    Rules:
        * No string field is ever ``None``; unrecognised values use ``""``.
        * ``extras`` preserves anything not covered by the fixed fields.
    """

    id: str
    title: str
    author: str
    isbn: str
    publisher: str
    language: str
    extras: dict[str, t.Any]
    content_path: str
    source_path: str
    checksum: str
    indexes: tuple[str, ...] = ()

    @classmethod
    def new(
        cls,
        *,
        title: str = "",
        author: str = "",
        isbn: str = "",
        publisher: str = "",
        language: str = "",
        extras: dict[str, t.Any] | None = None,
        content_path: str = "",
        source_path: str = "",
        checksum: str = "",
        book_id: str | None = None,
    ) -> Book:
        """Construct a :class:`Book` with a freshly generated id.

        All string fields default to ``""`` so a partially-known book can be
        created without ever producing ``None`` values. Pass ``book_id`` to
        use a caller-supplied id instead of an auto-generated one.

        Args:
            title: Book title (default ``""``).
            author: Author (default ``""``).
            isbn: ISBN (default ``""``).
            publisher: Publisher (default ``""``).
            language: Language tag (default ``""``).
            extras: Extra metadata; copied into a new dict.
            content_path: Path to ``CONTENT.md`` (default ``""``).
            source_path: Path to the source file (default ``""``).
            checksum: Source-file checksum (default ``""``).
            book_id: Optional explicit id; auto-generated when ``None``.

        Returns:
            A frozen :class:`Book` instance.
        """
        return cls(
            id=book_id if book_id is not None else gen_id(prefix="book_"),
            title=title,
            author=author,
            isbn=isbn,
            publisher=publisher,
            language=language,
            extras=dict(extras) if extras else {},
            content_path=content_path,
            source_path=source_path,
            checksum=checksum,
            indexes=(),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class IndexInfo:
    """Snapshot of one index manifest row for a book.

    Attributes:
        index_type: The index type name ("summary", "chunk", "graph", ...).
        status: Lifecycle status: pending|building|built|failed|removed.
        count: Number of entries the indexer produced.
        error: Error message when status == "failed"; "" otherwise.
        built_at: Epoch seconds when the index was built; 0.0 if not built.
    """

    index_type: str
    status: str
    count: int
    error: str
    built_at: float


@dataclasses.dataclass(frozen=True, slots=True)
class BookNode:
    """A structural node of a book, forming a tree via an adjacency list.

    Each node references a slice of the book's ``CONTENT.md``:

        * ``title_offset`` / ``title_length`` → the heading text range.
        * ``content_offset`` / ``content_length`` → the node's **own** body
          text range (excluding children). ``content_length == 0`` means the
          node has no own body, though it may still have children.

    Subtree content is **not** stored; it is computed by traversing the node
    and its descendants.

    Attributes:
        id: Stable unique node id.
        book_id: Id of the owning :class:`Book`.
        parent_id: Parent node id; ``""`` for the root node.
        level: Tree depth; ``0`` for the root, ``> 0`` otherwise.
        order_index: Sibling order under the same parent (ascending stable).
        title: Heading text; the root uses the book title (or ``""``).
        title_offset: Character offset of the heading in ``CONTENT.md``.
        title_length: Character length of the heading in ``CONTENT.md``.
        content_offset: Character offset of the node's own body in
            ``CONTENT.md``.
        content_length: Character length of the node's own body; may be ``0``.

    Rules:
        * No field is ever ``None``.
        * Exactly one root per book with ``parent_id == ""`` and ``level == 0``.
        * A child's ``level`` must exceed its parent's.
        * Siblings sort stably by ascending ``order_index``.
        * ``title_length`` and ``content_length`` must be ``>= 0``.
        * Offsets/lengths must fall within the ``CONTENT.md`` character range.
    """

    id: str
    book_id: str
    parent_id: str
    level: int
    order_index: int
    title: str
    title_offset: int
    title_length: int
    content_offset: int
    content_length: int

    @property
    def is_root(self) -> bool:
        """``True`` when this node is a book's root (``parent_id == ""``)."""
        return self.parent_id == ""

    @property
    def title_end(self) -> int:
        """Character offset one past the end of the heading range."""
        return self.title_offset + self.title_length

    @property
    def content_end(self) -> int:
        """Character offset one past the end of the own-body range."""
        return self.content_offset + self.content_length

    @classmethod
    def new(
        cls,
        *,
        book_id: str,
        parent_id: str = "",
        level: int = 0,
        order_index: int = 0,
        title: str = "",
        title_offset: int = 0,
        title_length: int = 0,
        content_offset: int = 0,
        content_length: int = 0,
        node_id: str | None = None,
    ) -> BookNode:
        """Construct a :class:`BookNode` with a freshly generated id.

        Args:
            book_id: Id of the owning book.
            parent_id: Parent node id; ``""`` denotes the root.
            level: Tree depth (``0`` for root).
            order_index: Sibling order under the parent.
            title: Heading text (default ``""``).
            title_offset: Heading character offset in ``CONTENT.md``.
            title_length: Heading character length.
            content_offset: Own-body character offset.
            content_length: Own-body character length (may be ``0``).
            node_id: Optional explicit id; auto-generated when ``None``.

        Returns:
            A frozen :class:`BookNode` instance.
        """
        return cls(
            id=node_id if node_id is not None else gen_id(prefix="node_"),
            book_id=book_id,
            parent_id=parent_id,
            level=level,
            order_index=order_index,
            title=title,
            title_offset=title_offset,
            title_length=title_length,
            content_offset=content_offset,
            content_length=content_length,
        )

    def iter_title_slice(self) -> Iterator[int]:
        """Yield the inclusive-then-exclusive ``[offset, end)`` title range.

        Convenience for callers that want ``range(title_offset, title_end)``.

        Yields:
            The title offset followed by the title end offset.
        """
        yield self.title_offset
        yield self.title_end

    def iter_content_slice(self) -> Iterator[int]:
        """Yield the ``[offset, end)`` own-body range.

        When ``content_length == 0`` both yielded values equal
        ``content_offset`` (an empty range).

        Yields:
            The content offset followed by the content end offset.
        """
        yield self.content_offset
        yield self.content_end
