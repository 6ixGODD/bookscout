# BookScout — Hermes Agent Integration

Compile, index, search, and read books from the Hermes Agent.

## Installation

### Step 1: Add MCP Server

Add the BookScout MCP server to your Hermes configuration. Edit `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  bookscout:
    command: bookscout-mcp
    args: []
```

Or use the Profile Builder web UI at `http://127.0.0.1:9119/profiles/new`:

1. Scroll to "MCP Servers"
2. Click "Add MCP Server"
3. Command: `bookscout-mcp`
4. Save

### Step 2: Enable the MCP Server

```bash
hermes mcp enable bookscout
```

### Step 3: Install Skills

Copy skills to your Hermes skills directory:

```bash
# Personal skills (all projects)
cp -r skills/* ~/.hermes/skills/

# Or project-level
cp -r skills/* $PROJECT/.agents/skills/
```

Or install via Skills Hub (coming soon).

### Step 4: Allowlist the Plugin

Hermes requires plugins to be allowlisted. Add to `~/.hermes/config.yaml`:

```yaml
plugins:
  allowlist:
    - bookscout
```

## Skills

| Skill   | Invocation | Description                    |
| ------- | ---------- | ------------------------------ |
| Compile | `$compile` | Add a PDF/EPUB to your library |
| Search  | `$search`  | Search book content            |
| Library | `$library` | Browse your book collection    |
| Read    | `$read`    | Read a specific book section   |
| Index   | `$index`   | Build semantic indexes         |

## MCP Tools

Once connected, Hermes can use all BookScout MCP tools:

- `list_books`, `get_book`, `search_books` — Library browsing
- `get_book_nodes`, `get_node_content` — Reading content
- `search_summaries`, `search_chunks`, `search_graph` — Semantic search
- `compile_book`, `get_compile_progress` — Compilation

## Prerequisites

- BookScout installed: `pip install bookscout-mcp`
- `bookscout-mcp` available in PATH
- Configuration at `~/.bookscout/config.yaml`

## Quick Start

1. Add the MCP server to Hermes config
2. Enable it: `hermes mcp enable bookscout`
3. Start Hermes
4. Say: "Add this book to my library: /path/to/book.pdf"
5. Once compiled, search with: "What does the book say about X?"

## Troubleshooting

- **MCP server not connecting**: Run `bookscout-mcp` directly in terminal to check for errors
- **Tools not appearing**: Check `hermes mcp list` to verify the server is active
- **Permission denied**: Make sure `bookscout-mcp` is in PATH and executable
