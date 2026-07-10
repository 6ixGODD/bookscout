# IndexProvider Explicit Context Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `**kw: t.Any` implicit parameter passing in IndexProvider factories with an explicit `IndexContext` dataclass.

**Architecture:** Introduce a frozen `IndexContext` dataclass holding all shared dependencies (logger, books_store, llm, embedding, vector_store, db_path). Change the three factory type aliases (`IndexerFactory`, `ToolFactory`, `StoreFactory`) to accept `IndexContext` instead of `**kw`. Rewrite the three provider modules and two call sites to use the new signatures.

**Tech Stack:** Python 3.12+, dataclasses, typing

## Global Constraints

- `IndexContext` must be `frozen=True, slots=True` (immutable, memory-efficient).
- Entry-point discovery mechanism (`bookscout.indexes` group) must not change.
- `IndexRegistry` API must not change.
- Dynamic mount/unmount (`add_index`/`remove_index`) must not change.
- `IndexProvider` metadata fields must not change.
- Each `INDEX_PROVIDER` singleton per index package must not change.
- All existing tests must pass after each task.

---

### Task 1: Add IndexContext and update factory type aliases

**Files:**
- Modify: `python/bookscout-doccompiler/bookscout/doccompiler/index_provider.py`
- Modify: `python/bookscout-doccompiler/bookscout/doccompiler/__init__.py`

**Interfaces:**
- Produces: `IndexContext` dataclass, updated `IndexerFactory`/`ToolFactory`/`StoreFactory` type aliases

- [ ] **Step 1: Write the failing test**

Add to `python/tests/test_index_registry.py`:

```python
import pathlib

from bookscout.doccompiler.index_provider import IndexContext


def test_index_context_is_frozen():
    ctx = IndexContext(logger=None, books_store=None)
    try:
        ctx.logger = "x"  # type: ignore[misc]
        raise AssertionError("should have raised FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_index_context_optional_fields_default_none():
    ctx = IndexContext(logger=None, books_store=None)
    assert ctx.llm is None
    assert ctx.embedding is None
    assert ctx.vector_store is None
    assert ctx.db_path is None


def test_index_context_all_fields():
    ctx = IndexContext(
        logger="log",  # type: ignore[arg-type]
        books_store="bs",  # type: ignore[arg-type]
        llm="chat",  # type: ignore[arg-type]
        embedding="emb",  # type: ignore[arg-type]
        vector_store="vs",  # type: ignore[arg-type]
        db_path=pathlib.Path("/tmp/x.sqlite"),
    )
    assert ctx.logger == "log"
    assert ctx.books_store == "bs"
    assert ctx.llm == "chat"
    assert ctx.embedding == "emb"
    assert ctx.vector_store == "vs"
    assert ctx.db_path == pathlib.Path("/tmp/x.sqlite")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd D:\WorkSpace\projects\2026\bookscout\python && python -m pytest tests/test_index_registry.py::test_index_context_is_frozen -v`
Expected: FAIL — `ImportError: cannot import name 'IndexContext'`

- [ ] **Step 3: Write minimal implementation**

Replace the entire content of `python/bookscout-doccompiler/bookscout/doccompiler/index_provider.py` with:

```python
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


IndexerFactory = t.Callable[[IndexContext], Indexer]
ToolFactory = t.Callable[[Indexer | None, t.Any, IndexContext], list[BaseTool]]
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
```

Then update `python/bookscout-doccompiler/bookscout/doccompiler/__init__.py` — add the import and export:

Change line 38 from:
```python
from .index_provider import IndexProvider
```
to:
```python
from .index_provider import IndexContext
from .index_provider import IndexProvider
```

Add `"IndexContext"` to the `__all__` list (after `"IndexProgress"` or in alphabetical order — place it between `"IndexContext"` and `"IndexProgress"`):

