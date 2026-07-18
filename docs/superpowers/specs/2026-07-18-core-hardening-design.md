# BookScout Core Hardening — Session Delete, Rate Limiting, Tool Max Rounds, LLM Retry, Message Formatting

**Date**: 2026-07-18
**Status**: Draft

## Overview

Five features to harden BookScout's core before mobile/web expansion:

1. **Session delete** — `:rm` command to delete a session
2. **Rate limiting** — `:usage` command + configurable request/token limits with rolling windows
3. **Tool max rounds** — per-agent override of the global tool-call iteration limit
4. **LLM retry** — exponential backoff retry at the ChatModel layer with TUI feedback
5. **User message formatting** — extra newline before user messages for visual separation

---

## Feature 1: Session Delete

### SessionManager

Add a `delete()` method:

```python
async def delete(self, session_id: str) -> None:
    """Delete a session and its message log (not ReadingMode SQLite)."""
    await self._sqlite.exec(
        "DELETE FROM message_log WHERE session_id = :sid",
        readonly=False, sid=session_id,
    )
    await self._sqlite.exec(
        "DELETE FROM session WHERE session_id = :sid",
        readonly=False, sid=session_id,
    )
```

ReadingMode's `reading_mode_<session_id>.sqlite` is left intact — it may contain checkpoints and is harmless without the session record.

Also remove the mode from `ReplContext._modes` cache so it's not reused:

```python
# In ReplContext:
async def delete_session(self, session_id: str) -> None:
    self._modes.pop(session_id, None)
    await self.session_manager.delete(session_id)
```

### TUI Commands

**Chat phase**:
- `:rm <name>` — delete session by name for the current book
- `:rm :current` — delete the current session, return to session select

**Session select phase**:
- `:rm` — delete the focused session (the one highlighted by arrow keys)

After deletion, if the deleted session was the active one, transition to session select for that book. If no sessions remain, return to book select.

### Safety

No confirmation prompt — sessions are lightweight chat history, not irreplaceable data. The `:rm` command is explicit enough.

---

## Feature 2: Rate Limiting

### Config

New section in `BookScoutConfig`:

```python
class RateLimitWindowConfig(BaseModel):
    limit: int = 0  # 0 = unlimited for this window

class RateLimitWindowsConfig(BaseModel):
    rolling_5h: RateLimitWindowConfig = Field(default_factory=lambda: RateLimitWindowConfig(limit=0))
    rolling_weekly: RateLimitWindowConfig = Field(default_factory=lambda: RateLimitWindowConfig(limit=0))
    rolling_monthly: RateLimitWindowConfig = Field(default_factory=lambda: RateLimitWindowConfig(limit=0))

class RateLimitConfig(BaseModel):
    mode: str = "off"  # "requests" | "tokens" | "off"
    windows: RateLimitWindowsConfig = Field(default_factory=RateLimitWindowsConfig)
```

YAML example:

```yaml
ratelimit:
  mode: "requests"
  windows:
    rolling_5h:
      limit: 1000
    rolling_weekly:
      limit: 2000
    rolling_monthly:
      limit: 4000
```

Default is `mode: "off"` — no limits unless explicitly configured.

### RateLimiter Class

New class in `bookscout-llm/bookscout/llm/rate_limiter.py`:

```python
class RateLimiter(LoggingMixin):
    """SQLite-backed rate limiter for LLM API calls.

    Two modes:
    - "requests": each API call counts as 1 unit
    - "tokens": input+output tokens count (tiktoken estimate on request,
      actual usage from response updates the record)

    Three rolling windows: 5h, weekly (7d), monthly (30d).
    Limits of 0 mean unlimited for that window.
    """

    WINDOW_SECS = {
        "rolling_5h": 5 * 3600,
        "rolling_weekly": 7 * 86400,
        "rolling_monthly": 30 * 86400,
    }

    def __init__(self, sqlite: SQLite, config: RateLimitConfig, logger: Logger) -> None: ...

    async def startup(self) -> None:
        """Create rate_limit_log table."""
        # Table: id, timestamp, mode, tokens_estimated, tokens_actual

    async def check_allowed(self, estimated_tokens: int | None = None) -> tuple[bool, str]:
        """Check if a new LLM call is allowed under all active windows.
        Returns (allowed, reason). reason is human-readable if blocked.
        """

    async def record_request(self, estimated_tokens: int | None = None) -> None:
        """Record a new LLM call before the API request is made."""

    async def record_actual_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Update the most recent record with actual token usage from the response.
        Called after receiving the response_complete event.
        """

    async def get_status(self) -> dict[str, Any]:
        """Return current usage for all windows. Used by :usage command.
        Returns: {"mode": str, "windows": {name: {"used": int, "limit": int, "remaining": int}}}
        """

    async def _count_usage(self, since: float, until: float) -> int:
        """Count usage in a time window. For requests mode, count rows.
        For tokens mode, sum tokens_actual (fall back to tokens_estimated
        where actual is not yet reported).
        """

    async def _cleanup_old_records(self) -> None:
        """Delete records older than the largest window (30d) to keep the table small."""
```

