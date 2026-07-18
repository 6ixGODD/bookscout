# Copyright 2026 BoChen SHEN
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# pylint: disable=too-many-lines
"""`bookscout.llm` package 鈥?provider-agnostic LLM backend.

Defines :class:`ChatModel`, the abstract base class for all LLM
implementations.  Concrete backends live in :mod:`bookscout.llm.openai` and
:mod:`bookscout.llm.anthropic`.
"""

from __future__ import annotations

import abc
import asyncio
import typing as t

from bookscout.core.lib.stream import AsyncStream
from bookscout.core.lib.utils import utcnow_ts
from bookscout.core.mixins import AsyncResourceMixin
from bookscout.filestore import FileStore
from bookscout.filestore import FileStoreConfig
from bookscout.logging.mixin import LoggingMixin
from bookscout.sqlite import SQLite
from bookscout.sqlite import SQLiteConfig
from bookscout.tools import BaseTool
from bookscout.tools import ToolExecutor

from .config import LLMConfig
from .exceptions import CompletionError
from .exceptions import ContextOverflowError
from .exceptions import handle_errors
from .rate_limiter import RateLimiter
from .store.conversation import ConversationStore
from .store.file import LLMFileStore
from .types import AssistantMessage
from .types import CompletionOptions
from .types import CompletionResponse
from .types import Message
from .types import ResponseCompleteEvent
from .types import StatusEvent
from .types import StreamEvent
from .types import ToolCall
from .types import ToolCallFunction
from .types import ToolResultEvent
from .types import ToolResultMessage
from .types import Usage

if t.TYPE_CHECKING:
    from bookscout.logging import Logger

    from .store.conversation import ConversationRow


