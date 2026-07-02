"""Tests for the bookscout-books ontology store (Step 1).

Covers Book CRUD, BookNode tree persistence/querying, tree validation, and
node/subtree content reading against a temporary CONTENT.md file.
"""

from __future__ import annotations

import pathlib

import pytest

from bookscout.books import Book
from bookscout.books import BookExistsError
from bookscout.books import BookNode
from bookscout.books import BookNotFoundError
from bookscout.books import BooksConfig
from bookscout.books import BooksStore
from bookscout.books import NodeNotFoundError
from bookscout.books import TreeValidationError

# ----------------------------------------------------------------- fixtures


def _config(base_path: pathlib.Path, **overrides) -> BooksConfig:
    return BooksConfig(base_path=base_path, **overrides)


@pytest.fixture()
def store(tmp_path: pathlib.Path, logger) -> BooksStore:
    s = BooksStore(logger=logger, config=_config(tmp_path / "books"))
    yield s


# ------------------------------------------------------------------- Book CRUD


async def test_create_and_get_book(store: BooksStore):
    async with store:
        book = Book.new(title="Refactoring", author="Fowler", isbn="9780134757599")
        await store.create_book(book)
        fetched = await store.get_book(book.id)
        assert fetched == book


async def test_create_book_duplicate_raises(store: BooksStore):
    async with store:
        book = Book.new(title="A")
        await store.create_book(book)
        with pytest.raises(BookExistsError):
            await store.create_book(book)


async def test_get_book_missing_raises(store: BooksStore):
    async with store:
        with pytest.raises(BookNotFoundError):
            await store.get_book("nope")


async def test_update_book_persists_fields(store: BooksStore):
    async with store:
        book = Book.new(title="Old")
        await store.create_book(book)
        updated = dataclasses_replace(book, title="New", author="Bob")
        await store.update_book(updated)
        assert (await store.get_book(book.id)).title == "New"


async def test_list_books(store: BooksStore):
    async with store:
        b1 = Book.new(title="One")
        b2 = Book.new(title="Two")
        await store.create_book(b1)
        await store.create_book(b2)
        listed = await store.list_books()
        assert {b.id for b in listed} == {b1.id, b2.id}


async def test_delete_book_removes_nodes(store: BooksStore):
    async with store:
        book = Book.new(title="Gone")
        await store.create_book(book)
        root = BookNode.new(book_id=book.id)
        await store.create_nodes(book.id, [root])
        await store.delete_book(book.id)
        with pytest.raises(BookNotFoundError):
            await store.get_book(book.id)
        with pytest.raises(NodeNotFoundError):
            await store.get_node(root.id)


async def test_persistence_survives_restart(tmp_path: pathlib.Path, logger):
    base = tmp_path / "books"
    book = Book.new(title="Persist")
    async with BooksStore(logger=logger, config=_config(base)) as s:
        await s.create_book(book)
    async with BooksStore(logger=logger, config=_config(base)) as s2:
        assert (await s2.get_book(book.id)) == book


# --------------------------------------------------------------- Node tree


async def test_create_and_query_root(store: BooksStore):
    async with store:
        book = Book.new(title="T")
        await store.create_book(book)
        root = BookNode.new(book_id=book.id, level=0, title=book.title)
        await store.create_nodes(book.id, [root])
        got = await store.get_root_node(book.id)
        assert got.id == root.id
        assert got.is_root


async def test_get_children_ordered_by_order_index(store: BooksStore):
    async with store:
        book = Book.new(title="T")
        await store.create_book(book)
        root = BookNode.new(book_id=book.id, level=0, title="Root")
        c2 = BookNode.new(book_id=book.id, parent_id=root.id, level=1, order_index=2, title="B")
        c1 = BookNode.new(book_id=book.id, parent_id=root.id, level=1, order_index=1, title="A")
        await store.create_nodes(book.id, [root, c1, c2])
        children = await store.get_children(root.id)
        assert [c.title for c in children] == ["A", "B"]