### Token Counting Strategy

**On request** (before API call):
- `requests` mode: no estimation needed, just count +1
- `tokens` mode: use `estimate_text_tokens()` (tiktoken cl100k_base) on the serialized messages to get `tokens_estimated`

**On response** (after `response_complete`):
- If the response includes `usage.input_tokens` and `usage.output_tokens`, update the record's `tokens_actual = input_tokens + output_tokens`
- If the response has no usage data, `tokens_actual` stays NULL and `tokens_estimated` is used for counting

This means: for `tokens` mode, the check before a call uses the estimate. After the call, the actual count replaces the estimate. The next check is more accurate.

### ChatModel Integration

In `chat_completion()` and `chat_completion_stream()`, before the API call:

```python
if self._rate_limiter is not None:
    est_tokens = None
    if self._rate_limiter.mode == "tokens":
        est_tokens = self.estimate_token(str(messages))
    allowed, reason = await self._rate_limiter.check_allowed(est_tokens)
    if not allowed:
        raise RateLimitError(reason)
    await self._rate_limiter.record_request(est_tokens)
```

After getting usage from the response:

```python
if self._rate_limiter is not None:
    usage = response.get("usage", {})
    await self._rate_limiter.record_actual_usage(
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
    )
```

New exception in `bookscout-llm/bookscout/llm/exceptions.py`:

```python
class RateLimitError(LLMError):
    """Raised when a rate limit window is exceeded."""
```

### RateLimiter Construction

The `RateLimiter` is created in `ChatModel.__init__()` if `config.ratelimit.mode != "off"`. It shares the ChatModel's SQLite instance (or creates its own if stateless).

In `ReplContext.startup()`, the `RateLimitConfig` is passed through `LLMConfig`:

```python
# LLMConfig gets a new field:
ratelimit: RateLimitConfig = Field(default_factory=RateLimitConfig)

# ReplContext.startup() passes it:
self._llm = OpenAIChatModel(
    logger=self.logger,
    config=LLMConfig(
        backend=OpenAIConfig(...),
        ratelimit=self._config.ratelimit,  # pass through
    ),
)
```

### TUI `:usage` Command

In chat phase, `:usage` displays:

```
Rate Limit: requests mode
  5h:     234 / 1000  (766 remaining)
  Weekly: 1200 / 2000  (800 remaining)
  Monthly: 3100 / 4000  (899 remaining)
```

Token mode:

```
Rate Limit: tokens mode
  5h:     125,000 / 500,000  (375,000 remaining)
  Weekly: 890,000 / 2,000,000  (1,110,000 remaining)
  Monthly: 3,200,000 / 10,000,000  (6,800,000 remaining)
```

Off:

```
Rate Limit: off (no limits configured)
```

Implementation: call `self._repl_context.llm._rate_limiter.get_status()` (or expose a public method on ReplContext), format as markdown, append to chat log.

---

## Feature 3: Tool Max Rounds per-Agent

### CompletionOptions

Add `max_tool_iterations: int | None = None` to `CompletionOptions`:

```python
class CompletionOptions(BaseModel):
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    stream: bool = False
    thinking: ThinkingConfig | None = None
    max_tool_iterations: int | None = None  # NEW: override global ToolcallConfig
```

### ChatModel

In `chat_completion()` and `_stream_with_tools()`, resolve the effective max:

```python
max_iterations = (
    opts.max_tool_iterations
    if opts.max_tool_iterations is not None
    else self.config.toolcall.max_iterations
)
```

### ReadingAgent

Override in `step()` and `run_stream()`:

```python
options = CompletionOptions(
    model=model, temperature=0.2,
    max_tool_iterations=50,
)
```

### Backward Compatibility

Default `None` means "use global config" — no change for existing callers.

---

## Feature 4: LLM Retry with Exponential Backoff

### Config

New section in `LLMConfig`:

```python
class RetryConfig(BaseModel):
    max_retries: int = 10
    initial_delay: float = 1.0   # seconds
    max_delay: float = 30.0      # seconds
    backoff_factor: float = 2.0  # delay = initial * backoff^(attempt-1)

retry: RetryConfig = Field(default_factory=RetryConfig)
```

### Retryable vs Non-Retryable Errors

**Retryable** (transient, likely to succeed on retry):
- HTTP 429 (rate limit) — respect `Retry-After` header if present
- HTTP 500/502/503 (server errors)
- `asyncio.TimeoutError`
- `ConnectionError` / `httpx.ConnectError` / `openai.APIConnectionError`
- `openai.RateLimitError`

