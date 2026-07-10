# AGENTS.md — BookScout Development Guide

## Project Overview

BookScout is a monorepo Python project that builds an interactive book reading assistant. You ingest EPUB/PDF books, build semantic indexes (ontology, summaries, chunks, knowledge graphs), and chat with an LLM agent that retrieves evidence from those indexes.

## Development Boundaries

- **Python**: 3.12–3.13 only (`>=3.12,<3.14`)
- **Package manager**: `uv` (never `pip` directly)
- **Linting**: `uv run ruff check .` before every commit
- **Type checking**: `uv run mypy python/<pkg>` for affected packages
- **Testing**: `uv run pytest python/tests/ -x -v` — all tests must pass
- **Pre-commit**: `uv run pre-commit run --all-files` enforces formatting, trailing whitespace, secrets detection
- **Imports**: Always `from __future__ import annotations` at the top of every Python file
- **Type hints**: All public methods and functions must be annotated
- **Pydantic**: v2 only, use `model_validate`, `model_dump`, `FieldInfo`
- **Async**: Use `asyncio` for I/O; `AsyncResourceMixin` + `LoggingMixin` for resource lifecycle
- **No breaking changes to public APIs** in existing packages without approval
- **Tests**: Required for new features — use pytest + pytest-asyncio; TUI tests use Textual's `run_test` with fake contexts

## Project Structure

```
bookscout/
├── AGENTS.md                          # This file
├── pyproject.toml                     # Root package config + workspace members
├── VERSION                            # Canonical version
├── SOUL.md                            # Agent persona (created by user in workdir)
├── scripts/                           # Dev tooling (bs CLI)
├── python/
│   ├── bookscout-core/                # Shared types, protocols, mixins, id generation
│   ├── bookscout-logging/             # Structured logging (LoggingMixin, Logger)
│   ├── bookscout-sqlite/              # Async SQLite wrapper (SQLite, SQLiteConfig)
│   ├── bookscout-llm/                 # LLM backend (OpenAI-compatible, ChatModel)
│   ├── bookscout-embedding/           # Embedding system (OpenAI-compatible)
│   ├── bookscout-vectorstore/         # LanceDB vector store
│   ├── bookscout-books/               # Book metadata store + ontology tools
│   ├── bookscout-doccompiler/         # Document compilation pipeline (parse → build → index)
│   │   └── bookscout/doccompiler/
│   │       ├── index_provider.py      # IndexProvider — defines an index type
│   │       ├── index_registry.py      # IndexRegistry — all known index types
│   │       ├── compiler.py            # RuleBasedBuilder, LlmToolBuilder
│   │       ├── task_manager.py        # Compile task orchestration
│   │       ├── workspace.py           # BookWorkspace — per-book directory layout
│   │       └── tools.py               # Compiler MCP tools
│   ├── bookscout-filestore/           # File storage abstraction
│   ├── bookscout-index-summary/       # Summary index (LLM-generated section summaries)
│   ├── bookscout-index-chunk/         # Chunk index (semantic + FTS search)
│   ├── bookscout-index-graph/         # Knowledge graph index (entities + relationships)
│   ├── bookscout-tools/               # BaseTool framework + built-in tools
│   │   └── bookscout/tools/
│   │       ├── __init__.py            # BaseTool, Property, Toolset
│   │       └── computation.py         # Wolfram + Python sandbox execution
│   ├── bookscout-agents/              # Agent framework (Mode, Agent, ReadingMode)
│   │   └── bookscout/agents/
│   │       ├── mode.py                # Mode base class (conversation, compact, checkpoint)
│   │       ├── agent.py               # ModeAgent base class
│   │       ├── context.py             # AgentContext, StepResult
│   │       └── reading/               # ReadingMode — the chat agent
│   │           ├── mode.py            # ReadingMode (handle, handle_stream)
│   │           ├── agent.py           # ReadingAgent + READING_SYSTEM_PROMPT
│   │           ├── toolset.py         # ReadingAgentToolset
│   │           ├── config.py          # ReadingModeConfig
│   │           └── session.py         # ReadingSession + ReadingSessionRepository
│   ├── bookscout-mcp/                 # MCP server manager (3 mount points)
│   ├── bookscout-progress/            # Progress monitoring (Monitor)
│   ├── bookscout-repl/                # REPL server + TUI (the user-facing app)
│   │   └── bookscout/repl/
│   │       ├── __main__.py            # CLI entry point (tui + serve subcommands)
│   │       ├── __init__.py            # Public API exports
│   │       ├── config.py              # BookScoutConfig (pydantic-settings)
│   │       ├── context.py             # ReplContext (shared runtime resources)
│   │       ├── tui.py                 # BookScoutTui (Textual app)
│   │       ├── server.py              # ReplServer (stdio transport)
│   │       ├── setup.py               # SetupWizard (Textual config wizard)
│   │       └── transport.py           # Transport layer
│   └── tests/                         # All tests
│       ├── conftest.py
│       ├── test_tui_commands.py       # TUI headless tests
│       ├── test_reading_agent.py      # Reading agent tests
│       └── ...
└── docs/                              # Documentation
    └── superpowers/
        ├── specs/                     # Design specs
        └── plans/                     # Implementation plans
```

