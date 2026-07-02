"""EPUB source-mapping SQLite store.

Persists :class:`~bookscout.doccompiler.types.EpubSourceMapping` entries and
provides reverse-lookup from ``CONTENT.md`` character offset to EPUB source
positions (spec §4.2, §4.5).
"""

from __future__ import annotations

import pathlib
import typing as t

from sqlmodel import select

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.doccompiler.types import EpubSourceMapping
from bookscout.logging.mixin import LoggingMixin
from bookscout.sqlite import SQLite
from bookscout.sqlite import SQLiteConfig

if t.TYPE_CHECKING:
    from bookscout.logging import Logger

    from ...models import EpubMappingModel  # noqa: F401


class EpubMappingStore(LoggingMixin, AsyncResourceMixin):
    """SQLite-backed store for EPUB → CONTENT.md source mappings.

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
        from ...models import EpubMappingModel

        await self._sqlite.startup()
        await self._sqlite.create_all([EpubMappingModel])
        await super().startup()
        self.logger.debug("epub mapping store started", db_path=str(self._db_path))

    async def shutdown(self) -> None:
        """Close the database engine."""
        await self._sqlite.shutdown()

    async def write_mappings(self, mappings: t.Sequence[EpubSourceMapping]) -> None:
        """Bulk-insert mapping entries.

        Args:
            mappings: The EPUB source mappings to persist.
        """
        from ...models import EpubMappingModel

        async with self._sqlite.session() as session:
            for m in mappings:
                session.add(
                    EpubMappingModel(
                        book_id=m.book_id,
                        content_offset=m.content_offset,
                        content_length=m.content_length,
                        href=m.href,
                        spine_index=m.spine_index,
                        element_tag=m.element_tag,
                        element_index=m.element_index,
                        element_id=m.element_id,
                        element_path=m.element_path,
                        parser_name=m.parser_name,
                        parser_version=m.parser_version,
                    )
                )
            await session.commit()
        self.logger.debug("epub mappings written", count=len(mappings))

    async def lookup(self, content_offset: int) -> list[EpubSourceMapping]:
        """Find all mappings covering the given ``CONTENT.md`` offset.

        A mapping covers ``content_offset`` when
        ``mapping.content_offset <= offset < mapping.content_offset + content_length``.

        Args:
            content_offset: Character offset in ``CONTENT.md``.

        Returns:
            All matching EPUB source mappings.
        """
        from ...models import EpubMappingModel

        async with self._sqlite.session() as session:
            stmt = select(EpubMappingModel).where(
                EpubMappingModel.content_offset <= content_offset,
                (EpubMappingModel.content_offset + EpubMappingModel.content_length) > content_offset,
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [
                EpubSourceMapping(
                    book_id=r.book_id,
                    content_offset=r.content_offset,
                    content_length=r.content_length,
                    href=r.href,
                    spine_index=r.spine_index,
                    element_tag=r.element_tag,
                    element_index=r.element_index,
                    element_id=r.element_id,
                    element_path=r.element_path,
                    parser_name=r.parser_name,
                    parser_version=r.parser_version,
                )
                for r in rows
            ]
