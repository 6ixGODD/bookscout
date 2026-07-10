"""Anthropic chat-completion backend for :class:`ChatModel`.

Uses the ``anthropic`` SDK's stateless ``messages.create`` endpoint.
Prompt caching is enabled by adding ``cache_control`` markers on the
system prompt and the last user message.
"""

from __future__ import annotations

import base64
import typing as t

from sqlmodel import Field as _Field
from sqlmodel import SQLModel as _SQLModel

from bookscout.tools import BaseTool

from . import ChatModel
from .config import AnthropicConfig
from .config import LLMConfig
from .types import AssistantMessage
from .types import CompletionOptions
from .types import CompletionResponse
from .types import FileContent
from .types import ImageContent
from .types import Message
from .types import ResponseCompleteEvent
from .types import StreamEvent
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
    from anthropic import AsyncAnthropic

    from bookscout.logging import Logger


class AnthropicToolDef(t.TypedDict):
    """Anthropic tool definition format.

    See: https://docs.anthropic.com/en/docs/build-with-claude/tool-use
    """

    name: str
    description: str
    input_schema: dict[str, t.Any]


class AnthropicConversationCacheRow(_SQLModel, table=True):
    """Maps our conversation_id to Anthropic cache-breakpoint tracking data."""

    __tablename__ = "llm_anthropic_conversation_cache"

    conversation_id: str = _Field(primary_key=True)
    cache_key: str = _Field(default="")
    """Opaque cache key for prompt-cache monitoring."""


class AnthropicFileMappingRow(_SQLModel, table=True):
    """Maps our internal file_id to any Anthropic file_id (if applicable)."""

    __tablename__ = "llm_anthropic_file_mapping"

    internal_file_id: str = _Field(primary_key=True)
    anthropic_file_id: str | None = _Field(default=None)
    status: str = _Field(default="pending")


