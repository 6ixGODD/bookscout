# BookScout TUI Optimization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform BookScout REPL TUI into a complete application with multi-session support, workdir-based organization, on-demand skills, external MCP integration, full-screen TUI, sci-py sandbox, and manual compact.

**Architecture:** Introduce `workdir` as the single root directory; sessions managed via a global `sessions.db` (SQLite); skills loaded on-demand via a `skill_fetch` tool; external MCP servers wrapped as `BaseTool` instances in a new `ExternalMcpToolset`; TUI CSS fixed for full-screen rendering; `ReadingMode` gains a public `compact()` method.

**Tech Stack:** Python 3.12+, Textual (TUI), SQLite/aiosqlite, pydantic v2, pytest-asyncio, MCP streamable HTTP client

## Global Constraints

- Python: `>=3.12,<3.14`
- All Python files start with `from __future__ import annotations`
- `BookScoutConfig()` must never raise — every field has a default
- No breaking changes to existing public APIs
- Tests use `uv run pytest python/tests/ -x -v`
- Pre-commit passes: `uv run pre-commit run --all-files`
- Config fields have defaults — existing config files remain valid
- Use `uv run` for all Python commands

---

## File Structure Map

### New Files
| File | Responsibility |
|------|---------------|
| `python/bookscout-repl/bookscout/repl/session_manager.py` | Global SessionManager — CRUD for sessions in `workdir/sessions.db` |
| `python/bookscout-tools/bookscout/tools/skill_fetch.py` | `SkillFetchTool` — on-demand skill content loading |
| `python/bookscout-tools/bookscout/tools/mcp_toolset.py` | `ExternalMcpToolset` — connects to external MCP servers, wraps tools |
| `python/bookscout-tools/bookscout/tools/mcp_client.py` | Low-level MCP streamable HTTP client |
| `python/bookscout-repl/bookscout/repl/skill_loader.py` | `SkillLoader` — reads config, caches skill content from disk |
| `python/bookscout-repl/bookscout/repl/prompt_builder.py` | `PromptBuilder` — assembles final system prompt string |

### Modified Files
| File | Changes |
|------|---------|
| `python/bookscout-repl/bookscout/repl/config.py` | Add `workdir`, `McpServerConfig`, `SkillConfig`, adjust `data_dir` default |
| `python/bookscout-repl/bookscout/repl/__main__.py` | Add `--workdir`/`-w`, `--resume`/`-r` CLI args |
| `python/bookscout-repl/bookscout/repl/tui.py` | Full-screen CSS, session list phase, `:resume`/`:rename`/`:session`/`:compact`/`:mcp` commands, compact display |
| `python/bookscout-repl/bookscout/repl/context.py` | workdir-aware paths, SessionManager, SkillLoader, PromptBuilder, MCP toolset injection |
| `python/bookscout-agents/bookscout/agents/reading/mode.py` | Public `compact()` method |
| `python/bookscout-agents/bookscout/agents/reading/agent.py` | Accept dynamic system prompt from PromptBuilder |
| `python/bookscout-agents/bookscout/agents/reading/toolset.py` | Inject ExternalMcpToolset + SkillFetchTool |
| `python/bookscout-tools/bookscout/tools/computation.py` | Add numpy, scipy to `_ALLOWED_MODULES`, fix submodule import |
| `python/bookscout-tools/bookscout/tools/__init__.py` | Export new tools |
| `python/bookscout-repl/config.example.yaml` | Document MCP + skills sections |

---

### Task 1: workdir Concept + Config Extension

**Files:**
- Modify: `python/bookscout-repl/bookscout/repl/config.py`
- Modify: `python/bookscout-repl/bookscout/repl/__main__.py`
- Modify: `python/bookscout-repl/bookscout/repl/context.py`
- Modify: `python/bookscout-repl/config.example.yaml`
- Test: `python/tests/test_tui_commands.py` (extend)

**Interfaces:**
- Produces: `BookScoutConfig.workdir: str`, `BookScoutConfig.data_dir` default changes to `{workdir}/data`
- Produces: `BookScoutConfig.mcp_servers: list[McpServerConfig]`, `BookScoutConfig.skills: list[SkillConfig]`
- Produces: `ReplContext.workdir: pathlib.Path`
- Produces: CLI `--workdir`/`-w` on both `tui` and `serve` subcommands

- [ ] **Step 1: Add new config models to config.py**

```python
# python/bookscout-repl/bookscout/repl/config.py — add after MinerUConfig


class McpServerConfig(BaseModel):
    """External MCP server definition."""
    name: str = Field(..., description="Display name for this MCP server")
    url: str | None = Field(default=None, description="Streamable HTTP endpoint URL")
    command: str | None = Field(default=None, description="Command to spawn (stdio transport)")
    args: list[str] = Field(default_factory=list, description="Arguments for command")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables for command")


class SkillConfig(BaseModel):
    """User-defined skill definition."""
    name: str = Field(..., description="Skill identifier")
    path: str = Field(..., description="Path to skill .md file, relative to workdir/skills/")
    description: str = Field(default="", description="What this skill does — shown to agent")
```

- [ ] **Step 2: Add workdir and new sections to BookScoutConfig**

```python
# In BookScoutConfig, modify/add these fields:

    workdir: str = Field(
        default=str(pathlib.Path.home() / ".bookscout"),
        description="Root workdir — everything lives here (config, data, sessions, skills, logs).",
    )

    data_dir: str = Field(
        default="",  # empty means "{workdir}/data"
        description="Base directory for book data, workspaces, and indexes. Defaults to {workdir}/data.",
    )

    mcp_servers: list[McpServerConfig] = Field(
        default_factory=list,
        description="External MCP servers to connect at startup.",
    )

    skills: list[SkillConfig] = Field(
        default_factory=list,
        description="User-defined skills available to the agent.",
    )

    @property
    def resolved_data_dir(self) -> pathlib.Path:
        """Resolve data_dir, defaulting to {workdir}/data."""
        if self.data_dir:
            return pathlib.Path(self.data_dir)
        return pathlib.Path(self.workdir) / "data"

    @property
    def resolved_workdir(self) -> pathlib.Path:
        return pathlib.Path(self.workdir).resolve()
```

- [ ] **Step 3: Update context.py to use workdir and resolved paths**

```python
# In ReplContext.__init__:
self._workdir = pathlib.Path(config.workdir).resolve()
self._data_dir = config.resolved_data_dir.resolve()
self._data_dir.mkdir(parents=True, exist_ok=True)
self._workdir.mkdir(parents=True, exist_ok=True)

# Also create subdirectories:
(self._workdir / "skills").mkdir(parents=True, exist_ok=True)
(self._workdir / "logs").mkdir(parents=True, exist_ok=True)

# Add property:
@property
def workdir(self) -> pathlib.Path:
    return self._workdir
```

- [ ] **Step 4: Add --workdir and --resume CLI args to __main__.py**

```python
# In tui() and serve() command functions, add parameter:
workdir: t.Annotated[
    str | None,
    typer.Option("--workdir", "-w", help=f"Workdir root. Default: {_DEFAULT_WORKSPACE}"),
] = None,

# In _resolve_config, handle workdir:
if workdir is not None:
    overrides["workdir"] = workdir
```

- [ ] **Step 5: Update config.example.yaml**

