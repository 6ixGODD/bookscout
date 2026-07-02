"""Parser abstraction — converts source documents to CONTENT.md + mappings.

Every parser implements :class:`DocParser`, taking a source file and a
:class:`~bookscout.doccompiler.workspace.BookWorkspace`, and producing a
:class:`~bookscout.doccompiler.types.ParserResult`.

Parsers do **not** write to the ``bookscout-books`` ontology database; that is
the compiler's job. Parsers own their mapping SQLite (spec §4.2, §5.2).
"""

from __future__ import annotations

import abc
import pathlib
import typing as t

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin

from ..types import ParserResult
from ..workspace import BookWorkspace

if t.TYPE_CHECKING:
    from bookscout.logging import Logger


class DocParser(LoggingMixin, AsyncResourceMixin, abc.ABC):
    """Abstract base class for all document parsers.

    Subclasses implement :meth:`parse` to convert a source document into
    ``CONTENT.md`` plus a source-mapping SQLite inside the given workspace.

    Args:
        logger: Logger instance.
    """

    def __init__(self, logger: Logger) -> None:
        super().__init__(logger=logger)

    @abc.abstractmethod
    async def parse(
        self,
        source_path: pathlib.Path,
        book_id: str,
        workspace: BookWorkspace,
    ) -> ParserResult:
        """Parse a source document and produce a :class:`ParserResult`.

        Args:
            source_path: Path to the source file (EPUB, PDF, etc.).
            book_id: The book id this parse belongs to.
            workspace: The book workspace to write artifacts into.

        Returns:
            A :class:`ParserResult` with paths to CONTENT.md and the
            mapping database.
        """

    async def startup(self) -> None:
        """Default no-op startup; subclasses may override."""
        await super().startup()

    async def shutdown(self) -> None:
        """Default no-op shutdown; subclasses may override."""
        await super().shutdown()
