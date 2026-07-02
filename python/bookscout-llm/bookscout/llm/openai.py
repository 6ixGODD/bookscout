"""OpenAI chat-completion backend for :class:`ChatModel`.

Uses the ``openai`` SDK's stateless ``chat.completions.create`` endpoint
(never the stateful Responses API).  Prompt caching is enabled by marking
the last two messages with ``cache_control``.
"""

from __future__ import annotations

import base64
import typing as t

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion as OpenAIChatCompletion
from openai.types.chat import ChatCompletionChunk as OpenAIChatCompletionChunk
from openai.types.chat import ChatCompletionMessageFunctionToolCall
from sqlmodel import Field as _Field
from sqlmodel import SQLModel as _SQLModel

from bookscout.tools import BaseTool
from bookscout.tools import SchemaDict

from . import ChatModel
from .config import LLMConfig
from .config import OpenAIConfig
from .exceptions import CompletionError
from .exceptions import ModelNotSupportedError
from .types import AssistantMessage
from .types import CompletionOptions
from .types import CompletionResponse
from .types import FileContent
from .types import ImageContent
from .types import Message
from .types import ResponseCompleteEvent
from .types import StreamEvent
from .types import SystemMessage
from .types import TextContent
from .types import TextDelta
from .types import TextDeltaEvent
from .types import ToolCall
from .types import ToolCallDelta
from .types import ToolCallDeltaEvent
from .types import ToolCallFunction
from .types import ToolResultMessage
from .types import Usage
from .types import UserMessage

if t.TYPE_CHECKING:
    from bookscout.logging import Logger


# ═══════════════════════════════════════════════════════════════════
# OpenAI-specific SQLite tables
# ═══════════════════════════════════════════════════════════════════


class OpenAIConversationCacheRow(_SQLModel, table=True):
    """Maps our conversation_id to OpenAI-specific cache tracking data."""

    __tablename__ = "llm_openai_conversation_cache"

    conversation_id: str = _Field(primary_key=True)
    cache_key: str = _Field(default="")
    """Opaque cache key for prompt-cache monitoring."""


class OpenAIFileMappingRow(_SQLModel, table=True):
    """Maps our internal file_id to any OpenAI file_id (if applicable)."""

    __tablename__ = "llm_openai_file_mapping"

    internal_file_id: str = _Field(primary_key=True)
    openai_file_id: str | None = _Field(default=None)
    status: str = _Field(default="pending")


