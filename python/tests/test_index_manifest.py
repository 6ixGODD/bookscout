"""Tests for the IndexManifest model and BooksStore manifest methods."""

from __future__ import annotations

import pytest

from bookscout.books import Book
from bookscout.books import BooksConfig
from bookscout.books import BooksStore
from bookscout.books.types import IndexInfo


def test_book_has_indexes_field_default_empty():
    book = Book.new(title="t")
    assert book.indexes == ()


def test_book_indexes_field_settable():
    book = Book.new(title="t")
    # Book is frozen; reconstruct with indexes via dataclasses.replace.
    import dataclasses

    book2 = dataclasses.replace(book, indexes=("chunk", "summary"))
    assert book2.indexes == ("chunk", "summary")


def test_index_info_dataclass():
    info = IndexInfo(index_type="chunk", status="built", count=10, error="", built_at=1000.0)
    assert info.index_type == "chunk"
    assert info.status == "built"
    assert info.count == 10


def test_index_manifest_model_tablename():
    from bookscout.books.models import IndexManifestModel

    assert IndexManifestModel.__tablename__ == "index_manifest"


@pytest.fixture()
async def store(tmp_path, logger):
    s = BooksStore(logger=logger, config=BooksConfig(base_path=tmp_path, db_name="books.sqlite"))
    await s.startup()
    yield s
    await s.shutdown()


@pytest.mark.asyncio()
async def test_upsert_and_list_indexes(store):
    from bookscout.books import Book

    book = Book.new(title="t")
    await store.create_book(book)
    await store.upsert_index(book.id, "chunk", "built", count=5)
    info = await store.list_indexes(book.id)
    assert len(info) == 1
    assert info[0].index_type == "chunk"
    assert info[0].status == "built"
    assert info[0].count == 5


@pytest.mark.asyncio()
async def test_list_index_types_only_built(store):
    from bookscout.books import Book

    book = Book.new(title="t")
    await store.create_book(book)
    await store.upsert_index(book.id, "chunk", "built")
    await store.upsert_index(book.id, "graph", "failed", error="boom")
    types = await store.list_index_types(book.id)
    assert types == {"chunk"}


@pytest.mark.asyncio()
async def test_upsert_is_idempotent_update(store):
    from bookscout.books import Book

    book = Book.new(title="t")
    await store.create_book(book)
    await store.upsert_index(book.id, "chunk", "building")
    await store.upsert_index(book.id, "chunk", "built", count=10)
    info = await store.list_indexes(book.id)
    assert len(info) == 1
    assert info[0].status == "built"
    assert info[0].count == 10


@pytest.mark.asyncio()
async def test_set_index_status_removed(store):
    from bookscout.books import Book

    book = Book.new(title="t")
    await store.create_book(book)
    await store.upsert_index(book.id, "graph", "built")
    await store.set_index_status(book.id, "graph", "removed")
    types = await store.list_index_types(book.id)
    assert types == set()
    info = await store.list_indexes(book.id)
    assert info[0].status == "removed"


@pytest.mark.asyncio()
async def test_all_book_ids(store):
    from bookscout.books import Book

    b1 = Book.new(title="a", book_id="book_a")
    b2 = Book.new(title="b", book_id="book_b")
    await store.create_book(b1)
    await store.create_book(b2)
    ids = await store.all_book_ids()
    assert set(ids) == {"book_a", "book_b"}


@pytest.mark.asyncio()
async def test_list_books_has_indexes(store):
    from bookscout.books import Book

    book = Book.new(title="t", book_id="book_x")
    await store.create_book(book)
    await store.upsert_index(book.id, "chunk", "built")
    await store.upsert_index(book.id, "summary", "built")
    books = await store.list_books()
    b = books[0]
    assert set(b.indexes) == {"chunk", "summary"}
