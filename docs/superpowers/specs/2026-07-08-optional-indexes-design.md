# Optional Per-Book Indexes & Dynamic Toolset — Design

**Date:** 2026-07-08
**Status:** Approved → Implementation Plan

## Problem

Today the BookScout compile pipeline always builds **all three** derived indexes
(summary, chunk, graph) unconditionally, and the reading agent always sees **all
21 tools** regardless of which indexes a book actually has. The graph index can
take hours and cost significant LLM spend; many books (essays, novels) do not
benefit from it. Users should be able to:

1. **Compile** a book with a subset of indexes (e.g. chunk only).
2. **Add** an index later (`:addindex graph`) without rebuilding the others.
3. **Remove** an index (`:rmindex graph`).
4. See, in the TUI book list, which indexes each book has.
5. Have the agent's toolset **automatically reflect** which indexes exist for
   the book it is reading — absent indexes → absent tools.

A new index package added in the future (`bookscout-index-somenewindex`) must
plug in **without touching existing code** — agent toolset assembly, compile
execution, and TUI checkbox lists all derive from a registry.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  Each index package (summary/chunk/graph/…)                 │
│    pyproject.toml:  [entry-points."bookscout.indexes"]      │
│    exports: INDEX_PROVIDER: IndexProvider                  │
└──────────────────────┬──────────────────────────────────────┘
                       │ importlib.metadata.entry_points
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  IndexRegistry (bookscout-doccompiler)                      │
│    providers: list[IndexProvider]                           │
│    .for_types(types: set[str]) → list[IndexProvider]        │
│    .default_enabled() → list[IndexProvider]                 │
└──────┬───────────────────────┬─────────────────────────┬────┘
       │                       │                         │
       ▼                       ▼                         ▼
┌──────────────┐    ┌──────────────────┐    ┌────────────────────┐
│ ReplContext  │    │ Compiler.compile │    │ ReadingAgentToolset │
│ builds indexers│   │ runs only selected │  │ registers only tools │
│ from registry│    │ indexers, writes   │  │ for book's manifest   │
│              │    │ manifest after     │  │ indexes              │
└──────────────┘    │ build              │  └────────────────────┘
                    └──────────────────┘
                              │
                    ┌─────────▼──────────┐
                    │ IndexManifestModel │
                    │ (bookscout-books)  │
                    │ book_id + type +   │
                    │ status (built/…)   │
                    └────────────────────┘
                              ▲
                    ┌─────────┴──────────┐
                    │ BooksStore exposes │
                    │ list_indexes /     │
                    │ upsert_index /     │
                    │ set_index_status   │
                    └────────────────────┘
```

## §1 — IndexManifest Table

**Location:** `bookscout-books` package, same SQLite DB as `BookModel`.

### Schema

```python
# bookscout/books/models.py
class IndexManifestModel(SQLModel, table=True):
    __tablename__ = "index_manifest"
    id: str = Field(primary_key=True)                       # gen_id("iman_")
    book_id: str = Field(foreign_key="book.id", index=True)
    index_type: str                                          # "summary"|"chunk"|"graph"|future
    status: str = Field(default="pending", index=True)      # pending|building|built|failed|removed
    count: int = Field(default=0)                           # indexer returned entry count
    error: str = Field(default="")
    built_at: float = Field(default=0.0)                    # epoch seconds
    created_at: float = Field(default_factory=utcnow_ts)
```

Composite UNIQUE constraint (`book_id`, `index_type`) via raw SQL DDL after `create_all`, following the existing pattern used for composite indexes on `book_nodes` (since SQLModel/SQLAlchemy composite uniqueness declaratively is awkward).

### BooksStore changes

`BooksStore.startup` calls `await self.sqlite.create_all([BookModel, BookNodeModel, IndexManifestModel])` (the per-table `create_all` you already require) then runs the raw UNIQUE-constraint DDL.

New public methods on `BooksStore`:

| Method | Returns | Purpose |
|---|---|---|
| `list_indexes(book_id) -> list[IndexInfo]` | frozen dataclasses of `(index_type, status, count, built_at, error)` | Read all manifest rows for a book |
| `list_index_types(book_id) -> set[str]` | set of `index_type` strings where `status == "built"` | Fast "which indexes are usable" |
| `upsert_index(book_id, type, status, count=0, error="") -> None` | None | INSERT-or-UPDATE manifest row |
| `set_index_status(book_id, type, status, **fields) -> None` | None | Patch status (+ count/error/built_at) |

`IndexInfo` is a frozen dataclass in `bookscout.books.types`, paralleling `Book`/`BookNode`:

```python
@dataclass(frozen=True, slots=True)
class IndexInfo:
    index_type: str
    status: str
    count: int
    error: str
    built_at: float
