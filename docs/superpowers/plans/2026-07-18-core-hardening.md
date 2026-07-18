# Core Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 5 core hardening features: session delete, rate limiting, tool max rounds, LLM retry, and message formatting.

**Architecture:** Layered changes across config → LLM → agent → TUI. Each feature is a vertical slice touching its natural layer. Rate limiting and LLM retry both live in ChatModel. Per-agent tool max rounds via CompletionOptions override.

**Tech Stack:** Python 3.13, pydantic, aiosqlite, tiktoken, Textual TUI

## Global Constraints

- All tests run via `uv run pytest python/tests/ -v`
- Pre-commit hooks: ruff, ruff-format, prettier — code must pass linting
- Config classes use pydantic BaseModel with Field defaults
- SQLite operations use `bookscout.sqlite.SQLite` with `exec(readonly=...)`
- TUI commands in chat phase are handled in `_handle_chat_input()`
- New exceptions extend `LLMError` in `bookscout-llm/bookscout/llm/exceptions.py`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `python/bookscout-repl/bookscout/repl/tui.py` | TUI commands (`:rm`, `:usage`), message formatting, retry status display |
| `python/bookscout-repl/bookscout/repl/session_manager.py` | `delete()` method |
| `python/bookscout-repl/bookscout/repl/context.py` | `delete_session()` passthrough, rate limit config passthrough |
| `python/bookscout-repl/bookscout/repl/config.py` | `RateLimitConfig` + `ratelimit` field on BookScoutConfig |
| `python/bookscout-llm/bookscout/llm/types.py` | `max_tool_iterations` on CompletionOptions |
| `python/bookscout-llm/bookscout/llm/config.py` | `RetryConfig`, `RateLimitConfig` (LLM-level), new fields on LLMConfig |
| `python/bookscout-llm/bookscout/llm/rate_limiter.py` | NEW — RateLimiter class |
| `python/bookscout-llm/bookscout/llm/__init__.py` | Retry logic, rate limit integration, max_tool_iterations override |
| `python/bookscout-llm/bookscout/llm/exceptions.py` | `RateLimitError` exception |
| `python/bookscout-agents/bookscout/agents/reading/agent.py` | Pass `max_tool_iterations=50` in CompletionOptions |
| `python/tests/test_session_manager.py` | Test delete |
| `python/tests/test_rate_limiter.py` | NEW — RateLimiter tests |
| `python/tests/test_tui_commands.py` | Test `:rm` command |

---

### Task 1: User Message Visual Separation (Feature 5)

**Files:**
- Modify: `python/bookscout-repl/bookscout/repl/tui.py` (line ~1427 and ~975)

**Interfaces:**
- Consumes: None
- Produces: None (visual-only change)

- [ ] **Step 1: Add extra newline before user blockquote in `_run_chat()`**

In `tui.py`, find the line in `_run_chat()`:
```python
self._chat_markdown += f"\n> {escaped}\n\n"
```
Replace with:
```python
self._chat_markdown += f"\n\n> {escaped}\n\n"
```

- [ ] **Step 2: Add extra newline before user blockquote in `_enter_chat_with_session()`**

In `tui.py`, find in `_enter_chat_with_session()`:
```python
                            md_parts.append(f"> {escaped}\n\n")
```
Replace with:
```python
                            md_parts.append(f"\n\n> {escaped}\n\n")
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest python/tests/test_tui_commands.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add python/bookscout-repl/bookscout/repl/tui.py
git commit -m "feat(tui): extra newline before user messages for visual separation"
```

---

### Task 2: Session Delete (Feature 1)

**Files:**
- Modify: `python/bookscout-repl/bookscout/repl/session_manager.py`
- Modify: `python/bookscout-repl/bookscout/repl/context.py`
- Modify: `python/bookscout-repl/bookscout/repl/tui.py`
- Modify: `python/tests/test_session_manager.py`

**Interfaces:**
- Consumes: None
- Produces: `SessionManager.delete(session_id)`, `ReplContext.delete_session(session_id)`, TUI `:rm` command

- [ ] **Step 1: Write failing test for `SessionManager.delete()`**

In `python/tests/test_session_manager.py`, append:

```python
@pytest.mark.asyncio
async def test_delete(session_manager: SessionManager):
    sess = await session_manager.create(book_id="book_1", name="ToDelete")
    # Add some messages
    await session_manager.append_message(sess.session_id, role="user", content="hello")
    await session_manager.append_message(sess.session_id, role="assistant", content="hi")
    # Delete
    await session_manager.delete(sess.session_id)
    # Session is gone
    loaded = await session_manager.get(sess.session_id)
    assert loaded is None
    # Messages are gone
    msgs = await session_manager.load_messages(sess.session_id)
    assert msgs == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/tests/test_session_manager.py::test_delete -v`
Expected: FAIL with `AttributeError: 'SessionManager' object has no attribute 'delete'`

- [ ] **Step 3: Implement `SessionManager.delete()`**