def _estimate_tokens(messages: list[Message]) -> int:
    """Estimate token count for a list of messages.

    Uses :mod:`tiktoken` when available for accurate tokenization
    (cl100k_base encoding, which covers GPT-4 / GPT-4o / Claude-style
    tokenizers).  Falls back to a ~4 characters-per-token heuristic when
    tiktoken is not installed.
    """
    text = "\n".join(msg.model_dump_json() for msg in messages)

    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return max(1, len(enc.encode(text)))
    except ImportError:
        pass

    # Fallback: ~4 chars per token
    return max(1, len(text) // 4)


def estimate_text_tokens(text: str) -> int:
    """Estimate token count for a plain text string.

    Uses :mod:`tiktoken` when available (cl100k_base encoding).
    Falls back to ~4 characters-per-token heuristic.

    Args:
        text: The text to estimate.

    Returns:
        Estimated token count (minimum 1).
    """
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return max(1, len(enc.encode(text)))
    except ImportError:
        pass

    return max(1, len(text) // 4)


MessageT = t.TypeVar("MessageT")
ToolT = t.TypeVar("ToolT")
CompletionT = t.TypeVar("CompletionT")
CompletionChunkT = t.TypeVar("CompletionChunkT")


class ChatModel(
    LoggingMixin,
    AsyncResourceMixin,
    abc.ABC,
    t.Generic[MessageT, ToolT, CompletionT, CompletionChunkT],
):
    """Provider-agnostic LLM backend.

    Two primary interfaces:

    - **chat_completion**: Stateless one-shot completion. Does not persist
      messages, but executes tools internally when provided.
    - **response**: Stateful conversation. Persists messages to SQLite,
      executes tools internally, and manages context budget.

    When ``tools`` is provided but ``tool_executor`` is ``None``, a
    :class:`ToolExecutor` is auto-instantiated from the tool list 鈥?the
    caller doesn't need to construct one manually.

    Subclasses must implement the ``_complete``, ``_complete_stream``,
    and conversion methods.

    Args:
        logger: Logger instance.
        config: LLM configuration.
    """

    def __init__(self, logger: Logger, config: LLMConfig) -> None:
        super().__init__(logger=logger)
        self.config = config
        self._concurrency_semaphore = asyncio.Semaphore(config.max_concurrency)
        self._stateless = config.stateless
        # In stateless mode, skip all local persistence.
        self.sqlite: SQLite | None = None
        self.conversation_store: ConversationStore | None = None
        self.llm_file_store: LLMFileStore | None = None
        self.filestore: FileStore | None = None
        self._rate_limiter: RateLimiter | None = None
        if not self._stateless:
            self.sqlite = SQLite(
                config=SQLiteConfig(uri=config.db_uri),
                logger=logger,
            )

    @staticmethod
    def estimate_token(text: str) -> int:
        """Estimate the token count for a plain text string.

        Uses :mod:`tiktoken` (cl100k_base) when available; falls back to
        ~4 characters-per-token heuristic.

        Args:
            text: The text to estimate.

        Returns:
            Estimated token count (minimum 1).
        """
        return estimate_text_tokens(text)

    async def get_rate_limit_usage(self) -> dict[str, dict[str, t.Any]] | None:
        """Return current rate-limit usage stats, or ``None`` if rate limiting is off."""
        if self._rate_limiter is None:
            return None
        return await self._rate_limiter.get_usage()

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

    async def startup(self) -> None:
        """Initialize resources.

        In stateless mode, only the provider client is initialized.
        In stateful mode, also initializes SQLite, conversation store,
        and file store.
        """
        if not self._stateless:
            assert self.sqlite is not None
            await self.sqlite.startup()

            # Initialize conversation store
            self.conversation_store = ConversationStore(
                logger=self.logger,
                sqlite=self.sqlite,
                budget_config=self.config.context_budget,
            )
            await self.conversation_store.startup()

            # Initialize FileStore for blob storage
            fs_base = self._resolve_filestore_base()
            self.filestore = FileStore(
                logger=self.logger,
                config=FileStoreConfig(base_path=fs_base),
            )
            await self.filestore.startup()

            # Initialize LLM file store
            self.llm_file_store = LLMFileStore(
                logger=self.logger,
                sqlite=self.sqlite,
                filestore=self.filestore,
            )
            await self.llm_file_store.startup()

        # Initialize rate limiter if configured.
        if self.config.ratelimit.mode != "off":
            self._rate_limiter = RateLimiter(
                config=self.config.ratelimit,
                logger=self.logger,
            )
            await self._rate_limiter.startup()

        # Let subclass do provider-specific initialization
        await self._startup_provider()

        await super().startup()
        self.logger.info("ChatModel started", backend=self.config.backend.type, stateless=self._stateless)

    async def shutdown(self) -> None:
        """Dispose resources.

        In stateless mode, only the provider client is shut down.
        """
        await self._shutdown_provider()

        if self._rate_limiter is not None:
            await self._rate_limiter.shutdown()

        if not self._stateless:
            if self.llm_file_store is not None:
                await self.llm_file_store.shutdown()
            if self.filestore is not None:
                await self.filestore.shutdown()
            if self.conversation_store is not None:
                await self.conversation_store.shutdown()
            if self.sqlite is not None:
                await self.sqlite.shutdown()
        self.logger.info("ChatModel stopped")

    def _resolve_filestore_base(self) -> str:
        """Derive a FileStore base path from the SQLite URI.

        Places the blob store next to the SQLite database file.
        """
        uri = self.config.db_uri
        # Extract path from URI like sqlite+aiosqlite:///./llm.db
        if ":///" in uri:
            path_part = uri.split(":///", 1)[1]
            # Remove query params if any
            if "?" in path_part:
                path_part = path_part.split("?", 1)[0]
            # Use parent directory + "llm_blobs"
            import pathlib

            db_path = pathlib.Path(path_part)
            return str(db_path.parent / "llm_blobs")
        return "/tmp/llm_blobs"

    async def _startup_provider(self) -> None:
        """Provider-specific startup (e.g., create API client)."""

    async def _shutdown_provider(self) -> None:
        """Provider-specific shutdown (e.g., dispose API client)."""

    @abc.abstractmethod
    async def _complete(
        self,
        messages: list[MessageT],
        tools: list[ToolT] | None,
        options: CompletionOptions,
    ) -> CompletionT:
        """Execute a non-streaming completion against the provider API."""

    @abc.abstractmethod
    async def _complete_stream(
        self,
        messages: list[MessageT],
        tools: list[ToolT] | None,
        options: CompletionOptions,
    ) -> t.AsyncIterator[CompletionChunkT]:
        """Execute a streaming completion against the provider API.

        An async generator that yields provider-specific chunks. Subclasses
        ``await`` the provider SDK call and ``yield`` chunks.

        Note: mypy treats ``async def -> T`` as returning
        ``Coroutine[Any, Any, T]``, which means the declared return type
        ``AsyncIterator[T]`` doesn't match the actual async-generator
        semantics at the type level.  Subclasses add
        ``# type: ignore[override]`` to suppress this known mypy limitation.
        """

    @abc.abstractmethod
    async def _convert_messages(self, messages: list[Message]) -> list[MessageT]:
        """Convert our Message types to provider-specific format.

        Subclasses are responsible for resolving file references
        (``ImageContent``, ``FileContent``) on demand via
        ``self.llm_file_store`` and ``self.filestore`` 鈥?do **not**
        pre-load all files into memory.

        Args:
            messages: Our message list.
        """

    @abc.abstractmethod
    def _convert_tools(self, tools: t.Sequence[BaseTool]) -> list[ToolT]:
        """Convert :class:`bookscout.tools.BaseTool` instances to
        provider-specific tool format."""

    @abc.abstractmethod
    def _convert_response(self, raw: CompletionT) -> CompletionResponse:
        """Convert a provider response to our CompletionResponse."""

    @abc.abstractmethod
    def _convert_chunk(self, raw: CompletionChunkT) -> StreamEvent | None:
        """Convert a provider stream chunk to our StreamEvent.

        Returns None if the chunk should be skipped (e.g., internal events).
        """

    @abc.abstractmethod
    def _apply_cache_control(self, messages: list[MessageT]) -> list[MessageT]:
        """Apply prompt-cache markers to the provider-specific message list."""

    @abc.abstractmethod
    def _get_default_model(self) -> str:
        """Return the default model name for this provider."""

    @handle_errors(exc_type=CompletionError)
    async def chat_completion(
        self,
        messages: list[Message],
        *,
        tools: t.Sequence[BaseTool] | None = None,
        tool_executor: ToolExecutor | None = None,
        options: CompletionOptions | None = None,
    ) -> CompletionResponse:
        """Stateless one-shot completion.

        Does not persist messages.  When ``tools`` is provided, tool calls
        are executed internally (using ``tool_executor`` if given, or an
        auto-instantiated :class:`ToolExecutor` otherwise).  Raises
        :class:`ContextOverflowError` if the context window budget is
        exceeded.

        Args:
            messages: Input messages.
            tools: Optional :class:`bookscout.tools.BaseTool` instances.
            tool_executor: Optional tool executor.  If ``tools`` is provided
                but ``tool_executor`` is ``None``, a ``ToolExecutor(tools)``
                is created automatically.
            options: Per-request options.

        Returns:
            The completion response (after tool calls resolve, if any).
        """
        # Auto-instantiate ToolExecutor when tools are provided but no
        # executor was passed 鈥?the caller shouldn't have to construct one
        # manually when they've already given us the tool list.
        if tools and tool_executor is None:
            tool_executor = ToolExecutor(tools)

        opts = options or CompletionOptions()
        model = opts.model or self._get_default_model()

        # Check context budget
        self._check_context_budget(messages)

        # Rate-limit check
        estimated_tokens = _estimate_tokens(messages) if self._rate_limiter else 0
        if self._rate_limiter:
            await self._rate_limiter.check_allowed(estimated_tokens=estimated_tokens)
            rl_row_id = await self._rate_limiter.record_request(estimated_tokens=estimated_tokens)
        else:
            rl_row_id = 0

        # Convert tools once — tools don't change across iterations
        provider_tools = self._convert_tools(tools) if tools else None

        self.logger.info(
            "chat_completion",
            model=model,
            message_count=len(messages),
            has_tools=tools is not None,
            stream=False,
        )

        # Tool-call loop (stateless 鈥?no persistence)
        max_iterations = (
            opts.max_tool_iterations
            if opts.max_tool_iterations is not None
            else self.config.toolcall.max_iterations
        )
        current_messages = list(messages)
        response: CompletionResponse = CompletionResponse(
            message=AssistantMessage(content=""),
            usage=Usage(input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0),
            model=model,
            finish_reason="stop",
        )
        for _iteration in range(max_iterations):
            provider_messages = await self._convert_messages(current_messages)
            if self.config.cache.enabled:
                provider_messages = self._apply_cache_control(provider_messages)

            start_time = utcnow_ts()
            # Retry loop for transient errors.
            import asyncio

            cfg = self.config.retry
            for _retry_attempt in range(1, cfg.max_retries + 1):
                try:
                    async with self._concurrency_semaphore:
                        raw = await self._complete(provider_messages, provider_tools, opts)
                    break  # success — exit retry loop
                except Exception as e:
                    from bookscout.llm.exceptions import CompletionError
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

            elapsed = utcnow_ts() - start_time
            self.logger.info(
                "chat_completion completed",
                model=response.get("model", model),
                finish_reason=response.get("finish_reason", ""),
                input_tokens=response.get("usage", {}).get("input_tokens", 0),
                output_tokens=response.get("usage", {}).get("output_tokens", 0),
                iteration=_iteration,
                elapsed_s=f"{elapsed:.3f}",
            )

            # Check for tool calls
            assistant_msg = response["message"]
            tool_calls = assistant_msg.tool_calls
            if not tool_calls or tool_executor is None:
                # Record actual usage before returning.
                if self._rate_limiter and rl_row_id:
                    usage = response.get("usage", {})
                    await self._rate_limiter.record_actual_usage(
                        rl_row_id,
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                    )
                return response

            # Execute tool calls
            from bookscout.tools import ToolCallsParams

            current_messages.append(assistant_msg)
            for tc in tool_calls:
                from bookscout.tools import Function as _Func

                params = ToolCallsParams(
                    call_id=tc.call_id,
                    function=_Func(name=tc.function.name, arguments=tc.function.arguments),
                )
                result = await tool_executor.execute(params)

                self.logger.info(
                    "Tool executed",
                    tool_name=tc.function.name,
                    call_id=tc.call_id,
                    iteration=_iteration,
                )

                # Create tool result message and append
                result_msg = ToolResultMessage(
                    tool_call_id=tc.call_id,
                    content=result.content,
                )
                current_messages.append(result_msg)

        # Max iterations reached 鈥?return last response
        self.logger.warning(
            "Tool-call loop reached max iterations",
            max_iterations=max_iterations,
        )
        return response

    @handle_errors(exc_type=CompletionError)
    async def chat_completion_stream(
        self,
        messages: list[Message],
        *,
        tools: t.Sequence[BaseTool] | None = None,
        tool_executor: ToolExecutor | None = None,
        options: CompletionOptions | None = None,
    ) -> AsyncStream[StreamEvent]:
        """Stateless streaming completion with tool execution.

        Yields :class:`StreamEvent` items as they arrive.  When ``tools``
        is provided, tool calls are executed internally (using
        ``tool_executor`` if given, or an auto-instantiated
        :class:`ToolExecutor` otherwise) and tool results are fed back
        into the conversation for further LLM turns.

        Unlike :meth:`response_stream`, this does **not** persist messages.

        Args:
            messages: Input messages.
            tools: Optional :class:`bookscout.tools.BaseTool` instances.
            tool_executor: Optional tool executor.  If ``tools`` is provided
                but ``tool_executor`` is ``None``, a ``ToolExecutor(tools)``
                is created automatically.
            options: Per-request options.

        Returns:
            An async stream of stream events.
        """
        # Auto-instantiate ToolExecutor when tools are provided
        if tools and tool_executor is None:
            tool_executor = ToolExecutor(tools)

        opts = options or CompletionOptions()
        model = opts.model or self._get_default_model()

        # Check context budget
        self._check_context_budget(messages)

        # Rate-limit check
        estimated_tokens = _estimate_tokens(messages) if self._rate_limiter else 0
        if self._rate_limiter:
            await self._rate_limiter.check_allowed(estimated_tokens=estimated_tokens)
            rl_row_id = await self._rate_limiter.record_request(estimated_tokens=estimated_tokens)
        else:
            rl_row_id = 0

        # Convert tools once — tools don't change across iterations
        provider_tools = self._convert_tools(tools) if tools else None

        self.logger.info(
            "chat_completion_stream",
            model=model,
            message_count=len(messages),
            has_tools=tools is not None,
            stream=True,
        )

        # Streaming event generator with tool execution
        async def _stream_with_tools() -> t.AsyncIterator[StreamEvent]:
            max_iterations = (
            opts.max_tool_iterations
            if opts.max_tool_iterations is not None
            else self.config.toolcall.max_iterations
        )
            current_messages = list(messages)

            # Variables that persist across iterations for the max-iterations fallback
            accumulated_text = ""
            usage: Usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
            finish_reason = ""

            for _iteration in range(max_iterations):
                provider_messages = await self._convert_messages(current_messages)
                if self.config.cache.enabled:
                    provider_messages = self._apply_cache_control(provider_messages)

                raw_stream = None
                import asyncio

                cfg = self.config.retry
                for _retry_attempt in range(1, cfg.max_retries + 1):
                    try:
                        raw_stream = self._complete_stream(provider_messages, provider_tools, opts)  # type: ignore[arg-type,attr-defined,misc]
                        break  # success
                    except Exception as e:
                        from bookscout.llm.exceptions import CompletionError
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
                        yield StatusEvent(
                            type="status",
                            phase="retry",
                            data={"attempt": _retry_attempt, "max_retries": cfg.max_retries, "error": str(e)},
                        )
                        await asyncio.sleep(delay)

                # Reset per-iteration accumulators
                accumulated_text = ""
                accumulated_tool_calls: dict[str, dict[str, str]] = {}
                current_call_id = ""
                finish_reason = ""
                usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}

                async for chunk in raw_stream:  # type: ignore[attr-defined]
                    event = self._convert_chunk(chunk)
                    if event is None:
                        continue

                    yield event

                    # Accumulate based on event type
                    if event["type"] == "text_delta":
                        accumulated_text += event["delta"]["text"]
                    elif event["type"] == "tool_call_delta":
                        delta = event["delta"]
                        if delta["call_id"]:
                            current_call_id = delta["call_id"]
                            if current_call_id not in accumulated_tool_calls:
                                accumulated_tool_calls[current_call_id] = {
                                    "name": delta["name"],
                                    "arguments": "",
                                }
                        if current_call_id and delta["arguments_delta"]:
                            accumulated_tool_calls[current_call_id]["arguments"] += delta["arguments_delta"]
                    elif event["type"] == "response_complete":
                        resp = event["response"]
                        usage = resp["usage"]
                        finish_reason = resp["finish_reason"]
                        # Record actual usage from the model.
                        if self._rate_limiter and rl_row_id:
                            await self._rate_limiter.record_actual_usage(
                                rl_row_id,
                                input_tokens=usage.get("input_tokens", 0),
                                output_tokens=usage.get("output_tokens", 0),
                            )

                # Build the full assistant message
                assistant_msg = AssistantMessage(content=accumulated_text)
                if accumulated_tool_calls:
                    assistant_msg.tool_calls = [
                        ToolCall(
                            call_id=call_id,
                            function=ToolCallFunction(
                                name=data["name"],
                                arguments=data["arguments"],
                            ),
                        )
                        for call_id, data in accumulated_tool_calls.items()
                    ]

                current_messages.append(assistant_msg)

                # Execute tool calls if any
                if assistant_msg.tool_calls and tool_executor is not None:
                    from bookscout.tools import Function as _Func
                    from bookscout.tools import ToolCallsParams

                    for tc in assistant_msg.tool_calls:
                        params = ToolCallsParams(
                            call_id=tc.call_id,
                            function=_Func(name=tc.function.name, arguments=tc.function.arguments),
                        )
                        result = await tool_executor.execute(params)

                        # Yield tool result event
                        yield ToolResultEvent(
                            type="tool_result",
                            result={"call_id": tc.call_id, "content": result.content},
                        )

                        # Append tool result for next iteration
                        result_msg = ToolResultMessage(
                            tool_call_id=tc.call_id,
                            content=result.content,
                        )
                        current_messages.append(result_msg)
                else:
                    # No tool calls 鈥?yield final response and finish
                    yield ResponseCompleteEvent(
                        type="response_complete",
                        response=CompletionResponse(
                            message=assistant_msg,
                            usage=usage,
                            model=model,
                            finish_reason=finish_reason,
                        ),
                    )
                    return

            self.logger.warning(
                "Stream tool-call loop reached max iterations",
                max_iterations=max_iterations,
            )
            yield ResponseCompleteEvent(
                type="response_complete",
                response=CompletionResponse(
                    message=AssistantMessage(content=accumulated_text),
                    usage=usage,
                    model=model,
                    finish_reason=finish_reason or "max_iterations",
                ),
            )

        return AsyncStream(_stream_with_tools())

    @handle_errors(exc_type=CompletionError)
    async def response(
        self,
        conversation_id: str | None = None,
        messages: list[Message] | None = None,
        *,
        tools: t.Sequence[BaseTool] | None = None,
        tool_executor: ToolExecutor | None = None,
        options: CompletionOptions | None = None,
    ) -> CompletionResponse:
        """Stateful conversation completion.

        Persists messages to SQLite, executes tools internally (if
        ``tool_executor`` is provided), and manages context budget by
        truncating oldest non-system messages when the budget is exceeded.

        Args:
            conversation_id: Existing conversation ID. If None, a new
                conversation is created.
            messages: New messages to add to the conversation. If None,
                uses existing messages only.
            tools: Optional :class:`bookscout.tools.BaseTool` instances.
            tool_executor: Optional tool executor.  If ``tools`` is provided
                but ``tool_executor`` is ``None``, a ``ToolExecutor(tools)``
                is created automatically.
            options: Per-request options.

        Returns:
            The final completion response after all tool calls resolve.
        """
        if self._stateless:
            raise RuntimeError(
                "response() is not available in stateless mode. "
                "Use chat_completion() instead, or set stateless=False in LLMConfig."
            )
        if self.conversation_store is None:
            raise RuntimeError("ConversationStore not initialized. Call startup() first.")

        # Auto-instantiate ToolExecutor when tools are provided
        if tools and tool_executor is None:
            tool_executor = ToolExecutor(tools)

        opts = options or CompletionOptions()
        model = opts.model or self._get_default_model()

        # Convert tools once 鈥?tools don't change across iterations
        provider_tools = self._convert_tools(tools) if tools else None

        # Create or load conversation
        if conversation_id is None:
            conversation_id = await self.conversation_store.create(model=model)
        else:
            conv = await self.conversation_store.get(conversation_id)
            if conv is None:
                conversation_id = await self.conversation_store.create(model=model)

        # Add new messages to conversation
        if messages:
            for msg in messages:
                await self.conversation_store.add_message(conversation_id, msg)

        # Load all messages and apply context budget
        all_messages = await self.conversation_store.get_messages(conversation_id)
        all_messages = await self._truncate_to_budget(all_messages)

        # Tool-call loop
        max_iterations = (
            opts.max_tool_iterations
            if opts.max_tool_iterations is not None
            else self.config.toolcall.max_iterations
        )
        response: CompletionResponse = CompletionResponse(
            message=AssistantMessage(content=""),
            usage=Usage(input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0),
            model=model,
            finish_reason="stop",
        )
        for _iteration in range(max_iterations):
            # Resolve file references and convert messages
            provider_messages = await self._convert_messages(all_messages)
            if self.config.cache.enabled:
                provider_messages = self._apply_cache_control(provider_messages)

            self.logger.info(
                "response iteration",
                conversation_id=conversation_id,
                iteration=_iteration,
                model=model,
                message_count=len(all_messages),
            )

            async with self._concurrency_semaphore:
                raw = await self._complete(provider_messages, provider_tools, opts)
            response = self._convert_response(raw)

            # Persist assistant message
            assistant_msg = response["message"]
            await self.conversation_store.add_message(conversation_id, assistant_msg)
            all_messages.append(assistant_msg)

            # Check for tool calls
            tool_calls = assistant_msg.tool_calls
            if not tool_calls or tool_executor is None:
                return response
            # Execute tool calls
            from bookscout.tools import ToolCallsParams

            tool_results: list[Message] = []
            for tc in tool_calls:
                # Reconstruct the Function NamedTuple
                from bookscout.tools import Function as _Func

                params = ToolCallsParams(
                    call_id=tc.call_id,
                    function=_Func(name=tc.function.name, arguments=tc.function.arguments),
                )
                result = await tool_executor.execute(params)

                self.logger.info(
                    "Tool executed",
                    tool_name=tc.function.name,
                    call_id=tc.call_id,
                    iteration=_iteration,
                )

                # Create tool result message
                result_msg = ToolResultMessage(
                    tool_call_id=tc.call_id,
                    content=result.content,
                )
                tool_results.append(result_msg)

            # Persist tool results and add to messages
            for tr_msg in tool_results:
                await self.conversation_store.add_message(conversation_id, tr_msg)
                all_messages.append(tr_msg)

        # Max iterations reached 鈥?return last response
        self.logger.warning(
            "Tool-call loop reached max iterations",
            conversation_id=conversation_id,
            max_iterations=max_iterations,
        )
        return response

    @handle_errors(exc_type=CompletionError)
    async def response_stream(
        self,
        conversation_id: str | None = None,
        messages: list[Message] | None = None,
        *,
        tools: t.Sequence[BaseTool] | None = None,
        tool_executor: ToolExecutor | None = None,
        options: CompletionOptions | None = None,
    ) -> AsyncStream[StreamEvent]:
        """Stateful streaming conversation.

        Like :meth:`response` but yields :class:`StreamEvent` items as
        they arrive. Tool calls are executed internally and yielded as
        :class:`ToolResultEvent` items.

        Args:
            conversation_id: Existing conversation ID.
            messages: New messages to add.
            tools: Optional :class:`bookscout.tools.BaseTool` instances.
            tool_executor: Optional tool executor.  If ``tools`` is provided
                but ``tool_executor`` is ``None``, a ``ToolExecutor(tools)``
                is created automatically.
            options: Per-request options.

        Returns:
            An async stream of stream events.
        """
        if self._stateless:
            raise RuntimeError(
                "response_stream() is not available in stateless mode. "
                "Use chat_completion_stream() instead, or set stateless=False in LLMConfig."
            )
        if self.conversation_store is None:
            raise RuntimeError("ConversationStore not initialized. Call startup() first.")
        conv_store = self.conversation_store

        # Auto-instantiate ToolExecutor when tools are provided
        if tools and tool_executor is None:
            tool_executor = ToolExecutor(tools)

        opts = options or CompletionOptions()
        model = opts.model or self._get_default_model()

        # Convert tools once 鈥?tools don't change across iterations
        provider_tools = self._convert_tools(tools) if tools else None

        # Create or load conversation
        if conversation_id is None:
            conversation_id = await conv_store.create(model=model)
        else:
            conv = await conv_store.get(conversation_id)
            if conv is None:
                conversation_id = await conv_store.create(model=model)

        # Add new messages
        if messages:
            for msg in messages:
                await self.conversation_store.add_message(conversation_id, msg)

        # Load and truncate
        all_messages = await conv_store.get_messages(conversation_id)
        all_messages = await self._truncate_to_budget(all_messages)

        # Rate-limit check (before entering the stream generator)
        estimated_tokens = _estimate_tokens(all_messages) if self._rate_limiter else 0
        if self._rate_limiter:
            await self._rate_limiter.check_allowed(estimated_tokens=estimated_tokens)
            rl_row_id = await self._rate_limiter.record_request(estimated_tokens=estimated_tokens)
        else:
            rl_row_id = 0

        # Create the streaming event generator with tool execution
        async def _stream_with_tools() -> t.AsyncIterator[StreamEvent]:
            max_iterations = (
            opts.max_tool_iterations
            if opts.max_tool_iterations is not None
            else self.config.toolcall.max_iterations
        )
            current_messages = list(all_messages)

            # Variables that persist across iterations for the max-iterations fallback
            accumulated_text = ""
            usage: Usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
            finish_reason = ""

            for _iteration in range(max_iterations):
                provider_messages = await self._convert_messages(current_messages)
                if self.config.cache.enabled:
                    provider_messages = self._apply_cache_control(provider_messages)

                raw_stream = None
                import asyncio

                cfg = self.config.retry
                for _retry_attempt in range(1, cfg.max_retries + 1):
                    try:
                        raw_stream = self._complete_stream(provider_messages, provider_tools, opts)  # type: ignore[arg-type,attr-defined,misc]
                        break  # success
                    except Exception as e:
                        from bookscout.llm.exceptions import CompletionError
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
                        yield StatusEvent(
                            type="status",
                            phase="retry",
                            data={"attempt": _retry_attempt, "max_retries": cfg.max_retries, "error": str(e)},
                        )
                        await asyncio.sleep(delay)

                # Reset per-iteration accumulators
                accumulated_text = ""
                accumulated_tool_calls: dict[str, dict[str, str]] = {}
                # Track the most recent tool-call id 鈥?argument deltas often
                # arrive without a call_id, so we attribute them to the last
                # seen call_id.
                current_call_id = ""
                finish_reason = ""
                usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}

                async for chunk in raw_stream:  # type: ignore[attr-defined]
                    event = self._convert_chunk(chunk)
                    if event is None:
                        continue

                    yield event

                    # Accumulate based on event type
                    if event["type"] == "text_delta":
                        accumulated_text += event["delta"]["text"]
                    elif event["type"] == "tool_call_delta":
                        delta = event["delta"]
                        # A non-empty call_id marks the start of a new tool call.
                        if delta["call_id"]:
                            current_call_id = delta["call_id"]
                            if current_call_id not in accumulated_tool_calls:
                                accumulated_tool_calls[current_call_id] = {
                                    "name": delta["name"],
                                    "arguments": "",
                                }
                        if current_call_id and delta["arguments_delta"]:
                            accumulated_tool_calls[current_call_id]["arguments"] += delta["arguments_delta"]
                    elif event["type"] == "response_complete":
                        resp = event["response"]
                        usage = resp["usage"]
                        finish_reason = resp["finish_reason"]
                        # Record actual usage from the model.
                        if self._rate_limiter and rl_row_id:
                            await self._rate_limiter.record_actual_usage(
                                rl_row_id,
                                input_tokens=usage.get("input_tokens", 0),
                                output_tokens=usage.get("output_tokens", 0),
                            )

                # Build the full assistant message
                assistant_msg = AssistantMessage(content=accumulated_text)
                if accumulated_tool_calls:
                    assistant_msg.tool_calls = [
                        ToolCall(
                            call_id=call_id,
                            function=ToolCallFunction(
                                name=data["name"],
                                arguments=data["arguments"],
                            ),
                        )
                        for call_id, data in accumulated_tool_calls.items()
                    ]

                # Persist assistant message
                await conv_store.add_message(conversation_id, assistant_msg)
                current_messages.append(assistant_msg)

                # Execute tool calls if any
                if assistant_msg.tool_calls and tool_executor is not None:
                    from bookscout.tools import Function as _Func
                    from bookscout.tools import ToolCallsParams

                    for tc in assistant_msg.tool_calls:
                        params = ToolCallsParams(
                            call_id=tc.call_id,
                            function=_Func(name=tc.function.name, arguments=tc.function.arguments),
                        )
                        result = await tool_executor.execute(params)

                        # Yield tool result event
                        yield ToolResultEvent(
                            type="tool_result",
                            result={"call_id": tc.call_id, "content": result.content},
                        )

                        # Persist tool result
                        result_msg = ToolResultMessage(
                            tool_call_id=tc.call_id,
                            content=result.content,
                        )
                        await conv_store.add_message(conversation_id, result_msg)
                        current_messages.append(result_msg)
                else:
                    # No tool calls 鈥?yield final response and finish
                    yield ResponseCompleteEvent(
                        type="response_complete",
                        response=CompletionResponse(
                            message=assistant_msg,
                            usage=usage,
                            model=model,
                            finish_reason=finish_reason,
                        ),
                    )
                    return

            self.logger.warning(
                "Stream tool-call loop reached max iterations",
                conversation_id=conversation_id,
                max_iterations=max_iterations,
            )
            # Yield a final response with the last accumulated state.
            yield ResponseCompleteEvent(
                type="response_complete",
                response=CompletionResponse(
                    message=AssistantMessage(content=accumulated_text),
                    usage=usage,
                    model=model,
                    finish_reason=finish_reason or "max_iterations",
                ),
            )

        return AsyncStream(_stream_with_tools())

    async def upload_file(
        self,
        data: bytes | t.IO[bytes],
        filename: str,
        mime_type: str | None = None,
    ) -> str:
        """Upload a file and return our internal file_id.

        Args:
            data: File data as bytes or a readable binary file-like object.
            filename: Original filename.
            mime_type: Optional MIME type. Guessed from filename if not provided.

        Returns:
            Our internal file_id.
        """
        if self._stateless:
            raise RuntimeError(
                "upload_file() is not available in stateless mode. "
                "Set stateless=False in LLMConfig to use file uploads."
            )
        if self.llm_file_store is None:
            raise RuntimeError("LLMFileStore not initialized. Call startup() first.")
        file_id = await self.llm_file_store.upload(data, filename, mime_type)
        return t.cast(str, file_id)

    async def create_conversation(self, title: str | None = None) -> str:
        """Create a new conversation and return its ID."""
        if self.conversation_store is None:
            raise RuntimeError("ConversationStore not initialized. Call startup() first.")
        model = self._get_default_model()
        return await self.conversation_store.create(model=model, title=title)

    async def get_conversation(self, conversation_id: str) -> ConversationRow | None:
        """Get a conversation by ID."""
        if self.conversation_store is None:
            raise RuntimeError("ConversationStore not initialized. Call startup() first.")
        return await self.conversation_store.get(conversation_id)

    async def list_conversations(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ConversationRow]:
        """List conversations ordered by most recently updated."""
        if self.conversation_store is None:
            raise RuntimeError("ConversationStore not initialized. Call startup() first.")
        return await self.conversation_store.list_conversations(limit=limit, offset=offset)

    async def delete_conversation(self, conversation_id: str) -> None:
        """Delete a conversation and all its messages."""
        if self.conversation_store is None:
            raise RuntimeError("ConversationStore not initialized. Call startup() first.")
        await self.conversation_store.delete(conversation_id)

    async def get_messages(self, conversation_id: str) -> list[Message]:
        """Get all messages for a conversation."""
        if self.conversation_store is None:
            raise RuntimeError("ConversationStore not initialized. Call startup() first.")
        return await self.conversation_store.get_messages(conversation_id)

    def _check_context_budget(self, messages: list[Message]) -> None:
        """Raise :class:`ContextOverflowError` if messages exceed the context budget.

        Used by ``chat_completion`` 鈥?for ``response``, we truncate instead.
        """
        budget = self.config.context_budget.max_context_tokens
        token_count = _estimate_tokens(messages)
        if token_count > budget:
            self.logger.error(
                "Context overflow",
                token_count=token_count,
                budget=budget,
            )
            raise ContextOverflowError(
                "Context window budget exceeded",
                token_count=token_count,
                budget=budget,
            )

    async def _truncate_to_budget(self, messages: list[Message]) -> list[Message]:
        """Truncate messages to fit within the context budget.

        Strategy: keep system messages, truncate oldest non-system
        messages first, one by one, until within budget. If only system
        messages remain and still over budget, raise
        :class:`ContextOverflowError`.
        """
        budget = self.config.context_budget.max_context_tokens
        token_count = _estimate_tokens(messages)

        if token_count <= budget:
            return messages

        self.logger.info(
            "Truncating messages to fit context budget",
            token_count=token_count,
            budget=budget,
            message_count=len(messages),
        )

        # Separate system and non-system messages
        system_msgs: list[Message] = []
        other_msgs: list[Message] = []
        for msg in messages:
            if msg.role == "system":
                system_msgs.append(msg)
            else:
                other_msgs.append(msg)

        # Remove oldest non-system messages one by one
        while other_msgs:
            other_msgs = other_msgs[1:]  # Remove oldest
            current = system_msgs + other_msgs
            if _estimate_tokens(current) <= budget:
                self.logger.info(
                    "Truncated messages",
                    removed_count=len(messages) - len(current),
                    remaining_count=len(current),
                )
                return current

        # Only system messages left 鈥?check if they fit
        if _estimate_tokens(system_msgs) > budget:
            raise ContextOverflowError(
                "System message alone exceeds context budget",
                token_count=_estimate_tokens(system_msgs),
                budget=budget,
            )

        return system_msgs