```yaml
# Add after the existing sections:
workdir: "~/.bookscout"

# External MCP servers (optional)
# mcp_servers:
#   - name: "my-tools"
#     url: "http://localhost:8080/mcp"

# User skills (optional)
# skills:
#   - name: "close-reading"
#     path: "skills/close-reading.md"
#     description: "Close reading mode: analyze text structure..."
```

- [ ] **Step 6: Update existing tests for new config defaults**

```python
# In python/tests/test_tui_commands.py, verify config fields:
def test_config_workdir_default():
    config = BookScoutConfig()
    assert config.workdir == str(pathlib.Path.home() / ".bookscout")
    assert config.mcp_servers == []
    assert config.skills == []

def test_config_resolved_data_dir():
    config = BookScoutConfig(workdir="/tmp/test_bs")
    assert str(config.resolved_data_dir) == str(pathlib.Path("/tmp/test_bs/data"))
```

- [ ] **Step 7: Run tests, fix failures, commit**

```bash
uv run pytest python/tests/test_tui_commands.py -x -v
git add python/bookscout-repl/bookscout/repl/config.py \
        python/bookscout-repl/bookscout/repl/__main__.py \
        python/bookscout-repl/bookscout/repl/context.py \
        python/bookscout-repl/config.example.yaml \
        python/tests/test_tui_commands.py
git commit -m "feat: add workdir concept, MCP + skill config sections"
```

---

### Task 2: Session System

**Files:**
- Create: `python/bookscout-repl/bookscout/repl/session_manager.py`
- Modify: `python/bookscout-repl/bookscout/repl/context.py`
- Test: `python/tests/test_session_manager.py` (new)

**Interfaces:**
- Produces: `SessionManager` class with `create_session(book_id, name, kind) -> Session`, `list_sessions(book_id) -> list[Session]`, `get_session(session_id) -> Session`, `rename_session(session_id, name)`, `update_after_turn(session_id, user_input, response_text)`, `list_all_sessions() -> list[Session]`
- Produces: `Session` pydantic model

- [ ] **Step 1: Create Session model and SessionManager**

```python
# python/bookscout-repl/bookscout/repl/session_manager.py
"""Global session manager — SQLite-backed session store at workdir/sessions.db."""

from __future__ import annotations

import json
import pathlib
import typing as t

import pydantic

from bookscout.core.ids import gen_id
from bookscout.core.time import utcnow_ts
from bookscout.core.mixins import AsyncResourceMixin, LoggingMixin
from bookscout.logging import Logger
from bookscout.sqlite import SQLite, SQLiteConfig


class Session(pydantic.BaseModel):
    session_id: str = pydantic.Field(default_factory=lambda: gen_id(prefix="sess_"))
    book_id: str
    name: str  # default: "<book_title>-<random6>"
    kind: str = "chat"
    created_at: float = pydantic.Field(default_factory=utcnow_ts)
    updated_at: float = pydantic.Field(default_factory=utcnow_ts)
    turn_count: int = 0
    status: str = "active"  # 'active' | 'archived'
    extra: dict[str, t.Any] = pydantic.Field(default_factory=dict)


class SessionManager(LoggingMixin, AsyncResourceMixin):
    def __init__(self, workdir: pathlib.Path, logger: Logger) -> None:
        super().__init__(logger=logger)
        db_path = workdir / "sessions.db"
        self._sqlite = SQLite(
            config=SQLiteConfig(uri=f"sqlite+aiosqlite:///{db_path}"),
            logger=logger,
        )

    async def startup(self) -> None:
        await self._sqlite.startup()
        await self._create_schema()
        await super().startup()

    async def shutdown(self) -> None:
        await self._sqlite.shutdown()
        await super().shutdown()

    async def _create_schema(self) -> None:
        await self._sqlite.exec(
            """CREATE TABLE IF NOT EXISTS session (
                session_id   TEXT PRIMARY KEY,
                book_id      TEXT NOT NULL,
                name         TEXT NOT NULL,
                kind         TEXT NOT NULL DEFAULT 'chat',
                created_at   REAL NOT NULL,
                updated_at   REAL NOT NULL,
                turn_count   INTEGER NOT NULL DEFAULT 0,
                status       TEXT NOT NULL DEFAULT 'active',
                extra_json   TEXT NOT NULL DEFAULT '{}'
            )""",
            readonly=False,
        )
        await self._sqlite.exec(
            "CREATE INDEX IF NOT EXISTS idx_session_book ON session(book_id)",
            readonly=False,
        )

    async def create(self, *, book_id: str, name: str, kind: str = "chat") -> Session:
        session = Session(book_id=book_id, name=name, kind=kind)
        await self._save(session)
        return session

    async def get(self, session_id: str) -> Session | None:
        result = await self._sqlite.exec(
            "SELECT * FROM session WHERE session_id = :sid",
            readonly=True, sid=session_id,
        )
        row = result.fetchone()
        return self._row_to_session(row) if row else None

    async def list_by_book(self, book_id: str) -> list[Session]:
        result = await self._sqlite.exec(
            "SELECT * FROM session WHERE book_id = :bid AND status = 'active' ORDER BY updated_at DESC",
            readonly=True, bid=book_id,
        )
        return [self._row_to_session(r) for r in result.fetchall()]

    async def list_all(self) -> list[Session]:
        result = await self._sqlite.exec(
            "SELECT * FROM session WHERE status = 'active' ORDER BY updated_at DESC",
            readonly=True,
        )
        return [self._row_to_session(r) for r in result.fetchall()]

    async def rename(self, session_id: str, name: str) -> None:
        await self._sqlite.exec(
            "UPDATE session SET name = :name, updated_at = :ts WHERE session_id = :sid",
            readonly=False, name=name, ts=utcnow_ts(), sid=session_id,
        )

    async def update_after_turn(self, session_id: str, *, user_input: str, response_text: str) -> None:
        await self._sqlite.exec(
            """UPDATE session SET
               updated_at = :ts, turn_count = turn_count + 1,
               extra_json = :extra
               WHERE session_id = :sid""",
            readonly=False,
            ts=utcnow_ts(),
            sid=session_id,
            extra=json.dumps({"last_user_input": user_input, "last_response": response_text[:200]}),
        )

    async def archive(self, session_id: str) -> None:
        await self._sqlite.exec(
            "UPDATE session SET status = 'archived', updated_at = :ts WHERE session_id = :sid",
            readonly=False, ts=utcnow_ts(), sid=session_id,
        )

    async def _save(self, session: Session) -> None:
        await self._sqlite.exec(
            """INSERT OR REPLACE INTO session (
                session_id, book_id, name, kind, created_at, updated_at,
                turn_count, status, extra_json
            ) VALUES (
                :sid, :bid, :name, :kind, :ca, :ua, :tc, :st, :ex
            )""",
            readonly=False,
            sid=session.session_id, bid=session.book_id, name=session.name,
            kind=session.kind, ca=session.created_at, ua=session.updated_at,
            tc=session.turn_count, st=session.status, ex=json.dumps(session.extra),
        )

    @staticmethod
    def _row_to_session(row: t.Any) -> Session:
        m = row._mapping if hasattr(row, "_mapping") else row
        return Session(
            session_id=m["session_id"], book_id=m["book_id"], name=m["name"],
            kind=m["kind"], created_at=m["created_at"], updated_at=m["updated_at"],
            turn_count=m["turn_count"], status=m["status"],
            extra=json.loads(m["extra_json"] or "{}"),
        )
```

- [ ] **Step 2: Wire SessionManager into ReplContext**

