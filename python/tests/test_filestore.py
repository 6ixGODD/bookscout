"""Tests for the content-addressed, SQLite-indexed FileStore."""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from bookscout.filestore import FileStore
from bookscout.filestore import FileStoreConfig
from bookscout.filestore.exceptions import IntegrityError

# ----------------------------------------------------------------- fixtures


def _config(base_path: pathlib.Path, **overrides) -> FileStoreConfig:
    return FileStoreConfig(base_path=base_path, **overrides)


@pytest.fixture()
def store(tmp_path: pathlib.Path, logger) -> FileStore:
    s = FileStore(logger=logger, config=_config(tmp_path / "store"))
    yield s


# ------------------------------------------------------------------- tests


async def test_startup_creates_empty_store(store: FileStore):
    """Empty base_path on startup → dirs + empty DB, no rows."""
    async with store:
        assert store.base_path.exists()
        assert store.blobs_path.exists()
        assert store.tmp_path.exists()
        assert store.quarantine_path.exists()
        assert store._db_path.exists()
        pages = [p async for p in store.list()]
        assert pages == []


async def test_upload_indexes_blob_and_metadata(store: FileStore):
    async with store:
        await store.upload(b"hello world", "books/epub/foo.epub", metadata={"a": 1})
        assert await store.exists("books/epub/foo.epub")
        meta = await store.get_metadata("books/epub/foo.epub")
        assert meta == {"a": 1}
        # blob stored by hash, not by key path
        expected_hash = hashlib.sha256(b"hello world").hexdigest()
        assert store._blob_path(expected_hash).exists()
        # the key path itself must NOT exist as a physical file
        assert not (store.base_path / "books" / "epub" / "foo.epub").exists()


async def test_upload_dedup_same_content_single_blob(store: FileStore):
    async with store:
        await store.upload(b"same content", "a.txt")
        await store.upload(b"same content", "b.txt")
        expected_hash = hashlib.sha256(b"same content").hexdigest()
        blobs = list(store.blobs_path.rglob("*"))
        blob_files = [b for b in blobs if b.is_file()]
        assert len(blob_files) == 1
        assert blob_files[0].name == expected_hash
        dups = await store.find_duplicates()
        assert dups == [(expected_hash, ["a.txt", "b.txt"])]


async def test_copy_is_zero_physical_copy(store: FileStore):
    async with store:
        await store.upload(b"payload", "src.txt")
        blob_count_before = len([b for b in store.blobs_path.rglob("*") if b.is_file()])
        await store.copy("src.txt", "dst.txt")
        blob_count_after = len([b for b in store.blobs_path.rglob("*") if b.is_file()])
        assert blob_count_after == blob_count_before  # no new physical blob
        assert await store.exists("dst.txt")
        assert (await store.download("dst.txt")) == b"payload"


async def test_delete_gc_blob_when_no_references(store: FileStore):
    async with store:
        await store.upload(b"gc me", "a.txt")
        await store.upload(b"gc me", "b.txt")
        expected_hash = hashlib.sha256(b"gc me").hexdigest()
        assert store._blob_path(expected_hash).exists()

        await store.delete("a.txt")
        # one ref remains → blob still present
        assert store._blob_path(expected_hash).exists()

        await store.delete("b.txt")
        # no refs → blob removed
        assert not store._blob_path(expected_hash).exists()
        assert not await store.exists("a.txt")


async def test_verify_passes_and_detects_corruption(store: FileStore):
    async with store:
        await store.upload(b"intact", "ok.txt")
        assert await store.verify() == []
        assert await store.verify("ok.txt") == []

        # corrupt the blob in place
        row = await store._get_row("ok.txt")
        blob = store._blob_path(row.content_hash)
        blob.write_bytes(b"tampered")

        mismatches = await store.verify()
        assert len(mismatches) == 1
        assert mismatches[0]["key"] == "ok.txt"
        assert mismatches[0]["expected"] == row.content_hash

        with pytest.raises(IntegrityError):
            await store.download("ok.txt", verify=True)


