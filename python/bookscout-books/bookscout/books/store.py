"""Public store for the bookscout-books ontology layer.

:class:`BooksStore` is the single entry point for creating, persisting and
querying books and their :class:`~bookscout.books.BookNode` trees. It owns a
private :class:`bookscout.sqlite.SQLite` instance and maps between the public
frozen dataclasses (:class:`~bookscout.books.Book` /
:class:`~bookscout.books.BookNode`) and internal SQLModel tables, so callers
never see SQLite, SQLAlchemy or SQLModel.
"""

from __future__ import annotations

import pathlib
import typing as t

import aiofiles  # type: ignore[import-untyped]
from pydantic import BaseModel
from pydantic import Field
from sqlmodel import col
from sqlmodel import select

from bookscout.core.lib.utils import utcnow_ts
from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin
from bookscout.sqlite import SQLite
from bookscout.sqlite import SQLiteConfig

from .exceptions import BookExistsError
from .exceptions import BookNotFoundError
from .exceptions import ContentError
from .exceptions import NodeNotFoundError
from .exceptions import StoreError
from .exceptions import TreeValidationError
from .exceptions import handle_errors
from .models import MANIFEST_UNIQUE_SQL
from .models import NODE_INDEX_SQL
from .models import BookModel
from .models import BookNodeModel
from .models import IndexManifestModel
from .types import Book
from .types import BookNode
from .types import IndexInfo

if t.TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from collections.abc import Sequence

    from bookscout.logging import Logger


class BooksConfig(BaseModel):
    """Configuration for :class:`BooksStore`.

    Attributes:
        base_path: Directory holding the ontology SQLite file. In the full
            workspace this is the per-book directory
            (``books/{book_id}/``); for Step 1 it can be any directory.
        db_name: SQLite filename inside ``base_path`` (default ``books.sqlite``).
    """

    base_path: pathlib.Path | str = Field(
        default="books",
        description="Directory holding the ontology SQLite database.",
    )

    db_name: str = Field(
        default="books.sqlite",
        description="SQLite database filename inside base_path (must end with .db or .sqlite).",
    )