async def test_get_parent_returns_none_for_root(store: BooksStore):
    async with store:
        book = Book.new(title="T")
        await store.create_book(book)
        root = BookNode.new(book_id=book.id, level=0)
        await store.create_nodes(book.id, [root])
        assert await store.get_parent(root.id) is None


async def test_get_parent_returns_parent(store: BooksStore):
    async with store:
        book = Book.new(title="T")
        await store.create_book(book)
        root = BookNode.new(book_id=book.id, level=0)
        child = BookNode.new(book_id=book.id, parent_id=root.id, level=1, order_index=0)
        await store.create_nodes(book.id, [root, child])
        parent = await store.get_parent(child.id)
        assert parent is not None
        assert parent.id == root.id


async def test_get_descendants_preorder(store: BooksStore):
    async with store:
        book = Book.new(title="T")
        await store.create_book(book)
        root = BookNode.new(book_id=book.id, level=0, title="0")
        a = BookNode.new(book_id=book.id, parent_id=root.id, level=1, order_index=0, title="A")
        b = BookNode.new(book_id=book.id, parent_id=root.id, level=1, order_index=1, title="B")
        a1 = BookNode.new(book_id=book.id, parent_id=a.id, level=2, order_index=0, title="A1")
        await store.create_nodes(book.id, [root, a, a1, b])
        desc = await store.get_descendants(root.id)
        assert [d.title for d in desc] == ["A", "A1", "B"]


async def test_get_tree_preorder_starts_with_root(store: BooksStore):
    async with store:
        book = Book.new(title="T")
        await store.create_book(book)
        root = BookNode.new(book_id=book.id, level=0, title="0")
        a = BookNode.new(book_id=book.id, parent_id=root.id, level=1, order_index=0, title="A")
        await store.create_nodes(book.id, [root, a])
        tree = await store.get_tree(book.id)
        assert tree[0].id == root.id
        assert [n.title for n in tree] == ["0", "A"]


async def test_iter_tree_yields_preorder(store: BooksStore):
    async with store:
        book = Book.new(title="T")
        await store.create_book(book)
        root = BookNode.new(book_id=book.id, level=0, title="0")
        a = BookNode.new(book_id=book.id, parent_id=root.id, level=1, order_index=0, title="A")
        await store.create_nodes(book.id, [root, a])
        titles = [n.title async for n in store.iter_tree(book.id)]
        assert titles == ["0", "A"]


async def test_create_nodes_replaces_existing(store: BooksStore):
    async with store:
        book = Book.new(title="T")
        await store.create_book(book)
        root = BookNode.new(book_id=book.id, level=0, title="old")
        await store.create_nodes(book.id, [root])
        new_root = BookNode.new(book_id=book.id, level=0, title="new")
        await store.create_nodes(book.id, [new_root])
        got = await store.get_root_node(book.id)
        assert got.title == "new"
        with pytest.raises(NodeNotFoundError):
            await store.get_node(root.id)


# ----------------------------------------------------------- Tree validation


async def test_empty_nodes_rejected(store: BooksStore):
    async with store:
        book = Book.new(title="T")
        await store.create_book(book)
        with pytest.raises(TreeValidationError):
            await store.create_nodes(book.id, [])


async def test_multiple_roots_rejected(store: BooksStore):
    async with store:
        book = Book.new(title="T")
        await store.create_book(book)
        r1 = BookNode.new(book_id=book.id, level=0)
        r2 = BookNode.new(book_id=book.id, level=0)
        with pytest.raises(TreeValidationError):
            await store.create_nodes(book.id, [r1, r2])


async def test_level_regression_rejected(store: BooksStore):
    async with store:
        book = Book.new(title="T")
        await store.create_book(book)
        root = BookNode.new(book_id=book.id, level=0)
        # child level not greater than parent.
        bad = BookNode.new(book_id=book.id, parent_id=root.id, level=0, order_index=0)
        with pytest.raises(TreeValidationError):
            await store.create_nodes(book.id, [root, bad])