```python
# In ReplContext.__init__, add:
self._session_manager: SessionManager | None = None

# In ReplContext.startup(), add after books_store creation:
from .session_manager import SessionManager
self._session_manager = SessionManager(workdir=self._workdir, logger=self.logger)
await self._session_manager.startup()

# Add property and accessor:
@property
def session_manager(self) -> SessionManager:
    if self._session_manager is None:
        raise RuntimeError("ReplContext not started")
    return self._session_manager

# In ReplContext.shutdown(), add:
if self._session_manager is not None:
    await self._session_manager.shutdown()
```

- [ ] **Step 3: Write the test file**

```python
# python/tests/test_session_manager.py
from __future__ import annotations

import pathlib
import tempfile

import pytest

from bookscout.logging import build_logger, LoggingConfig
from bookscout.repl.session_manager import Session, SessionManager


@pytest.fixture
async def session_manager():
    with tempfile.TemporaryDirectory() as tmp:
        logger = build_logger(LoggingConfig(name="test", level="ERROR", targets=[]))
        mgr = SessionManager(workdir=pathlib.Path(tmp), logger=logger)
        await mgr.startup()
        yield mgr
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_create_and_get(session_manager: SessionManager):
    sess = await session_manager.create(book_id="book_1", name="My Session", kind="chat")
    assert sess.book_id == "book_1"
    assert sess.name == "My Session"

    loaded = await session_manager.get(sess.session_id)
    assert loaded is not None
    assert loaded.name == "My Session"


@pytest.mark.asyncio
async def test_list_by_book(session_manager: SessionManager):
    await session_manager.create(book_id="book_1", name="A")
    await session_manager.create(book_id="book_1", name="B")
    await session_manager.create(book_id="book_2", name="C")

    b1 = await session_manager.list_by_book("book_1")
    assert len(b1) == 2

    all_s = await session_manager.list_all()
    assert len(all_s) == 3


@pytest.mark.asyncio
async def test_rename(session_manager: SessionManager):
    sess = await session_manager.create(book_id="book_1", name="Old")
    await session_manager.rename(sess.session_id, "New")
    loaded = await session_manager.get(sess.session_id)
    assert loaded.name == "New"


@pytest.mark.asyncio
async def test_update_after_turn(session_manager: SessionManager):
    sess = await session_manager.create(book_id="book_1", name="Test")
    await session_manager.update_after_turn(sess.session_id, user_input="hello", response_text="hi there")
    loaded = await session_manager.get(sess.session_id)
    assert loaded.turn_count == 1


@pytest.mark.asyncio
async def test_archive(session_manager: SessionManager):
    sess = await session_manager.create(book_id="book_1", name="ToArchive")
    await session_manager.archive(sess.session_id)
    loaded = await session_manager.get(sess.session_id)
    assert loaded.status == "archived"
    active = await session_manager.list_by_book("book_1")
    assert len(active) == 0
```

- [ ] **Step 4: Run tests, fix failures, commit**

```bash
uv run pytest python/tests/test_session_manager.py -x -v
git add python/bookscout-repl/bookscout/repl/session_manager.py \
        python/bookscout-repl/bookscout/repl/context.py \
        python/tests/test_session_manager.py
git commit -m "feat: add global session manager with SQLite backend"
```

---

### Task 3: TUI Full-Screen Fix + Shell Tab Title

**Files:**
- Modify: `python/bookscout-repl/bookscout/repl/tui.py`

**Interfaces:**
- No new public interfaces — CSS-only changes + one-liner for tab title

- [ ] **Step 1: Fix full-screen CSS**

Replace the `Screen` CSS block and add global reset styles:

```python
# In BookScoutTui.CSS, replace the Screen block:
CSS = """
$surface: #000000;
$panel: #000000;
$boost: #111111;
$text-muted: #999999;

* {
    scrollbar-size: 0 0;
    margin: 0;
    padding: 0;
}

Screen {
    background: #000000;
    color: #c0c0c0;
    scrollbar-size: 0 0;
    layers: none;
    overflow: hidden;
}

#status_bar {
    dock: bottom;
    height: 1;
    color: #666666;
    padding: 0 1;
}
#header {
    layout: vertical;
    height: auto;
    padding: 0 1;
}
#header_brand {
    color: #ffffff;
    text-style: bold;
    height: 1;
}
#header_hint {
    color: #555555;
    height: auto;
    min-height: 1;
}
#header_rule {
    color: #ffffff;
    height: 1;
}
#main {
    layout: vertical;
    width: 100%;
    height: 1fr;
    padding: 0 1;
}
/* ... rest unchanged, but ensure all containers have width: 100% */
Container {
    background: #000000;
    width: 100%;
}
Input {
    border-top: solid #ffffff;
    border-bottom: solid #ffffff;
    border-left: none;
    border-right: none;
    background: #000000;
    color: #c0c0c0;
    padding: 0 0 0 0;
    height: 3;
    width: 100%;
}
/* ... rest of existing CSS unchanged ... */
"""
```

- [ ] **Step 2: Add shell tab title setter**

Add a helper method and call it when entering chat:

```python
# In BookScoutTui, add method:
@staticmethod
def _set_console_title(title: str) -> None:
    """Set the terminal/console window title."""
    import sys
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass
    else:
        # OSC escape sequence for most Unix terminals
        print(f"\033]0;{title}\007", end="", flush=True)

# In _enter_chat(), add after setting phase:
def _enter_chat(self, book: Book) -> None:
    # ... existing code ...
    title = book.title or "(untitled)"
    author = book.author or "Unknown"
    self._set_console_title(f"{title} - {author}")
    # ... rest of existing code ...
```

- [ ] **Step 3: Run existing TUI tests to verify CSS didn't break anything**

```bash
uv run pytest python/tests/test_tui_commands.py -x -v
```

- [ ] **Step 4: Commit**

```bash
git add python/bookscout-repl/bookscout/repl/tui.py
git commit -m "fix(tui): full-screen CSS and console tab title"
```

---

### Task 4: SKILL System (SkillFetchTool + SkillLoader + PromptBuilder)

**Files:**
- Create: `python/bookscout-tools/bookscout/tools/skill_fetch.py`
- Create: `python/bookscout-repl/bookscout/repl/skill_loader.py`
- Create: `python/bookscout-repl/bookscout/repl/prompt_builder.py`
- Modify: `python/bookscout-tools/bookscout/tools/__init__.py` (export)
- Modify: `python/bookscout-agents/bookscout/agents/reading/agent.py`
- Modify: `python/bookscout-agents/bookscout/agents/reading/toolset.py`
- Modify: `python/bookscout-repl/bookscout/repl/context.py`
- Test: `python/tests/test_skill_system.py` (new)

**Interfaces:**
- Produces: `SkillFetchTool(name="skill_fetch", ...)` — BaseTool
- Produces: `SkillLoader(workdir, skill_configs)` — `.list_skills() -> list[dict]`, `.get_skill(name) -> str`
- Produces: `PromptBuilder(skill_descriptions, soul_path, current_system_prompt)` — `.build() -> str`

- [ ] **Step 1: Create SkillFetchTool**