In `python/bookscout-repl/bookscout/repl/session_manager.py`, add after the `archive()` method:

```python
    async def delete(self, session_id: str) -> None:
        """Delete a session and its message log (not ReadingMode SQLite)."""
        await self._sqlite.exec(
            "DELETE FROM message_log WHERE session_id = :sid",
            readonly=False,
            sid=session_id,
        )
        await self._sqlite.exec(
            "DELETE FROM session WHERE session_id = :sid",
            readonly=False,
            sid=session_id,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/tests/test_session_manager.py::test_delete -v`
Expected: PASS

- [ ] **Step 5: Add `ReplContext.delete_session()`**

In `python/bookscout-repl/bookscout/repl/context.py`, add after the `remove_index()` method:

```python
    async def delete_session(self, session_id: str) -> None:
        """Delete a session: remove from mode cache, then delete from store."""
        self._modes.pop(session_id, None)
        await self.session_manager.delete(session_id)
```

- [ ] **Step 6: Add `:rm` command to TUI chat phase**

In `tui.py`, inside `_handle_chat_input()`, after the `:rename` block and before the `:session new` block, add:

```python
        if low.startswith(":rm"):
            parts = text.split(None, 1)
            if len(parts) < 2:
                self._set_status("  usage: :rm <name> | :rm :current")
                return
            target = parts[1].strip()
            assert self._repl_context is not None
            mgr = self._repl_context.session_manager
            if target == ":current":
                if not self._current_session:
                    self._set_status("  no active session")
                    return
                sid = self._current_session.session_id
                await self._repl_context.delete_session(sid)
                self._current_session = None
                self._session_id = None
                # Return to session select for this book.
                if self._selected_book:
                    sessions = await mgr.list_by_book(self._selected_book.id)
                    if sessions:
                        await self._enter_session_select(self._selected_book, sessions)
                    else:
                        await self._refresh_books_list()
                        self.phase = "select"
                        self._set_status(f"  {len(self._books)} book(s)")
                self._focus_input()
                return
            # Delete by name for current book.
            if not self._selected_book:
                self._set_status("  no book selected")
                return
            sessions = await mgr.list_by_book(self._selected_book.id)
            match = next((s for s in sessions if s.name == target), None)
            if match is None:
                self._set_status(f"  session not found: {target}")
                return
            await self._repl_context.delete_session(match.session_id)
            # If we deleted the current session, leave chat.
            if self._current_session and self._current_session.session_id == match.session_id:
                self._current_session = None
                self._session_id = None
                sessions = await mgr.list_by_book(self._selected_book.id)
                if sessions:
                    await self._enter_session_select(self._selected_book, sessions)
                else:
                    await self._refresh_books_list()
                    self.phase = "select"
                    self._set_status(f"  {len(self._books)} book(s)")
            else:
                self._set_status(f"  deleted session: {target}")
            self._focus_input()
            return
```

- [ ] **Step 7: Add `:rm` command to session_select phase**

In `tui.py`, inside `_handle_session_select_input()`, add handling for `:rm` command that deletes the focused session. Find the section that handles commands in session_select phase. Add before the unknown-command handler:

```python
        if low == ":rm":
            if self._session_list and 0 <= self._session_focus_idx < len(self._session_list):
                session = self._session_list[self._session_focus_idx]
                assert self._repl_context is not None
                await self._repl_context.delete_session(session.session_id)
                # Refresh session list.
                if self._session_select_cross_book:
                    self._session_list = await self._repl_context.session_manager.list_all()
                elif self._selected_book:
                    self._session_list = await self._repl_context.session_manager.list_by_book(self._selected_book.id)
                if not self._session_list:
                    # No sessions left — go back to book select.
                    await self._refresh_books_list()
                    self.phase = "select"
                    self._set_status(f"  {len(self._books)} book(s)")
                    self._focus_input()
                    return
                self._session_focus_idx = min(self._session_focus_idx, len(self._session_list) - 1)
                if self._session_select_cross_book:
                    await self._render_cross_book_session_list()
                else:
                    await self._render_session_list()
                self._set_status(f"  deleted. {len(self._session_list)} session(s) remaining")
            return
```

- [ ] **Step 8: Run all tests**

Run: `uv run pytest python/tests/ -v`
Expected: All pass

- [ ] **Step 9: Commit**

```bash
git add python/bookscout-repl/bookscout/repl/session_manager.py python/bookscout-repl/bookscout/repl/context.py python/bookscout-repl/bookscout/repl/tui.py python/tests/test_session_manager.py
git commit -m "feat: session delete — :rm command, SessionManager.delete(), ReplContext.delete_session()"
```

---

### Task 3: Tool Max Rounds per-Agent (Feature 3)

**Files:**
- Modify: `python/bookscout-llm/bookscout/llm/types.py` (CompletionOptions)
- Modify: `python/bookscout-llm/bookscout/llm/__init__.py` (chat_completion + _stream_with_tools)
- Modify: `python/bookscout-agents/bookscout/agents/reading/agent.py` (step + run_stream)

