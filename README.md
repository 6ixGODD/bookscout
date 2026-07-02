# BookScout

Book ingestion, indexing, and retrieval toolkit.

## Setup

Requires Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/6ixGODD/bookscout.git && cd bookscout
uv sync
```

## Architecture

Monorepo with 16 Python namespace packages under `python/`:

| Package                   | Description                                          |
| ------------------------- | ---------------------------------------------------- |
| `bookscout-core`          | Shared types, mixins, utilities                      |
| `bookscout-tools`         | `BaseTool` / `ToolExecutor` / MCP integration        |
| `bookscout-llm`           | Provider-agnostic LLM backend (OpenAI, Anthropic)    |
| `bookscout-logging`       | Structured logging with SQLite sink                  |
| `bookscout-sqlite`        | Async SQLite helpers                                 |
| `bookscout-filestore`     | Content-addressable blob storage                     |
| `bookscout-doccompiler`   | Document compilation (EPUB, PDF → structured output) |
| `bookscout-embedding`     | Text embedding (OpenAI)                              |
| `bookscout-vectorstore`   | Vector search (LanceDB)                              |
| `bookscout-books`         | Book metadata and storage                            |
| `bookscout-agents`        | Agent abstraction layer                              |
| `bookscout-repl`          | Interactive REPL                                     |
| `bookscout-index-chunk`   | Chunk-level indexing                                 |
| `bookscout-index-graph`   | Graph-based indexing                                 |
| `bookscout-index-summary` | Summary indexing                                     |
| `bookscout-mcp`           | MCP server                                           |

## CLI

```bash
uv run bs <command>
```

### Package management

```bash
bs package list              # list all packages
bs package list --tree       # namespace tree view
bs package new <name> -y     # create a package (interactive without -y)
bs package build             # build all wheels
bs package build -p bookscout-llm  # build specific package
bs package rm <name>         # remove a package
```

### Documentation

```bash
bs docs gen                  # generate API reference stubs
bs docs gen --clean          # regenerate from scratch
bs docs build                # build static MkDocs site
bs docs build --strict       # fail on warnings
bs docs serve                # preview with live reload
bs docs serve -p 8000        # specify port
```

### Version

```bash
bs bump                      # interactive version bump
bs bump --bump minor         # bump minor version
bs bump --version 1.0.0      # set explicit version
```

## Development

```bash
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy python/          # type check
uv run pytest                # test
```

Pre-commit hooks run ruff and prettier on commit.

## License

Private. All rights reserved.