```python
# python/bookscout-tools/bookscout/tools/skill_fetch.py
"""On-demand skill content loading tool."""

from __future__ import annotations

import typing as t
from typing import Annotated

from bookscout.tools import BaseTool, Property


class SkillFetchTool(
    BaseTool,
    name="skill_fetch",
    description=(
        "Fetch the full content of a user-defined skill. "
        "Use this when you need detailed instructions for a specific skill. "
        "Call with the skill name to get its complete guidance."
    ),
):
    """Tool that loads skill content on demand, keeping it out of context until needed."""

    def __init__(self, skill_loader: t.Any) -> None:
        self._loader = skill_loader

    async def __call__(
        self,
        skill_name: Annotated[str, Property(description="Name of the skill to fetch (e.g. 'close-reading')")],
    ) -> str:
        content = self._loader.get_skill(skill_name)
        if content is None:
            available = [s["name"] for s in self._loader.list_skills()]
            return f"Skill '{skill_name}' not found. Available skills: {', '.join(available)}"
        return content
```

- [ ] **Step 2: Create SkillLoader**

```python
# python/bookscout-repl/bookscout/repl/skill_loader.py
"""Skill loader — reads skill configs, loads .md files, caches content."""

from __future__ import annotations

import pathlib
import typing as t

from .config import SkillConfig


class SkillLoader:
    """Loads skill definitions from config and fetches content from disk.

    Content is cached in memory after first fetch — skills are only read
    once per session.
    """

    def __init__(self, workdir: pathlib.Path, skills: list[SkillConfig]) -> None:
        self._workdir = workdir
        self._skills = skills
        self._cache: dict[str, str] = {}

    def list_skills(self) -> list[dict[str, str]]:
        """Return skill summaries (name + description) for system prompt."""
        return [
            {"name": s.name, "description": s.description}
            for s in self._skills
        ]

    def get_skill(self, name: str) -> str | None:
        """Get skill content by name. Returns None if not found."""
        if name in self._cache:
            return self._cache[name]

        cfg = next((s for s in self._skills if s.name == name), None)
        if cfg is None:
            return None

        skill_path = self._workdir / "skills" / cfg.path
        if not skill_path.exists():
            return None

        content = skill_path.read_text(encoding="utf-8")
        self._cache[name] = content
        return content
```

- [ ] **Step 3: Create PromptBuilder**

```python
# python/bookscout-repl/bookscout/repl/prompt_builder.py
"""Assembles the final system prompt from skills, SOUL, and base instructions."""

from __future__ import annotations

import pathlib
import datetime


class PromptBuilder:
    """Builds the system prompt with skills section, base instructions, SOUL, and date/time."""

    def __init__(
        self,
        skill_descriptions: list[dict[str, str]],
        soul_path: pathlib.Path,
        base_system_prompt: str,
    ) -> None:
        self._skill_descriptions = skill_descriptions
        self._soul_path = soul_path
        self._base_system_prompt = base_system_prompt

    def build(self) -> str:
        """Assemble the complete system prompt."""
        parts: list[str] = []

        # 1. Skills section
        if self._skill_descriptions:
            parts.append("## Available Skills")
            parts.append(
                "You have access to a `skill_fetch` tool. Call it with a skill name "
                "to load its full instructions when needed. Do NOT guess skill content — "
                "always fetch it first."
            )
            parts.append("")
            for skill in self._skill_descriptions:
                parts.append(f"- **{skill['name']}**: {skill['description']}")
            parts.append("")
            parts.append("---")
            parts.append("")

        # 2. Base system prompt
        parts.append(self._base_system_prompt)

        # 3. SOUL
        soul_content = self._read_soul()
        if soul_content:
            parts.append("")
            parts.append("---")
            parts.append("")
            parts.append(soul_content)

        # 4. Current date/time
        now = datetime.datetime.now().astimezone()
        tz_name = now.tzinfo.tzname(now) if now.tzinfo else "UTC"
        parts.append("")
        parts.append("---")
        parts.append("")
        parts.append(f"Current date: {now.strftime('%Y-%m-%d')}")
        parts.append(f"Current time: {now.strftime('%H:%M:%S')} {tz_name}")

        return "\n".join(parts)

    def _read_soul(self) -> str | None:
        """Read SOUL.md if it exists."""
        if self._soul_path.exists():
            return self._soul_path.read_text(encoding="utf-8").strip()
        return None
```

- [ ] **Step 4: Wire into ReadingAgent to accept dynamic instructions**

```python
# In ReadingAgent.__init__, allow instructions override:
class ReadingAgent(ModeAgent):
    def __init__(
        self,
        *,
        name: str = "reading_agent",
        toolset: Toolset,
        profiles: ReadingLLMProfiles | None = None,
        logger: Logger,
        instructions: str | None = None,  # NEW: override system prompt
    ) -> None:
        super().__init__(
            name=name,
            instructions=instructions or READING_SYSTEM_PROMPT,
            toolset=toolset,
            logger=logger,
        )
        self.profiles = profiles or ReadingLLMProfiles()
```

- [ ] **Step 5: Wire into toolset and ReplContext**

```python
# In ReadingAgentToolset, add skill_fetch tool:
# In startup():
from bookscout.tools.skill_fetch import SkillFetchTool
# ... after computation tools:
if skill_loader is not None:  # NEW parameter
    tools.append(SkillFetchTool(skill_loader))

# In ReplContext.get_or_create_mode(), create the prompt builder:
from .skill_loader import SkillLoader
from .prompt_builder import PromptBuilder

skill_loader = SkillLoader(self._workdir, self._config.skills)
soul_path = self._workdir / "SOUL.md"
prompt = PromptBuilder(
    skill_descriptions=skill_loader.list_skills(),
    soul_path=soul_path,
    base_system_prompt=READING_SYSTEM_PROMPT,
).build()
# Pass `instructions=prompt` to ReadingAgent
# Pass skill_loader to ReadingAgentToolset
```

- [ ] **Step 6: Write test**

```python
# python/tests/test_skill_system.py
from __future__ import annotations

import pathlib
import tempfile

from bookscout.repl.config import SkillConfig
from bookscout.repl.skill_loader import SkillLoader
from bookscout.repl.prompt_builder import PromptBuilder


def test_skill_loader_list():
    skills = [
        SkillConfig(name="test-skill", path="test-skill.md", description="A test skill"),
    ]
    loader = SkillLoader(workdir=pathlib.Path("/tmp"), skills=skills)
    result = loader.list_skills()
    assert len(result) == 1
    assert result[0]["name"] == "test-skill"


def test_skill_loader_get_missing():
    loader = SkillLoader(workdir=pathlib.Path("/tmp"), skills=[])
    assert loader.get_skill("nonexistent") is None


def test_skill_loader_get_cached():
    with tempfile.TemporaryDirectory() as tmp:
        workdir = pathlib.Path(tmp)
        skills_dir = workdir / "skills"
        skills_dir.mkdir()
        skill_file = skills_dir / "test-skill.md"
        skill_file.write_text("# Test Skill\nContent here.")

        skills = [SkillConfig(name="test-skill", path="test-skill.md", description="desc")]
        loader = SkillLoader(workdir=workdir, skills=skills)
        content = loader.get_skill("test-skill")
        assert content == "# Test Skill\nContent here."
        # Second call hits cache
        content2 = loader.get_skill("test-skill")
        assert content2 == content


def test_prompt_builder_basic():
    with tempfile.TemporaryDirectory() as tmp:
        workdir = pathlib.Path(tmp)
        builder = PromptBuilder(
            skill_descriptions=[{"name": "s1", "description": "d1"}],
            soul_path=workdir / "SOUL.md",
            base_system_prompt="BASE PROMPT",
        )
        result = builder.build()
        assert "## Available Skills" in result
        assert "- **s1**: d1" in result
        assert "BASE PROMPT" in result
        assert "Current date:" in result
        assert "Current time:" in result


def test_prompt_builder_with_soul():
    with tempfile.TemporaryDirectory() as tmp:
        workdir = pathlib.Path(tmp)
        soul_path = workdir / "SOUL.md"
        soul_path.write_text("You are a wise librarian.")

        builder = PromptBuilder(
            skill_descriptions=[],
            soul_path=soul_path,
            base_system_prompt="BASE",
        )
        result = builder.build()
        assert "You are a wise librarian." in result


def test_prompt_builder_no_skills():
    builder = PromptBuilder(
        skill_descriptions=[],
        soul_path=pathlib.Path("/nonexistent"),
        base_system_prompt="BASE",
    )
    result = builder.build()
    assert "## Available Skills" not in result
    assert "BASE" in result
```