async def test_missing_parent_rejected(store: BooksStore):
    async with store:
        book = Book.new(title="T")
        await store.create_book(book)
        orphan = BookNode.new(book_id=book.id, parent_id="ghost", level=1, order_index=0)
        with pytest.raises(TreeValidationError):
            await store.create_nodes(book.id, [orphan])


async def test_duplicate_order_index_rejected(store: BooksStore):
    async with store:
        book = Book.new(title="T")
        await store.create_book(book)
        root = BookNode.new(book_id=book.id, level=0)
        c1 = BookNode.new(book_id=book.id, parent_id=root.id, level=1, order_index=0)
        c2 = BookNode.new(book_id=book.id, parent_id=root.id, level=1, order_index=0)
        with pytest.raises(TreeValidationError):
            await store.create_nodes(book.id, [root, c1, c2])


async def test_cycle_rejected(store: BooksStore):
    async with store:
        book = Book.new(title="T")
        await store.create_book(book)
        # a -> b -> a; root is a but a.parent_id != "" so no root — also caught.
        a = BookNode.new(book_id=book.id, node_id="a", parent_id="b", level=2, order_index=0)
        b = BookNode.new(book_id=book.id, node_id="b", parent_id="a", level=1, order_index=0)
        with pytest.raises(TreeValidationError):
            await store.create_nodes(book.id, [a, b])


# ----------------------------------------------------------- Content reading


async def test_read_node_content_and_subtree(store: BooksStore, tmp_path: pathlib.Path):
    """CONTENT.md: 'TITLEHEADFIRSTBODYSECONDBODY'

    root: own body 'FIRSTBODY'
    child A: own body 'SECONDBODY'
    child B: no own body (content_length=0) but still traversed.
    """
    content = "TITLEHEADFIRSTBODYSECONDBODY"
    content_path = tmp_path / "CONTENT.md"
    content_path.write_text(content, encoding="utf-8")

    async with store:
        book = Book.new(title="T", content_path=str(content_path))
        await store.create_book(book)
        root = BookNode.new(
            book_id=book.id,
            level=0,
            title="TITLE",
            title_offset=0,
            title_length=5,
            content_offset=9,
            content_length=9,
        )
        a = BookNode.new(
            book_id=book.id,
            parent_id=root.id,
            level=1,
            order_index=0,
            title="HEAD",
            title_offset=5,
            title_length=4,
            content_offset=18,
            content_length=10,
        )
        b = BookNode.new(
            book_id=book.id,
            parent_id=root.id,
            level=1,
            order_index=1,
            title="",
            content_length=0,
        )
        await store.create_nodes(book.id, [root, a, b])

        assert await store.read_node_content(root.id) == "FIRSTBODY"
        assert await store.read_node_content(a.id) == "SECONDBODY"
        assert await store.read_node_content(b.id) == ""

        # subtree(root) = root body + A body; B contributes nothing.
        assert await store.read_subtree_content(root.id) == "FIRSTBODYSECONDBODY"
        # subtree(A) = only A's own body.
        assert await store.read_subtree_content(a.id) == "SECONDBODY"


async def test_read_node_content_empty_when_length_zero(store: BooksStore, tmp_path: pathlib.Path):
    content_path = tmp_path / "CONTENT.md"
    content_path.write_text("x", encoding="utf-8")
    async with store:
        book = Book.new(title="T", content_path=str(content_path))
        await store.create_book(book)
        root = BookNode.new(book_id=book.id, level=0, content_length=0)
        await store.create_nodes(book.id, [root])
        assert await store.read_node_content(root.id) == ""


# ----------------------------------------------------------------- helpers


def dataclasses_replace(obj: Book, **changes) -> Book:
    """Replace fields on a frozen dataclass without importing dataclasses inline."""
    import dataclasses

    return dataclasses.replace(obj, **changes)
