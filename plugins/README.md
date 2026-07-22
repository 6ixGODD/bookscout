# BookScout Plugins

Deep integration plugins for 5 AI coding agent platforms. Not just MCP — native-feeling extensions that fuse BookScout's compile, index, search, and read capabilities into each platform's ecosystem.

## Platforms

| Plugin                       | Platform                    | Type                                         | Status   |
| ---------------------------- | --------------------------- | -------------------------------------------- | -------- |
| [claude-code/](claude-code/) | Claude Code (Anthropic)     | Full plugin (skills + hooks + MCP)           | ✅ Ready |
| [codex/](codex/)             | OpenAI Codex CLI            | Plugin (skills + MCP)                        | ✅ Ready |
| [pi/](pi/)                   | Pi (badlogic)               | TypeScript extension (native tools + skills) | ✅ Ready |
| [openclaw/](openclaw/)       | OpenClaw / OpenClawX        | npm extension (native tools + skills)        | ✅ Ready |
| [hermes/](hermes/)           | Hermes Agent (NousResearch) | MCP config + skills                          | ✅ Ready |

## Shared Skills

All platforms share the same [Agent Skills](https://agentskills.io) standard SKILL.md files from [bookscout-skills/](bookscout-skills/):

| Skill   | Description                    | When to use                        |
| ------- | ------------------------------ | ---------------------------------- |
| compile | Add a PDF/EPUB to your library | "Add this book", "Import document" |
| search  | Search book content            | "What does the book say about X"   |
| library | Browse your book collection    | "Show me my books"                 |
| read    | Read a specific book section   | "Read chapter 3"                   |
| index   | Build semantic indexes         | "Build indexes for this book"      |

## Architecture

```
User's AI Agent (Claude Code / Codex / Pi / OpenClaw / Hermes)
  │
  ├── Skills (SKILL.md) ← tells the AI HOW to use BookScout
  │   └── bookscout-skills/ (shared across all platforms)
  │
  ├── Tools (native registration or MCP)
  │   ├── Claude Code / Codex: MCP stdio (bookscout-mcp)
  │   ├── Pi / OpenClaw: pi.registerTool() → subprocess → MCP
  │   └── Hermes: MCP stdio (bookscout-mcp)
  │
  └── bookscout-mcp (Python FastMCP server)
      ├── compile_book
      ├── get_compile_progress
      ├── list_books / get_book / search_books
      ├── get_book_nodes / get_node_content
      ├── search_summaries / search_chunks / search_graph
      └── (auto-registered based on available config)
```

## Prerequisites (all platforms)

1. **Install BookScout**: `pip install bookscout-mcp`
2. **Verify**: `bookscout-mcp --help` (should start without errors)
3. **Configure**: Create `~/.bookscout/config.yaml` with LLM/embedding keys

## Quick Platform-Specific Install

### Claude Code

```bash
claude --plugin-dir ./plugins/claude-code
```

### Codex

```bash
# Copy to plugins directory
cp -r ./plugins/codex ~/.codex/plugins/bookscout
```

### Pi

```bash
# Copy to extensions directory
cp -r ./plugins/pi .pi/agent/extensions/bookscout
```

### OpenClaw

```bash
openbot extension install ./plugins/openclaw
```

### Hermes

```bash
# Add MCP config from plugins/hermes/mcp-config.yaml to ~/.hermes/config.yaml
cat ./plugins/hermes/mcp-config.yaml >> ~/.hermes/config.yaml
hermes mcp enable bookscout
```
