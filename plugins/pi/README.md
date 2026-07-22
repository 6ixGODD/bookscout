# BookScout — Pi Extension

Compile, index, search, and read books directly from the Pi coding agent.

## Installation

### Local extension (development)

Place in your Pi extensions directory:

```bash
mkdir -p .pi/agent/extensions/bookscout
cp -r * .pi/agent/extensions/bookscout/
```

Pi auto-discovers extensions. Use `/reload` to hot-reload after changes.

### As Pi Package

```bash
pi package install @bookscout/pi-extension
```

## Registered Tools

The extension registers 10 BookScout tools directly into Pi's tool registry:

| Tool                             | Description                        |
| -------------------------------- | ---------------------------------- |
| `bookscout_list_books`           | List all books                     |
| `bookscout_get_book`             | Get book details                   |
| `bookscout_search_books`         | Search by title/description        |
| `bookscout_get_book_nodes`       | Get table of contents              |
| `bookscout_get_node_content`     | Read a section                     |
| `bookscout_search_summaries`     | Search summary index               |
| `bookscout_search_chunks`        | Search chunk index (semantic)      |
| `bookscout_search_graph`         | Search graph index (relationships) |
| `bookscout_compile_book`         | Compile a PDF/EPUB                 |
| `bookscout_get_compile_progress` | Check compile progress             |

## Skills

Skills are auto-loaded from the `skills/` directory. Available skills:

| Skill   | Description                    |
| ------- | ------------------------------ |
| compile | Add a PDF/EPUB to your library |
| search  | Search book content            |
| library | Browse your book collection    |
| read    | Read a specific book section   |
| index   | Build semantic indexes         |

## Prerequisites

- BookScout installed: `pip install bookscout-mcp`
- `bookscout-mcp` available in PATH
- Configuration at `~/.bookscout/config.yaml`

## How It Works

The extension spawns a `bookscout-mcp` subprocess and communicates via MCP stdio JSON-RPC. Tools are registered natively in Pi's tool registry, so the agent can use them just like built-in tools (read, bash, edit, write).

## Quick Start

1. Install the extension
2. Start Pi
3. Say: "Add this book to my library: /path/to/book.pdf"
4. Once compiled, search with: "What does the book say about X?"