**Interfaces:**
- Consumes: None
- Produces: `CompletionOptions.max_tool_iterations` field

- [ ] **Step 1: Add `max_tool_iterations` to `CompletionOptions`**

In `python/bookscout-llm/bookscout/llm/types.py`, in the `CompletionOptions` class, after the `stream` field:

```python
    stream: bool = False
    max_tool_iterations: int | None = None
    """Override the global ToolcallConfig.max_iterations for this request.
    When None (default), the global config value is used."""
```

- [ ] **Step 2: Use `max_tool_iterations` override in `chat_completion()`**

In `python/bookscout-llm/bookscout/llm/__init__.py`, in the `chat_completion()` method, find the line:
```python
        max_iterations = self.config.toolcall.max_iterations
```
Replace with:
```python
        max_iterations = (
            opts.max_tool_iterations
            if opts.max_tool_iterations is not None
            else self.config.toolcall.max_iterations
        )
```

- [ ] **Step 3: Use `max_tool_iterations` override in `_stream_with_tools()`**

In `python/bookscout-llm/bookscout/llm/__init__.py`, in the `_stream_with_tools()` inner function, find the line:
```python
            max_iterations = self.config.toolcall.max_iterations
```
Replace with:
```python
            max_iterations = (
                opts.max_tool_iterations
                if opts.max_tool_iterations is not None
                else self.config.toolcall.max_iterations
            )
```

- [ ] **Step 4: Override in `ReadingAgent.step()`**

In `python/bookscout-agents/bookscout/agents/reading/agent.py`, in the `step()` method, find:
```python
        options = CompletionOptions(model=model, temperature=0.2)
```
Replace with:
```python
        options = CompletionOptions(model=model, temperature=0.2, max_tool_iterations=50)
```

- [ ] **Step 5: Override in `ReadingAgent.run_stream()`**

In `python/bookscout-agents/bookscout/agents/reading/agent.py`, in the `run_stream()` method, find:
```python
        options = CompletionOptions(model=model, temperature=0.2, stream=True)
```
Replace with:
```python
        options = CompletionOptions(model=model, temperature=0.2, stream=True, max_tool_iterations=50)
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest python/tests/ -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add python/bookscout-llm/bookscout/llm/types.py python/bookscout-llm/bookscout/llm/__init__.py python/bookscout-agents/bookscout/agents/reading/agent.py
git commit -m "feat(llm): per-agent tool max rounds via CompletionOptions.max_tool_iterations"
```

---

### Task 4: LLM Retry with Exponential Backoff (Feature 4)

**Files:**
- Modify: `python/bookscout-llm/bookscout/llm/config.py` (RetryConfig)
- Modify: `python/bookscout-llm/bookscout/llm/exceptions.py` (RateLimitError)
- Modify: `python/bookscout-llm/bookscout/llm/__init__.py` (retry logic + _is_retryable)
- Modify: `python/bookscout-repl/bookscout/repl/tui.py` (retry status display)

**Interfaces:**
- Consumes: None
- Produces: `RetryConfig`, `RateLimitError`, retry status StreamChunks

- [ ] **Step 1: Add `RateLimitError` to exceptions**

In `python/bookscout-llm/bookscout/llm/exceptions.py`, after the `ToolExecutionError` class, add:

```python
class RateLimitError(LLMError):
    """Raised when a configured rate limit window is exceeded."""
```

- [ ] **Step 2: Add `RetryConfig` to LLM config**

In `python/bookscout-llm/bookscout/llm/config.py`, after the `ToolcallConfig` class, add:

```python
class RetryConfig(BaseModel):
    """Configuration for LLM call retry with exponential backoff."""

    max_retries: int = Field(
        default=10,
        description="Maximum number of retries for transient LLM errors.",
    )
    initial_delay: float = Field(
        default=1.0,
        description="Initial delay in seconds before first retry.",
    )
    max_delay: float = Field(
        default=30.0,
        description="Maximum delay in seconds between retries.",
    )
    backoff_factor: float = Field(
        default=2.0,
        description="Exponential backoff multiplier. delay = initial_delay * backoff_factor^(attempt-1).",
    )
```

Then in `LLMConfig`, after the `cache` field, add:

```python
    retry: RetryConfig = Field(
        default_factory=RetryConfig,
        description="LLM call retry configuration.",
    )
```

- [ ] **Step 3: Add `_is_retryable` static method to `ChatModel`**

In `python/bookscout-llm/bookscout/llm/__init__.py`, add a static method to the `ChatModel` class (after the `estimate_token` method):

