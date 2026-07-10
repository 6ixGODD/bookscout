# IndexProvider Refactor: Explicit Context over **kw Magic

**Date**: 2026-07-10
**Status**: Draft

## Problem

The three `IndexProvider` factory callables (`indexer_factory`, `tool_factory`,
`store_factory`) all use `**kw: t.Any` signatures. Callers pass keyword
arguments implicitly; providers fish them out of the dict with `kw["name"]`.
This violates "explicit > implicit":

1. **No type safety** — `kw["embedding"]` typo is a runtime `KeyError`, not a
   compile-time error.
2. **Hidden contract** — what a provider needs is only visible in its source,
   not in its signature.
3. **Inconsistent calling** — the two call sites (toolset.py, context.py) each
   assemble a different bag of kwargs; summary's `tool_factory` gets special
   treatment (`indexer=None, store=store, logger=..., db_path=...`).

## Design

### 1. IndexContext dataclass

Introduce a frozen dataclass that holds all shared dependencies explicitly:

```python
@dataclasses.dataclass(frozen=True, slots=True)
class IndexContext:
    """Explicit dependency context — replaces **kw: t.Any implicit passing."""
    logger: Logger
    books_store: BooksStore
    llm: ChatModel | None = None
    embedding: EmbeddingSystem | None = None
    vector_store: VectorStore | None = None
    db_path: pathlib.Path | None = None
```

Placed in `index_provider.py` alongside `IndexProvider` (tight coupling, same
module).

- `frozen=True` — immutable after construction.
- Optional fields (`llm`, `embedding`, `vector_store`, `db_path`) — not every
  index needs all of them; `requires_vector_store` on `IndexProvider` already
  documents which ones do.

### 2. Factory type aliases

```python
# Before
IndexerFactory = t.Callable[..., "Indexer"]
ToolFactory = t.Callable[..., list["BaseTool"]]
StoreFactory = t.Callable[..., t.Any]

# After
IndexerFactory = t.Callable[[IndexContext], "Indexer"]
ToolFactory = t.Callable[[Indexer | None, t.Any, IndexContext], list["BaseTool"]]
StoreFactory = t.Callable[[IndexContext], t.Any]
```

`ToolFactory` keeps `indexer` and `store` as positional args because:
- `indexer` can be `None` (summary tools don't need an indexer).
- `store` type varies (SummaryStore / ChunkStore / GraphStore) — can't narrow
  further without a common protocol that adds no value.
- `ctx` is last, as the "environment" parameter.

### 3. Provider rewrites

Each provider's three factory functions change from `**kw: t.Any` to
`ctx: IndexContext`, accessing dependencies as `ctx.logger`, `ctx.embedding`,
etc.

**summary/provider.py**:

```python
def _indexer_factory(ctx: IndexContext) -> Indexer:
    from . import SummaryIndexer
    return SummaryIndexer(logger=ctx.logger, books_store=ctx.books_store, model=ctx.llm)

def _store_factory(ctx: IndexContext) -> SummaryStore:
    from . import SummaryStore
    return SummaryStore(logger=ctx.logger, db_path=ctx.db_path)

def _tool_factory(indexer: Indexer | None, store: t.Any, ctx: IndexContext) -> list[BaseTool]:
    from .tools import create_summary_tools
    return create_summary_tools(ctx.logger, ctx.db_path)
```

**chunk/provider.py**:

```python
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

def _tool_factory(indexer: Indexer | None, store: t.Any, ctx: IndexContext) -> list[BaseTool]:
    from .tools import create_chunk_tools
    return create_chunk_tools(indexer, store)
```

**graph/provider.py**:

```python
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

def _tool_factory(indexer: Indexer | None, store: t.Any, ctx: IndexContext) -> list[BaseTool]:
    from .tools import create_graph_tools
    return create_graph_tools(indexer, store)
```

### 4. Call-site rewrites

**agents/reading/toolset.py** — `ReadingAgentToolset.startup`:

Before: each provider call assembles a different bag of kwargs; summary gets
special-cased with `indexer=None, store=store, logger=..., db_path=...`.

After:
construct one `IndexContext` per provider (varying only `db_path`), pass it
uniformly:

```python
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
```

**repl/context.py** — `ReplContext.startup`:

Before: `provider.indexer_factory(logger=..., books_store=..., llm=..., embedding=..., vector_store=...)`

After:

```python
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

### 5. Indexer base class — not changed

`Indexer.__init__(self, logger, books_store)` stays as-is. Subclasses already
declare their extra parameters explicitly (no `**kw`). The `**kw` problem is
only in the factory layer, not in the Indexer hierarchy.

### 6. Unchanged

- Entry-point discovery (`bookscout.indexes` group).
- `IndexRegistry` API (`load`, `all`, `by_type`, `for_types`, `default_enabled`, `letters`).
- Dynamic mount/unmount (`add_index`, `remove_index`).
- `IndexProvider` metadata fields (`index_type`, `display_name`, `short_letter`,
  `requires_vector_store`, `default_enabled`, `db_path_name`, `description`).
- `INDEX_PROVIDER` singleton per index package.

## Files changed

| File | Change |
|------|--------|
| `python/bookscout-doccompiler/bookscout/doccompiler/index_provider.py` | Add `IndexContext`; change `IndexerFactory`, `ToolFactory`, `StoreFactory` type aliases |
| `python/bookscout-index-summary/bookscout/index/summary/provider.py` | Factory signatures: `**kw` → `ctx: IndexContext` |
| `python/bookscout-index-chunk/bookscout/index/chunk/provider.py` | Same |
| `python/bookscout-index-graph/bookscout/index/graph/provider.py` | Same |
| `python/bookscout-agents/bookscout/agents/reading/toolset.py` | Construct `IndexContext`, pass to factories |
| `python/bookscout-repl/bookscout/repl/context.py` | Same |
| `python/tests/test_index_registry.py` | Update factory calls in tests |
| `python/tests/test_tui_commands.py` | Same |

## Risks

- **Optional field misuse**: a provider that needs `ctx.embedding` but gets
  `None` will crash at runtime. Mitigated by `requires_vector_store` flag on
  `IndexProvider` — callers already check this before invoking the factory.
- **db_path None**: `store_factory` always needs `db_path`. Callers must ensure
  it's set. This is already the case in both call sites.