```

`BooksStore` never leaks SQLModel objects.

### Book dataclass extension

`Book` gains a read-only `indexes: tuple[str, ...] = ()` field (default empty for backward compat). `BooksStore.list_books()` joins `(SELECT index_type FROM index_manifest WHERE book_id=? AND status='built')` per book into `Book.indexes`. The TUI uses this for display.

### Status lifecycle

```
pending → building → built
                  ↘ failed
built → removed   (:rmindex)
removed → pending  (:addindex — reuses row, sets pending → building → built)
```

- `pending` — recorded but not yet built (used transiently between compile kickoff and `build_index` start)
- `building` — set just before `indexer.build_index` is called
- `built` — set on success, `count=idx_result.count`
- `failed` — set on exception, `error=repr(e)`
- `removed` — soft delete flag; `BookWorkspace.indexes/<type>.sqlite` file is also deleted

## §2 — IndexProvider & entry_points Auto-Registration

### IndexProvider dataclass

Lives in `bookscout-doccompiler` (the natural home — it owns the Indexer ABC already):

```python
# bookscout/doccompiler/index_provider.py
@dataclass(frozen=True, slots=True)
class IndexProvider:
    index_type: str                # "chunk" etc.
    display_name: str             # "Chunk" — for TUI checkbox labels
    short_letter: str             # "c" — for book-list [csg] indicator
    requires_vector_store: bool   # True for chunk/graph, False for summary
    default_enabled: bool         # Chunk=True, Summary=True, Graph=False
    indexer_factory: IndexerFactory
    tool_factory: ToolFactory
    store_factory: StoreFactory    # builds the index's store at a given db_path
    db_path_name: str             # name passed to BookWorkspace.index_db_path(); usually == index_type

IndexerFactory = Callable[..., Indexer]   # (logger, books_store, llm=, embedding=, vector_store=, ...) -> Indexer
ToolFactory    = Callable[..., list[BaseTool]]  # (indexer, store, logger=, ...) -> list[BaseTool]
StoreFactory   = Callable[..., AsyncResource]   # (db_path: pathlib.Path, logger, **kw) -> store instance
```

### Per-package declaration

Each index package's `__init__.py` (or a dedicated `provider.py`) exposes the singleton:

```python
# bookscout/index/chunk/__init__.py  (or provider.py)
INDEX_PROVIDER = IndexProvider(
    index_type="chunk",
    display_name="Chunk",
    short_letter="c",
    requires_vector_store=True,
    default_enabled=True,
    indexer_factory=lambda logger, books_store, **kw: ChunkIndexer(
        logger=logger, books_store=books_store,
        embedding=kw["embedding"], vector_store=kw["vector_store"],
    ),
    tool_factory=lambda indexer, store, **kw: create_chunk_tools(indexer, store),
)
```

And registers via entry point in `pyproject.toml` of that package:

```toml
[project.entry-points."bookscout.indexes"]
chunk = "bookscout.index.chunk:INDEX_PROVIDER"
```

### IndexRegistry

```python
# bookscout/doccompiler/index_registry.py
class IndexRegistry:
    def __init__(self, providers: list[IndexProvider]) -> None: ...
    @classmethod
    def load(cls) -> IndexRegistry:
        eps = importlib.metadata.entry_points(group="bookscout.indexes")
        return cls([ep.load() for ep in eps])
    def all(self) -> list[IndexProvider]: ...
    def for_types(self, types: set[str]) -> list[IndexProvider]: ...
    def default_enabled(self) -> list[IndexProvider]: ...
    def by_type(self, t: str) -> IndexProvider | None: ...
    @property
    def letters(self) -> str:  # concatenation of short_letters in registration order, e.g. "csg"