async def test_legacy_ingest_moves_files_into_cas(tmp_path: pathlib.Path, logger):
    base = tmp_path / "legacy"
    base.mkdir()
    # pre-existing loose file with the old key-path layout
    (base / "docs").mkdir()
    (base / "docs" / "note.txt").write_bytes(b"legacy note")
    (base / "loose.bin").write_bytes(b"loose bytes")

    store = FileStore(logger=logger, config=_config(base))
    async with store:
        # ingest happened on startup via index()
        assert await store.exists("docs/note.txt")
        assert await store.exists("loose.bin")
        assert (await store.download("docs/note.txt")) == b"legacy note"
        # original loose files are gone
        assert not (base / "loose.bin").exists()
        assert not (base / "docs" / "note.txt").exists()
        # ...and content is now in blobs/
        blob_files = [b for b in store.blobs_path.rglob("*") if b.is_file()]
        assert len(blob_files) == 2


async def test_reconcile_repairs_dangling_and_quarantines_corrupt(
    store: FileStore,
):
    async with store:
        await store.upload(b"good", "keep.txt")
        await store.upload(b"doomed", "drop.txt")

        # simulate a lost blob: delete drop.txt's blob but keep the row
        row = await store._get_row("drop.txt")
        store._blob_path(row.content_hash).unlink()

        # simulate a corrupt blob: rewrite keep.txt's blob under its hash name
        keep_row = await store._get_row("keep.txt")
        keep_blob = store._blob_path(keep_row.content_hash)
        keep_blob.write_bytes(b"corrupted content here")

        await store.index()  # reconcile

        # dangling key removed
        assert not await store.exists("drop.txt")
        # corrupt blob quarantined → keep.txt now dangling → removed
        assert not await store.exists("keep.txt")
        quarantined = list(store.quarantine_path.glob("*"))
        assert len(quarantined) == 1


async def test_fts_search_matches_keys(store: FileStore):
    async with store:
        await store.upload(b"a", "books/epub/alice.epub")
        await store.upload(b"b", "books/pdf/bob_report.pdf")
        await store.upload(b"c", "images/logo.png")

        results = await store.search("alice")
        assert results == ["books/epub/alice.epub"]

        # prefix token search
        results = sorted(await store.search("book*"))
        assert results == ["books/epub/alice.epub", "books/pdf/bob_report.pdf"]

        # invalid FTS expression falls back to substring LIKE
        results = await store.search('"unbalanced')
        assert results == []


async def test_list_and_list_dir_and_find_by_checksum(store: FileStore):
    async with store:
        await store.upload(b"1", "books/epub/alice.epub")
        await store.upload(b"2", "books/epub/bob.epub")
        await store.upload(b"3", "books/pdf/report.pdf")
        await store.upload(b"4", "images/logo.png")

        # list with prefix + paging
        pages = [p async for p in store.list(prefix="books/", page_size=2)]
        flat = [k for page in pages for k in page]
        assert flat == [
            "books/epub/alice.epub",
            "books/epub/bob.epub",
            "books/pdf/report.pdf",
        ]

        # list_dir at root
        entries = await store.list_dir("")
        names = {e.name: e for e in entries}
        assert names["books"].is_dir
        assert names["images"].is_dir
        assert names["books"].key == "books/"

        # list_dir under books/
        sub = await store.list_dir("books/")
        sub_names = {e.name: e for e in sub}
        assert sub_names["epub"].is_dir
        assert sub_names["pdf"].is_dir

        # find_by_checksum
        h = hashlib.sha256(b"1").hexdigest()
        assert await store.find_by_checksum(h) == ["books/epub/alice.epub"]


async def test_shutdown_keeps_db_file(store: FileStore):
    async with store:
        await store.upload(b"persist", "p.txt")
    # after context exit (shutdown), the index.db file must remain on disk
    assert store._db_path.exists()