```python
__all__ = [
    "BookWorkspace",
    "BuildResult",
    "Builder",
    "CompileMetrics",
    "CompileResult",
    "CompileStage",
    "CompileStatus",
    "Compiler",
    "DocParser",
    "EpubParser",
    "EpubSourceMapping",
    "IndexContext",
    "IndexProgress",
    "IndexProvider",
    "IndexRegistry",
    "IndexResult",
    "Indexer",
    "LlmToolBuilder",
    "MineruPdfParser",
    "ParserResult",
    "PdfParser",
    "PdfSourceMapping",
    "RuleBasedBuilder",
    "SourceInfo",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd D:\WorkSpace\projects\2026\bookscout\python && python -m pytest tests/test_index_registry.py -v`
Expected: ALL PASS (old tests still pass because the fake factories still use `**kw` which is compatible with the new type aliases at runtime; new IndexContext tests pass)

- [ ] **Step 5: Commit**

```bash
git add python/bookscout-doccompiler/bookscout/doccompiler/index_provider.py python/bookscout-doccompiler/bookscout/doccompiler/__init__.py python/tests/test_index_registry.py
git commit -m "feat(index): add IndexContext dataclass, update factory type aliases"
```

---

### Task 2: Rewrite summary provider to use IndexContext

**Files:**
- Modify: `python/bookscout-index-summary/bookscout/index/summary/provider.py`

**Interfaces:**
- Consumes: `IndexContext` from Task 1
- Produces: `_indexer_factory(ctx)`, `_store_factory(ctx)`, `_tool_factory(indexer, store, ctx)` — new signatures

- [ ] **Step 1: Write the failing test**

Add to `python/tests/test_index_registry.py`:

```python
from bookscout.index.summary.provider import INDEX_PROVIDER as SUMMARY_PROVIDER


def test_summary_indexer_factory_uses_ctx():
    """Summary indexer_factory should accept IndexContext and extract ctx.llm as model."""
    ctx = IndexContext(logger="log", books_store="bs", llm="my-model")  # type: ignore[arg-type]
    indexer = SUMMARY_PROVIDER.indexer_factory(ctx)
    # SummaryIndexer stores the ChatModel as self._model
    assert indexer._model == "my-model"


def test_summary_store_factory_uses_ctx():
    """Summary store_factory should accept IndexContext and extract ctx.db_path."""
    db = pathlib.Path("/tmp/summary.sqlite")
    ctx = IndexContext(logger="log", books_store="bs", db_path=db)  # type: ignore[arg-type]
    store = SUMMARY_PROVIDER.store_factory(ctx)
    assert store is not None


def test_summary_tool_factory_uses_ctx():
    """Summary tool_factory should accept (indexer, store, ctx)."""
    ctx = IndexContext(logger="log", books_store="bs", db_path=pathlib.Path("/tmp/summary.sqlite"))  # type: ignore[arg-type]
    tools = SUMMARY_PROVIDER.tool_factory(indexer=None, store=None, ctx=ctx)
    assert isinstance(tools, list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd D:\WorkSpace\projects\2026\bookscout\python && python -m pytest tests/test_index_registry.py::test_summary_indexer_factory_uses_ctx -v`
Expected: FAIL — the old `_indexer_factory(logger, books_store, **kw)` signature doesn't match `IndexContext` call

- [ ] **Step 3: Rewrite the provider**

Replace the entire content of `python/bookscout-index-summary/bookscout/index/summary/provider.py` with:

```python
"""IndexProvider descriptor for the Summary index."""

from __future__ import annotations

import typing as t

from bookscout.doccompiler.index_provider import IndexContext
from bookscout.doccompiler.index_provider import IndexProvider

if t.TYPE_CHECKING:
    from bookscout.doccompiler import Indexer
    from bookscout.index.summary import SummaryStore
    from bookscout.tools import BaseTool


def _indexer_factory(ctx: IndexContext) -> Indexer:
    from . import SummaryIndexer

    return SummaryIndexer(
        logger=ctx.logger,
        books_store=ctx.books_store,
        model=ctx.llm,
    )


def _store_factory(ctx: IndexContext) -> SummaryStore:
    from . import SummaryStore

    return SummaryStore(logger=ctx.logger, db_path=ctx.db_path)


def _tool_factory(indexer: Indexer | None, store: t.Any, ctx: IndexContext) -> list[BaseTool]:
    # create_summary_tools takes (logger, db_path) and internally builds its own
    # SummaryStore per tool; the toolset's _startup_hidden_summary_stores starts them.
    from .tools import create_summary_tools

    return create_summary_tools(ctx.logger, ctx.db_path)


INDEX_PROVIDER = IndexProvider(
    index_type="summary",
    display_name="Summary",
    short_letter="s",
    requires_vector_store=False,
    default_enabled=True,
    indexer_factory=_indexer_factory,
    tool_factory=_tool_factory,
    store_factory=_store_factory,
    db_path_name="summary",
    description="Book-level digest; cheap, good for high-level questions",
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd D:\WorkSpace\projects\2026\bookscout\python && python -m pytest tests/test_index_registry.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add python/bookscout-index-summary/bookscout/index/summary/provider.py python/tests/test_index_registry.py
git commit -m "refactor(summary): provider factories use IndexContext instead of **kw"
```

---

### Task 3: Rewrite chunk provider to use IndexContext

**Files:**
- Modify: `python/bookscout-index-chunk/bookscout/index/chunk/provider.py`

**Interfaces:**
- Consumes: `IndexContext` from Task 1
- Produces: `_indexer_factory(ctx)`, `_store_factory(ctx)`, `_tool_factory(indexer, store, ctx)` — new signatures

- [ ] **Step 1: Write the failing test**

Add to `python/tests/test_index_registry.py`:

```python
from bookscout.index.chunk.provider import INDEX_PROVIDER as CHUNK_PROVIDER


def test_chunk_indexer_factory_uses_ctx():
    """Chunk indexer_factory should accept IndexContext and extract embedding/vector_store."""
    ctx = IndexContext(
        logger="log",  # type: ignore[arg-type]
        books_store="bs",  # type: ignore[arg-type]
        embedding="emb",  # type: ignore[arg-type]
        vector_store="vs",  # type: ignore[arg-type]
    )
    indexer = CHUNK_PROVIDER.indexer_factory(ctx)
    assert indexer._embedding == "emb"
    assert indexer._vector_store == "vs"


def test_chunk_store_factory_uses_ctx():
    """Chunk store_factory should accept IndexContext and extract ctx.db_path."""
    db = pathlib.Path("/tmp/chunks.sqlite")
    ctx = IndexContext(logger="log", books_store="bs", db_path=db)  # type: ignore[arg-type]
    store = CHUNK_PROVIDER.store_factory(ctx)
    assert store is not None


def test_chunk_tool_factory_uses_ctx():
    """Chunk tool_factory should accept (indexer, store, ctx)."""
    ctx = IndexContext(logger="log", books_store="bs", db_path=pathlib.Path("/tmp/chunks.sqlite"))  # type: ignore[arg-type]
    tools = CHUNK_PROVIDER.tool_factory(indexer=None, store=None, ctx=ctx)
    assert isinstance(tools, list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd D:\WorkSpace\projects\2026\bookscout\python && python -m pytest tests/test_index_registry.py::test_chunk_indexer_factory_uses_ctx -v`
Expected: FAIL

- [ ] **Step 3: Rewrite the provider**

Replace the entire content of `python/bookscout-index-chunk/bookscout/index/chunk/provider.py` with:

