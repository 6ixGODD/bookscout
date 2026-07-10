# Copyright 2026 BoChen SHEN
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""File-based content-addressed blob store backed by a SQLite index.

Physical blobs are stored by sha256 (content-addressed) under
``<base_path>/blobs/<hh>/<sha256>``; the storage ``key`` is a *virtual* path
that exists only as a row in the SQLite index. The index also holds user
metadata (replacing the legacy ``.metadata.json`` sidecar), enabling checksum
verification, deduplication, FTS5 search over keys, and directory listings.

The :class:`FileStore` composes a :class:`bookscout.sqlite.SQLite` instance for
index storage and shares its async lifecycle via :class:`AsyncResourceMixin`.
``shutdown`` disposes the engine but **keeps** the ``index.db`` file on disk.
"""

from __future__ import annotations

import builtins
import dataclasses
import hashlib
import os
import pathlib
import typing as t
import uuid

import aiofiles
import aiofiles.os
from pydantic import BaseModel
from pydantic import Field
from sqlalchemy import func
from sqlmodel import col
from sqlmodel import select

from bookscout.core.lib.utils import utcnow_ts
from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin
from bookscout.sqlite import SQLite
from bookscout.sqlite import SQLiteConfig

from .exceptions import CopyError
from .exceptions import DeleteError
from .exceptions import DownloadError
from .exceptions import FetchError
from .exceptions import FileStoreError as FileStoreError
from .exceptions import IndexError  # pylint: disable=redefined-builtin
from .exceptions import IntegrityError
from .exceptions import UploadError
from .exceptions import handle_errors
from .models import FTS_CREATE_SQL
from .models import FTS_TRIGGER_SQL
from .models import FileIndex

if t.TYPE_CHECKING:
    from bookscout.logging import Logger


@dataclasses.dataclass(slots=True)
class DirEntry:
    """A virtual directory entry produced by :meth:`FileStore.list_dir`."""

    name: str
    """Entry name (a single path segment)."""

    is_dir: bool
    """True if the entry is a virtual directory (has children beneath it)."""

    key: str
    """Full virtual key for the entry (directories end with ``/``)."""


class FileStoreConfig(BaseModel):
    """Configuration for :class:`FileStore`."""

    type: t.Literal["filesystem"] = Field(
        default="filesystem",
        description="Type of blob store backend (must be 'filesystem' for FileStore).",
    )

    base_path: os.PathLike[str] | str = Field(
        default="/store",
        description="Base path for local filesystem storage.",
    )

    index_db_name: str = Field(
        default="index.db",
        description="Filename of the SQLite index database inside base_path.",
    )

    shard_depth: int = Field(
        default=2,
        ge=1,
        le=4,
        description="Number of leading hex chars used to shard blob directories.",
    )

    fts: bool = Field(
        default=True,
        description="Whether to build the FTS5 index over storage keys.",
    )


class FileStore(LoggingMixin, AsyncResourceMixin):
    """Content-addressed file store with a SQLite-backed index.

    Blobs are written by sha256 under ``blobs/<hh>/<sha256>``; the storage
    ``key`` is virtual and tracked in the ``file_index`` table. Identical
    content is stored once (deduplication), and :meth:`copy` is a cheap
    key remap. The index supports checksum verification, duplicate lookup,
    FTS5 search over keys, and virtual directory listings.

    Args:
        logger: Logger instance.
        config: Blob store configuration.
    """

    DEFAULT_CHUNK_SIZE: t.ClassVar[int] = 8192

    _RESERVED_DIRS: t.ClassVar[tuple[str, ...]] = ("blobs", "tmp", "quarantine")

    def __init__(self, logger: Logger, config: FileStoreConfig):
        super().__init__(logger=logger)
        self.config = config
        self.base_path = pathlib.Path(config.base_path).resolve()
        self.blobs_path = self.base_path / "blobs"
        self.tmp_path = self.base_path / "tmp"
        self.quarantine_path = self.base_path / "quarantine"
        self.shard_depth = config.shard_depth
        self._db_path = self.base_path / config.index_db_name
        self._sqlite = SQLite(
            config=SQLiteConfig(uri=self._db_uri),
            logger=logger,
        )

    @property
    def _db_uri(self) -> str:
        return f"sqlite+aiosqlite:///{self._db_path.as_posix()}"

    def _blob_path(self, content_hash: str) -> pathlib.Path:
        """Return the physical blob path for a content hash."""
        shard = content_hash[: self.shard_depth]
        return self.blobs_path / shard / content_hash

    @staticmethod
    def _normalize_key(key: str) -> str:
        """Strip leading slashes from a storage key."""
        return key.lstrip("/")

    async def startup(self) -> None:
        """Ensure directories exist, open the index DB, create schema, reconcile.

        If ``base_path`` is missing it is created (with an empty index). If it
        already contains loose files (a legacy layout), :meth:`index` moves
        them into the content-addressed layout and indexes them. The DB engine
        is opened idempotently across restarts.
        """
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.blobs_path.mkdir(parents=True, exist_ok=True)
        self.tmp_path.mkdir(parents=True, exist_ok=True)
        self.quarantine_path.mkdir(parents=True, exist_ok=True)

        await self._sqlite.startup()
        await self._sqlite.create_all([FileIndex])
        if self.config.fts:
            await self._sqlite.exec(FTS_CREATE_SQL, readonly=False)
            for stmt in FTS_TRIGGER_SQL:
                await self._sqlite.exec(stmt, readonly=False)

        await self.index()
        await super().startup()

    async def shutdown(self) -> None:
        """Dispose the SQLite engine. The ``index.db`` file is left on disk."""
        await self._sqlite.shutdown()

    async def _hash_file(self, path: pathlib.Path) -> tuple[str, int]:
        """Return ``(sha256 hex, size)`` for a file on disk."""
        hasher = hashlib.sha256()
        size = 0
        async with aiofiles.open(path, "rb") as f:
            while True:
                chunk = await f.read(self.DEFAULT_CHUNK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
                size += len(chunk)
        return hasher.hexdigest(), size

    async def _stage_bytes(self, data: bytes | t.IO[bytes]) -> tuple[pathlib.Path, str, int]:
        """Stream ``data`` to a temp file while hashing; return tmp/hash/size."""
        tmp_path = self.tmp_path / uuid.uuid4().hex
        hasher = hashlib.sha256()
        size = 0
        async with aiofiles.open(tmp_path, "wb") as f:
            if isinstance(data, bytes):
                hasher.update(data)
                size = len(data)
                await f.write(data)
            else:
                while True:
                    chunk = data.read(self.DEFAULT_CHUNK_SIZE)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    size += len(chunk)
                    await f.write(chunk)
        return tmp_path, hasher.hexdigest(), size

    async def _stage_parts(self, parts: t.AsyncIterable[bytes]) -> tuple[pathlib.Path, str, int]:
        """Stream an async iterable of chunks to a temp file while hashing."""
        tmp_path = self.tmp_path / uuid.uuid4().hex
        hasher = hashlib.sha256()
        size = 0
        async with aiofiles.open(tmp_path, "wb") as f:
            async for part in parts:
                hasher.update(part)
                size += len(part)
                await f.write(part)
        return tmp_path, hasher.hexdigest(), size

    async def _promote(self, tmp_path: pathlib.Path, content_hash: str) -> pathlib.Path:
        """Move a staged temp file into the content-addressed layout (dedup).

        If a blob with the same hash already exists, the temp file is discarded.
        Returns the canonical blob path.
        """
        blob_path = self._blob_path(content_hash)
        if blob_path.exists():
            await aiofiles.os.remove(tmp_path)
            return blob_path
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        await aiofiles.os.rename(tmp_path, blob_path)
        return blob_path

    async def _get_row(self, key: str) -> FileIndex | None:
        async with self._sqlite.session() as session:
            return t.cast("FileIndex | None", await session.get(FileIndex, key))

    async def _upsert_row(
        self,
        key: str,
        content_hash: str,
        size: int,
        metadata: t.Mapping[str, t.Any] | None,
    ) -> None:
        meta: dict[str, t.Any] | None = dict(metadata) if metadata else None
        async with self._sqlite.session() as session:
            row = await session.get(FileIndex, key)
            if row is None:
                row = FileIndex(
                    key=key,
                    content_hash=content_hash,
                    size=size,
                    meta=meta,
                )
                session.add(row)
            else:
                row.content_hash = content_hash
                row.size = size
                row.meta = meta
                row.modified_at = utcnow_ts()
            await session.commit()

    async def _ref_count(self, content_hash: str) -> int:
        async with self._sqlite.session() as session:
            stmt = select(func.count()).select_from(FileIndex).where(FileIndex.content_hash == content_hash)  # pylint: disable=not-callable
            result = await session.execute(stmt)
            return int(result.scalar_one())

    async def _gc_blob(self, content_hash: str) -> None:
        """Remove a blob if no key references it."""
        if await self._ref_count(content_hash) == 0:
            blob_path = self._blob_path(content_hash)
            if blob_path.exists():
                await aiofiles.os.remove(blob_path)

    @handle_errors(exc_type=UploadError)  # type: ignore[untyped-decorator]
    async def upload(
        self,
        data: bytes | t.IO[bytes],
        key: str,
        metadata: t.Mapping[str, t.Any] | None = None,
        **_kwargs: t.Any,
    ) -> str:
        """Upload data to storage (content-addressed, deduplicated).

        Args:
            data: File data as bytes or a readable binary file-like object.
            key: Storage key (virtual path).
            metadata: Optional metadata to persist in the index.
            **_kwargs: Ignored; present for interface compatibility.

        Returns:
            The storage key.
        """
        key = self._normalize_key(key)
        tmp_path, content_hash, size = await self._stage_bytes(data)
        await self._promote(tmp_path, content_hash)
        await self._upsert_row(key, content_hash, size, metadata)
        return key

    @handle_errors(exc_type=UploadError)  # type: ignore[untyped-decorator]
    async def upload_multipart(
        self,
        parts: t.AsyncIterable[bytes],
        key: str,
        metadata: t.Mapping[str, t.Any] | None = None,
        **_kwargs: t.Any,
    ) -> str:
        """Upload from an async iterable of byte chunks (content-addressed).

        Args:
            parts: Async iterable of byte chunks.
            key: Storage key (virtual path).
            metadata: Optional metadata to persist in the index.
            **_kwargs: Ignored; present for interface compatibility.

        Returns:
            The storage key.
        """
        key = self._normalize_key(key)
        tmp_path, content_hash, size = await self._stage_parts(parts)
        await self._promote(tmp_path, content_hash)
        await self._upsert_row(key, content_hash, size, metadata)
        return key

    @handle_errors(exc_type=FetchError)  # type: ignore[untyped-decorator]
    async def get_metadata(self, key: str) -> builtins.dict[str, t.Any]:
        """Retrieve metadata for a stored object.

        Args:
            key: Storage key.

        Returns:
            A metadata dict (empty if none was stored).

        Raises:
            FileNotFoundError: If the object does not exist.
        """
        key = self._normalize_key(key)
        row = await self._get_row(key)
        if row is None:
            raise FileNotFoundError(f"Object not found: {key!r}")
        return dict(row.meta) if row.meta else {}

    @handle_errors(exc_type=DownloadError)  # type: ignore[untyped-decorator]
    async def download(
        self,
        key: str,
        *,
        stream: bool = False,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        verify: bool = False,
        **_kwargs: t.Any,
    ) -> bytes | t.AsyncIterable[bytes]:
        """Download an object from storage.

        Args:
            key: Storage key.
            stream: When True, return an async iterable of chunks.
            chunk_size: Chunk size in bytes (used only when *stream* is True).
            verify: When True, re-hash the blob and raise
                :class:`IntegrityError` on mismatch.
            **_kwargs: Ignored; present for interface compatibility.

        Returns:
            Raw bytes, or an async iterable of byte chunks when *stream* is True.

        Raises:
            FileNotFoundError: If the object does not exist.
            IntegrityError: If *verify* is True and the checksum mismatches.
        """
        key = self._normalize_key(key)
        row = await self._get_row(key)
        if row is None:
            raise FileNotFoundError(f"Object not found: {key!r}")
        blob_path = self._blob_path(row.content_hash)
        if not blob_path.exists():
            raise FileNotFoundError(f"Blob missing for key: {key!r}")

        if verify:
            actual, _ = await self._hash_file(blob_path)
            if actual != row.content_hash:
                raise IntegrityError(f"Checksum mismatch for {key!r}: expected {row.content_hash}, got {actual}")

        if stream:
            return self._stream_file(blob_path, chunk_size)
        async with aiofiles.open(blob_path, "rb") as f:
            return await f.read()  # type: ignore[no-any-return]

    async def _stream_file(
        self,
        file_path: pathlib.Path,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> t.AsyncIterator[bytes]:
        """Yield the file contents in fixed-size chunks."""
        async with aiofiles.open(file_path, "rb") as f:
            while True:
                chunk = await f.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    @handle_errors(exc_type=DeleteError)  # type: ignore[untyped-decorator]
    async def delete(self, key: str) -> None:
        """Delete an object. The blob is GC'd when no key references it.

        Args:
            key: Storage key.
        """
        key = self._normalize_key(key)
        row = await self._get_row(key)
        if row is None:
            return
        async with self._sqlite.session() as session:
            await session.delete(row)
            await session.commit()
        await self._gc_blob(row.content_hash)

    @handle_errors(exc_type=FetchError)  # type: ignore[untyped-decorator]
    async def list(  # type: ignore[override]
        self,
        prefix: str = "",
        page_size: int = 10,
        **_kwargs: t.Any,
    ) -> t.AsyncIterator[builtins.list[str]]:
        """Yield pages of storage keys whose path begins with *prefix*.

        Args:
            prefix: Path prefix used to filter results.
            page_size: Maximum number of keys per yielded page.
            **_kwargs: Ignored; present for interface compatibility.

        Yields:
            Non-empty lists of storage keys.
        """
        prefix = self._normalize_key(prefix)
        async with self._sqlite.session() as session:
            stmt = select(FileIndex.key).order_by(FileIndex.key)
            result = await session.execute(stmt)
            keys = [row[0] for row in result.all() if row[0].startswith(prefix)]

        current_page: builtins.list[str] = []
        for key in keys:
            current_page.append(key)
            if len(current_page) >= page_size:
                yield current_page
                current_page = []
        if current_page:
            yield current_page

    @handle_errors(exc_type=FetchError)  # type: ignore[untyped-decorator]
    async def exists(self, key: str) -> bool:
        """Return True if an object with the given key exists."""
        return await self._get_row(self._normalize_key(key)) is not None

    @handle_errors(exc_type=DeleteError)  # type: ignore[untyped-decorator]
    async def clear(self, prefix: str = "") -> None:
        """Delete all objects whose key starts with *prefix* and GC blobs.

        Args:
            prefix: Path prefix to scope the deletion.
        """
        prefix = self._normalize_key(prefix)
        affected_hashes: set[str] = set()
        async with self._sqlite.session() as session:
            stmt = select(FileIndex).where(col(FileIndex.key).like(f"{prefix}%"))  # pylint: disable=no-member
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            for row in rows:
                affected_hashes.add(row.content_hash)
                await session.delete(row)
            await session.commit()
        for content_hash in affected_hashes:
            await self._gc_blob(content_hash)

    @handle_errors(exc_type=CopyError)  # type: ignore[untyped-decorator]
    async def copy(self, source_key: str, dest_key: str, **_kwargs: t.Any) -> str:
        """Copy an object to a new key (cheap key remap, no physical copy).

        Args:
            source_key: Source storage key.
            dest_key: Destination storage key.
            **_kwargs: Ignored; present for interface compatibility.

        Returns:
            The destination key.

        Raises:
            FileNotFoundError: If the source object does not exist.
        """
        source_key = self._normalize_key(source_key)
        dest_key = self._normalize_key(dest_key)
        row = await self._get_row(source_key)
        if row is None:
            raise FileNotFoundError(f"Source object not found: {source_key!r}")
        await self._upsert_row(dest_key, row.content_hash, row.size, row.meta)
        return dest_key

    @handle_errors(exc_type=IndexError)  # type: ignore[untyped-decorator]
    async def index(self) -> None:
        """Reconcile the filesystem with the index DB.

        Three passes:

        1. **Integrity**: re-hash every blob; quarantine any whose name does not
           match its content hash.
        2. **Dangling keys**: remove index rows whose blob is missing (e.g.
           quarantined above).
        3. **Legacy ingest**: any loose file under ``base_path`` outside the
           reserved directories is treated as a key (its relative path), hashed,
           moved into the content-addressed layout, and indexed.
        """
        # 1. Integrity check.
        if self.blobs_path.exists():
            for blob in self.blobs_path.rglob("*"):
                if not blob.is_file():
                    continue
                actual, _ = await self._hash_file(blob)
                if actual != blob.name:
                    dest = self.quarantine_path / f"{blob.name}.{actual[:8]}"
                    self.logger.warning(f"Quarantining corrupt blob {blob.name}: got {actual}")
                    await aiofiles.os.rename(blob, dest)

        # 2. Drop dangling keys.
        async with self._sqlite.session() as session:
            stmt = select(FileIndex.key, FileIndex.content_hash)
            result = await session.execute(stmt)
            dangling = [(key, h) for key, h in result.all() if not self._blob_path(h).exists()]
            for key, _ in dangling:
                obj = await session.get(FileIndex, key)
                if obj is not None:
                    await session.delete(obj)
            await session.commit()

        # 3. Legacy ingest.
        await self._ingest_legacy_files()

    async def _ingest_legacy_files(self) -> None:
        """Move loose pre-existing files into the CAS layout and index them."""
        db_name = self.config.index_db_name
        candidates: list[pathlib.Path] = []
        for entry in self.base_path.iterdir():
            if entry.name in self._RESERVED_DIRS:
                continue
            if entry.name == db_name or entry.name.startswith(db_name + "-"):
                continue
            if entry.is_dir():
                candidates.extend(f for f in entry.rglob("*") if f.is_file())
            elif entry.is_file():
                candidates.append(entry)

        for path in candidates:
            key = path.relative_to(self.base_path).as_posix()
            content_hash, size = await self._hash_file(path)
            blob_path = self._blob_path(content_hash)
            if blob_path.exists():
                await aiofiles.os.remove(path)
            else:
                blob_path.parent.mkdir(parents=True, exist_ok=True)
                await aiofiles.os.rename(path, blob_path)
            await self._upsert_row(key, content_hash, size, None)
            self._prune_empty_dirs(path.parent)

    def _prune_empty_dirs(self, start: pathlib.Path) -> None:
        """Remove empty directories up the tree from *start* (stopping at base)."""
        current = start
        while current != self.base_path and current.exists():
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    @handle_errors(exc_type=FetchError)  # type: ignore[untyped-decorator]
    async def verify(self, key: str | None = None) -> builtins.list[builtins.dict[str, str]]:
        """Verify blob checksums against the index.

        Args:
            key: When given, verify only that key's blob; otherwise verify all.

        Returns:
            A list of mismatch dicts ``{"key", "expected", "actual"}``.
        """
        mismatches: builtins.list[builtins.dict[str, str]] = []
        rows: list[tuple[str, str]] = []
        if key is not None:
            row = await self._get_row(self._normalize_key(key))
            if row is None:
                raise FileNotFoundError(f"Object not found: {key!r}")
            rows = [(row.key, row.content_hash)]
        else:
            async with self._sqlite.session() as session:
                result = await session.execute(select(FileIndex.key, FileIndex.content_hash))
                rows = list(result.all())

        for k, expected in rows:
            blob_path = self._blob_path(expected)
            if not blob_path.exists():
                mismatches.append({"key": k, "expected": expected, "actual": "<missing>"})
                continue
            actual, _ = await self._hash_file(blob_path)
            if actual != expected:
                mismatches.append({"key": k, "expected": expected, "actual": actual})
        return mismatches

    @handle_errors(exc_type=FetchError)  # type: ignore[untyped-decorator]
    async def find_duplicates(self) -> builtins.list[tuple[str, builtins.list[str]]]:
        """Return ``(content_hash, [keys])`` for content stored under >1 key."""
        async with self._sqlite.session() as session:
            result = await session.execute(select(FileIndex.key, FileIndex.content_hash))
            by_hash: dict[str, builtins.list[str]] = {}
            for key, content_hash in result.all():
                by_hash.setdefault(content_hash, []).append(key)
        return [(h, keys) for h, keys in by_hash.items() if len(keys) > 1]

    @handle_errors(exc_type=FetchError)  # type: ignore[untyped-decorator]
    async def find_by_checksum(self, content_hash: str) -> builtins.list[str]:
        """Return all keys whose content checksum equals *content_hash*."""
        async with self._sqlite.session() as session:
            result = await session.execute(select(FileIndex.key).where(FileIndex.content_hash == content_hash))
            return [row[0] for row in result.all()]

    @handle_errors(exc_type=FetchError)  # type: ignore[untyped-decorator]
    async def list_dir(self, prefix: str = "") -> builtins.list[DirEntry]:
        """List immediate virtual children under *prefix*.

        Args:
            prefix: Virtual directory prefix to list.

        Returns:
            Sorted list of :class:`DirEntry` (directories first).
        """
        prefix = self._normalize_key(prefix)
        async with self._sqlite.session() as session:
            result = await session.execute(select(FileIndex.key))
            keys = [row[0] for row in result.all() if row[0].startswith(prefix)]

        children: dict[str, bool] = {}
        for key in keys:
            rest = key[len(prefix) :].lstrip("/")
            if not rest:
                continue
            name, _, remainder = rest.partition("/")
            # A name is a directory if any key has a segment beneath it.
            children[name] = children.get(name, False) or bool(remainder)

        entries = [
            DirEntry(
                name=name,
                is_dir=is_dir,
                key=prefix + name + "/" if is_dir else prefix + name,
            )
            for name, is_dir in children.items()
        ]
        entries.sort(key=lambda e: (not e.is_dir, e.name))
        return entries

    @handle_errors(exc_type=FetchError)  # type: ignore[untyped-decorator]
    async def search(self, query: str) -> builtins.list[str]:
        """Full-text search storage keys via FTS5.

        Falls back to a ``LIKE`` substring match when FTS5 is disabled or the
        query is not a valid FTS5 expression.

        Args:
            query: FTS5 match expression (e.g. a word or prefix with ``*``).

        Returns:
            Matching storage keys.
        """
        if not self.config.fts:
            async with self._sqlite.session() as session:
                result = await session.execute(select(FileIndex.key).where(col(FileIndex.key).like(f"%{query}%")))  # pylint: disable=no-member
                return [row[0] for row in result.all()]

        try:
            result = await self._sqlite.exec(
                "SELECT key FROM file_index_fts WHERE key MATCH :q",
                readonly=True,
                q=query,
            )
            return [row[0] for row in result.all()]
        except Exception:  # pylint: disable=broad-exception-caught
            # Invalid FTS5 expression or query error 鈥?fall back to substring
            # match. Caught before the ``handle_errors`` decorator wraps it.
            async with self._sqlite.session() as session:
                like_result = await session.execute(select(FileIndex.key).where(col(FileIndex.key).like(f"%{query}%")))  # pylint: disable=no-member
                return [row[0] for row in like_result.all()]

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(config={self.config!r})"