## Package Dependency Flow

```
bookscout-core          ← everything depends on this
bookscout-logging       ← core
bookscout-sqlite        ← core + logging
bookscout-llm           ← core + logging + sqlite
bookscout-embedding     ← core + logging
bookscout-vectorstore   ← core + logging
bookscout-books         ← core + logging + sqlite
bookscout-filestore     ← core + logging
bookscout-tools         ← core + logging
bookscout-index-*       ← core + logging + llm + embedding + vectorstore + tools
bookscout-doccompiler   ← core + logging + llm + embedding + books + index-* + tools
bookscout-agents        ← core + logging + llm + tools + books + sqlite
bookscout-mcp           ← core + logging + books + doccompiler + index-*
bookscout-repl          ← core + logging + llm + embedding + books + doccompiler + agents + tools + mcp
```

## Key Patterns

### Async Resource Lifecycle
Every service class inherits `LoggingMixin` + `AsyncResourceMixin`:
```python
class MyService(LoggingMixin, AsyncResourceMixin):
    async def startup(self) -> None:
        # init resources
        await super().startup()

    async def shutdown(self) -> None:
        # clean up resources
        await super().shutdown()
```

### Tool Definition
Tools extend `BaseTool` with Annotated parameters:
```python
class MyTool(BaseTool, name="my_tool", description="..."):
    async def __call__(
        self,
        param: Annotated[str, Property(description="...")],
    ) -> str:
        ...
```

### Configuration
Always use pydantic `BaseModel` for config, `BaseSettings` for top-level config with env support. All fields must have defaults — `BookScoutConfig()` should never raise.

### TUI Testing
Use Textual's `run_test` with fake contexts — never require real API keys:
```python
async def test_something():
    app = BookScoutTui(config)
    async with app.run_test() as pilot:
        app._repl_context = _FakeReplContext()
        ...
```

## Working on This Codebase

1. **Think in packages**: Each `python/bookscout-*` directory is an independent package with its own `pyproject.toml`. Keep dependencies minimal.
2. **New tools go in `bookscout-tools`**: All agent-facing tools live there.
3. **New index types**: Use `IndexProvider` + register in `IndexRegistry`.
4. **Agent changes**: Understand `Mode → ReadingMode` and `ModeAgent → ReadingAgent` inheritance.
5. **TUI changes**: The TUI is a Textual `App` with phases (`select`, `index_select`, `builder_select`, `compile`, `chat`). Input handling goes through `CommandInput._on_key` and `on_input_submitted`.
6. **Config changes**: Add new fields to `BookScoutConfig` with defaults — never break existing config files.
7. **Test everything**: Run `uv run pytest python/tests/ -x -v` before pushing.
8. **Commit hygiene**: One logical change per commit. Use conventional commits (`feat:`, `fix:`, `chore:`, `docs:`).