class OpenAIChatModel(
    ChatModel[
        dict[str, t.Any],
        SchemaDict,
        OpenAIChatCompletion,
        OpenAIChatCompletionChunk,
    ]
):
    """OpenAI chat-completion implementation.

    Uses the ``openai`` SDK's ``AsyncOpenAI.chat.completions.create``
    endpoint.  Prompt caching is activated by appending
    ``cache_control: {"type": "ephemeral"}`` to the last two messages.

    Args:
        logger: Logger instance.
        config: LLM configuration with ``backend`` set to :class:`OpenAIConfig`.
    """

    def __init__(self, logger: Logger, config: LLMConfig) -> None:
        super().__init__(logger=logger, config=config)
        if not isinstance(config.backend, OpenAIConfig):
            raise ValueError("OpenAIChatModel requires backend.type='openai'")
        self._backend_config = config.backend
        self._client: AsyncOpenAI | None = None

    async def _startup_provider(self) -> None:
        """Create the AsyncOpenAI client and provider-specific tables."""
        self._client = AsyncOpenAI(
            api_key=self._backend_config.api_key,
            base_url=self._backend_config.base_url,
            organization=self._backend_config.organization,
            project=self._backend_config.project,
            default_headers=self._backend_config.default_headers,
            default_query=self._backend_config.default_query,
            timeout=self._backend_config.timeout,
            max_retries=self._backend_config.max_retries,
        )
        if self.sqlite is not None:
            await self.sqlite.create_all([OpenAIConversationCacheRow, OpenAIFileMappingRow])
        self.logger.info("OpenAI client initialized")

    async def _shutdown_provider(self) -> None:
        """Close the AsyncOpenAI client."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    def _get_default_model(self) -> str:
        return self._backend_config.model

    async def _complete(
        self,
        messages: list[dict[str, t.Any]],
        tools: list[SchemaDict] | None,
        options: CompletionOptions,
    ) -> OpenAIChatCompletion:
        """Execute a non-streaming chat completion."""
        if self._client is None:
            raise RuntimeError("OpenAI client not initialized. Call startup() first.")

        model = options.model or self._backend_config.model
        kwargs = self._build_request_kwargs(messages, tools, options, model, stream=False)
        return await self._client.chat.completions.create(**kwargs)  # type: ignore[no-any-return]

    async def _complete_stream(  # type: ignore[override,misc]
        self,
        messages: list[dict[str, t.Any]],
        tools: list[SchemaDict] | None,
        options: CompletionOptions,
    ) -> t.AsyncIterator[OpenAIChatCompletionChunk]:
        """Execute a streaming chat completion.

        Awaits the SDK call (``stream=True`` returns an ``AsyncStream`` when
        awaited) and yields chunks.
        """
        if self._client is None:
            raise RuntimeError("OpenAI client not initialized. Call startup() first.")

        model = options.model or self._backend_config.model
        kwargs = self._build_request_kwargs(messages, tools, options, model, stream=True)
        # The SDK's create() is `async def`; awaiting it yields an AsyncStream.
        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            yield chunk

    async def _convert_messages(self, messages: list[Message]) -> list[t.Any]:
        """Convert our Message types to OpenAI chat-completion format."""
        result: list[dict[str, t.Any]] = []
        for msg in messages:
            if isinstance(msg, UserMessage):
                result.append(await self._convert_user_message(msg))
            elif isinstance(msg, AssistantMessage):
                result.append(self._convert_assistant_message(msg))
            elif isinstance(msg, ToolResultMessage):
                result.append(self._convert_tool_result_message(msg))
            elif isinstance(msg, SystemMessage):
                result.append({"role": "system", "content": msg.content})
        return result

    def _convert_tools(self, tools: t.Sequence[BaseTool]) -> list[SchemaDict]:
        """Convert :class:`BaseTool` instances to OpenAI function-calling format.

        Each :class:`BaseTool.schema_dict` already returns the OpenAI shape
        ``{"type": "function", "function": {"name", "description", "parameters"}}``.
        """
        return [tool.schema_dict for tool in tools]

    def _convert_response(self, raw: OpenAIChatCompletion) -> CompletionResponse:
        """Convert an OpenAI ChatCompletion to our CompletionResponse."""
        choice = raw.choices[0] if raw.choices else None
        if choice is None:
            raise CompletionError("OpenAI returned no choices")

        message = choice.message
        tool_calls: list[ToolCall] | None = None
        if message.tool_calls:
            tool_calls = [
                ToolCall(
                    call_id=tc.id,
                    function=ToolCallFunction(
                        name=tc.function.name,
                        arguments=tc.function.arguments,
                    ),
                )
                for tc in message.tool_calls
                if isinstance(tc, ChatCompletionMessageFunctionToolCall)
            ]

        assistant_msg = AssistantMessage(
            content=message.content or "",
            tool_calls=tool_calls,
        )

        usage_data = raw.usage
        usage: Usage = {
            "input_tokens": usage_data.prompt_tokens if usage_data else 0,
            "output_tokens": usage_data.completion_tokens if usage_data else 0,
            "cache_read_tokens": (
                getattr(usage_data, "prompt_tokens_details", None)
                and (usage_data.prompt_tokens_details.cached_tokens if usage_data.prompt_tokens is not None else 0)  # type: ignore[union-attr]
            )
            or 0,
            "cache_write_tokens": 0,
        }

        return CompletionResponse(
            message=assistant_msg,
            usage=usage,
            model=raw.model,
            finish_reason=choice.finish_reason or "stop",  # type: ignore[unreachable]
        )

    def _convert_chunk(self, raw: OpenAIChatCompletionChunk) -> StreamEvent | None:
        """Convert an OpenAI stream chunk to our StreamEvent."""
        # The final chunk (with stream_options.include_usage=True) carries
        # usage and an empty choices list (or a choice with finish_reason).
        usage = getattr(raw, "usage", None)
        if usage is not None and not raw.choices:
            return ResponseCompleteEvent(
                type="response_complete",
                response=CompletionResponse(
                    message=AssistantMessage(content=""),
                    usage=Usage(
                        input_tokens=getattr(usage, "prompt_tokens", 0),
                        output_tokens=getattr(usage, "completion_tokens", 0),
                        cache_read_tokens=(
                            (
                                getattr(usage, "prompt_tokens_details", None)
                                and getattr(usage.prompt_tokens_details, "cached_tokens", 0)
                            )
                            or 0
                        ),
                        cache_write_tokens=0,
                    ),
                    model=getattr(raw, "model", "") or "",
                    finish_reason="stop",
                ),
            )

        if not raw.choices:
            return None

        choice = raw.choices[0]
        delta = choice.delta

        # Text delta
        if delta.content is not None:
            return TextDeltaEvent(
                type="text_delta",
                delta=TextDelta(text=delta.content),
            )

        # Tool call delta
        if delta.tool_calls:
            tc_delta = delta.tool_calls[0]
            event: StreamEvent | None = None
            if tc_delta.function:
                if tc_delta.function.name:
                    event = ToolCallDeltaEvent(
                        type="tool_call_delta",
                        delta=ToolCallDelta(
                            call_id=tc_delta.id or "",
                            name=tc_delta.function.name,
                            arguments_delta=tc_delta.function.arguments or "",
                        ),
                    )
                elif tc_delta.function.arguments:
                    event = ToolCallDeltaEvent(
                        type="tool_call_delta",
                        delta=ToolCallDelta(
                            call_id=tc_delta.id or "",
                            name="",
                            arguments_delta=tc_delta.function.arguments,
                        ),
                    )
            return event

        return None

    def _apply_cache_control(self, messages: list[dict[str, t.Any]]) -> list[dict[str, t.Any]]:
        """OpenAI applies prompt caching automatically — no per-message markers.

        OpenAI's automatic prompt caching activates for prompts ≥ 1024 tokens
        without any explicit ``cache_control`` field (that concept is
        Anthropic-specific).  Adding it would be an unknown field.  We return
        the messages unchanged.
        """
        return messages

    def _build_request_kwargs(
        self,
        messages: list[dict[str, t.Any]],
        tools: list[SchemaDict] | None,
        options: CompletionOptions,
        model: str,
        *,
        stream: bool,
    ) -> dict[str, t.Any]:
        """Build the keyword arguments for the OpenAI API call."""
        kwargs: dict[str, t.Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }

        # Request usage stats in the final chunk when streaming.
        if stream:
            kwargs["stream_options"] = {"include_usage": True}

        if tools:
            kwargs["tools"] = tools

        if options.temperature is not None:
            kwargs["temperature"] = options.temperature
        if options.max_tokens is not None:
            kwargs["max_tokens"] = options.max_tokens
        if options.top_p is not None:
            kwargs["top_p"] = options.top_p
        if options.stop is not None:
            kwargs["stop"] = options.stop

        # Thinking / reasoning effort
        if options.thinking and options.thinking.effort:
            kwargs["reasoning_effort"] = options.thinking.effort

        return kwargs

    async def _convert_user_message(self, msg: UserMessage) -> dict[str, t.Any]:
        """Convert a UserMessage to OpenAI format."""
        if isinstance(msg.content, str):
            return {"role": "user", "content": msg.content}

        # Multimodal content — resolve file references on demand
        parts: list[dict[str, t.Any]] = []
        for part in msg.content:
            if isinstance(part, TextContent):
                parts.append({"type": "text", "text": part.text})
            elif isinstance(part, ImageContent):
                parts.append(await self._convert_image_content(part))
            elif isinstance(part, FileContent):
                parts.append(await self._convert_file_content(part))

        return {"role": "user", "content": parts}

    async def _convert_image_content(self, content: ImageContent) -> dict[str, t.Any]:
        """Convert ImageContent to OpenAI image_url format.

        Resolves the file on demand from FileStore.
        """
        row = await self.llm_file_store.get(content.file_id)  # type: ignore[union-attr]
        data_bytes = await self.filestore.download(row.filestore_key)  # type: ignore[union-attr]
        mime_type = row.mime_type or "image/png"

        b64 = base64.b64encode(data_bytes).decode("ascii")
        data_uri = f"data:{mime_type};base64,{b64}"

        return {
            "type": "image_url",
            "image_url": {
                "url": data_uri,
                "detail": content.detail,
            },
        }

    async def _convert_file_content(self, content: FileContent) -> dict[str, t.Any]:
        """Convert FileContent to OpenAI format.

        Resolves the file on demand from FileStore.  OpenAI's chat
        completion API does not natively support arbitrary file attachments.
        We include text-based files as text content; others raise
        :class:`ModelNotSupportedError`.
        """
        row = await self.llm_file_store.get(content.file_id)  # type: ignore[union-attr]
        data_bytes = await self.filestore.download(row.filestore_key)  # type: ignore[union-attr]
        mime_type = row.mime_type or "application/octet-stream"

        if mime_type.startswith("text/"):
            text = data_bytes.decode("utf-8") if isinstance(data_bytes, bytes) else str(data_bytes)
            return {"type": "text", "text": text}

        raise ModelNotSupportedError(
            f"OpenAI chat completion does not support file type '{mime_type}'. "
            f"Model '{self._backend_config.model}' may not support this file format."
        )

    @staticmethod
    def _convert_assistant_message(msg: AssistantMessage) -> dict[str, t.Any]:
        """Convert an AssistantMessage to OpenAI format."""
        result: dict[str, t.Any] = {"role": "assistant"}

        if isinstance(msg.content, str):
            result["content"] = msg.content
        elif isinstance(msg.content, list):
            # Multiple text blocks → concatenate
            result["content"] = "".join(part.text for part in msg.content if isinstance(part, TextContent))

        if msg.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.call_id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]

        return result

    @staticmethod
    def _convert_tool_result_message(msg: ToolResultMessage) -> dict[str, t.Any]:
        """Convert a ToolResultMessage to OpenAI format."""
        content: str
        if isinstance(msg.content, str):
            content = msg.content
        elif isinstance(msg.content, list):
            content = "\n".join(msg.content)
        else:
            content = str(msg.content)  # type: ignore[unreachable]

        return {
            "role": "tool",
            "tool_call_id": msg.tool_call_id,
            "content": content,
        }