```python
"""IndexProvider descriptor for the Chunk index."""

from __future__ import annotations

import typing as t

from bookscout.doccompiler.index_provider import IndexContext
from bookscout.doccompiler.index_provider import IndexProvider

if t.TYPE_CHECKING:
    from bookscout.doccompiler import Indexer
    from bookscout.index.chunk import ChunkIndexer
    from bookscout.index.chunk import ChunkStore
    from bookscout.tools import BaseTool


def _indexer_factory(ctx: IndexContext) -> Indexer:
    from bookscout.llm import ChatModel

    from . import ChunkIndexer

    return ChunkIndexer(
        logger=ctx.logger,
        books_store=ctx.books_store,
        embedding=ctx.embedding,
        vector_store=ctx.vector_store,
        estimate_token_fn=ChatModel.estimate_token,
    )


def _store_factory(ctx: IndexContext) -> ChunkStore:
    from . import ChunkStore

    return ChunkStore(logger=ctx.logger, db_path=ctx.db_path)


def _tool_factory(indexer: ChunkIndexer, store: ChunkStore, ctx: IndexContext) -> list[BaseTool]:
    from .tools import create_chunk_tools

    return create_chunk_tools(indexer, store)


INDEX_PROVIDER = IndexProvider(
    index_type="chunk",
    display_name="Chunk",
    short_letter="c",
    requires_vector_store=True,
    default_enabled=True,
    indexer_factory=_indexer_factory,
    tool_factory=_tool_factory,
    store_factory=_store_factory,
    db_path_name="chunks",
    description="Passage-level chunks for precise citation and semantic search",
)


# NOTE: db_path_name is "chunks" because BookWorkspace.index_db_path("chunks")
# resolves to indexes/chunks.sqlite, matching the existing conventions in
# ReadingModeConfig.resolved_chunk_db_path (filename "chunks.sqlite").
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd D:\WorkSpace\projects\2026\bookscout\python && python -m pytest tests/test_index_registry.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add python/bookscout-index-chunk/bookscout/index/chunk/provider.py python/tests/test_index_registry.py
git commit -m "refactor(chunk): provider factories use IndexContext instead of **kw"
```

---

### Task 4: Rewrite graph provider to use IndexContext

**Files:**
- Modify: `python/bookscout-index-graph/bookscout/index/graph/provider.py`

**Interfaces:**
- Consumes: `IndexContext` from Task 1
- Produces: `_indexer_factory(ctx)`, `_store_factory(ctx)`, `_tool_factory(indexer, store, ctx)` — new signatures

- [ ] **Step 1: Write the failing test**

Add to `python/tests/test_index_registry.py`:

```python
from bookscout.index.graph.provider import INDEX_PROVIDER as GRAPH_PROVIDER


def test_graph_indexer_factory_uses_ctx():
    """Graph indexer_factory should accept IndexContext and extract llm/embedding/vector_store."""
    ctx = IndexContext(
        logger="log",  # type: ignore[arg-type]
        books_store="bs",  # type: ignore[arg-type]
        llm="chat",  # type: ignore[arg-type]
        embedding="emb",  # type: ignore[arg-type]
        vector_store="vs",  # type: ignore[arg-type]
    )
    indexer = GRAPH_PROVIDER.indexer_factory(ctx)
    assert indexer._model == "chat"
    assert indexer._embedding == "emb"
    assert indexer._vector_store == "vs"


def test_graph_store_factory_uses_ctx():
    """Graph store_factory should accept IndexContext and extract ctx.db_path."""
    db = pathlib.Path("/tmp/graph.sqlite")
    ctx = IndexContext(logger="log", books_store="bs", db_path=db)  # type: ignore[arg-type]
    store = GRAPH_PROVIDER.store_factory(ctx)
    assert store is not None


def test_graph_tool_factory_uses_ctx():
    """Graph tool_factory should accept (indexer, store, ctx)."""
    ctx = IndexContext(logger="log", books_store="bs", db_path=pathlib.Path("/tmp/graph.sqlite"))  # type: ignore[arg-type]
    tools = GRAPH_PROVIDER.tool_factory(indexer=None, store=None, ctx=ctx)
    assert isinstance(tools, list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd D:\WorkSpace\projects\2026\bookscout\python && python -m pytest tests/test_index_registry.py::test_graph_indexer_factory_uses_ctx -v`
Expected: FAIL

- [ ] **Step 3: Rewrite the provider**

Replace the entire content of `python/bookscout-index-graph/bookscout/index/graph/provider.py` with:

