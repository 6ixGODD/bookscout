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
"""Indexer abstraction 鈥?builds derived indexes from a compiled ontology.

Every indexer implements :class:`Indexer`, taking a book_id + workspace
and building its index (Summary, Chunk, or Graph). The indexer tracks
progress via :class:`IndexProgress`.

Concrete implementations live in separate packages:
    * ``bookscout-index-summary`` 鈥?:class:`SummaryIndexer`
    * ``bookscout-index-chunk`` 鈥?:class:`ChunkIndexer`
    * ``bookscout-index-graph`` 鈥?:class:`GraphIndexer`
"""

from __future__ import annotations

import abc
import dataclasses
import typing as t

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin

if t.TYPE_CHECKING:
    from bookscout.books import BooksStore
    from bookscout.doccompiler.workspace import BookWorkspace
    from bookscout.logging import Logger


@dataclasses.dataclass(slots=True)
class IndexProgress:
    """Progress snapshot for an index build operation.

    Attributes:
        total: Total items to process (nodes, chunks, etc.).
        processed: Items processed so far.
        status: Current status ("pending", "running", "done", "failed").
        error: Error message if failed.
    """

    total: int
    processed: int
    status: str
    error: str


@dataclasses.dataclass(slots=True)
class IndexResult:
    """Result of an index build operation.

    Attributes:
        index_type: The index type name ("summary", "chunk", "graph").
        count: Number of items indexed (summaries, chunks, entities, etc.).
        progress: Final progress state.
    """

    index_type: str
    count: int
    progress: IndexProgress


class Indexer(LoggingMixin, AsyncResourceMixin, abc.ABC):
    """Abstract base class for derived-layer indexers.

    Subclasses implement :meth:`build_index` to construct their index
    from a compiled book ontology.

    Args:
        logger: Logger instance.
        books_store: The BooksStore to read node content from.
    """

    def __init__(self, logger: Logger, books_store: BooksStore) -> None:
        super().__init__(logger=logger)
        self._books_store = books_store
        self._progress = IndexProgress(total=0, processed=0, status="pending", error="")

    @property
    def progress(self) -> IndexProgress:
        """Current build progress."""
        return self._progress

    def _update_progress(self, **kwargs: t.Any) -> None:
        """Update progress fields."""
        for k, v in kwargs.items():
            setattr(self._progress, k, v)

    @abc.abstractmethod
    async def build_index(
        self,
        book_id: str,
        workspace: BookWorkspace,
        *,
        monitor: t.Any = None,
        parent_id: str | None = None,
    ) -> IndexResult:
        """Build the derived index for a book.

        Args:
            book_id: The book id.
            workspace: The book workspace (for index DB paths).
            monitor: Optional progress Monitor for fine-grained reporting.
            parent_id: Parent task id in the monitor (for nesting).

        Returns:
            An :class:`IndexResult` with the build outcome.
        """

    @property
    @abc.abstractmethod
    def index_type(self) -> str:
        """The index type name (e.g. ``"summary"``, ``"chunk"``, ``"graph"``)."""
