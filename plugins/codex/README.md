# BookScout — Codex CLI Plugin

Compile, index, search, and read books directly from OpenAI Codex.

## Installation

### Local plugin (development)

Place this directory in your Codex plugins folder:

```bash
# Personal plugins (all projects)
cp -r . ~/.codex/plugins/bookscout

# Or project-level
cp -r . $REPO_ROOT/.agents/plugins/bookscout
```

Then enable in Codex:

```
/plugins
```

### Using @plugin-creator

Inside Codex, run:

```
@plugin-creator
```

Point it to this directory.

## Skills

| Skill   | Invocation | Description                                    |
| ------- | ---------- | ---------------------------------------------- |
| Compile | `$compile` | Add a PDF/EPUB to your library                 |
| Search  | `$search`  | Search book content (summaries, chunks, graph) |
| Library | `$library` | Browse your book collection                    |
| Read    | `$read`    | Read a specific book section                   |
| Index   | `$index`   | Build semantic indexes for a book              |

Skills are auto-discovered from the `skills/` directory. Codex loads only the name and description initially, then loads the full skill when a task matches.

## MCP Tools

The plugin declares a `bookscout-mcp` stdio MCP server. Available tools:

- `list_books`, `get_book`, `search_books` — Library browsing
- `get_book_nodes`, `get_node_content` — Reading content
- `search_summaries`, `search_chunks`, `search_graph` — Semantic search
- `compile_book`, `get_compile_progress` — Compilation

## Prerequisites

- BookScout installed: `pip install bookscout-mcp`
- `bookscout-mcp` available in PATH
- Configuration at `~/.bookscout/config.yaml`

## Quick Start

1. Install the plugin
2. Start Codex
3. Say: "Add this book to my library: /path/to/book.pdf"
4. Once compiled, search with: "What does the book say about X?"

## Configuration

Add to `~/.codex/config.toml` if you need custom MCP settings:

```toml
[mcp_servers.bookscout]
command = ["bookscout-mcp"]
```