```

- `ReplContext.__init__` calls `IndexRegistry.load()` once and stores on `self._registry`.
- `TaskManager`, `Compiler`, `ReadingAgentToolset` all accept the registry (or a relevant slice) at construction — they never directly import a specific index package.

### Removing existing hardcoded index imports

`context.py:160-188` currently does `from bookscout.index.{chunk,graph,summary} import ...`. After this design, that block becomes:

```python
self._registry = IndexRegistry.load()
providers = [p for p in self._registry.all()
             if not p.requires_vector_store or self._vector_store is not None]
self._indexers: list[Indexer] = []
self._indexer_providers: list[IndexProvider] = []
for p in providers:
    indexer = p.indexer_factory(
        logger=self.logger,
        books_store=self._books_store,
        llm=self._llm,
        embedding=self._embedding,
        vector_store=self._vector_store,
    )
    self._indexers.append(indexer)
    self._indexer_providers.append(p)
```

## §3 — Compile Flow With Index Selection

### Compiler.compile signature change

```python
async def compile(
    self,
    source_path: str | pathlib.Path,
    *,
    index_types: set[str] | None = None,
) -> CompileResult:
```

- `index_types is None` → run registry's `default_enabled()` providers
- Non-None → run `registry.for_types(index_types)` (intersection with actually-instantiated indexers)

### BUILD_INDEXES stage (compiler.py:288-323) — shared manifest writer

Manifest writes must happen on **both** compile and incremental `:addindex` paths.
To avoid duplication, `Compiler` exposes a single helper:

```python
async def _build_one_index(
    self, indexer: Indexer, book_id: str, workspace: BookWorkspace,
    *, monitor, parent_id,
) -> IndexResult:
    await self._books_store.set_index_status(book_id, indexer.index_type, "building")
    try:
        result = await indexer.build_index(book_id, workspace, monitor=monitor, parent_id=parent_id)
        await self._books_store.upsert_index(book_id, indexer.index_type, "built", count=result.count)
        return result
    except Exception as e:
        await self._books_store.upsert_index(book_id, indexer.index_type, "failed", error=repr(e))
        raise  # fail-fast, surface to TaskManager
```

Compile's BUILD_INDEXES stage becomes:

```python
selected = [i for i in self._indexers
            if index_types is None or i.index_type in index_types]
for indexer in selected:
    await self._build_one_index(indexer, book_id, workspace, monitor=self._monitor, parent_id=idx_tid)
```

And `TaskManager._run_index` (used by `:addindex` via `start_index`) also calls
through to the same `Compiler._build_one_index` helper (passed in or repeated
inline) so manifest always reflects reality regardless of entry path.

### TaskManager.start_compile

```python
async def start_compile(self, source_path, *, index_types: set[str] | None = None) -> str:
```

Passes through to `Compiler.compile`.

### ReplContext.compile

```python
async def compile(self, source_path: str, *, index_types: set[str] | None = None) -> str:
```

Passes `index_types` into `task_manager.start_compile`.

### ReplServer `compile` request

Existing `"compile"` dispatch (server.py) gains optional `index_types: list[str]` field; passes through. (TOML/JSON list→set conversion at boundary.)

## §4 — TUI Changes

### 4.1 Book list shows index letters

`_refresh_books_list` extends the book label:

```
 1  三体                  - 刘慈欣            [csg]
 2  散文集                - 某某              [cs-]
 3  刚刚导入的书           - 某某              [---]
```

- `Book.indexes` (set of built type strings) → for each provider in registry order, render `short_letter` if built, else `-`
- Failed-but-recorded rows could be hinted with lowercase letter, but to keep the line simple we show only `built` and absent; manifest details available via a future `:indexes` command if needed later (out of scope for this round)

### 4.2 New `index_select` phase

After user enters a path in the select input (and the path is not `:delete N` or a number), instead of jumping straight to `compile`:

1. `phase = "index_select"`
2. Render a static panel listing checkbox rows (one per provider, in registry order):

```
Indexes for new book:
  [x] c  chunk     default, fast
  [x] s  summary
  [ ] g  graph     slow (hours, expensive)