**Non-retryable** (will fail again):
- `ContextOverflowError` — context too large
- `ModelNotSupportedError` — wrong model/config
- Auth errors (401/403)
- Content filter errors
- `RateLimitError` (our own rate limit, not the provider's)

### Implementation

Wrap the provider API call in a retry loop. For non-streaming:

```python
async def _complete_with_retry(self, messages, tools, options):
    cfg = self.config.retry
    for attempt in range(1, cfg.max_retries + 1):
        try:
            return await self._complete(messages, tools, options)
        except LLMError as e:
            if not self._is_retryable(e) or attempt >= cfg.max_retries:
                raise
            delay = min(
                cfg.initial_delay * (cfg.backoff_factor ** (attempt - 1)),
                cfg.max_delay,
            )
            # Respect Retry-After header for 429s
            retry_after = getattr(e, "retry_after", None)
            if retry_after:
                delay = max(delay, float(retry_after))
            self.logger.warning(
                "LLM call failed, retrying",
                attempt=attempt, delay=f"{delay:.1f}s", error=str(e),
            )
            await asyncio.sleep(delay)
```

For streaming, the retry wraps the `_complete_stream` setup. Once chunks start flowing, we don't retry mid-stream — if the stream breaks mid-way, the error propagates to the tool-call loop which may retry the entire iteration.

### TUI Feedback

The retry happens inside `chat_completion_stream`'s `_stream_with_tools` loop. When a retry occurs, yield a status event:

```python
yield StreamChunk(
    kind="status",
    data={"phase": "retry", "attempt": attempt, "max_retries": cfg.max_retries, "error": str(e)},
)
```

TUI `_handle_chunk()` displays it:

```python
elif chunk.kind == "status":
    data = chunk.data if isinstance(chunk.data, dict) else {}
    phase = data.get("phase", "")
    if phase == "retry":
        attempt = data.get("attempt", "?")
        max_r = data.get("max_retries", "?")
        self._set_status(f"  LLM error, retrying ({attempt}/{max_r})...")
```

### _is_retryable Helper

```python
@staticmethod
def _is_retryable(error: LLMError) -> bool:
    """Determine if an LLM error is transient and worth retrying."""
    # Non-retryable types
    if isinstance(error, (ContextOverflowError, ModelNotSupportedError, RateLimitError)):
        return False
    msg = str(error).lower()
    # Retryable patterns
    retryable_patterns = ["rate_limit", "429", "timeout", "connection", "server_error", "500", "502", "503", "overloaded"]
    return any(p in msg for p in retryable_patterns)
```

---

## Feature 5: User Message Visual Separation

### Change

In `tui.py:_run_chat()`, add an extra newline before the user blockquote:

```python
# Before:
self._chat_markdown += f"\n> {escaped}\n\n"

# After:
self._chat_markdown += f"\n\n> {escaped}\n\n"
```

Same change in `_enter_chat_with_session()` when loading history:

```python
if role == "user":
    md_parts.append(f"\n\n> {escaped}\n\n")
```

This creates a blank line between the assistant's previous response and the user's next message, making it visually clear where each user turn begins.

---

## Implementation Order

1. **Feature 5** (message formatting) — trivial, 2 lines
2. **Feature 1** (session delete) — small, self-contained
3. **Feature 3** (tool max rounds) — small, backward-compatible
4. **Feature 4** (LLM retry) — medium, touches ChatModel core
5. **Feature 2** (rate limiting) — largest, new class + config + SQLite + TUI command

Features 1, 3, 5 have no dependencies on each other. Feature 4 should come before Feature 2 because rate limit errors (our own `RateLimitError`) must be classified as non-retryable.

---

## Files Touched

| Feature | Files |
|---------|-------|
| 1 (session delete) | `session_manager.py`, `context.py`, `tui.py` |
| 2 (rate limiting) | `config.py`, `llm/config.py`, `llm/rate_limiter.py` (new), `llm/__init__.py`, `llm/exceptions.py`, `context.py`, `tui.py` |
| 3 (tool max rounds) | `llm/types.py`, `llm/__init__.py`, `agents/reading/agent.py` |
| 4 (LLM retry) | `llm/config.py`, `llm/__init__.py`, `llm/exceptions.py`, `tui.py` |
| 5 (message formatting) | `tui.py` |

---

## Testing

| Feature | Tests |
|---------|-------|
| 1 | `test_session_manager.py`: test_delete_removes_session_and_messages; `test_tui_commands.py`: test_chat_rm_command |
| 2 | `test_rate_limiter.py` (new): test_requests_mode, test_tokens_mode, test_window_expiry, test_off_mode, test_status_output |
| 3 | `test_reading_agent.py`: test_max_tool_iterations_override |
| 4 | `test_llm_retry.py` (new): test_retryable_error_retries, test_non_retryable_fails_immediately, test_max_retries_exhausted |
| 5 | No new tests needed (visual formatting only) |