```python
"""IndexProvider descriptor for the Graph index."""

from __future__ import annotations

import typing as t

from bookscout.doccompiler.index_provider import IndexContext
from bookscout.doccompiler.index_provider import IndexProvider

if t.TYPE_CHECKING:
    from bookscout.doccompiler import Indexer
    from bookscout.index.graph import GraphIndexer
    from bookscout.index.graph import GraphStore
    from bookscout.tools import BaseTool


def _indexer_factory(ctx: IndexContext) -> Indexer:
    from bookscout.llm import ChatModel

    from . import GraphIndexer

    return GraphIndexer(
        logger=ctx.logger,
        books_store=ctx.books_store,
        model=ctx.llm,
        embedding=ctx.embedding,
        vector_store=ctx.vector_store,
        estimate_token_fn=ChatModel.estimate_token,
    )


def _store_factory(ctx: IndexContext) -> GraphStore:
    from . import GraphStore

    return GraphStore(logger=ctx.logger, db_path=ctx.db_path)


def _tool_factory(indexer: GraphIndexer, store: GraphStore, ctx: IndexContext) -> list[BaseTool]:
    from .tools import create_graph_tools

    return create_graph_tools(indexer, store)


INDEX_PROVIDER = IndexProvider(
    index_type="graph",
    display_name="Graph",
    short_letter="g",
    requires_vector_store=True,
    default_enabled=False,
    indexer_factory=_indexer_factory,
    tool_factory=_tool_factory,
    store_factory=_store_factory,
    db_path_name="graph",
    description="Relationship map between entities; slow and expensive",
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd D:\WorkSpace\projects\2026\bookscout\python && python -m pytest tests/test_index_registry.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add python/bookscout-index-graph/bookscout/index/graph/provider.py python/tests/test_index_registry.py
git commit -m "refactor(graph): provider factories use IndexContext instead of **kw"
```

---

### Task 5: Update ReadingAgentToolset call site

**Files:**
- Modify: `python/bookscout-agents/bookscout/agents/reading/toolset.py`

**Interfaces:**
- Consumes: `IndexContext` from Task 1, new factory signatures from Tasks 2-4

- [ ] **Step 1: Rewrite the startup method**

Replace the `startup` method body in `ReadingAgentToolset` (lines 52-107 of `toolset.py`). The full new file content:

