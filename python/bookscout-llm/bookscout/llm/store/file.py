"""LLM file store — upload files, track metadata in SQLite, delegate blob
storage to FileStore.

Files uploaded through the LLM subsystem are stored as blobs in the
:class:`bookscout.filestore.FileStore` and indexed in a local SQLite table.
Images are automatically classified based on their MIME type.
"""

from __future__ import annotations

import mimetypes
import typing as t

from sqlmodel import Field as _Field
from sqlmodel import SQLModel as _SQLModel
from sqlmodel import col
from sqlmodel import select

from bookscout.core.lib.utils import gen_id
from bookscout.core.lib.utils import utcnow_ts
from bookscout.filestore import FileStore
from bookscout.llm.exceptions import FileNotFoundLLMError
from bookscout.llm.exceptions import FileUploadError
from bookscout.llm.exceptions import handle_errors
from bookscout.logging.mixin import LoggingMixin
from bookscout.sqlite import SQLite

if t.TYPE_CHECKING:
    from bookscout.logging import Logger


class LLMFileRow(_SQLModel, table=True):
    """Index row for an uploaded file."""

    __tablename__ = "llm_file"

    file_id: str = _Field(primary_key=True)
    """Our internal file ID — generated, never from provider APIs."""

    filestore_key: str = _Field(default="")
    """Key in the underlying FileStore (virtual path)."""

    filename: str = _Field(default="")
    """Original filename."""

    mime_type: str = _Field(default="", index=True)
    """MIME type of the file (indexed for queries)."""

    size: int = _Field(default=0)
    """File size in bytes."""

    category: str = _Field(default="document")
    """Auto-classified category — 'image' for image/* MIME types, 'document'
    otherwise."""

    created_at: float = _Field(default_factory=utcnow_ts)


class LLMFileStore(LoggingMixin):
    """Upload files, track metadata in SQLite, delegate blob storage to
    FileStore.

    Args:
        logger: Logger instance.
        sqlite: Initialized :class:`SQLite` instance.
        filestore: Initialized :class:`FileStore` instance for blob storage.
    """

    def __init__(self, logger: Logger, sqlite: SQLite, filestore: FileStore) -> None:
        super().__init__(logger=logger)
        self.sqlite = sqlite
        self.filestore = filestore

    async def startup(self) -> None:
        """Create the file index table."""
        await self.sqlite.create_all([LLMFileRow])
        self.logger.info("LLMFileStore started")

    async def shutdown(self) -> None:
        """No-op — the SQLite engine and FileStore are owned by the parent ChatModel."""
        self.logger.info("LLMFileStore stopped")

    @staticmethod
    def _compute_size(data: bytes | t.IO[bytes]) -> int:
        """Compute the size in bytes of the given data.

        For ``bytes``, returns ``len(data)``. For file-like objects, seeks to
        the end and back, restoring the original position.
        """
        if isinstance(data, bytes):
            return len(data)

        # File-like object: seek to end to measure, then restore position.
        # ``t.IO[bytes]`` already declares tell/seek, so no attribute guards needed.
        original = data.tell()
        data.seek(0, 2)  # Seek to end
        size = data.tell()
        data.seek(original)  # Restore position
        return size

    @handle_errors(exc_type=FileUploadError)  # type: ignore[untyped-decorator]
    async def upload(
        self,
        data: bytes | t.IO[bytes],
        filename: str,
        mime_type: str | None = None,
    ) -> str:
        """Upload a file and return our internal file_id.

        Args:
            data: File data as bytes or a readable binary file-like object.
            filename: Original filename.
            mime_type: Optional MIME type. Guessed from filename if not provided.

        Returns:
            Our internal file_id.
        """
        file_id = gen_id(prefix="file_")

        # Guess MIME type if not provided
        if mime_type is None:
            guessed, _ = mimetypes.guess_type(filename)
            mime_type = guessed or "application/octet-stream"

        # Classify category
        category: str = "image" if mime_type.startswith("image/") else "document"

        # Compute size for metadata
        size = self._compute_size(data)

        # Store blob in FileStore
        filestore_key = f"llm/{file_id}/{filename}"
        await self.filestore.upload(data, key=filestore_key, metadata={"file_id": file_id, "mime_type": mime_type})

        # Index in SQLite
        row = LLMFileRow(
            file_id=file_id,
            filestore_key=filestore_key,
            filename=filename,
            mime_type=mime_type,
            size=size,
            category=category,
        )
        async with self.sqlite.session() as session:
            session.add(row)
            await session.commit()

        self.logger.info(
            "Uploaded file",
            file_id=file_id,
            filename=filename,
            mime_type=mime_type,
            category=category,
            size=size,
        )
        return file_id  # type: ignore[no-any-return]

    @handle_errors(exc_type=FileNotFoundLLMError)  # type: ignore[untyped-decorator]
    async def get(self, file_id: str) -> LLMFileRow:
        """Get file metadata by file_id.

        Args:
            file_id: Our internal file ID.

        Returns:
            The file metadata row.

        Raises:
            FileNotFoundLLMError: If the file does not exist.
        """
        async with self.sqlite.session() as session:
            row = await session.get(LLMFileRow, file_id)
            if row is None:
                raise FileNotFoundLLMError(f"File not found: {file_id!r}")
            return row  # type: ignore[no-any-return]

    @handle_errors(exc_type=FileNotFoundLLMError)  # type: ignore[untyped-decorator]
    async def download(self, file_id: str) -> bytes:
        """Download a file's content by file_id.

        Args:
            file_id: Our internal file ID.

        Returns:
            The file's raw bytes.

        Raises:
            FileNotFoundLLMError: If the file does not exist.
        """
        row = await self.get(file_id)
        data = await self.filestore.download(row.filestore_key)
        # FileStore.download returns bytes when stream=False
        return t.cast(bytes, data)

    @handle_errors(exc_type=FileNotFoundLLMError)  # type: ignore[untyped-decorator]
    async def delete(self, file_id: str) -> None:
        """Delete a file from both the index and FileStore.

        Args:
            file_id: Our internal file ID.
        """
        async with self.sqlite.session() as session:
            row = await session.get(LLMFileRow, file_id)
            if row is None:
                return
            await session.delete(row)
            await session.commit()

        # Also delete from FileStore
        await self.filestore.delete(row.filestore_key)
        self.logger.info("Deleted file", file_id=file_id)

    async def list_by_category(
        self,
        category: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[LLMFileRow]:
        """List files by category, ordered by most recent first."""
        async with self.sqlite.session() as session:
            stmt = (
                select(LLMFileRow)
                .where(LLMFileRow.category == category)
                .order_by(col(LLMFileRow.created_at).desc())  # pylint: disable=no-member
                .offset(offset)
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_by_mime_type(
        self,
        mime_prefix: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[LLMFileRow]:
        """List files whose MIME type starts with the given prefix."""
        async with self.sqlite.session() as session:
            stmt = (
                select(LLMFileRow)
                .where(col(LLMFileRow.mime_type).like(f"{mime_prefix}%"))  # pylint: disable=no-member
                .order_by(col(LLMFileRow.created_at).desc())  # pylint: disable=no-member
                .offset(offset)
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
