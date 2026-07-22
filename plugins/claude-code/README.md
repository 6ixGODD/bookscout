# BookScout — Claude Code Plugin

Compile, index, search, and read books directly from Claude Code.

## Installation

### From local directory (development)

```bash
claude --plugin-dir /path/to/bookscout/plugins/claude-code
```

### From marketplace (coming soon)

```bash
claude plugin install bookscout
```

## Skills

| Skill   | Invocation           | Description                                    |
| ------- | -------------------- | ---------------------------------------------- |
| Compile | `/bookscout:compile` | Add a PDF/EPUB to your library                 |
| Search  | `/bookscout:search`  | Search book content (summaries, chunks, graph) |
| Library | `/bookscout:library` | Browse your book collection                    |
| Read    | `/bookscout:read`    | Read a specific book section                   |
| Index   | `/bookscout:index`   | Build semantic indexes for a book              |

## MCP Tools

The plugin starts a `bookscout-mcp` stdio server automatically. Available tools:

- `list_books` — List all books in the library
- `get_book` — Get detailed book info
- `search_books` — Search books by title/description
- `get_book_nodes` — Get book table of contents
- `get_node_content` — Read a specific section
- `search_summaries` — Search summary index
- `search_chunks` — Search chunk index (semantic)
- `search_graph` — Search graph index (relationships)
- `compile_book` — Compile a PDF/EPUB
- `get_compile_progress` — Check compile progress

## Prerequisites

- BookScout installed: `pip install bookscout-mcp`
- `bookscout-mcp` available in PATH
- Configuration at `~/.bookscout/config.yaml` (auto-created on first run)

## Quick Start

1. Install the plugin
2. Start Claude Code
3. Say: "Add this book to my library: /path/to/book.pdf"
4. Or use: `/bookscout:compile /path/to/book.pdf`
5. Once compiled, search with: "What does the book say about X?"

## Configuration

BookScout reads from `~/.bookscout/config.yaml`. Key settings:

```yaml
llm:
  api_key: 'sk-...' # pragma: allowlist secret — Required for summary/graph indexes
  base_url: 'https://api.example.com/v1'
  model: 'deepseek-chat'

embedding:
  api_key: 'sk-...' # pragma: allowlist secret — Required for chunk/graph indexes
  base_url: 'https://api.example.com/v1'
  model: 'text-embedding-3-small'
```
