"""Tests for the IndexManifest model and BooksStore manifest methods."""

from __future__ import annotations

from bookscout.books import Book
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