- [ ] **Step 7: Run tests, fix failures, commit**

```bash
uv run pytest python/tests/test_skill_system.py -x -v
git add python/bookscout-tools/bookscout/tools/skill_fetch.py \
        python/bookscout-repl/bookscout/repl/skill_loader.py \
        python/bookscout-repl/bookscout/repl/prompt_builder.py \
        python/bookscout-agents/bookscout/agents/reading/agent.py \
        python/bookscout-agents/bookscout/agents/reading/toolset.py \
        python/bookscout-repl/bookscout/repl/context.py \
        python/tests/test_skill_system.py
git commit -m "feat: add on-demand skill system with SkillFetchTool"
```

---

### Task 5: MCP Integration (ExternalMcpToolset)

**Files:**
- Create: `python/bookscout-tools/bookscout/tools/mcp_client.py`
- Create: `python/bookscout-tools/bookscout/tools/mcp_toolset.py`
- Modify: `python/bookscout-tools/bookscout/tools/__init__.py` (export)
- Modify: `python/bookscout-agents/bookscout/agents/reading/toolset.py`
- Modify: `python/bookscout-repl/bookscout/repl/context.py`
- Test: `python/tests/test_mcp_toolset.py` (new)

**Interfaces:**
- Produces: `McpClient(url)` — `async list_tools() -> list[dict]`, `async call_tool(name, args) -> str`
- Produces: `ExternalMcpToolset(configs, logger)` — extends `Toolset`, each MCP tool becomes a `BaseTool`
- Consumes: `BookScoutConfig.mcp_servers`

- [ ] **Step 1: Create MCP streamable HTTP client**

```python
# python/bookscout-tools/bookscout/tools/mcp_client.py
"""Minimal MCP streamable HTTP client for tool discovery and invocation."""

from __future__ import annotations

import json
import typing as t

import httpx


class McpClientError(Exception):
    pass


class McpClient:
    """Connects to an MCP server over streamable HTTP, discovers and invokes tools."""

    def __init__(self, url: str, *, timeout: float = 30.0) -> None:
        self._url = url.rstrip("/")
        self._timeout = timeout
        self._session_id: str | None = None

    async def __aenter__(self) -> McpClient:
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *args: t.Any) -> None:
        await self._client.aclose()

    async def _post(self, method: str, params: dict | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1}
        resp = await self._client.post(self._url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise McpClientError(f"MCP server returned {resp.status_code}: {resp.text}")
        session_id = resp.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id
        return resp.json()

    async def initialize(self) -> dict:
        return await self._post("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "bookscout", "version": "0.2.0"},
        })

    async def list_tools(self) -> list[dict]:
        result = await self._post("tools/list")
        return result.get("result", {}).get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self._post("tools/call", {"name": name, "arguments": arguments})
        content = result.get("result", {}).get("content", [])
        if content and isinstance(content, list):
            return "\n".join(
                c.get("text", "") if isinstance(c, dict) else str(c)
                for c in content
            )
        return json.dumps(content)
```

- [ ] **Step 2: Create ExternalMcpToolset**

```python
# python/bookscout-tools/bookscout/tools/mcp_toolset.py
"""Wraps external MCP server tools as BaseTool instances."""

from __future__ import annotations

import json
import typing as t
from typing import Annotated

from bookscout.logging import Logger
from bookscout.tools import BaseTool, Property, Toolset

from .mcp_client import McpClient, McpClientError


def _make_mcp_wrapper(client: McpClient, tool_def: dict) -> type[BaseTool]:
    """Generate a BaseTool subclass wrapping a single MCP tool."""
    name = tool_def["name"]
    description = tool_def.get("description", f"MCP tool: {name}")
    input_schema = tool_def.get("inputSchema", {})
    properties = input_schema.get("properties", {})

    async def _call(self, **kwargs: t.Any) -> str:
        try:
            async with client as c:
                await c.initialize()
                return await c.call_tool(name, kwargs)
        except McpClientError as e:
            return f"MCP tool '{name}' error: {e}"

    # Build the tool class dynamically.
    attrs: dict[str, t.Any] = {
        "__call__": _call,
        "__function_name__": name,
        "__function_description__": description,
        "_tool_def": tool_def,
    }

    # Create Annotated parameters from schema.
    import inspect
    params = []
    for prop_name, prop_schema in properties.items():
        prop_desc = prop_schema.get("description", "")
        prop_type = prop_schema.get("type", "string")
        if prop_type == "string":
            ann = Annotated[str, Property(description=prop_desc)]
        elif prop_type in ("number", "integer"):
            ann = Annotated[float, Property(description=prop_desc)]
        elif prop_type == "boolean":
            ann = Annotated[bool, Property(description=prop_desc)]
        else:
            ann = Annotated[str, Property(description=prop_desc)]
        params.append(inspect.Parameter(
            prop_name, inspect.Parameter.KEYWORD_ONLY, annotation=ann,
        ))
        attrs[prop_name] = None  # placeholder

    # Create the class.
    tool_cls = type(f"MCP_{name}", (BaseTool,), attrs)
    return tool_cls()


class ExternalMcpToolset(Toolset):
    """Toolset connecting to external MCP servers.

    Each server's tools are wrapped as BaseTool instances and registered
    in this toolset. Connection failures are non-fatal — the server is
    skipped with a warning.
    """

    def __init__(self, configs: list[t.Any], logger: Logger) -> None:
        super().__init__(
            name="external_mcp",
            description="External MCP server tools.",
            tools=[],
            logger=logger,
        )
        self._configs = configs

    async def startup(self) -> None:
        tools: list[BaseTool] = []
        for cfg in self._configs:
            self.logger.info("connecting to MCP server", name=cfg.name)
            try:
                if cfg.url:
                    async with McpClient(cfg.url) as client:
                        await client.initialize()
                        tool_list = await client.list_tools()
                        for tool_def in tool_list:
                            wrapped = _make_mcp_wrapper(client, tool_def)
                            tools.append(wrapped)
                            self.logger.debug(
                                "registered MCP tool", server=cfg.name, tool=tool_def["name"],
                            )
                elif cfg.command:
                    self.logger.warning("stdio MCP not yet implemented", name=cfg.name)
            except McpClientError as e:
                self.logger.warning("MCP server unavailable", name=cfg.name, error=str(e))
            except Exception as e:
                self.logger.warning("MCP server error", name=cfg.name, error=str(e))

        self.internal_tools = tools
        await super().startup()
```

- [ ] **Step 3: Wire into ReadingAgentToolset and ReplContext**