Enter: confirm    Esc/Back: cancel    c/s/g: toggle
```

- `_selected_index_types: set[str]` — initialized to `default_enabled` provider types
- On `Input.Submitted` (Enter): if `_selected_index_types` non-empty → start compile with those types, jump to `compile` phase
- On input letter matching a `short_letter` → toggle membership in `_selected_index_types`, refresh display
- On `:back` typed in the input → return to `select` phase without compiling
- For an *existing* book's `:addindex`, the same panel renders in **incremental mode**: built indexes shown `[x]` locked (toggling them is a no-op); only unbuilt ones are toggleable. Enter triggers `ReplContext.add_index(book_id, newly_toggled_types)` for the newly-toggled types only.

**Two entry points to `ReplContext.add_index`** (same underlying path):
- Panel entry: the incremental-mode `index_select` panel above — visual picker, useful when exploring which indexes exist
- Chat-command entry: `:addindex graph` typed in chat — quick single-index add from chat without leaving the conversation

Both feed into `ReplContext.add_index(book_id, index_types)` which calls `task_manager.start_index` and writes manifest via Compiler's shared `_build_one_index`. The TUI renders the same compile-phase progress panel either way.

**Composable layout consideration:** The simplest approach is one `index_select_panel` Container mirroring the existing `select_panel`/`compile_panel`/`chat_panel` pattern. The compose result already switches based on `phase`. We add `"index_select"` as a fourth value of the `phase` reactive with its own panel.

### 4.3 Fix: compile success returns to select (not stuck on success panel)

`_finish_compile` success branch *currently* calls `self._enter_chat(book); return`, leaving user stuck on the compile panel that has no further action surface. Change to:

```python
# Success branch
self._pending_task_id = None
self._set_status("  compile OK — pick a book")
self.phase = "select"
self._refresh_books_list()
self._focus_input()
```

- User sees the freshly-compiled book in the list with its new `[csg]` or `[cs-]` indicator
- Selects it by number to enter chat

Fail branch unchanged (already returns to select with error display).

### 4.4 `:addindex` / `:rmindex` chat commands

`_handle_chat_input` gains:

```
:addindex graph
:rmindex graph
```

| Command | Effect |
|---|---|
| `:addindex X` | Validate X is known provider and not in book's `indexes`. Switch to compile phase, `await repl_context.add_index(book_id, {X})` which runs `task_manager.start_index` → progress polling same as compile flow → on success pop `self._modes[book_id]` so next chat recreates toolset → return to chat phase |
| `:rmindex X` | Validate X is in book's `indexes`. `await repl_context.remove_index(book_id, X)` (sets manifest `removed`, deletes `workspace.index_db_path(X)` file). Pop mode cache. Stay in chat. Show "removed graph" in chat log |

`ReplContext` gains:

```python
async def add_index(self, book_id: str, index_types: set[str]) -> str:
    return await self._task_manager.start_index(book_id, list(index_types))

async def remove_index(self, book_id: str, index_type: str) -> None:
    workspace = self._workspace_for(book_id)
    path = workspace.index_db_path(index_type)
    path.unlink(missing_ok=True)
    await self._books_store.set_index_status(book_id, index_type, "removed")
    self._modes.pop(book_id, None)  # invalidate cached mode so next chat rebuilds toolset
```

`:addindex` flow uses the same `_poll_progress` / `_render_monitor` machinery as compile; only difference is the task is `start_index` not `start_compile`. The polling machinery keys off `self._pending_task_id` so it works for both.

## §5 — ReadingAgentToolset Dynamic Tool Registration

`ReadingAgentToolset.startup` currently unconditionally imports all three index packages and registers 21 tools. Refactor:

### Constructor signature

```python
class ReadingAgentToolset(Toolset):
    def __init__(
        self,
        config: ReadingModeConfig,
        llm: BaseLLM,
        embedding: BaseEmbedding | None,
        logger: Logger,
        *,
        book_id: str,
        registry: IndexRegistry,
        books_store: BooksStore,
    ): ...
```

`ReadingMode.__init__` constructs the toolset with the per-book `book_id` it already has.

### Startup behavior

```python
async def startup(self) -> None:
    # Book ontology tools (always available regardless of indexes)
    internal = create_ontology_tools(self._books_store)
    # Computation tools (always)
    internal += create_computation_tools()

    # Index-driven tools — only for indexes this book actually has built
    built_types = await self._books_store.list_index_types(self._book_id)
    active = [p for p in self._registry.all() if p.index_type in built_types]
    for p in active:
        indexer = p.indexer_factory(logger=..., books_store=..., llm=..., embedding=..., vector_store=...)
        store = p.store_factory(...)  # provider exposes how to build its store
        await store.startup()
        internal += p.tool_factory(indexer, store)

    self._internal_tools = internal
    await super().startup()
