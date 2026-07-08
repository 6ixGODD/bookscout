"""IndexProvider — declarative descriptor for a pluggable derived index.

Each index package (summary, chunk, graph, ...) exports an ``INDEX_PROVIDER``
singleton of this type and registers it via a Python entry_point in the
``"bookscout.indexes"`` group. This lets all consumers (Compiler, TUI,
ReadingAgentToolset) iterate available indexes without importing any specific
index package.
"""

from __future__ import annotations

import dataclasses
import typing as t

if t.TYPE_CHECKING:
    from bookscout.doccompiler.indexer import Indexer
    from bookscout.tools import BaseTool


IndexerFactory = t.Callable[..., "Indexer"]
ToolFactory = t.Callable[..., list["BaseTool"]]
StoreFactory = t.Callable[..., t.Any]


@dataclasses.dataclass(frozen=True, slots=True)
class IndexProvider:
    """Declarative descriptor for a pluggable derived index.

    Attributes:
        index_type: Unique short name ("chunk", "summary", "graph", ...).
        display_name: Human-readable name for TUI checkbox labels.
        short_letter: Single lowercase char for TUI book-list [csg] indicator.
        requires_vector_store: True if the indexer needs an embedding/vector store.
        default_enabled: True if this index is ticked by default in the TUI.
        indexer_factory: Callable(logger, books_store, llm=, embedding=, vector_store=, ...) -> Indexer.
        tool_factory: Callable(indexer, store, logger=, ...) -> list[BaseTool].
        store_factory: Callable(db_path: pathlib.Path, logger, ...) -> store instance.
        db_path_name: Name passed to ``BookWorkspace.index_db_path(name)``, usually == index_type.
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


__all__ = ["IndexProvider"]