```python
# In ReadingAgentToolset.__init__, add parameter:
external_mcp_configs: list[t.Any] | None = None

# In ReadingAgentToolset.startup(), after existing tools:
if external_mcp_configs:
    from bookscout.tools.mcp_toolset import ExternalMcpToolset
    mcp_toolset = ExternalMcpToolset(external_mcp_configs, logger=self.logger)
    await mcp_toolset.startup()
    self._resources.append(mcp_toolset)
    tools.extend(mcp_toolset.get_all_tools())

# In ReplContext.get_or_create_mode(), pass mcp configs:
# ... to ReadingAgentToolset(..., external_mcp_configs=self._config.mcp_servers)
```

- [ ] **Step 4: Write test**

```python
# python/tests/test_mcp_toolset.py
from __future__ import annotations

from bookscout.repl.config import McpServerConfig


def test_mcp_config_model():
    cfg = McpServerConfig(name="test", url="http://localhost:8080/mcp")
    assert cfg.name == "test"
    assert cfg.url == "http://localhost:8080/mcp"


def test_mcp_config_optional_fields():
    cfg = McpServerConfig(name="test")
    assert cfg.url is None
    assert cfg.command is None
    assert cfg.args == []
    assert cfg.env == {}
```

- [ ] **Step 5: Run tests, commit**

```bash
uv run pytest python/tests/test_mcp_toolset.py -x -v
git add python/bookscout-tools/bookscout/tools/mcp_client.py \
        python/bookscout-tools/bookscout/tools/mcp_toolset.py \
        python/bookscout-tools/bookscout/tools/__init__.py \
        python/bookscout-agents/bookscout/agents/reading/toolset.py \
        python/bookscout-repl/bookscout/repl/context.py \
        python/tests/test_mcp_toolset.py
git commit -m "feat: add external MCP toolset with streamable HTTP client"
```

---

### Task 6: SciPy + NumPy Sandbox Support

**Files:**
- Modify: `python/bookscout-tools/bookscout/tools/computation.py`
- Test: `python/tests/test_tools_computation.py` (new)

**Interfaces:**
- Modifies: `_ALLOWED_MODULES` frozen set
- Modifies: module import logic in `PythonExecuteTool.__call__`

- [ ] **Step 1: Update _ALLOWED_MODULES and import logic**

```python
# In computation.py, replace _ALLOWED_MODULES:
_ALLOWED_MODULES = frozenset({
    "math", "statistics", "itertools", "functools", "operator",
    "decimal", "fractions", "random", "array", "json", "re",
    "datetime", "collections", "dataclasses", "typing",
    "hashlib", "base64", "textwrap", "string", "unicodedata",
    "cmath", "numbers",
    # Scientific computing
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
})

# In PythonExecuteTool.__call__, replace the module import loop:
for mod_name in _ALLOWED_MODULES:
    try:
        parts = mod_name.split(".")
        mod = __import__(mod_name)
        for part in parts[1:]:
            mod = getattr(mod, part)
        safe_globals[mod_name] = mod
        # Also register top-level name (e.g. "numpy" for "numpy.linalg")
        if "." in mod_name:
            safe_globals.setdefault(parts[0], __import__(parts[0]))
    except ImportError:
        pass
```

- [ ] **Step 2: Write test**

```python
# python/tests/test_tools_computation.py
from __future__ import annotations

import pytest
from bookscout.tools.computation import PythonExecuteTool, WolframExecuteTool


@pytest.mark.asyncio
async def test_python_execute_numpy():
    tool = PythonExecuteTool()
    result = await tool(code="import numpy as np; a = np.array([1, 2, 3]); print(a.sum())")
    assert "6" in result


@pytest.mark.asyncio
async def test_python_execute_scipy_stats():
    tool = PythonExecuteTool()
    result = await tool(code=(
        "from scipy import stats\n"
        "import numpy as np\n"
        "a = np.array([1, 2, 3, 4, 5])\n"
        "mean = a.mean()\n"
        "std = a.std()\n"
        "print(f'mean={mean}, std={std}')"
    ))
    assert "mean=" in result


@pytest.mark.asyncio
async def test_python_execute_basic():
    tool = PythonExecuteTool()
    result = await tool(code="print(1 + 1)")
    assert "2" in result


@pytest.mark.asyncio
async def test_python_execute_timeout():
    tool = PythonExecuteTool()
    result = await tool(code="import time; time.sleep(20)")
    assert "timed out" in result.lower()
```

- [ ] **Step 3: Run tests, commit**

```bash
uv run pytest python/tests/test_tools_computation.py -x -v
git add python/bookscout-tools/bookscout/tools/computation.py \
        python/tests/test_tools_computation.py
git commit -m "feat: add numpy and scipy to Python sandbox"
```

---

### Task 7: Manual Compact Support (public compact() + :compact command)

**Files:**
- Modify: `python/bookscout-agents/bookscout/agents/reading/mode.py`
- Modify: `python/bookscout-repl/bookscout/repl/tui.py`
- Modify: `python/bookscout-repl/bookscout/repl/context.py`
- Test: extend `python/tests/test_reading_agent.py`

**Interfaces:**
- Produces: `ReadingMode.compact() -> str` (public, force-compact)
- Produces: TUI `:compact` command handler

- [ ] **Step 1: Add public compact() method to ReadingMode**

```python
# In ReadingMode, add method:
async def compact(self) -> str:
    """Force-compact the conversation regardless of token threshold.

    Returns:
        The generated summary text, or empty string if nothing to compact
        (e.g. fewer than KEEP_MESSAGES in history).
    """
    from bookscout.agents.mode import _COMPACT_KEEP_MESSAGES

    if len(self._messages) <= _COMPACT_KEEP_MESSAGES:
        return ""

    # Estimate tokens.
    text = "\n".join(m["content"] for m in self._messages)
    self.logger.info("manual compact requested", messages=len(self._messages))

    # Split: summarize oldest, keep recent.
    old_messages = self._messages[:-_COMPACT_KEEP_MESSAGES]
    recent_messages = self._messages[-_COMPACT_KEEP_MESSAGES:]

    old_text = "\n".join(f"[{m['role']}] {m['content']}" for m in old_messages)

    from bookscout.llm.types import CompletionOptions, SystemMessage, UserMessage

    summary_response = await self.llm.chat_completion(
        [
            SystemMessage(
                content=(
                    "Summarize the following conversation history concisely. "
                    "Preserve key facts, decisions, and context. "
                    "Output only the summary, no preamble."
                )
            ),
            UserMessage(content=old_text),
        ],
        options=CompletionOptions(max_tokens=500, temperature=0.3),
    )
    summary_text = summary_response["message"].content.strip()

    # Replace.
    self._messages = [
        {"role": "user", "content": f"[Conversation summary]\n{summary_text}"},
        *recent_messages,
    ]

    self._sync_state_messages()
    self.logger.info("manual compact done", kept=len(self._messages))
    return summary_text
```

- [ ] **Step 2: Add :compact command to TUI**