```python
"""Toolset wiring for reading over existing indexes."""

from __future__ import annotations

import pathlib
import typing as t

from bookscout.doccompiler.index_provider import IndexContext
from bookscout.tools import BaseTool
from bookscout.tools.toolset import Toolset

from .config import ReadingModeConfig

if t.TYPE_CHECKING:
    from bookscout.books import BooksStore
    from bookscout.embedding import EmbeddingSystem
    from bookscout.llm import ChatModel
    from bookscout.logging import Logger


class ReadingAgentToolset(Toolset):
    """Retrieval tools for reading, built from existing package factories.

    Only tools for indexes the book actually has built are registered.
    Filter set is determined by the IndexManifest table for ``book_id``.
    """

    def __init__(
        self,
        *,
        config: ReadingModeConfig,
        llm: ChatModel,
        embedding: EmbeddingSystem,
        logger: Logger,
        book_id: str,
        registry: t.Any,
        books_store: BooksStore,
    ) -> None:
        super().__init__(
            name="reading_retrieval",
            description="Ontology + index retrieval tools for reading.",
            tools=[],
            logger=logger,
        )
        self.config = config
        self._llm = llm
        self._embedding = embedding
        self._book_id = book_id
        self._registry = registry
        self._books_store = books_store
        self._resources: list[t.Any] = []

    async def startup(self) -> None:
        from bookscout.books.tools import create_ontology_tools
        from bookscout.tools.computation import create_computation_tools

        tools: list[BaseTool] = []

        # 1. Ontology tools (always available).
        tools.extend(create_ontology_tools(self._books_store))

        # 2. Computation tools (always available).
        tools.extend(create_computation_tools())

        # 3. Index-driven tools — only for indexes this book has built.
        built_types = await self._books_store.list_index_types(self._book_id)
        active_providers = [p for p in self._registry.all() if p.index_type in built_types]

        vector_store = None
        if any(p.requires_vector_store for p in active_providers):
            from bookscout.vectorstore.lancedb import LanceDBConfig
            from bookscout.vectorstore.lancedb import LanceDBStore

            vector_store = LanceDBStore(
                LanceDBConfig(
                    uri=self.config.resolved_lancedb_uri,
                    table_name=self.config.lancedb_table_name,
                )
            )
            await vector_store.init()
            self._resources.append(vector_store)

        for provider in active_providers:
            db_path = pathlib.Path(self.config.workspace_root) / "indexes" / f"{provider.db_path_name}.sqlite"
            ctx = IndexContext(
                logger=self.logger,
                books_store=self._books_store,
                llm=self._llm,
                embedding=self._embedding,
                vector_store=vector_store,
                db_path=db_path,
            )
            store = provider.store_factory(ctx)
            if hasattr(store, "startup"):
                await store.startup()
            self._resources.append(store)

            if provider.index_type == "summary":
                tools_list = provider.tool_factory(indexer=None, store=store, ctx=ctx)
                await self._startup_hidden_summary_stores(tools_list)
                tools.extend(tools_list)
            else:
                indexer = provider.indexer_factory(ctx)
                if hasattr(indexer, "startup"):
                    await indexer.startup()
                self._resources.append(indexer)
                tools.extend(provider.tool_factory(indexer=indexer, store=store, ctx=ctx))

        self.internal_tools = tools  # pylint: disable=attribute-defined-outside-init
        await super().startup()

    async def shutdown(self) -> None:
        await super().shutdown()
        for resource in reversed(self._resources):
            if hasattr(resource, "shutdown"):
                await resource.shutdown()
            elif hasattr(resource, "close"):
                await resource.close()
        self._resources = []

    async def _startup_hidden_summary_stores(self, tools: t.Sequence[BaseTool]) -> None:
        seen: set[int] = set()
        for tool in tools:
            store = getattr(tool, "_store", None)
            if store is None or id(store) in seen:
                continue
            seen.add(id(store))
            if hasattr(store, "startup"):
                await store.startup()
                self._resources.append(store)
```

- [ ] **Step 2: Run existing tests to verify nothing broke**

Run: `cd D:\WorkSpace\projects\2026\bookscout\python && python -m pytest tests/test_reading_agent.py tests/test_index_registry.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add python/bookscout-agents/bookscout/agents/reading/toolset.py
git commit -m "refactor(toolset): use IndexContext for provider factory calls"
```

---

### Task 6: Update ReplContext call site

**Files:**
- Modify: `python/bookscout-repl/bookscout/repl/context.py`

**Interfaces:**
- Consumes: `IndexContext` from Task 1, new factory signatures from Tasks 2-4

- [ ] **Step 1: Rewrite the indexer creation in ReplContext.startup**

In `python/bookscout-repl/bookscout/repl/context.py`, make two changes:

**Change 1** — Add import at the top of `startup()` method (after the existing `from bookscout.doccompiler.index_registry import IndexRegistry` line around line 174):

```python
from bookscout.doccompiler.index_provider import IndexContext
```

**Change 2** — Replace the indexer creation loop (lines 177-188):

Old:
```python
        if self._llm is not None and self._embedding is not None and self._vector_store is not None:
            for provider in self._registry.all():
                if provider.requires_vector_store and self._vector_store is None:
                    continue
                indexer = provider.indexer_factory(
                    logger=self.logger,
                    books_store=self._books_store,
                    llm=self._llm,
                    embedding=self._embedding,
                    vector_store=self._vector_store,
                )
                self._indexers.append(indexer)
```

New:
```python
        if self._llm is not None and self._embedding is not None and self._vector_store is not None:
            ctx = IndexContext(
                logger=self.logger,
                books_store=self._books_store,
                llm=self._llm,
                embedding=self._embedding,
                vector_store=self._vector_store,
            )
            for provider in self._registry.all():
                if provider.requires_vector_store and self._vector_store is None:
                    continue
                indexer = provider.indexer_factory(ctx)
                self._indexers.append(indexer)
```

- [ ] **Step 2: Run existing tests to verify nothing broke**