```python
    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        """Determine if an LLM error is transient and worth retrying."""
        from bookscout.llm.exceptions import ContextOverflowError
        from bookscout.llm.exceptions import ModelNotSupportedError
        from bookscout.llm.exceptions import RateLimitError

        # Non-retryable types — these will fail again.
        if isinstance(error, (ContextOverflowError, ModelNotSupportedError, RateLimitError)):
            return False
        # Auth / content filter errors are non-retryable.
        msg = str(error).lower()
        if any(p in msg for p in ("auth", "401", "403", "content_filter", "invalid_api_key")):
            return False
        # Retryable patterns — transient server/connection issues.
        retryable_patterns = [
            "rate_limit", "429", "timeout", "connection",
            "server_error", "500", "502", "503", "overloaded",
            "api_connection", "apiconnection",
        ]
        return any(p in msg for p in retryable_patterns)
```

- [ ] **Step 4: Add retry wrapper for `_complete()` in `chat_completion()`**

In `python/bookscout-llm/bookscout/llm/__init__.py`, in the `chat_completion()` method, find the tool-call loop section. The key change is wrapping the `self._complete()` call in a retry loop.

Find this block inside the `for _iteration in range(max_iterations):` loop:
```python
            start_time = utcnow_ts()
            async with self._concurrency_semaphore:
                raw = await self._complete(provider_messages, provider_tools, opts)
            response = self._convert_response(raw)
```

Replace with:
```python
            start_time = utcnow_ts()
            cfg = self.config.retry
            for _retry_attempt in range(1, cfg.max_retries + 1):
                try:
                    async with self._concurrency_semaphore:
                        raw = await self._complete(provider_messages, provider_tools, opts)
                    break  # success — exit retry loop
                except Exception as e:
                    from bookscout.llm.exceptions import LLMError
                    if not isinstance(e, LLMError):
                        e = CompletionError(f"LLM call failed: {e}")
                    if not self._is_retryable(e) or _retry_attempt >= cfg.max_retries:
                        raise
                    delay = min(
                        cfg.initial_delay * (cfg.backoff_factor ** (_retry_attempt - 1)),
                        cfg.max_delay,
                    )
                    self.logger.warning(
                        "LLM call failed, retrying",
                        attempt=_retry_attempt,
                        delay=f"{delay:.1f}s",
                        error=str(e),
                    )
                    await asyncio.sleep(delay)
            response = self._convert_response(raw)
```

Add `import asyncio` at the top of the file if not already present.

- [ ] **Step 5: Add retry wrapper for `_complete_stream()` in `_stream_with_tools()`**

In `python/bookscout-llm/bookscout/llm/__init__.py`, in the `_stream_with_tools()` inner function, find the line that creates the raw stream:

```python
                raw_stream = self._complete_stream(provider_messages, provider_tools, opts)  # type: ignore[arg-type,attr-defined,misc]
```

Replace with a retry wrapper around the stream acquisition:

```python
                # Retry the stream creation on transient errors.
                cfg = self.config.retry
                raw_stream = None
                for _retry_attempt in range(1, cfg.max_retries + 1):
                    try:
                        raw_stream = self._complete_stream(provider_messages, provider_tools, opts)  # type: ignore[arg-type,attr-defined,misc]
                        break  # success
                    except Exception as e:
                        from bookscout.llm.exceptions import LLMError
                        if not isinstance(e, LLMError):
                            e = CompletionError(f"LLM stream failed: {e}")
                        if not self._is_retryable(e) or _retry_attempt >= cfg.max_retries:
                            raise
                        delay = min(
                            cfg.initial_delay * (cfg.backoff_factor ** (_retry_attempt - 1)),
                            cfg.max_delay,
                        )
                        self.logger.warning(
                            "LLM stream setup failed, retrying",
                            attempt=_retry_attempt,
                            delay=f"{delay:.1f}s",
                            error=str(e),
                        )
                        # Notify consumer about retry.
                        yield StreamChunk(
                            kind="status",
                            data={"phase": "retry", "attempt": _retry_attempt, "max_retries": cfg.max_retries, "error": str(e)},
                        )
                        await asyncio.sleep(delay)
```

- [ ] **Step 6: Add retry status display in TUI**

In `python/bookscout-repl/bookscout/repl/tui.py`, in the `_handle_chunk()` method, find the section that handles `chunk.kind == "status"`. Add handling for the `"retry"` phase:

```python
                if phase == "retry":
                    attempt = data.get("attempt", "?")
                    max_r = data.get("max_retries", "?")
                    err_short = str(data.get("error", ""))[:60]
                    self._set_status(f"  LLM error, retrying ({attempt}/{max_r}): {err_short}")
                    return
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest python/tests/ -v`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add python/bookscout-llm/bookscout/llm/config.py python/bookscout-llm/bookscout/llm/exceptions.py python/bookscout-llm/bookscout/llm/__init__.py python/bookscout-repl/bookscout/repl/tui.py
git commit -m "feat(llm): exponential backoff retry for transient errors with TUI feedback"
```

---

### Task 5: Rate Limiting — Config and RateLimiter Class (Feature 2, part 1)

**Files:**
- Modify: `python/bookscout-repl/bookscout/repl/config.py` (RateLimitConfig for BookScoutConfig)
- Create: `python/bookscout-llm/bookscout/llm/rate_limiter.py`
- Modify: `python/bookscout-llm/bookscout/llm/config.py` (RateLimitConfig for LLMConfig)
- Create: `python/tests/test_rate_limiter.py`

**Interfaces:**
- Consumes: `bookscout.sqlite.SQLite`, `RateLimitConfig`
- Produces: `RateLimiter` class with `check_allowed()`, `record_request()`, `record_actual_usage()`, `get_status()`

- [ ] **Step 1: Add `RateLimitConfig` to BookScoutConfig**

In `python/bookscout-repl/bookscout/repl/config.py`, add before the `BookScoutConfig` class:

```python
class RateLimitWindowConfig(BaseModel):
    """Rate limit for a single rolling window."""
    limit: int = Field(default=0, description="Max units allowed in this window. 0 = unlimited.")


class RateLimitWindowsConfig(BaseModel):
    """Rate limit windows configuration."""
    rolling_5h: RateLimitWindowConfig = Field(
        default_factory=lambda: RateLimitWindowConfig(limit=0),
    )
    rolling_weekly: RateLimitWindowConfig = Field(
        default_factory=lambda: RateLimitWindowConfig(limit=0),
    )
    rolling_monthly: RateLimitWindowConfig = Field(
        default_factory=lambda: RateLimitWindowConfig(limit=0),
    )


class RateLimitConfig(BaseModel):
    """Rate limiting configuration for LLM API calls."""
    mode: str = Field(
        default="off",
        description='Rate limit mode: "requests", "tokens", or "off".',
    )
    windows: RateLimitWindowsConfig = Field(default_factory=RateLimitWindowsConfig)
```

Then add a field to `BookScoutConfig` (after the `skills` field):

```python
    ratelimit: RateLimitConfig = Field(
        default_factory=RateLimitConfig,
        description="Rate limiting configuration for LLM API calls.",
    )
```

- [ ] **Step 2: Add `RateLimitConfig` to LLMConfig**

In `python/bookscout-llm/bookscout/llm/config.py`, add the same config classes (they're duplicated because `bookscout-llm` can't depend on `bookscout-repl`):

```python
class _RLWindowConfig(BaseModel):
    limit: int = Field(default=0, description="Max units in this window. 0 = unlimited.")

class _RLWindowsConfig(BaseModel):
    rolling_5h: _RLWindowConfig = Field(default_factory=lambda: _RLWindowConfig(limit=0))
    rolling_weekly: _RLWindowConfig = Field(default_factory=lambda: _RLWindowConfig(limit=0))
    rolling_monthly: _RLWindowConfig = Field(default_factory=lambda: _RLWindowConfig(limit=0))

class RateLimitConfig(BaseModel):
    """Rate limiting configuration for LLM API calls."""
    mode: str = Field(default="off", description='"requests" | "tokens" | "off"')
    windows: _RLWindowsConfig = Field(default_factory=_RLWindowsConfig)
```

Add to `LLMConfig` (after the `retry` field):

```python
    ratelimit: RateLimitConfig = Field(
        default_factory=RateLimitConfig,
        description="Rate limiting configuration.",
    )
```

- [ ] **Step 3: Create `RateLimiter` class**

Create `python/bookscout-llm/bookscout/llm/rate_limiter.py`:

```python
"""SQLite-backed rate limiter for LLM API calls."""

from __future__ import annotations

import typing as t

from bookscout.core.lib.utils import utcnow_ts
from bookscout.logging.mixin import LoggingMixin

if t.TYPE_CHECKING:
    from bookscout.logging import Logger
    from bookscout.sqlite import SQLite

from .config import RateLimitConfig


