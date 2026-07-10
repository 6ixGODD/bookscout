"""IndexProvider — declarative descriptor for a pluggable derived index.

Each index package (summary, chunk, graph, ...) exports an ``INDEX_PROVIDER``
singleton of this type and registers it via a Python entry_point in the
``"bookscout.indexes"`` group. This lets all consumers (Compiler, TUI,
ReadingAgentToolset) iterate available indexes without importing any specific
index package.
"""

from __future__ import annotations

import dataclasses
import pathlib
import typing as t

if t.TYPE_CHECKING:
    from bookscout.books import BooksStore
    from bookscout.doccompiler.indexer import Indexer
    from bookscout.embedding import EmbeddingSystem
    from bookscout.llm import ChatModel
    from bookscout.logging import Logger
    from bookscout.tools import BaseTool
    from bookscout.vectorstore.lancedb import LanceDBStore


@dataclasses.dataclass(frozen=True, slots=True)
class IndexContext:
    """Explicit dependency context — replaces **kw: t.Any implicit passing.

    All IndexProvider factory callables receive this instead of scattered
    keyword arguments. Optional fields are ``None`` when the corresponding
    infrastructure is unavailable; callers must check ``requires_vector_store``
    on the provider before relying on ``embedding`` / ``vector_store``.

    Attributes:
        logger: Logger instance.
        books_store: The BooksStore to read node content from.
        llm: ChatModel, or ``None`` if no API key configured.
        embedding: Embedding system, or ``None`` if unavailable.
        vector_store: Vector store, or ``None`` if unavailable.
        db_path: Path to the index-specific SQLite file.
    """

    logger: Logger
    books_store: BooksStore
    llm: ChatModel | None = None
    embedding: EmbeddingSystem | None = None
    vector_store: LanceDBStore | None = None
    db_path: pathlib.Path | None = None


IndexerFactory = t.Callable[[IndexContext], "Indexer"]
ToolFactory = t.Callable[["Indexer | None", t.Any, IndexContext], list["BaseTool"]]
StoreFactory = t.Callable[[IndexContext], t.Any]


@dataclasses.dataclass(frozen=True, slots=True)
class IndexProvider:
    """Declarative descriptor for a pluggable derived index.

    Attributes:
        index_type: Unique short name ("chunk", "summary", "graph", ...).
        display_name: Human-readable name for TUI checkbox labels.
        short_letter: Single lowercase char for TUI book-list [csg] indicator.
        requires_vector_store: True if the indexer needs an embedding/vector store.
        default_enabled: True if this index is ticked by default in the TUI.
        indexer_factory: Callable(ctx: IndexContext) -> Indexer.
        tool_factory: Callable(indexer, store, ctx: IndexContext) -> list[BaseTool].
        store_factory: Callable(ctx: IndexContext) -> store instance.
        db_path_name: Name passed to ``BookWorkspace.index_db_path(name)``, usually == index_type.
        description: Optional one-line description.
    """

    index_type: str
    display_name: str
    short_letter: str
    requires_vector_store: bool
    default_enabled: bool
    indexer_factory: IndexerFactory
    tool_factory: ToolFactory
    store_factory: StoreFactory
    db_path_name: str
    description: str = ""


__all__ = ["IndexContext", "IndexProvider"]