class BooksStore(LoggingMixin, AsyncResourceMixin):
    """Ontology store for books and their ``BookNode`` trees.

    The store self-manages a SQLite database under ``base_path/db_name`` and
    exposes a pure-dataclass API. All persistence is async. SQLite/SQLModel
    objects never leak out of this class.

    Args:
        logger: Logger instance.
        config: Store configuration.
    """

    def __init__(self, logger: Logger, config: BooksConfig) -> None:
        super().__init__(logger=logger)
        self.config = config
        self.base_path = pathlib.Path(config.base_path).resolve()
        self.db_path = self.base_path / config.db_name
        self.sqlite = SQLite(
            config=SQLiteConfig(uri=self._db_uri),
            logger=logger,
        )

    @property
    def _db_uri(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path.as_posix()}"

    async def startup(self) -> None:
        """Create the base directory, open SQLite, and create schema/indexes."""
        self.base_path.mkdir(parents=True, exist_ok=True)
        await self.sqlite.startup()
        await self.sqlite.create_all([BookModel, BookNodeModel, IndexManifestModel])
        for stmt in NODE_INDEX_SQL:
            await self.sqlite.exec(stmt, readonly=False)
        for stmt in MANIFEST_UNIQUE_SQL:
            await self.sqlite.exec(stmt, readonly=False)
        await super().startup()
        self.logger.info("books store started", db_path=str(self.db_path))

    async def shutdown(self) -> None:
        """Dispose the SQLite engine. The database file is left on disk."""
        await self.sqlite.shutdown()
        self.logger.info("books store stopped", db_path=str(self.db_path))

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def create_book(self, book: Book) -> Book:
        """Persist a new book.

        Args:
            book: The book to create.

        Returns:
            The persisted book (as stored).

        Raises:
            BookExistsError: If a book with the same id already exists.
        """
        async with self.sqlite.session() as session:
            existing = await session.get(BookModel, book.id)
            if existing is not None:
                raise BookExistsError(f"Book already exists: id={book.id}")
            session.add(self._book_to_model(book))
            await session.commit()
        self.logger.info("book created", book_id=book.id, title=book.title)
        return book

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def get_book(self, book_id: str) -> Book:
        """Fetch a book by id.

        Args:
            book_id: The book id.

        Returns:
            The book.

        Raises:
            BookNotFoundError: If no book has this id.
        """
        async with self.sqlite.session() as session:
            row = await session.get(BookModel, book_id)
            if row is None:
                raise BookNotFoundError(f"Book not found: id={book_id}")
            return self._model_to_book(row)

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def update_book(self, book: Book) -> Book:
        """Update an existing book's mutable fields.

        Args:
            book: The book with updated fields (id must match an existing row).

        Returns:
            The updated book.

        Raises:
            BookNotFoundError: If the book id does not exist.
        """
        async with self.sqlite.session() as session:
            row = await session.get(BookModel, book.id)
            if row is None:
                raise BookNotFoundError(f"Book not found: id={book.id}")
            row.title = book.title
            row.author = book.author
            row.isbn = book.isbn
            row.publisher = book.publisher
            row.language = book.language
            row.extras = dict(book.extras)
            row.content_path = book.content_path
            row.source_path = book.source_path
            row.checksum = book.checksum
            row.updated_at = utcnow_ts()
            await session.commit()
        self.logger.info("book updated", book_id=book.id, title=book.title)
        return book

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def delete_book(self, book_id: str) -> None:
        """Delete a book and all of its nodes.

        Args:
            book_id: The book id.
        """
        async with self.sqlite.session() as session:
            row = await session.get(BookModel, book_id)
            if row is not None:
                await session.delete(row)
            stmt = select(BookNodeModel).where(BookNodeModel.book_id == book_id)
            nodes = (await session.execute(stmt)).scalars().all()
            for node in nodes:
                await session.delete(node)
            await session.commit()
        self.logger.info("book deleted", book_id=book_id)

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def list_books(self) -> list[Book]:
        """List all books, with their built index types populated.

        Returns:
            A list of all stored books.
        """
        async with self.sqlite.session() as session:
            stmt = select(BookModel).order_by(col(BookModel.created_at))
            rows = (await session.execute(stmt)).scalars().all()
            books = [self._model_to_book(r) for r in rows]
            for book in books:
                idx_stmt = select(IndexManifestModel.index_type).where(
                    IndexManifestModel.book_id == book.id,
                    IndexManifestModel.status == "built",
                )
                idx_rows = (await session.execute(idx_stmt)).scalars().all()
                import dataclasses

                book_with_idx = dataclasses.replace(book, indexes=tuple(idx_rows))
                books[books.index(book)] = book_with_idx
            return books

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def create_nodes(self, book_id: str, nodes: Sequence[BookNode]) -> list[BookNode]:
        """Persist a complete node tree for a book in one transaction.

        The batch is validated as a whole (single root, level monotonicity,
        sibling order, no cycles, range validity) before being written.

        Args:
            book_id: The owning book id (must already exist).
            nodes: The full set of nodes to persist. Any pre-existing nodes
                for this book are replaced.

        Returns:
            The persisted nodes, in input order.

        Raises:
            BookNotFoundError: If the book does not exist.
            TreeValidationError: If the node set violates tree invariants.
        """
        await self.get_book(book_id)  # raises BookNotFoundError
        self._validate_tree(book_id, nodes)
        async with self.sqlite.session() as session:
            # Replace existing nodes for this book.
            stmt = select(BookNodeModel).where(BookNodeModel.book_id == book_id)
            for old in (await session.execute(stmt)).scalars().all():
                await session.delete(old)
            for node in nodes:
                session.add(self._node_to_model(node))
            await session.commit()
        self.logger.info("nodes persisted", book_id=book_id, count=len(nodes))
        return list(nodes)

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def get_node(self, node_id: str) -> BookNode:
        """Fetch a single node by id.

        Args:
            node_id: The node id.

        Returns:
            The node.

        Raises:
            NodeNotFoundError: If no node has this id.
        """
        async with self.sqlite.session() as session:
            row = await session.get(BookNodeModel, node_id)
            if row is None:
                raise NodeNotFoundError(f"Node not found: id={node_id}")
            return self._model_to_node(row)

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def get_root_node(self, book_id: str) -> BookNode:
        """Fetch the root node of a book.

        Args:
            book_id: The book id.

        Returns:
            The root node (``level == 0``, ``parent_id == ""``).

        Raises:
            NodeNotFoundError: If the book has no root node.
            TreeValidationError: If the book has more than one root node.
        """
        async with self.sqlite.session() as session:
            stmt = select(BookNodeModel).where(BookNodeModel.book_id == book_id, BookNodeModel.parent_id == "")
            roots = (await session.execute(stmt)).scalars().all()
            if not roots:
                raise NodeNotFoundError(f"Root node not found for book: id={book_id}")
            if len(roots) > 1:
                raise TreeValidationError(f"Multiple root nodes for book: id={book_id}")
            return self._model_to_node(roots[0])

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def get_children(self, node_id: str) -> list[BookNode]:
        """Fetch the direct children of a node, ordered by ``order_index``.

        Args:
            node_id: The parent node id.

        Returns:
            Child nodes in ascending ``order_index`` order.

        Raises:
            NodeNotFoundError: If the parent node does not exist.
        """
        async with self.sqlite.session() as session:
            parent = await session.get(BookNodeModel, node_id)
            if parent is None:
                raise NodeNotFoundError(f"Node not found: id={node_id}")
            stmt = (
                select(BookNodeModel).where(BookNodeModel.parent_id == node_id).order_by(col(BookNodeModel.order_index))
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [self._model_to_node(r) for r in rows]

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def get_parent(self, node_id: str) -> BookNode | None:
        """Fetch the parent of a node, or ``None`` for a root node.

        Args:
            node_id: The child node id.

        Returns:
            The parent node, or ``None`` if the node is a root.

        Raises:
            NodeNotFoundError: If the node does not exist.
        """
        async with self.sqlite.session() as session:
            row = await session.get(BookNodeModel, node_id)
            if row is None:
                raise NodeNotFoundError(f"Node not found: id={node_id}")
            if row.parent_id == "":
                return None
            parent = await session.get(BookNodeModel, row.parent_id)
            if parent is None:
                return None
            return self._model_to_node(parent)

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def get_descendants(self, node_id: str) -> list[BookNode]:
        """Fetch all descendants of a node in pre-order (parent before child).

        Order: children are visited in ascending ``order_index``; each child's
        subtree is fully traversed before the next sibling.

        Args:
            node_id: The ancestor node id.

        Returns:
            Descendant nodes (excluding the node itself) in pre-order.

        Raises:
            NodeNotFoundError: If the node does not exist.
        """
        result: list[BookNode] = []
        async with self.sqlite.session() as session:
            row = await session.get(BookNodeModel, node_id)
            if row is None:
                raise NodeNotFoundError(f"Node not found: id={node_id}")
            await self._collect_descendants(session, node_id, result)
        return result

    async def _collect_descendants(
        self,
        session: t.Any,
        parent_id: str,
        out: list[BookNode],
    ) -> None:
        stmt = (
            select(BookNodeModel).where(BookNodeModel.parent_id == parent_id).order_by(col(BookNodeModel.order_index))
        )
        rows = (await session.execute(stmt)).scalars().all()
        for row in rows:
            out.append(self._model_to_node(row))
            await self._collect_descendants(session, row.id, out)

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def get_tree(self, book_id: str) -> list[BookNode]:
        """Fetch the full node tree for a book in pre-order.

        The returned list starts with the root node, followed by its
        descendants in pre-order (children ordered by ``order_index``).

        Args:
            book_id: The book id.

        Returns:
            All nodes for the book in pre-order.

        Raises:
            NodeNotFoundError: If the book has no root node.
        """
        root = await self.get_root_node(book_id)
        descendants = await self.get_descendants(root.id)
        return [root, *descendants]

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def delete_nodes(self, book_id: str) -> None:
        """Delete all nodes for a book.

        Args:
            book_id: The book id.
        """
        async with self.sqlite.session() as session:
            stmt = select(BookNodeModel).where(BookNodeModel.book_id == book_id)
            for row in (await session.execute(stmt)).scalars().all():
                await session.delete(row)
            await session.commit()
        self.logger.info("nodes deleted", book_id=book_id)

    def iter_tree(self, book_id: str) -> AsyncIterator[BookNode]:
        """Async iterator yielding the full tree for a book in pre-order.

        Convenience wrapper around :meth:`get_tree`.

        Args:
            book_id: The book id.

        Yields:
            Nodes in pre-order.
        """
        return self._iter_tree(book_id)

    async def _iter_tree(self, book_id: str) -> AsyncIterator[BookNode]:
        for node in await self.get_tree(book_id):
            yield node

    @handle_errors(exc_type=ContentError)  # type: ignore[untyped-decorator]
    async def read_node_content(self, node_id: str) -> str:
        """Read a node's own-body text from ``CONTENT.md``.

        Uses the owning book's ``content_path`` and slices
        ``[content_offset, content_offset + content_length)`` with Python
        string indexing semantics (spec §3.2 #7).

        Args:
            node_id: The node id.

        Returns:
            The node's own-body text. Empty when ``content_length == 0``.

        Raises:
            NodeNotFoundError: If the node does not exist.
            ContentError: If ``CONTENT.md`` is missing or the node's book has
                no ``content_path``.
        """
        node = await self.get_node(node_id)
        if node.content_length == 0:
            return ""
        text = await self._load_content(node.book_id)
        end = node.content_offset + node.content_length
        _assert_range(text, node.content_offset, end, node_id, "content")
        return text[node.content_offset : end]

    @handle_errors(exc_type=ContentError)  # type: ignore[untyped-decorator]
    async def read_subtree_content(self, node_id: str) -> str:
        """Read the concatenation of a node's and all descendants' own-body text.

        The node itself contributes first, then descendants in pre-order
        (children ordered by ``order_index``). Each contributing node's
        own-body slice is taken from ``CONTENT.md``. Nodes with
        ``content_length == 0`` contribute nothing but are still traversed
        to reach their children.

        Args:
            node_id: The (sub)tree root node id.

        Returns:
            The concatenated own-body text of the node and its descendants.

        Raises:
            NodeNotFoundError: If the node does not exist.
            ContentError: If ``CONTENT.md`` is missing or a range is invalid.
        """
        node = await self.get_node(node_id)
        text = await self._load_content(node.book_id)
        parts: list[str] = []
        # Node itself.
        if node.content_length > 0:
            end = node.content_offset + node.content_length
            _assert_range(text, node.content_offset, end, node.id, "content")
            parts.append(text[node.content_offset : end])
        # Descendants in pre-order.
        descendants = await self.get_descendants(node.id)
        for desc in descendants:
            if desc.content_length > 0:
                end = desc.content_offset + desc.content_length
                _assert_range(text, desc.content_offset, end, desc.id, "content")
                parts.append(text[desc.content_offset : end])
        return "".join(parts)

    async def _load_content(self, book_id: str) -> str:
        book = await self.get_book(book_id)
        if not book.content_path:
            raise ContentError(f"Book has no content_path: id={book_id}")
        path = pathlib.Path(book.content_path)
        if not path.exists():
            raise ContentError(f"CONTENT.md not found: path={path}")
        async with aiofiles.open(path, encoding="utf-8") as f:
            return t.cast(str, await f.read())

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def list_indexes(self, book_id: str) -> list[IndexInfo]:
        """List all manifest rows for a book.

        Args:
            book_id: The book id.

        Returns:
            List of :class:`IndexInfo` snapshots.
        """
        async with self.sqlite.session() as session:
            stmt = select(IndexManifestModel).where(IndexManifestModel.book_id == book_id)
            rows = (await session.execute(stmt)).scalars().all()
            return [
                IndexInfo(
                    index_type=r.index_type,
                    status=r.status,
                    count=r.count,
                    error=r.error,
                    built_at=r.built_at,
                )
                for r in rows
            ]

    async def list_index_types(self, book_id: str) -> set[str]:
        """Return the set of index types with status ``"built"`` for a book."""
        infos = await self.list_indexes(book_id)
        return {i.index_type for i in infos if i.status == "built"}

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def upsert_index(
        self,
        book_id: str,
        index_type: str,
        status: str,
        *,
        count: int = 0,
        error: str = "",
        built_at: float = 0.0,
    ) -> None:
        """Insert or update a manifest row for (book_id, index_type)."""
        from bookscout.core.lib.utils import gen_id
        from bookscout.core.lib.utils import utcnow_ts

        async with self.sqlite.session() as session:
            stmt = select(IndexManifestModel).where(
                IndexManifestModel.book_id == book_id,
                IndexManifestModel.index_type == index_type,
            )
            row = (await session.execute(stmt)).scalars().first()
            if row is None:
                session.add(
                    IndexManifestModel(
                        id=gen_id(prefix="iman_"),
                        book_id=book_id,
                        index_type=index_type,
                        status=status,
                        count=count,
                        error=error,
                        built_at=built_at if built_at else utcnow_ts(),
                    )
                )
            else:
                row.status = status
                row.count = count
                row.error = error
                row.built_at = built_at if built_at else (utcnow_ts() if status == "built" else row.built_at)
            await session.commit()

    @handle_errors(exc_type=StoreError)  # type: ignore[untyped-decorator]
    async def set_index_status(
        self,
        book_id: str,
        index_type: str,
        status: str,
        **fields: t.Any,
    ) -> None:
        """Patch the status (and optional fields) of an existing manifest row."""
        async with self.sqlite.session() as session:
            stmt = select(IndexManifestModel).where(
                IndexManifestModel.book_id == book_id,
                IndexManifestModel.index_type == index_type,
            )
            row = (await session.execute(stmt)).scalars().first()
            if row is None:
                raise StoreError(f"Manifest row not found: book={book_id} type={index_type}")
            row.status = status
            for k, v in fields.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            await session.commit()

    async def all_book_ids(self) -> list[str]:
        """Return all book ids in creation order."""
        async with self.sqlite.session() as session:
            stmt = select(BookModel.id).order_by(col(BookModel.created_at))
            rows = (await session.execute(stmt)).scalars().all()
            return list(rows)

    @staticmethod
    def _book_to_model(book: Book) -> BookModel:
        return BookModel(
            id=book.id,
            title=book.title,
            author=book.author,
            isbn=book.isbn,
            publisher=book.publisher,
            language=book.language,
            extras=dict(book.extras),
            content_path=book.content_path,
            source_path=book.source_path,
            checksum=book.checksum,
        )

    @staticmethod
    def _model_to_book(row: BookModel) -> Book:
        return Book(
            id=row.id,
            title=row.title,
            author=row.author,
            isbn=row.isbn,
            publisher=row.publisher,
            language=row.language,
            extras=dict(row.extras) if row.extras is not None else {},
            content_path=row.content_path,
            source_path=row.source_path,
            checksum=row.checksum,
        )

    @staticmethod
    def _node_to_model(node: BookNode) -> BookNodeModel:
        return BookNodeModel(
            id=node.id,
            book_id=node.book_id,
            parent_id=node.parent_id,
            level=node.level,
            order_index=node.order_index,
            title=node.title,
            title_offset=node.title_offset,
            title_length=node.title_length,
            content_offset=node.content_offset,
            content_length=node.content_length,
        )

    @staticmethod
    def _model_to_node(row: BookNodeModel) -> BookNode:
        return BookNode(
            id=row.id,
            book_id=row.book_id,
            parent_id=row.parent_id,
            level=row.level,
            order_index=row.order_index,
            title=row.title,
            title_offset=row.title_offset,
            title_length=row.title_length,
            content_offset=row.content_offset,
            content_length=row.content_length,
        )

    def _validate_tree(self, book_id: str, nodes: Sequence[BookNode]) -> None:
        """Validate a node set against spec §3.5 tree invariants.

        Args:
            book_id: Expected owning book id.
            nodes: The nodes to validate.

        Raises:
            TreeValidationError: On any invariant violation.
        """
        if not nodes:
            raise TreeValidationError("Cannot create an empty node tree; a root is required.")

        ids: dict[str, BookNode] = {}
        for node in nodes:
            if node.book_id != book_id:
                raise TreeValidationError(
                    f"Node book_id mismatch: node={node.id} book_id={node.book_id} expected={book_id}"
                )
            if node.id in ids:
                raise TreeValidationError(f"Duplicate node id: {node.id}")
            ids[node.id] = node
            if node.title_length < 0:
                raise TreeValidationError(f"Negative title_length: node={node.id}")
            if node.content_length < 0:
                raise TreeValidationError(f"Negative content_length: node={node.id}")

        # Exactly one root.
        roots = [n for n in nodes if n.is_root]
        if len(roots) != 1:
            raise TreeValidationError(f"Expected exactly one root node, found {len(roots)}.")
        root = roots[0]
        if root.level != 0:
            raise TreeValidationError(f"Root node must have level=0: node={root.id} level={root.level}")

        # Parent existence + level monotonicity.
        for node in nodes:
            if node.is_root:
                continue
            parent = ids.get(node.parent_id)
            if parent is None:
                raise TreeValidationError(f"Node references missing parent: node={node.id} parent_id={node.parent_id}")
            if node.level <= parent.level:
                raise TreeValidationError(
                    f"Child level must exceed parent level: node={node.id} level={node.level} "
                    f"parent={parent.id} parent_level={parent.level}"
                )

        # Sibling order uniqueness (stable ascending handled by sort on read).
        siblings: dict[str, list[int]] = {}
        for node in nodes:
            siblings.setdefault(node.parent_id, []).append(node.order_index)
        for parent_id, orders in siblings.items():
            if len(set(orders)) != len(orders):
                raise TreeValidationError(f"Duplicate order_index under parent: parent_id={parent_id}")

        # Cycle detection: every non-root must reach the root via parents.
        for node in nodes:
            if node.is_root:
                continue
            seen: set[str] = set()
            current = node.id
            while current in ids and not ids[current].is_root:
                if current in seen:
                    raise TreeValidationError(f"Cycle detected starting at node: {node.id}")
                seen.add(current)
                current = ids[current].parent_id
            if current not in ids:
                raise TreeValidationError(f"Node parent chain does not reach root: node={node.id}")


def _assert_range(text: str, start: int, end: int, node_id: str, label: str) -> None:
    """Raise :class:`ContentError` if ``[start, end)`` is outside ``text``.

    Args:
        text: The full ``CONTENT.md`` text.
        start: Range start (inclusive).
        end: Range end (exclusive).
        node_id: Node id for the error message.
        label: Which range ("title" or "content") for the error message.
    """
    if start < 0 or end < start or end > len(text):
        raise ContentError(
            f"Node {label} range out of bounds: node={node_id} start={start} end={end} content_len={len(text)}"
        )