class AnthropicChatModel(
    ChatModel[
        dict[str, t.Any],
        AnthropicToolDef,
        t.Any,
        t.Any,
    ]
):
    """Anthropic chat-completion implementation.

    Uses the ``anthropic`` SDK's ``AsyncAnthropic.messages.create``
    endpoint.  Prompt caching is activated by adding
    ``cache_control: {"type": "ephemeral"}`` markers on the system
    prompt and the last user message.

    Args:
        logger: Logger instance.
        config: LLM configuration with ``backend`` set to
            :class:`AnthropicConfig`.
    """

    def __init__(self, logger: Logger, config: LLMConfig) -> None:
        super().__init__(logger=logger, config=config)
        if not isinstance(config.backend, AnthropicConfig):
            raise ValueError("AnthropicChatModel requires backend.type='anthropic'")
        self._backend_config = config.backend
        self._client: AsyncAnthropic | None = None

    async def _startup_provider(self) -> None:
        """Create the AsyncAnthropic client and provider-specific tables."""
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(
            api_key=self._backend_config.api_key,
            base_url=self._backend_config.base_url,
            default_headers=self._backend_config.default_headers,
            timeout=self._backend_config.timeout,
            max_retries=self._backend_config.max_retries,
        )
        if self.sqlite is not None:
            await self.sqlite.create_all([AnthropicConversationCacheRow, AnthropicFileMappingRow])
        self.logger.info("Anthropic client initialized")

    async def _shutdown_provider(self) -> None:
        """Close the AsyncAnthropic client."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    def _get_default_model(self) -> str:
        return self._backend_config.model

    async def _complete(
        self,
        messages: list[dict[str, t.Any]],
        tools: list[AnthropicToolDef] | None,
        options: CompletionOptions,
    ) -> t.Any:
        """Execute a non-streaming chat completion."""
        if self._client is None:
            raise RuntimeError("Anthropic client not initialized. Call startup() first.")

        model = options.model or self._backend_config.model
        kwargs = self._build_request_kwargs(messages, tools, options, model)
        return await self._client.messages.create(**kwargs)

    async def _complete_stream(  # type: ignore[override,misc]
        self,
        messages: list[dict[str, t.Any]],
        tools: list[AnthropicToolDef] | None,
        options: CompletionOptions,
    ) -> t.AsyncIterator[t.Any]:
        """Execute a streaming chat completion.

        Yields Anthropic stream events. This is an async generator.
        """
        if self._client is None:
            raise RuntimeError("Anthropic client not initialized. Call startup() first.")

        model = options.model or self._backend_config.model
        kwargs = self._build_request_kwargs(messages, tools, options, model)

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                yield event

    async def _convert_messages(
        self,
        messages: list[Message],
    ) -> list[dict[str, t.Any]]:
        """Convert our Message types to Anthropic messages format.

        Anthropic separates system messages from the message list —
        the system prompt is passed as a top-level parameter.
        We still include it in the returned list for consistency;
        the ``_build_request_kwargs`` method extracts it.
        """
        result: list[dict[str, t.Any]] = []
        for msg in messages:
            if isinstance(msg, UserMessage):
                result.append(await self._convert_user_message(msg))
            elif isinstance(msg, AssistantMessage):
                result.append(self._convert_assistant_message(msg))
            elif isinstance(msg, ToolResultMessage):
                result.append(self._convert_tool_result_message(msg))
            else:
                # SystemMessage — Anthropic handles these separately
                result.append({"role": "system", "content": msg.content})
        return result

    def _convert_tools(self, tools: t.Sequence[BaseTool]) -> list[AnthropicToolDef]:
        """Convert :class:`BaseTool` instances to Anthropic tool format.

        Anthropic tools take ``{name, description, input_schema}`` (no ``type``
        wrapper). The input schema is the tool's parameter JSON Schema, read
        from :attr:`BaseTool.schema_dict`.
        """
        result: list[AnthropicToolDef] = []
        for tool in tools:
            schema = tool.schema_dict["function"]["parameters"]
            input_schema: dict[str, t.Any] = {"type": "object"}
            if schema:
                input_schema.update(schema)
            result.append(
                AnthropicToolDef(
                    name=tool.__function_name__,
                    description=tool.__function_description__,
                    input_schema=input_schema,
                )
            )
        return result

    def _convert_response(self, raw: t.Any) -> CompletionResponse:
        """Convert an Anthropic Message to our CompletionResponse."""
        # Extract text content
        text_parts: list[str] = []
        tool_calls: list[ToolCall] | None = None
        finish_reason = "stop"

        for block in raw.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                # block.input is a parsed dict from the Anthropic SDK —
                # serialise to a JSON string for our ToolCallFunction.arguments.
                if isinstance(block.input, str):
                    arguments = block.input
                else:
                    import json

                    arguments = json.dumps(block.input)
                tool_calls.append(
                    ToolCall(
                        call_id=block.id,
                        function=ToolCallFunction(
                            name=block.name,
                            arguments=arguments,
                        ),
                    )
                )

        if tool_calls:
            finish_reason = "tool_calls"

        content = "\n".join(text_parts) if text_parts else ""
        assistant_msg = AssistantMessage(content=content, tool_calls=tool_calls)

        # Extract usage
        usage: Usage = {
            "input_tokens": raw.usage.input_tokens if raw.usage else 0,
            "output_tokens": raw.usage.output_tokens if raw.usage else 0,
            "cache_read_tokens": getattr(raw.usage, "cache_read_input_tokens", 0) if raw.usage else 0,
            "cache_write_tokens": getattr(raw.usage, "cache_creation_input_tokens", 0) if raw.usage else 0,
        }

        stop_reason = raw.stop_reason or "end_turn"
        return CompletionResponse(
            message=assistant_msg,
            usage=usage,
            model=raw.model,
            finish_reason=finish_reason if finish_reason == "tool_calls" else stop_reason,
        )

    def _convert_chunk(self, raw: t.Any) -> StreamEvent | None:
        """Convert an Anthropic stream event to our StreamEvent."""
        event_type = getattr(raw, "type", None)

        if event_type == "content_block_delta":
            delta = raw.delta
            if delta.type == "text_delta":
                return TextDeltaEvent(
                    type="text_delta",
                    delta=TextDelta(text=delta.text),
                )
            if delta.type == "input_json_delta":
                return ToolCallDeltaEvent(
                    type="tool_call_delta",
                    delta=ToolCallDelta(
                        call_id="",
                        name="",
                        arguments_delta=delta.partial_json,
                    ),
                )
            return None

        if event_type == "content_block_start":
            block = raw.content_block
            if block.type == "tool_use":
                return ToolCallDeltaEvent(
                    type="tool_call_delta",
                    delta=ToolCallDelta(
                        call_id=block.id,
                        name=block.name,
                        arguments_delta="",
                    ),
                )
            return None

        if event_type == "content_block_stop":
            # Could emit a ToolCallCompleteEvent if we tracked accumulated args
            return None

        if event_type == "message_stop":
            return None

        if event_type == "message_delta":
            # message_delta carries the final stop_reason and cumulative usage.
            stop_reason = getattr(raw, "delta", None)
            usage_obj = getattr(raw, "usage", None)
            finish = ""
            if stop_reason is not None:
                reason = getattr(stop_reason, "stop_reason", None)
                if reason == "tool_use":
                    finish = "tool_calls"
                elif reason:
                    finish = reason
            if usage_obj is not None:
                return ResponseCompleteEvent(
                    type="response_complete",
                    response=CompletionResponse(
                        message=AssistantMessage(content=""),
                        usage=Usage(
                            input_tokens=getattr(usage_obj, "input_tokens", 0),
                            output_tokens=getattr(usage_obj, "output_tokens", 0),
                            cache_read_tokens=getattr(usage_obj, "cache_read_input_tokens", 0),
                            cache_write_tokens=getattr(usage_obj, "cache_creation_input_tokens", 0),
                        ),
                        model="",
                        finish_reason=finish,
                    ),
                )
            return None

        return None

    def _apply_cache_control(self, messages: list[dict[str, t.Any]]) -> list[dict[str, t.Any]]:
        """Apply Anthropic prompt-cache markers.

        Anthropic requires explicit ``cache_control`` markers. We mark:
            1. The system message (if present)
            2. The last user or tool-result message

        Minimum 1024 tokens per cached prefix for caching to activate.
        """
        if not messages:
            return messages

        result = list(messages)

        # Mark system message with cache_control
        for i, msg in enumerate(result):
            if msg.get("role") == "system":
                result[i] = self._add_cache_control_to_message(msg)
                break

        # Mark the last user or tool-result message
        for i in range(len(result) - 1, -1, -1):
            role = result[i].get("role")
            if role in ("user", "tool"):
                result[i] = self._add_cache_control_to_message(result[i])
                break

        return result

    @staticmethod
    def _build_request_kwargs(
        messages: list[dict[str, t.Any]],
        tools: list[AnthropicToolDef] | None,
        options: CompletionOptions,
        model: str,
    ) -> dict[str, t.Any]:
        """Build the keyword arguments for the Anthropic API call."""
        # Extract system messages from the message list. Anthropic takes the
        # system prompt as a top-level ``system`` parameter (a string or a list
        # of content blocks), separate from the conversational messages.
        system_content: list[dict[str, t.Any]] | None = None
        non_system: list[dict[str, t.Any]] = []

        for msg in messages:
            if msg.get("role") == "system":
                if system_content is None:
                    system_content = []
                content = msg.get("content", "")
                if isinstance(content, list):
                    # Already a list of content blocks (cache_control applied).
                    system_content.extend(content)
                else:
                    # Plain string system prompt.
                    content_entry: dict[str, t.Any] = {"type": "text", "text": content}
                    if "cache_control" in msg:
                        content_entry["cache_control"] = msg["cache_control"]
                    system_content.append(content_entry)
            else:
                non_system.append(msg)

        kwargs: dict[str, t.Any] = {
            "model": model,
            "messages": non_system,
            "max_tokens": options.max_tokens or 4096,
        }

        if system_content is not None:
            kwargs["system"] = system_content

        if tools:
            # Anthropic Messages API tool shape: {name, description, input_schema}.
            # No wrapping/`type` field — that is a Managed Agents concept.
            kwargs["tools"] = list(tools)

        if options.temperature is not None:
            kwargs["temperature"] = options.temperature
        if options.top_p is not None:
            kwargs["top_p"] = options.top_p
        if options.stop is not None:
            kwargs["stop_sequences"] = options.stop

        # Thinking / extended reasoning. Only emit when explicitly configured.
        if options.thinking and options.thinking.budget_tokens is not None:
            # budget_tokens must be less than max_tokens — clamp to be safe.
            budget = min(options.thinking.budget_tokens, kwargs["max_tokens"] - 1)
            if budget > 0:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}

        return kwargs

    async def _convert_user_message(
        self,
        msg: UserMessage,
    ) -> dict[str, t.Any]:
        """Convert a UserMessage to Anthropic format."""
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

    async def _convert_image_content(
        self,
        content: ImageContent,
    ) -> dict[str, t.Any]:
        """Convert ImageContent to Anthropic image block format.

        Resolves the file on demand from FileStore.
        """
        row = await self.llm_file_store.get(content.file_id)  # type: ignore[union-attr]
        data_bytes = await self.filestore.download(row.filestore_key)  # type: ignore[union-attr]
        mime_type = row.mime_type or "image/png"

        b64_data = base64.b64encode(data_bytes).decode("ascii")

        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime_type,
                "data": b64_data,
            },
        }

    async def _convert_file_content(
        self,
        content: FileContent,
    ) -> dict[str, t.Any]:
        """Convert FileContent to Anthropic document block format.

        Resolves the file on demand from FileStore.
        """
        row = await self.llm_file_store.get(content.file_id)  # type: ignore[union-attr]
        data_bytes = await self.filestore.download(row.filestore_key)  # type: ignore[union-attr]
        mime_type = row.mime_type or "application/octet-stream"

        b64_data = base64.b64encode(data_bytes).decode("ascii")

        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": mime_type,
                "data": b64_data,
            },
        }

    @staticmethod
    def _convert_assistant_message(msg: AssistantMessage) -> dict[str, t.Any]:
        """Convert an AssistantMessage to Anthropic format."""
        content: list[dict[str, t.Any]] = []

        if isinstance(msg.content, str):
            if msg.content:
                content.append({"type": "text", "text": msg.content})
        elif isinstance(msg.content, list):
            for part in msg.content:
                if isinstance(part, TextContent):
                    content.append({"type": "text", "text": part.text})

        result: dict[str, t.Any] = {"role": "assistant", "content": content}

        if msg.tool_calls:
            for tc in msg.tool_calls:
                # Parse arguments back to dict for Anthropic
                import json

                try:
                    input_dict = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    input_dict = {}

                content.append({
                    "type": "tool_use",
                    "id": tc.call_id,
                    "name": tc.function.name,
                    "input": input_dict,
                })

        return result

    @staticmethod
    def _convert_tool_result_message(msg: ToolResultMessage) -> dict[str, t.Any]:
        """Convert a ToolResultMessage to Anthropic format."""
        if isinstance(msg.content, str):
            content_value: str | list[dict[str, str]] = msg.content
        elif isinstance(msg.content, list):
            content_value = [{"type": "text", "text": item} for item in msg.content]
        else:
            content_value = [{"type": "text", "text": str(msg.content)}]  # type: ignore[unreachable]

        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id,
                    "content": content_value,
                }
            ],
        }

    @staticmethod
    def _add_cache_control_to_message(msg: dict[str, t.Any]) -> dict[str, t.Any]:
        """Add cache_control to a message, handling both simple and complex
        content."""
        result = dict(msg)

        # If content is a string, convert to content block list
        if isinstance(result.get("content"), str):
            text = result["content"]
            result["content"] = [
                {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}},
            ]
        elif isinstance(result.get("content"), list):
            # Add cache_control to the last content block
            content_list = [dict(block) for block in result["content"]]
            if content_list:
                last_block = dict(content_list[-1])
                last_block["cache_control"] = {"type": "ephemeral"}
                content_list[-1] = last_block
            result["content"] = content_list

        return result