```python
# In BookScoutTui._handle_chat_input, add command handler:
if low == ":compact":
    if self._repl_context is None or self._selected_book is None or self._current_session is None:
        return
    mode = self._repl_context._modes.get(self._current_session.session_id)
    if mode is None:
        self._set_status("  no active session")
        return
    self._chat_busy = True
    self._set_status("  compacting...")
    try:
        summary = await mode.compact()
        if summary:
            # Display compact result in dim gray.
            self._chat_markdown += f"\n*[compacted: {summary[:200]}{'...' if len(summary) > 200 else ''}]*\n\n"
        else:
            self._chat_markdown += "\n*[nothing to compact]*\n\n"
        log = self.query_one("#chat_log", Markdown)
        await log.update(self._chat_markdown)
        log.scroll_end(animate=False)
    except Exception as e:
        self._set_status(f"  compact failed: {e}")
    finally:
        self._chat_busy = False
        self._set_status(f"  {self._selected_book.title or '(untitled)'}")
    self._focus_input()
    return
```

- [ ] **Step 3: Also render auto-compact events in gray**

```python
# In _handle_chunk, when auto_compacted fires:
elif chunk.kind == "status":
    data = chunk.data if isinstance(chunk.data, dict) else {}
    phase = data.get("phase", "")
    if phase == "auto_compacted":
        self._chat_markdown += "\n*[auto-compacted]*\n\n"
        log = self.query_one("#chat_log", Markdown)
        await log.update(self._chat_markdown)
        log.scroll_end(animate=False)
```

- [ ] **Step 4: Run tests, commit**

```bash
uv run pytest python/tests/test_reading_agent.py -x -v
git add python/bookscout-agents/bookscout/agents/reading/mode.py \
        python/bookscout-repl/bookscout/repl/tui.py
git commit -m "feat: add public compact() method and :compact TUI command"
```

---

### Task 8: Session List UI + :resume / :rename / :session Commands

**Files:**
- Modify: `python/bookscout-repl/bookscout/repl/tui.py`
- Modify: `python/bookscout-repl/bookscout/repl/context.py`
- Modify: `python/bookscout-repl/bookscout/repl/__main__.py`
- Test: extend `python/tests/test_tui_commands.py`

**Interfaces:**
- Consumes: `SessionManager` (Task 2)
- Consumes: `ReplContext.get_or_create_mode()` already exists
- Produces: New TUI phase `session_select`, commands `:resume`, `:rename`, `:session new`, `:session list`

- [ ] **Step 1: Add session_select phase to TUI**

```python
# Add to _COMMANDS:
("resume", "Resume a previous session", ("select", "chat", "session_select")),
("rename", "Rename current session: :rename <NAME>", ("chat",)),
("session new", "Create a new session: :session new <name> [kind]", ("chat", "session_select")),
("session list", "List sessions for current book", ("chat",)),
("compact", "Manually compact conversation history", ("chat",)),
("mcp list", "List connected MCP servers", ("chat", "select")),

# Add to panel_map:
"session_select": "select_panel",  # reuse select panel for session list

# Add _header_hint_for_phase:
if phase == "session_select":
    return "type : for commands    Enter: resume    :new: create    :back: return to books"
```

- [ ] **Step 2: Add session_select flow to _enter_chat**

When `_enter_chat` is called, check for existing sessions:

```python
def _enter_chat(self, book: Book) -> None:
    if not self._repl_context or not self._repl_context.has_chat:
        self._set_status("  chat unavailable: LLM/embedding not configured.")
        return
    self._selected_book = book

    # Check for existing sessions.
    mgr = self._repl_context.session_manager
    sessions = await mgr.list_by_book(book.id)
    if sessions:
        self._enter_session_select(book, sessions)
    else:
        # Auto-create default session.
        import random, string
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        name = f"{(book.title or 'untitled')[:20]}-{suffix}"
        session = await mgr.create(book_id=book.id, name=name, kind="chat")
        self._current_session = session
        self._enter_chat_with_session(book, session)
```

- [ ] **Step 3: Session select flow methods**

```python
def _enter_session_select(self, book: Book, sessions: list[Session]) -> None:
    self._session_list = sessions
    self._session_focus_idx = 0
    self._render_session_list()
    self.phase = "session_select"
    self._set_status(f"  {len(sessions)} session(s) for {book.title or '(untitled)'}")
    self._focus_input()

def _render_session_list(self) -> None:
    out = Text()
    for idx, sess in enumerate(self._session_list):
        if idx > 0:
            out.append(Text("\n"))
        focused = idx == self._session_focus_idx
        style = "bold white" if focused else "#888888"
        import datetime
        ts = datetime.datetime.fromtimestamp(sess.updated_at).strftime("%Y-%m-%d %H:%M")
        out.append(Text(f"  {idx+1:>2}  ", style=style))
        out.append(Text(f"{sess.name[:30]:<30}", style=style))
        out.append(Text(f"  {sess.kind:<10}", style="#666666" if focused else "#444444"))
        out.append(Text(f"  {sess.turn_count:>4} turns", style="#666666" if focused else "#444444"))
        out.append(Text(f"  {ts}", style="#444444"))
    out.append(Text("\n\n"))
    out.append(Text("  Enter: resume    :new: create    :back: return to books", style="#666666"))
    # Display in books list area.
    lv = self.query_one("#books_list", ListView)
    lv.clear()
    lv.append(ListItem(Static(out)))

def _enter_chat_with_session(self, book: Book, session: Session) -> None:
    self._current_session = session
    self._chat_markdown = ""
    self.query_one("#chat_log", Markdown).update(self._chat_markdown)
    self.phase = "chat"
    title = book.title or "(untitled)"
    author = book.author or "Unknown"
    self._set_console_title(f"{title} - {author}")
    hint = f"{title}  by {author}  [{session.name}]"
    self.query_one("#header_hint", Static).update(hint)
    self._set_status(f"  {session.name}")
    self._focus_input()
```

- [ ] **Step 4: Handle :resume command**

```python
# In _handle_select_input, add:
if low == ":resume":
    mgr = self._repl_context.session_manager
    all_sessions = await mgr.list_all()
    if not all_sessions:
        self._set_status("  no sessions to resume")
        return
    # Show cross-book session list.
    self._session_list = all_sessions
    self._session_focus_idx = 0
    self._render_cross_book_session_list()
    self.phase = "session_select"
    self._set_status(f"  {len(all_sessions)} session(s) total")
    return

# When Enter is pressed in session_select phase:
# Find the book, set selected_book, and enter chat with that session.
```

- [ ] **Step 5: Handle :rename, :session new, :session list**

```python
# In _handle_chat_input:
if low.startswith(":rename "):
    parts = text.split(None, 1)
    if len(parts) < 2:
        self._set_status("  usage: :rename <NAME>")
        return
    new_name = parts[1].strip()
    if self._current_session:
        await self._repl_context.session_manager.rename(self._current_session.session_id, new_name)
        self._current_session = await self._repl_context.session_manager.get(self._current_session.session_id)
        self._set_status(f"  renamed to: {new_name}")
    return

if low.startswith(":session new "):
    parts = text.split(None, 2)
    name = parts[2].strip() if len(parts) > 2 else None
    # ... create new session and switch to it
    return

if low == ":session list":
    sessions = await self._repl_context.session_manager.list_by_book(self._selected_book.id)
    # Show inline list in chat
    lines = ["**Sessions for this book:**"]
    for s in sessions:
        lines.append(f"- {s.name} ({s.kind}, {s.turn_count} turns)")
    self._chat_markdown += "\n" + "\n".join(lines) + "\n\n"
    log = self.query_one("#chat_log", Markdown)
    await log.update(self._chat_markdown)
    log.scroll_end(animate=False)
    return
```

- [ ] **Step 6: Wire update_after_turn into chat flow**

After each chat turn, update the session record:

