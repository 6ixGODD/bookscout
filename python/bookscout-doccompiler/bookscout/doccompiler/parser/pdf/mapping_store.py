"""PDF source-mapping SQLite store.

Persists :class:`~bookscout.doccompiler.types.PdfSourceMapping` entries and
provides reverse-lookup from ``CONTENT.md`` character offset to PDF page/bbox
(spec §4.2, §4.4).
"""

from __future__ import annotations

import pathlib
import typing as t

from sqlmodel import select

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.doccompiler.types import PdfSourceMapping
from bookscout.logging.mixin import LoggingMixin
from bookscout.sqlite import SQLite
from bookscout.sqlite import SQLiteConfig

if t.TYPE_CHECKING:
    from bookscout.logging import Logger


class PdfMappingStore(LoggingMixin, AsyncResourceMixin):
    """SQLite-backed store for PDF → CONTENT.md source mappings.

    Args:
        logger: Logger instance.
        db_path: Path to the mapping SQLite database file.
    """

    def __init__(self, logger: Logger, db_path: pathlib.Path) -> None:
        super().__init__(logger=logger)
        self._db_path = db_path
        self._sqlite = SQLite(
            config=SQLiteConfig(uri=f"sqlite+aiosqlite:///{db_path.as_posix()}"),
            logger=logger,
        )

    async def startup(self) -> None:
        """Open the database and create the mapping table."""
        from ...models import PdfMappingModel

        await self._sqlite.startup()
        await self._sqlite.create_all([PdfMappingModel])
        await super().startup()
        self.logger.debug("pdf mapping store started", db_path=str(self._db_path))

    async def shutdown(self) -> None:
        """Close the database engine."""
        await self._sqlite.shutdown()

    async def write_mappings(self, mappings: t.Sequence[PdfSourceMapping]) -> None:
        """Bulk-insert mapping entries.

        Args:
            mappings: The PDF source mappings to persist.
        """
        from ...models import PdfMappingModel

        async with self._sqlite.session() as session:
            for m in mappings:
                session.add(
                    PdfMappingModel(
                        book_id=m.book_id,
                        content_offset=m.content_offset,
                        content_length=m.content_length,
                        page_index=m.page_index,
                        x0=m.x0,
                        y0=m.y0,
                        x1=m.x1,
                        y1=m.y1,
                        parser_name=m.parser_name,
                        parser_version=m.parser_version,
                    )
                )
            await session.commit()
        self.logger.debug("pdf mappings written", count=len(mappings))

    async def lookup(self, content_offset: int) -> list[PdfSourceMapping]:
        """Find all mappings covering the given ``CONTENT.md`` offset.

        Args:
            content_offset: Character offset in ``CONTENT.md``.

        Returns:
            All matching PDF source mappings.
        """
        from ...models import PdfMappingModel

        async with self._sqlite.session() as session:
            stmt = select(PdfMappingModel).where(
                PdfMappingModel.content_offset <= content_offset,
                (PdfMappingModel.content_offset + PdfMappingModel.content_length) > content_offset,
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [
                PdfSourceMapping(
                    book_id=r.book_id,
                    content_offset=r.content_offset,
                    content_length=r.content_length,
                    page_index=r.page_index,
                    x0=r.x0,
                    y0=r.y0,
                    x1=r.x1,
                    y1=r.y1,
                    parser_name=r.parser_name,
                    parser_version=r.parser_version,
                )
                for r in rows
            ]

    async def lookup_page(self, page_index: int) -> list[PdfSourceMapping]:
        """Find all mappings for a given PDF page.

        Args:
            page_index: PDF page index (0-based).

        Returns:
            All mappings on that page.
        """
        from ...models import PdfMappingModel

        async with self._sqlite.session() as session:
            stmt = select(PdfMappingModel).where(PdfMappingModel.page_index == page_index)
            rows = (await session.execute(stmt)).scalars().all()
            return [
                PdfSourceMapping(
                    book_id=r.book_id,
                    content_offset=r.content_offset,
                    content_length=r.content_length,
                    page_index=r.page_index,
                    x0=r.x0,
                    y0=r.y0,
                    x1=r.x1,
                    y1=r.y1,
                    parser_name=r.parser_name,
                    parser_version=r.parser_version,
                )
                for r in rows
            ]