class RateLimiter(LoggingMixin):
    """SQLite-backed rate limiter for LLM API calls.

    Two modes:
    - "requests": each API call counts as 1 unit
    - "tokens": input+output tokens count (tiktoken estimate on request,
      actual usage from response updates the record)

    Three rolling windows: 5h, weekly (7d), monthly (30d).
    Limits of 0 mean unlimited for that window.
    """

    WINDOW_SECS: dict[str, float] = {
        "rolling_5h": 5 * 3600,
        "rolling_weekly": 7 * 86400,
        "rolling_monthly": 30 * 86400,
    }

    def __init__(self, sqlite: SQLite, config: RateLimitConfig, logger: Logger) -> None:
        super().__init__(logger=logger)
        self._sqlite = sqlite
        self._config = config

    @property
    def mode(self) -> str:
        return self._config.mode

    async def startup(self) -> None:
        """Create rate_limit_log table."""
        await self._sqlite.exec(
            """CREATE TABLE IF NOT EXISTS rate_limit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                mode TEXT NOT NULL,
                tokens_estimated INTEGER,
                tokens_actual INTEGER
            )""",
            readonly=False,
        )
        await self._sqlite.exec(
            "CREATE INDEX IF NOT EXISTS idx_rl_ts ON rate_limit_log(timestamp)",
            readonly=False,
        )

    async def check_allowed(self, estimated_tokens: int | None = None) -> tuple[bool, str]:
        """Check if a new LLM call is allowed under all active windows.

        Returns (allowed, reason). reason is human-readable if blocked.
        """
        if self._config.mode == "off":
            return True, ""

        now = utcnow_ts()
        for window_name, window_secs in self.WINDOW_SECS.items():
            limit = self._get_limit(window_name)
            if limit <= 0:
                continue
            usage = await self._count_usage(now - window_secs, now)
            if usage >= limit:
                return False, f"{window_name} limit reached ({usage}/{limit})"
        return True, ""

    async def record_request(self, estimated_tokens: int | None = None) -> None:
        """Record a new LLM call before the API request is made."""
        if self._config.mode == "off":
            return
        await self._sqlite.exec(
            """INSERT INTO rate_limit_log (timestamp, mode, tokens_estimated, tokens_actual)
               VALUES (:ts, :mode, :est, NULL)""",
            readonly=False,
            ts=utcnow_ts(),
            mode=self._config.mode,
            est=estimated_tokens,
        )
        # Periodic cleanup.
        await self._cleanup_old_records()

    async def record_actual_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Update the most recent record with actual token usage from the response."""
        if self._config.mode == "off":
            return
        total = input_tokens + output_tokens
        await self._sqlite.exec(
            """UPDATE rate_limit_log SET tokens_actual = :actual
               WHERE id = (SELECT MAX(id) FROM rate_limit_log)""",
            readonly=False,
            actual=total,
        )

    async def get_status(self) -> dict[str, t.Any]:
        """Return current usage for all windows. Used by :usage command."""
        if self._config.mode == "off":
            return {"mode": "off", "windows": {}}

        now = utcnow_ts()
        windows: dict[str, dict[str, int]] = {}
        for window_name, window_secs in self.WINDOW_SECS.items():
            limit = self._get_limit(window_name)
            used = await self._count_usage(now - window_secs, now) if limit > 0 else 0
            windows[window_name] = {
                "used": used,
                "limit": limit,
                "remaining": max(0, limit - used) if limit > 0 else -1,
            }
        return {"mode": self._config.mode, "windows": windows}

    def _get_limit(self, window_name: str) -> int:
        """Get the configured limit for a window."""
        windows = self._config.windows
        if window_name == "rolling_5h":
            return windows.rolling_5h.limit
        if window_name == "rolling_weekly":
            return windows.rolling_weekly.limit
        if window_name == "rolling_monthly":
            return windows.rolling_monthly.limit
        return 0

    async def _count_usage(self, since: float, until: float) -> int:
        """Count usage in a time window."""
        if self._config.mode == "requests":
            result = await self._sqlite.exec(
                "SELECT COUNT(*) FROM rate_limit_log WHERE timestamp >= :since AND timestamp <= :until AND mode = 'requests'",
                readonly=True,
                since=since,
                until=until,
            )
            row = result.fetchone()
            return row[0] if row else 0
        # tokens mode: sum actual tokens, fall back to estimated.
        result = await self._sqlite.exec(
            """SELECT COALESCE(SUM(COALESCE(tokens_actual, tokens_estimated, 0)), 0)
               FROM rate_limit_log
               WHERE timestamp >= :since AND timestamp <= :until AND mode = 'tokens'""",
            readonly=True,
            since=since,
            until=until,
        )
        row = result.fetchone()
        return row[0] if row else 0

    async def _cleanup_old_records(self) -> None:
        """Delete records older than the largest window (30d) + 1h buffer."""
        cutoff = utcnow_ts() - (30 * 86400 + 3600)
        await self._sqlite.exec(
            "DELETE FROM rate_limit_log WHERE timestamp < :cutoff",
            readonly=False,
            cutoff=cutoff,
        )
```

- [ ] **Step 4: Write tests for RateLimiter**

Create `python/tests/test_rate_limiter.py`:

```python
"""Tests for the RateLimiter class."""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from bookscout.llm.config import RateLimitConfig
from bookscout.llm.rate_limiter import RateLimiter
from bookscout.logging import LoggingConfig
from bookscout.logging import build_logger
from bookscout.sqlite import SQLite
from bookscout.sqlite import SQLiteConfig


@pytest.fixture
async def rate_limiter_off():
    """Rate limiter in off mode."""
    with tempfile.TemporaryDirectory() as tmp:
        logger = build_logger(LoggingConfig(name="test", level="ERROR", targets=[]))
        sqlite = SQLite(config=SQLiteConfig(uri=f"sqlite+aiosqlite:///{pathlib.Path(tmp) / 'rl.db'}"), logger=logger)
        await sqlite.startup()
        rl = RateLimiter(sqlite=sqlite, config=RateLimitConfig(mode="off"), logger=logger)
        await rl.startup()
        yield rl
        await sqlite.shutdown()


@pytest.fixture
async def rate_limiter_requests():
    """Rate limiter in requests mode with small limits."""
    from bookscout.llm.config import _RLWindowConfig, _RLWindowsConfig

    with tempfile.TemporaryDirectory() as tmp:
        logger = build_logger(LoggingConfig(name="test", level="ERROR", targets=[]))
        sqlite = SQLite(config=SQLiteConfig(uri=f"sqlite+aiosqlite:///{pathlib.Path(tmp) / 'rl.db'}"), logger=logger)
        await sqlite.startup()
        config = RateLimitConfig(
            mode="requests",
            windows=_RLWindowsConfig(
                rolling_5h=_RLWindowConfig(limit=5),
                rolling_weekly=_RLWindowConfig(limit=100),
                rolling_monthly=_RLWindowConfig(limit=1000),
            ),
        )
        rl = RateLimiter(sqlite=sqlite, config=config, logger=logger)
        await rl.startup()
        yield rl
        await sqlite.shutdown()


@pytest.mark.asyncio
async def test_off_mode_always_allows(rate_limiter_off: RateLimiter):
    allowed, reason = await rate_limiter_off.check_allowed()
    assert allowed
    assert reason == ""


@pytest.mark.asyncio
async def test_requests_mode_counts(rate_limiter_requests: RateLimiter):
    # First 5 requests should be allowed.
    for _ in range(5):
        allowed, reason = await rate_limiter_requests.check_allowed()
        assert allowed, reason
        await rate_limiter_requests.record_request()

    # 6th should be blocked by 5h window.
    allowed, reason = await rate_limiter_requests.check_allowed()
    assert not allowed
    assert "rolling_5h" in reason


@pytest.mark.asyncio
async def test_get_status(rate_limiter_requests: RateLimiter):
    await rate_limiter_requests.record_request()
    await rate_limiter_requests.record_request()
    status = await rate_limiter_requests.get_status()
    assert status["mode"] == "requests"
    assert status["windows"]["rolling_5h"]["used"] == 2
    assert status["windows"]["rolling_5h"]["limit"] == 5
    assert status["windows"]["rolling_5h"]["remaining"] == 3


@pytest.mark.asyncio
async def test_record_actual_usage(rate_limiter_off: RateLimiter):
    """record_actual_usage should not crash in off mode."""
    await rate_limiter_off.record_request(estimated_tokens=100)
    await rate_limiter_off.record_actual_usage(input_tokens=50, output_tokens=30)
    # No error = pass.
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest python/tests/test_rate_limiter.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add python/bookscout-repl/bookscout/repl/config.py python/bookscout-llm/bookscout/llm/config.py python/bookscout-llm/bookscout/llm/rate_limiter.py python/tests/test_rate_limiter.py
git commit -m "feat(llm): RateLimiter class with SQLite-backed rolling window rate limiting"
```

---

### Task 6: Rate Limiting — ChatModel Integration (Feature 2, part 2)

**Files:**
- Modify: `python/bookscout-llm/bookscout/llm/__init__.py` (rate limit check + record)
- Modify: `python/bookscout-repl/bookscout/repl/context.py` (pass ratelimit config to LLMConfig)

**Interfaces:**
- Consumes: `RateLimiter` from Task 5, `RateLimitConfig` from Task 5
- Produces: Rate-limited `chat_completion()` and `chat_completion_stream()`

- [ ] **Step 1: Initialize RateLimiter in ChatModel**

In `python/bookscout-llm/bookscout/llm/__init__.py`, in `ChatModel.__init__()`, add after the `self._concurrency_semaphore` line:

```python
        self._rate_limiter: RateLimiter | None = None
```

In `ChatModel.startup()`, after `await super().startup()`, add:

```python
        # Initialize rate limiter if configured.
        if self.config.ratelimit.mode != "off":
            from bookscout.llm.rate_limiter import RateLimiter

            if self.sqlite is not None:
                self._rate_limiter = RateLimiter(
                    sqlite=self.sqlite,
                    config=self.config.ratelimit,
                    logger=self.logger,
                )
                await self._rate_limiter.startup()
                self.logger.info("Rate limiter initialized", mode=self.config.ratelimit.mode)
```

- [ ] **Step 2: Add rate limit check before LLM calls in `chat_completion()`**

In `python/bookscout-llm/bookscout/llm/__init__.py`, in `chat_completion()`, before the tool-call loop (`for _iteration in range(max_iterations):`), add:

```python
        # Rate limit check
        if self._rate_limiter is not None:
            est_tokens = None
            if self._rate_limiter.mode == "tokens":
                est_tokens = self.estimate_token(str(messages))
            allowed, reason = await self._rate_limiter.check_allowed(est_tokens)
            if not allowed:
                from bookscout.llm.exceptions import RateLimitError
                raise RateLimitError(reason)
            await self._rate_limiter.record_request(est_tokens)
```

- [ ] **Step 3: Record actual usage after response in `chat_completion()`**

In `chat_completion()`, after the `response = self._convert_response(raw)` line (inside the retry loop, after the `break`), add:

```python
            # Record actual token usage for rate limiting.
            if self._rate_limiter is not None:
                usage = response.get("usage", {})
                await self._rate_limiter.record_actual_usage(
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                )
```

- [ ] **Step 4: Add rate limit check in `chat_completion_stream()`**

In `chat_completion_stream()`, before the `_stream_with_tools()` call, add the same rate limit check:

```python
        # Rate limit check
        if self._rate_limiter is not None:
            est_tokens = None
            if self._rate_limiter.mode == "tokens":
                est_tokens = self.estimate_token(str(messages))
            allowed, reason = await self._rate_limiter.check_allowed(est_tokens)
            if not allowed:
                from bookscout.llm.exceptions import RateLimitError
                raise RateLimitError(reason)
            await self._rate_limiter.record_request(est_tokens)
```

- [ ] **Step 5: Record actual usage in `_stream_with_tools()`**

In `_stream_with_tools()`, in the `elif event_type == "response_complete":` handler, after updating `usage` and `finish_reason`, add:

```python
                        # Record actual token usage for rate limiting.
                        if self._rate_limiter is not None:
                            await self._rate_limiter.record_actual_usage(
                                input_tokens=resp.get("usage", {}).get("input_tokens", 0),
                                output_tokens=resp.get("usage", {}).get("output_tokens", 0),
                            )
```

Also record usage at the end of the tool-call loop (the max-iterations fallback) and at the final `response_complete` yield.

- [ ] **Step 6: Pass ratelimit config from BookScoutConfig to LLMConfig in ReplContext**

In `python/bookscout-repl/bookscout/repl/context.py`, in `startup()`, find the `OpenAIChatModel` construction and update the `LLMConfig` to include `ratelimit`:

```python
            self._llm = OpenAIChatModel(
                logger=self.logger,
                config=LLMConfig(
                    backend=OpenAIConfig(
                        api_key=cm.api_key,
                        base_url=cm.base_url,
                        model=cm.model,
                    ),
                    stateless=cm.stateless,
                    ratelimit=RateLimitConfig(
                        mode=self._config.ratelimit.mode,
                        windows=_RLWindowsConfig(
                            rolling_5h=_RLWindowConfig(limit=self._config.ratelimit.windows.rolling_5h.limit),
                            rolling_weekly=_RLWindowConfig(limit=self._config.ratelimit.windows.rolling_weekly.limit),
                            rolling_monthly=_RLWindowConfig(limit=self._config.ratelimit.windows.rolling_monthly.limit),
                        ),
                    ),
                ),
            )
```

Add the necessary imports at the top of context.py:
```python
from bookscout.llm.config import RateLimitConfig as _LLMRateLimitConfig
from bookscout.llm.config import _RLWindowConfig, _RLWindowsConfig
```

- [ ] **Step 7: Add `:usage` command to TUI**

In `tui.py`, inside `_handle_chat_input()`, add before the unknown-command handler:

```python
        if low == ":usage":
            if self._repl_context and self._repl_context._llm and self._repl_context._llm._rate_limiter:
                status = await self._repl_context._llm._rate_limiter.get_status()
                if status["mode"] == "off":
                    self._set_status("  rate limit: off (no limits configured)")
                else:
                    lines = [f"**Rate Limit: {status['mode']} mode**"]
                    for wname, wdata in status["windows"].items():
                        if wdata["limit"] <= 0:
                            continue
                        used = wdata["used"]
                        limit = wdata["limit"]
                        remaining = wdata["remaining"]
                        lines.append(f"- {wname}: {used:,} / {limit:,}  ({remaining:,} remaining)")
                    self._chat_markdown += "\n" + "\n".join(lines) + "\n\n"
                    log = self.query_one("#chat_log", Markdown)
                    await log.update(self._chat_markdown)
                    log.scroll_end(animate=False)
            else:
                self._set_status("  rate limit: off (not configured)")
            return
```

- [ ] **Step 8: Run all tests**

Run: `uv run pytest python/tests/ -v`
Expected: All pass

- [ ] **Step 9: Commit**

```bash
git add python/bookscout-llm/bookscout/llm/__init__.py python/bookscout-repl/bookscout/repl/context.py python/bookscout-repl/bookscout/repl/tui.py
git commit -m "feat(llm): integrate RateLimiter into ChatModel + :usage TUI command"
```

---

### Task 7: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest python/tests/ -v`
Expected: All pass

- [ ] **Step 2: Run ruff check**

Run: `uv run ruff check python/`
Expected: No errors

- [ ] **Step 3: Run mypy (if configured)**

Run: `uv run mypy python/bookscout-llm python/bookscout-repl python/bookscout-agents --ignore-missing-imports`
Expected: No errors (or only pre-existing ones)

- [ ] **Step 4: Push all commits**

```bash
git push
```