```python
# At end of _run_chat, after appending assistant message:
if self._current_session and self._repl_context:
    await self._repl_context.session_manager.update_after_turn(
        self._current_session.session_id,
        user_input=user_input,
        response_text=response_text,
    )
```

- [ ] **Step 7: Add --resume CLI arg to __main__.py**

```python
# In tui() command:
resume: t.Annotated[
    str | None,
    typer.Option("--resume", "-r", help="Resume a session: --resume for list, --resume <id> for specific"),
] = None,

# Pass to BookScoutTui:
tui_app = BookScoutTui(bs_config, initial_book_id=book_id, resume_session_id=resume)
```

- [ ] **Step 8: Run tests, commit**

```bash
uv run pytest python/tests/test_tui_commands.py -x -v
uv run pytest python/tests/test_session_manager.py -x -v
git add python/bookscout-repl/bookscout/repl/tui.py \
        python/bookscout-repl/bookscout/repl/context.py \
        python/bookscout-repl/bookscout/repl/__main__.py
git commit -m "feat: add session list UI, :resume, :rename, :session commands"
```

---

### Task 2.5: Per-Session ReadingMode Isolation

**Files:**
- Modify: `python/bookscout-repl/bookscout/repl/context.py`
- Modify: `python/bookscout-agents/bookscout/agents/reading/mode.py`
- Modify: `python/bookscout-agents/bookscout/agents/reading/config.py`

**Goal:** Key `_modes` by `session_id` instead of `book_id`, giving each session its own SQLite DB and isolated message history. This is the bridge between the global `SessionManager` and the per-session agent runtime.

- [ ] **Step 1: Change _modes key from book_id to session_id**

```python
# In ReplContext:
# Replace: self._modes: dict[str, ReadingMode] = {}
# With:
self._modes: dict[str, ReadingMode] = {}  # key = session_id

# Replace get_or_create_mode signature:
async def get_or_create_mode(self, book_id: str, session_id: str) -> ReadingMode | None:
    """Get or create a ReadingMode for a specific session.

    Each session gets its own reading_mode_<session_id>.sqlite,
    isolated from other sessions for the same book.
    """
    if session_id in self._modes:
        return self._modes[session_id]

    if not self.has_chat:
        return None

    # ... rest of creation logic, using session_id for DB path ...

    mode = ReadingMode(...)
    await mode.startup()
    self._modes[session_id] = mode
    return mode
```

- [ ] **Step 2: Per-session SQLite DB path**

```python
# In get_or_create_mode:
book_dir = self._data_dir / book_id
book_dir.mkdir(parents=True, exist_ok=True)
session_db = book_dir / f"reading_mode_{session_id}.sqlite"

config = ReadingModeConfig(
    books_base_path=self._data_dir,
    book_id=book_id,
    db_uri=f"sqlite+aiosqlite:///{session_db}",
    # ...
)
```

- [ ] **Step 3: Update shutdown to iterate correctly**

```python
# In shutdown():
for mode in self._modes.values():  # unchanged — values() still works
    await mode.shutdown()
```

- [ ] **Step 4: Update remove_index to invalidate by session_id**

```python
# In remove_index — instead of self._modes.pop(book_id, None),
# we need to iterate and find sessions for that book:
keys_to_pop = [sid for sid, mode in self._modes.items()
               if mode.config.book_id == book_id]
for sid in keys_to_pop:
    self._modes.pop(sid, None)
```

- [ ] **Step 5: Update TUI to pass session_id through the flow**

```python
# In _enter_chat_with_session, when calling get_or_create_mode:
mode = await self._repl_context.get_or_create_mode(
    book_id=book.id, session_id=session.session_id,
)
```

- [ ] **Step 6: Run tests, commit**

```bash
uv run pytest python/tests/test_session_manager.py python/tests/test_reading_agent.py -x -v
git add python/bookscout-repl/bookscout/repl/context.py \
        python/bookscout-agents/bookscout/agents/reading/mode.py \
        python/bookscout-agents/bookscout/agents/reading/config.py
git commit -m "feat: per-session ReadingMode isolation with separate SQLite DBs"
```

---

### Task 9: Integration — Wire Everything Together + Final Tests

**Files:**
- Modify: `python/bookscout-repl/bookscout/repl/context.py`
- Test: `python/tests/test_reading_agent.py` (extend)

**Goal:** Ensure all new components (SessionManager, SkillLoader, PromptBuilder, ExternalMcpToolset, per-session isolation) are wired into the chat flow end-to-end.

- [ ] **Step 1: Update ReplContext.get_or_create_mode() with full wiring**

```python
async def get_or_create_mode(self, book_id: str, session_id: str) -> ReadingMode | None:
    if session_id in self._modes:
        return self._modes[session_id]

    if not self.has_chat:
        return None

    from bookscout.agents.reading.agent import READING_SYSTEM_PROMPT
    from bookscout.agents.reading.config import ReadingLLMProfiles, ReadingModeConfig
    from bookscout.agents.reading.mode import ReadingMode
    from bookscout.tools.skill_fetch import SkillFetchTool
    from .skill_loader import SkillLoader
    from .prompt_builder import PromptBuilder

    book_dir = self._data_dir / book_id
    book_dir.mkdir(parents=True, exist_ok=True)
    session_db = book_dir / f"reading_mode_{session_id}.sqlite"
    cm = self._config.chatmodel

    # Build skill system.
    skill_loader = SkillLoader(self._workdir, self._config.skills)
    soul_path = self._workdir / "SOUL.md"
    prompt = PromptBuilder(
        skill_descriptions=skill_loader.list_skills(),
        soul_path=soul_path,
        base_system_prompt=READING_SYSTEM_PROMPT,
    ).build()

    config = ReadingModeConfig(
        books_base_path=self._data_dir,
        book_id=book_id,
        db_uri=f"sqlite+aiosqlite:///{session_db}",
        books_db_base_path=self._data_dir,
        lancedb_uri=str(self._data_dir / "lancedb"),
        llm_profiles=ReadingLLMProfiles(
            cheap=cm.model, standard=cm.model, strong=cm.model,
        ),
    )
    mode = ReadingMode(
        config=config,
        llm=self._llm,
        embedding=self._embedding,
        logger=self.logger,
        book_id=book_id,
        registry=self._registry,
        books_store=self._books_store,
        system_prompt=prompt,  # NEW: dynamic prompt
        skill_loader=skill_loader,  # NEW
        external_mcp_configs=self._config.mcp_servers,  # NEW
    )
    await mode.startup()
    self._modes[session_id] = mode
    return mode
```

- [ ] **Step 2: Update ReplContext.chat() to require session_id**

```python
async def chat(
    self,
    book_id: str,
    session_id: str,
    user_input: str,
) -> t.AsyncIterator[StreamChunk]:
    mode = await self.get_or_create_mode(book_id, session_id=session_id)
    if mode is None:
        raise RuntimeError("Cannot create reading mode (missing LLM or embedding)")
    ctx = self.make_agent_context(book_id)
    async for chunk in mode.handle_stream(user_input, ctx=ctx):
        yield chunk
```

- [ ] **Step 2: Run full test suite**

```bash
uv run pytest python/tests/ -x -v
```

- [ ] **Step 3: Run linting**

```bash
uv run ruff check python/bookscout-repl python/bookscout-agents python/bookscout-tools
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: wire all subsystems — sessions, skills, MCP, compact into chat flow"
```
