# BookScout TUI Optimization — Design Spec

**Date:** 2026-07-10
**Status:** approved
**Scope:** 8 subsystems transforming the BookScout REPL TUI into a complete, production-grade application

---

## 1. workdir Concept

### Motivation
Currently, configuration, logs, data, and sessions are scattered. A single root directory (`workdir`) anchors everything so users never manually configure paths.

### Structure
```
workdir/                          # default: ~/.bookscout
├── SOUL.md                       # Agent persona / style settings
├── config.yaml                   # Main config
├── sessions.db                   # SQLite — all session records
├── skills/                       # User skill .md files
├── logs/                         # Rotating log files
└── data/                         # data_dir (default: workdir/data)
    ├── books.sqlite
    ├── lancedb/
    └── <book_id>/
        ├── reading_mode.sqlite
        └── indexes/
```

### Changes
- `BookScoutConfig`:
  - **New field**: `workdir: str` (default `~/.bookscout`)
  - `data_dir` default changed from `~/.bookscout` → `~/.bookscout/data`
  - Existing fields (`logging.file`, etc.) are **preserved** — just that users don't need to set them because relative paths resolve under `workdir/` by convention
- CLI: `--workdir` / `-w` flag on both `tui` and `serve` subcommands
- `_resolve_config()`: if `--workdir` is given, overrides `workdir`; if `data_dir` not explicitly set, defaults to `workdir/data`

---

## 2. Multi-Session System

### Data Model (`workdir/sessions.db`)
```sql
CREATE TABLE session (
    session_id   TEXT PRIMARY KEY,
    book_id      TEXT NOT NULL,
    name         TEXT NOT NULL,          -- default: "<book_title>-<random6>"
    kind         TEXT NOT NULL DEFAULT 'chat',  -- 'chat' | 'research' | etc.
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    turn_count   INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'archived'
    extra_json   TEXT NOT NULL DEFAULT '{}'
);
```

A `kind` field lets users tag what a session is for (e.g., "character analysis"), displayed in the `:resume` list.

### Session Lifecycle
1. User selects a book → check for existing sessions for that book
2. If sessions exist → show session list (name | kind | turns | last_updated), user picks one or `:new`
3. If no sessions → auto-create a default session (name = `<book_title>-<random6>`, kind = `chat`) and enter chat
4. `:session new <name> [kind]` creates a named session with optional kind tag
5. Each session gets its own `ReadingMode` instance (own `reading_mode.sqlite` + message history)

### New Commands
| Command | Context | Behavior |
|---------|---------|----------|
| `:resume` | select | Cross-book session list → pick to resume |
| `:resume <id>` | any | Resume specific session |
| `:rename <NAME>` | chat | Rename current session |
| `:session new <name>` | chat | Create new session for current book |
| `:session list` | chat | List sessions for current book |

### CLI
```bash
bookscout tui --resume              # Open directly to session list
bookscout tui --resume <session_id> # Resume a specific session
bookscout tui -b <book_id>          # Open book → show its sessions
```

---

## 3. MCP Integration

### Config (`config.yaml`)
```yaml
mcp_servers:
  - name: "my-tools"
    url: "http://localhost:8080/mcp"       # streamable HTTP
  - name: "filesystem"
    command: "npx"
    args: ["-y", "@anthropic/mcp-filesystem", "/tmp"]
    env:                                    # optional
      HOME: "/home/user"
```

### Implementation
- **`ExternalMcpToolset`** (new, in `bookscout-tools` or new `bookscout-mcp`):
  - On startup: connects to each configured MCP server, calls `tools/list`, wraps each tool as a `BaseTool`
  - On shutdown: disconnects
  - Connection failures → log warning, skip that server (non-fatal)
- **Injected into `ReadingAgentToolset`**: external MCP tools appear alongside built-in tools
- **TUI commands**: `:mcp list`, `:mcp add <name> <url>`, `:mcp remove <name>` (writes to config)

### Protocol
- Primary: streamable HTTP (matches existing FastMCP servers)
- Stdio: supported via `command` + `args` config (spawn subprocess)

---

## 4. SKILL System

### Config (`config.yaml`)
```yaml
skills:
  - name: "close-reading"
    path: "skills/close-reading.md"
    description: "Close reading mode: analyze text structure, rhetoric, and argumentation paragraph by paragraph"
  - name: "academic-writing"
    path: "skills/academic-writing.md"
    description: "Academic writing assistant for papers and dissertations"
```

### Mechanism: On-Demand Loading
Skills are **NOT** injected into system prompt at startup. Instead:
1. System prompt lists skill **names + descriptions** only (minimal tokens)
2. Agent calls `skill_fetch` tool when it needs a skill's full content
3. `SkillFetchTool` reads the `.md` file from `workdir/skills/` and returns it
4. Content is cached in memory after first fetch

### System Prompt Structure
```
## Available Skills
- close-reading: Close reading mode — analyze text structure, rhetoric...
- academic-writing: Academic writing assistant for papers...

---

<READING_SYSTEM_PROMPT (original agent instructions)>

---

<SOUL.md content — agent persona, style, tone>

---

Current date: 2026-07-10
Current time: 14:30:25 CST
```

### Implementation
- `SkillFetchTool` in `bookscout-tools`:
  - Accepts `skill_name: str`
  - Reads `workdir/skills/<name>.md` or looks up path from config
  - Returns content as string
  - Caches in memory (dict) for subsequent calls
- `SkillLoader`: loads config, provides `list_skills()` and `get_skill(name)` with caching
- `SOUL.md`: read once at startup from `workdir/SOUL.md`; injected at bottom of system prompt

---

## 5. TUI Optimization