```

`IndexProvider` needs a `store_factory` (or the `tool_factory` handles it internally) — this spec adds `store_factory: Callable[..., AsyncResource] | None` to the provider where `None` means the tool_factory handles its own storage lifecycle. For the existing three:

- chunk: `store_factory = lambda db_path, **kw: ChunkStore(db_path=db_path, logger=...)`
- summary: `store_factory = lambda db_path, **kw: SummaryStore(db_path=db_path, logger=...)`
- graph: `store_factory = lambda db_path, **kw: GraphStore(db_path=db_path, logger=...)`

The provider also exposes `db_path_name(index_type)` — i.e. which `workspace.index_db_path(name)` to open. (For the existing three, the db_path name == index_type.)

### Mode cache invalidation

`ReplContext._modes: dict[str, ReadingMode]` already exists. On `:addindex`/`:rmindex` we `pop(book_id, None)`; the next `chat` call goes through `get_or_create_mode(book_id)` which builds a fresh toolset reflecting the new manifest state.

### BookWorkspace access from toolset

`BookWorkspace.index_db_path(provider.index_type)` needs to be constructable from `book_id`. `ReplContext` already knows `_data_dir` and how workspaces are rooted, so it passes `workspace_base: pathlib.Path` (or a ready `BookWorkspace`) into `ReadingMode.__init__` → `ReadingAgentToolset.__init__`. The toolset then opens per-provider stores at `workspace.index_db_path(provider.index_type)`.

## §6 — Old-Book Bootstrap Migration

`BooksStore.startup` (or `ReplContext.startup`) runs (after create_all + DDL):

```python
async def _bootstrap_manifest_from_files(self) -> None:
    """Idempotent backfill: for books whose index sqlite files exist but no
    `built` manifest row, insert a `built` row with count=0."""
    book_ids = await self._all_book_ids()  # SELECT id FROM books
    for book_id in book_ids:
        existing = await self.list_index_types(book_id)  # built ones
        # Need provider list to know which files to probe
        for provider in self._registry.all():
            if provider.index_type in existing:
                continue
            ws = BookWorkspace(root=self._workspace_base, book_id=book_id)
            db_path = ws.index_db_path(provider.index_type)
            if db_path.exists() and db_path.stat().st_size > 0:
                await self.upsert_index(book_id, provider.index_type, "built", count=0)
```

This is **idempotent** — only fills in gaps. On any future startup, "missing manifest but file exists" gets backfilled automatically. Your current books (with chunk/summary/graph sqlite all already built) become `[csg]` on next launch.

### Where to host this

Requires knowledge of both `IndexRegistry` (for provider list) and `BookWorkspace` (for path layout). To avoid `bookscout-books` depending on `bookscout-doccompiler`, host the bootstrap in `ReplContext.startup` after `BooksStore.startup()` completes:

```python
# ReplContext.startup, after BooksStore.startup():
await self._bootstrap_manifest_from_files()

async def _bootstrap_manifest_from_files(self) -> None:
    book_ids = await self._books_store.all_book_ids()
    for book_id in book_ids:
        built = await self._books_store.list_index_types(book_id)
        for provider in self._registry.all():
            if provider.index_type in built:
                continue
            ws = BookWorkspace(root=self._data_dir, book_id=book_id)
            db_path = ws.index_db_path(provider.index_type)
            if db_path.exists() and db_path.stat().st_size > 0:
                await self._books_store.upsert_index(book_id, provider.index_type, "built", count=0)