Run: `cd D:\WorkSpace\projects\2026\bookscout\python && python -m pytest tests/test_index_registry.py tests/test_tui_commands.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add python/bookscout-repl/bookscout/repl/context.py
git commit -m "refactor(repl): use IndexContext for provider factory calls"
```

---

### Task 7: Update test fake factories to use IndexContext

**Files:**
- Modify: `python/tests/test_index_registry.py`
- Modify: `python/tests/test_tui_commands.py`

**Interfaces:**
- Consumes: `IndexContext` from Task 1

- [ ] **Step 1: Update test_index_registry.py fake factories**

Replace the three fake factory functions and `make_provider` helper:

```python
from bookscout.doccompiler.index_provider import IndexContext
from bookscout.doccompiler.index_provider import IndexProvider
from bookscout.doccompiler.index_registry import IndexRegistry


def _fake_indexer_factory(ctx: IndexContext):
    return type("FakeIndexer", (), {"index_type": "fake"})()


def _fake_tool_factory(_indexer, _store, ctx: IndexContext):
    return []


def _fake_store_factory(ctx: IndexContext):
    return None


def make_provider(t: str, letter: str = "x", default=True, requires_v=False) -> IndexProvider:
    return IndexProvider(
        index_type=t,
        display_name=t.capitalize(),
        short_letter=letter,
        requires_vector_store=requires_v,
        default_enabled=default,
        indexer_factory=_fake_indexer_factory,
        tool_factory=_fake_tool_factory,
        store_factory=_fake_store_factory,
        db_path_name=t,
    )
```

- [ ] **Step 2: Update test_tui_commands.py fake factories**

Replace the three fake factory functions and `make_provider` helper (lines 25-55):

```python
from bookscout.doccompiler.index_provider import IndexContext
from bookscout.doccompiler.index_provider import IndexProvider
from bookscout.doccompiler.index_registry import IndexRegistry


def _fake_indexer_factory(ctx: IndexContext):
    return type("FakeIndexer", (), {"index_type": "fake"})()


def _fake_tool_factory(_indexer, _store, ctx: IndexContext):
    return []


def _fake_store_factory(ctx: IndexContext):
    return None


def make_provider(
    t: str,
    letter: str = "x",
    default: bool = True,
    requires_v: bool = False,
    desc: str = "",
) -> IndexProvider:
    return IndexProvider(
        index_type=t,
        display_name=t.capitalize(),
        short_letter=letter,
        requires_vector_store=requires_v,
        default_enabled=default,
        indexer_factory=_fake_indexer_factory,
        tool_factory=_fake_tool_factory,
        store_factory=_fake_store_factory,
        db_path_name=t,
        description=desc,
    )
```

- [ ] **Step 3: Run all tests**

Run: `cd D:\WorkSpace\projects\2026\bookscout\python && python -m pytest tests/test_index_registry.py tests/test_tui_commands.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add python/tests/test_index_registry.py python/tests/test_tui_commands.py
git commit -m "refactor(tests): update fake factories to use IndexContext"
```

---

### Task 8: Final verification and cleanup

**Files:**
- No new changes — verification only

- [ ] **Step 1: Run full test suite**

Run: `cd D:\WorkSpace\projects\2026\bookscout\python && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Verify no remaining **kw in provider factories**

Run: `grep -rn "\*\*kw\|\*\*_kw" python/bookscout-index-summary/bookscout/index/summary/provider.py python/bookscout-index-chunk/bookscout/index/chunk/provider.py python/bookscout-index-graph/bookscout/index/graph/provider.py`
Expected: No matches

- [ ] **Step 3: Verify IndexContext is exported from doccompiler**

Run: `cd D:\WorkSpace\projects\2026\bookscout\python && python -c "from bookscout.doccompiler import IndexContext; print(IndexContext)"`
Expected: `<class 'bookscout.doccompiler.index_provider.IndexContext'>`

- [ ] **Step 4: Final commit (if any stray changes)**

```bash
git add -A
git commit -m "chore: final cleanup for IndexContext refactor"
```
