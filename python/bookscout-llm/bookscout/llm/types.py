"""Message and data types for the LLM subsystem.

**Input types** are Pydantic ``BaseModel`` subclasses (validation, serialization).
**Output types** are ``TypedDict`` subclasses (lightweight, no validation overhead).
"""

from __future__ import annotations

import typing as t

from pydantic import BaseModel
from pydantic import Field

from .config import ThinkingConfig


class TextContent(BaseModel):
    """A text content part within a message."""

    type: t.Literal["text"] = Field(default="text", description="")
    text: str


class ImageContent(BaseModel):
    """An image content part — references a file uploaded via
    :meth:`ChatModel.upload_file`."""

    type: t.Literal["image"] = "image"
    file_id: str
    detail: t.Literal["auto", "low", "high"] = "auto"


class FileContent(BaseModel):
    """A file content part — references a file uploaded via
    :meth:`ChatModel.upload_file`."""

    type: t.Literal["file"] = "file"
    file_id: str


ContentPart = TextContent | ImageContent | FileContent
"""Union of all content parts that can appear inside a :class:`UserMessage`."""


class SystemMessage(BaseModel):
    """System instruction message."""

    role: t.Literal["system"] = "system"
    content: str


class UserMessage(BaseModel):
    """User message — plain text or multimodal content parts."""

    role: t.Literal["user"] = "user"
    content: str | list[ContentPart]


class ToolCallFunction(BaseModel):
    """Function specification within a tool call."""

    name: str
    arguments: str
    """JSON-serialised argument string."""


class ToolCall(BaseModel):
    """A single tool call emitted by the assistant."""

    call_id: str
    function: ToolCallFunction


class AssistantMessage(BaseModel):
    """Assistant response message."""

    role: t.Literal["assistant"] = "assistant"
    content: str | list[TextContent]
    tool_calls: list[ToolCall] | None = None


class ToolResultMessage(BaseModel):
    """Tool execution result message."""

    role: t.Literal["tool"] = "tool"
    tool_call_id: str
    content: str | list[str]


Message = SystemMessage | UserMessage | AssistantMessage | ToolResultMessage
"""Union of all message types accepted by :class:`ChatModel`."""


# ═══════════════════════════════════════════════════════════════════
# Tool definition
# ═══════════════════════════════════════════════════════════════════


class ToolDef(BaseModel):
    """Our own tool definition — converted from :class:`bookscout.tools.BaseTool` schema_dict."""

    name: str
    description: str
    parameters: dict[str, t.Any] | None = None


# ═══════════════════════════════════════════════════════════════════
# Per-request options
# ═══════════════════════════════════════════════════════════════════


class CompletionOptions(BaseModel):
    """Per-request options for :meth:`ChatModel.chat_completion` and friends."""

    model: str | None = None
    """Override the default model from config."""

    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    thinking: ThinkingConfig | None = None
    stream: bool = False


# ═══════════════════════════════════════════════════════════════════
# Output types (TypedDict — lightweight)
# ═══════════════════════════════════════════════════════════════════


class Usage(t.TypedDict):
    """Token usage statistics."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


class TextDelta(t.TypedDict):
    """Incremental text fragment."""

    text: str


class ToolCallDelta(t.TypedDict):
    """Incremental tool-call fragment."""

    call_id: str
    name: str
    arguments_delta: str


class ToolCallComplete(t.TypedDict):
    """Complete tool-call with full arguments."""

    call_id: str
    name: str
    arguments: str


class ToolResult(t.TypedDict):
    """Tool execution result."""

    call_id: str
    content: str | list[str]


class CompletionResponse(t.TypedDict):
    """Non-streaming completion response."""

    message: AssistantMessage
    usage: Usage
    model: str
    finish_reason: str


# ═══════════════════════════════════════════════════════════════════
# Stream event types
# ═══════════════════════════════════════════════════════════════════


class TextDeltaEvent(t.TypedDict):
    """Stream event: incremental text."""

    type: t.Literal["text_delta"]
    delta: TextDelta


class ToolCallDeltaEvent(t.TypedDict):
    """Stream event: incremental tool-call fragment."""

    type: t.Literal["tool_call_delta"]
    delta: ToolCallDelta


class ToolCallCompleteEvent(t.TypedDict):
    """Stream event: tool-call finished with full arguments."""

    type: t.Literal["tool_call_complete"]
    call: ToolCallComplete


class ToolResultEvent(t.TypedDict):
    """Stream event: tool execution result."""

    type: t.Literal["tool_result"]
    result: ToolResult


class ResponseCompleteEvent(t.TypedDict):
    """Stream event: final response with usage stats."""

    type: t.Literal["response_complete"]
    response: CompletionResponse


StreamEvent = TextDeltaEvent | ToolCallDeltaEvent | ToolCallCompleteEvent | ToolResultEvent | ResponseCompleteEvent
"""Union of all streaming event types."""