```

`BookWorkspace` must be constructed the *same way* `Compiler` constructs it during compile. `ReplContext` should expose a `_workspace_for(book_id) -> BookWorkspace` helper (if not already present) and both `Compiler.compile` and `_bootstrap_manifest_from_files` use it, so path resolution (`indexes_dir`, `index_db_path`) is identical. If `Compiler` already constructs workspaces internally, that construction should be lifted into a shared helper (e.g. on `BookWorkspace` itself as a classmethod `for_book(root, book_id)`) to guarantee the bootstrap probes the same files compile writes.

## §7 — Testing

### Unit tests (python/tests)

- `test_index_manifest`: create book, upsert_index built, list_indexes returns it; set removed, list_index_types empty; idempotent re-upsert
- `test_index_registry`: fake providers via monkeypatched entry_points; `for_types`, `default_enabled`, `letters`
- `test_compile_with_subset`: Compiler with `index_types={"chunk"}` only builds chunk index (mocked), writes manifest only for chunk
- `test_toolset_filtering`: ReadingAgentToolset with a book that has only chunk → no graph tools in `internal_tools` (mock `BooksStore.list_index_types`)
- `test_bootstrap_manifest`: existing `.sqlite` file present + manifest empty → post-bootstrap row `status="built"`

### Manual verification

- Launch TUI on existing data dir → book list shows `[csg]` for built books
- Enter a path → see index_select panel tick default providers
- Untick graph, Enter → compile runs only chunk+summary, faster
- Compile success → return to select (not stuck panel) → see new book with `[cs-]`
- Select book, chat → confirm no graph tools exposed (e.g. agent system prompt no graph citations)
- `:rmindex summary` → chat continues, summary tools gone on next chat turn (mode cache rebuilt)
- `:addindex summary` → progress panel → success → summary tools back

## §8 — Out of Scope / Later

- `:indexes` command listing full manifest details (status, count, error) for a book — could be added but not needed for v1
- Re-compile from scratch option (full re-build of all indexes via `:rebuild`) — current `:rmindex` + `:addindex` covers the workflow
- MCP server `build_indexes` tool already supports `index_types: list[str] | None` and will continue working; only its callers in TUI change behavior
- Persisting which compile `index_types` a user typically picks as a per-user default — useful later, out of scope now

## §9 — Migration Impact

- Old `Book` dataclass gains `indexes` field with `()` default — backward compatible for existing unpickled `Book` values and existing parsers
- SQLite schema gets a new table — `create_all([IndexManifestModel])` is additive, no column drops
- Existing `ReplContext.startup` index-builder block gets simpler (loop vs. hardcoded imports) — no behavior change for old books since runtime still creates same indexers
- `ReadingAgentToolset.__init__` signature change — internal callers in `bookscout-agents` / `bookscout-repl` updated; no external API users

## §10 — File Change Surface

| Package | File | Change |
|---|---|---|
| bookscout-doccompiler | `bookscout/doccompiler/index_provider.py` | NEW — IndexProvider dataclass |
| bookscout-doccompiler | `bookscout/doccompiler/index_registry.py` | NEW — IndexRegistry with entry_points loader |
| bookscout-doccompiler | `bookscout/doccompiler/compiler.py` | `compile()` gains `index_types` param, BUILD_INDEXES filters, writes manifest |
| bookscout-doccompiler | `bookscout/doccompiler/task_manager.py` | `start_compile` gains `index_types`; existing `start_index` reuses manifest writer |
| bookscout-books | `bookscout/books/models.py` | NEW `IndexManifestModel`; UNIQUE DDL constant |
| bookscout-books | `bookscout/books/types.py` | NEW `IndexInfo` dataclass; `Book.indexes` field |
| bookscout-books | `bookscout/books/store.py` | `create_all` adds IndexManifestModel; `list_indexes`/`list_index_types`/`upsert_index`/`set_index_status`/`all_book_ids` |
| bookscout-index-summary | `provider.py` or `__init__.py` | Expose `INDEX_PROVIDER`; pyproject entry-point |
| bookscout-index-chunk | same | same |
| bookscout-index-graph | same | same |
| bookscout-agents | `bookscout/agents/reading/toolset.py` | Refactor startup to filter via registry + manifest |
| bookscout-agents | `bookscout/agents/reading/mode.py` | Pass `book_id`/`registry`/`books_store` into toolset constructor |
| bookscout-repl | `bookscout/repl/context.py` | Load registry, loop-build indexers, bootstrap_manifest, add/remove_index, invalidate mode cache |
| bookscout-repl | `bookscout/repl/tui.py` | index_select phase, `[csg]` indicators, fix _finish_compile, :addindex/:rmindex commands |
| python/tests | `test_index_manifest.py`, `test_index_registry.py`, etc. | NEW tests |