### Full-Screen Fix
Current issue: gray margins on left/right of the Textual app.
Root cause investigation:
- Textual's `Screen` has default CSS layers that may add padding
- Terminal emulator may not be fully utilized

Fixes:
1. CSS: Add `Screen { layers: none; }` and `* { margin: 0; }`
2. Verify terminal size detection — `shutil.get_terminal_size()` vs Textual's auto-detect
3. CSS: Ensure `width: 100%` on all top-level containers

### PowerShell Tab Title
On Windows, set console title when entering a book's chat:
```python
if sys.platform == "win32":
    import ctypes
    ctypes.windll.kernel32.SetConsoleTitleW(f"{book.title} - {book.author}")
```

### Session List UI
When entering a book with existing sessions, show a list similar to the book list:
```
  1  Chapter 3 analysis        chat        12 turns   2026-07-09 14:30
  2  Default                   chat        3 turns    2026-07-08 09:15
  3  Character mapping         research    25 turns   2026-07-05 18:00

  Enter: resume    :new: create    :back: return to books
```

---

## 6. Python Sandbox: SciPy + NumPy

### Changes to `computation.py`
Add to `_ALLOWED_MODULES`:
```python
"numpy",
"numpy.linalg",
"scipy",
"scipy.stats",
"scipy.optimize",
"scipy.integrate",
"scipy.linalg",
"scipy.special",
"scipy.interpolate",
"scipy.fft",
"scipy.signal",
"scipy.sparse",
"scipy.spatial",
```

For submodules (e.g. `scipy.stats`), use a dynamic import fallback:
```python
for mod_name in _ALLOWED_MODULES:
    try:
        safe_globals[mod_name] = __import__(mod_name)
    except ImportError:
        pass
```
The existing code already does `__import__(mod_name)` which handles top-level modules. For submodules like `scipy.stats`, `__import__("scipy.stats")` returns `scipy`, so we need:
```python
parts = mod_name.split(".")
mod = __import__(mod_name)
for part in parts[1:]:
    mod = getattr(mod, part)
safe_globals[mod_name] = mod
```

---

## 7. `:compact` Command

### Implementation
- `ReadingMode` gains a **public** `async compact() -> str` method that:
  1. Runs the same compaction logic as `_maybe_auto_compact()` but **unconditionally** — no token-threshold check; force-compact even if context is small
  2. If messages ≤ `_COMPACT_KEEP_MESSAGES`, returns empty string (nothing to compact)
  3. Returns the generated summary text on success
- In TUI chat handler, `:compact` triggers `mode.compact()` and renders result as **dim gray** markdown:
  ```python
  self._chat_markdown += f"\n*[compacted: {summary}]*\n\n"
  # CSS ensures compact markers are gray/dim
  ```

---

## 8. Compact Output Display

When auto-compact or manual `:compact` fires, the TUI chat log shows the compact event in **gray**:
```
> What is Kant's categorical imperative?

*Kant argues that the categorical imperative is...*

*[compacted: Previous conversation summarized. Key topics: Kant's ethics, deontology, moral law.]*

> Tell me more about the Formula of Universal Law.
```

Implementation: add a CSS class or use Rich `Text(style="dim #666666")` for compact markers in the Markdown widget.

---

## Implementation Order

1. **workdir concept** (foundational — everything else depends on it)
2. **Config extensions**: MCP + SKILL fields in `BookScoutConfig`
3. **Session system** (SQLite schema + repository + TUI flow)
4. **`--resume` / `:resume`** (depends on session system)
5. **TUI full-screen fix** (standalone, quick win)
6. **SKILL system** (SkillFetchTool + loader + prompt assembly)
7. **MCP integration** (ExternalMcpToolset)
8. **`:compact` + compact display** (depends on ReadingMode changes)
9. **SciPy/NumPy support** (standalone, quick win)
10. **PowerShell tab title** (standalone, quick win)

---

## Files to Modify / Create

### New Files
| File | Purpose |
|------|---------|
| `python/bookscout-repl/bookscout/repl/session_manager.py` | Cross-session management, session DB operations |
| `python/bookscout-tools/bookscout/tools/skill_fetch.py` | SkillFetchTool |
| `python/bookscout-tools/bookscout/tools/mcp_toolset.py` | ExternalMcpToolset |
| `python/bookscout-repl/bookscout/repl/skill_loader.py` | SkillLoader (config → content with caching) |
| `python/bookscout-repl/bookscout/repl/prompt_builder.py` | Assembles system prompt from skills + SOUL + config |

### Modified Files
| File | Changes |
|------|---------|
| `python/bookscout-repl/bookscout/repl/config.py` | `workdir` field, `McpServerConfig`, `SkillConfig` |
| `python/bookscout-repl/bookscout/repl/__main__.py` | `--workdir`, `--resume` CLI args |
| `python/bookscout-repl/bookscout/repl/tui.py` | Full-screen CSS, session list, `:resume`/`:rename`/`:compact` commands, compact display, tab title |
| `python/bookscout-repl/bookscout/repl/context.py` | workdir-aware paths, session manager, MCP/skill integration |
| `python/bookscout-agents/bookscout/agents/reading/mode.py` | Public `compact()` method |
| `python/bookscout-agents/bookscout/agents/reading/agent.py` | Modified system prompt assembly |
| `python/bookscout-agents/bookscout/agents/reading/toolset.py` | Inject ExternalMcpToolset, SkillFetchTool |
| `python/bookscout-tools/bookscout/tools/computation.py` | NumPy/SciPy in `_ALLOWED_MODULES` |
| `python/bookscout-repl/config.example.yaml` | Document new MCP/skills sections |
