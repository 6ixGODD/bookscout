"""Configuration models for the LLM subsystem.

All configs are Pydantic ``BaseModel`` subclasses. :class:`LLMConfig` uses a
discriminated union on ``backend.type`` to select between OpenAI and Anthropic
providers.
"""

from __future__ import annotations

import typing as t

from pydantic import BaseModel
from pydantic import Field


class OpenAIConfig(BaseModel):
    """Configuration for the OpenAI chat-completion backend."""

    type: t.Literal["openai"] = Field(
        default="openai",
        description="Discriminator — must be 'openai'.",
    )

    api_key: str | None = Field(
        default=None,
        description="OpenAI API key (optional, can also be set via OPENAI_API_KEY env var).",
    )

    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Base URL for the OpenAI API.",
    )

    organization: str | None = Field(
        default=None,
        description="OpenAI organization ID (optional).",
    )

    project: str | None = Field(
        default=None,
        description="OpenAI project ID (optional).",
    )

    model: str = Field(
        default="gpt-4o",
        description="Default model name for completions.",
    )

    default_headers: dict[str, str] | None = Field(
        default=None,
        description="Default headers to include in all requests (optional).",
    )

    default_query: dict[str, str] | None = Field(
        default=None,
        description="Default query parameters to include in all requests (optional).",
    )

    timeout: float = Field(
        default=120.0,
        description="Request timeout in seconds.",
    )

    max_retries: int = Field(
        default=3,
        description="Maximum number of retries for failed requests.",
    )


class AnthropicConfig(BaseModel):
    """Configuration for the Anthropic chat-completion backend."""

    type: t.Literal["anthropic"] = Field(
        default="anthropic",
        description="Discriminator — must be 'anthropic'.",
    )

    api_key: str | None = Field(
        default=None,
        description="Anthropic API key (optional, can also be set via ANTHROPIC_API_KEY env var).",
    )

    base_url: str = Field(
        default="https://api.anthropic.com",
        description="Base URL for the Anthropic API.",
    )

    model: str = Field(
        default="claude-sonnet-4-6-20250514",
        description="Default model name for completions.",
    )

    default_headers: dict[str, str] | None = Field(
        default=None,
        description="Default headers to include in all requests (optional).",
    )

    timeout: float = Field(
        default=120.0,
        description="Request timeout in seconds.",
    )

    max_retries: int = Field(
        default=3,
        description="Maximum number of retries for failed requests.",
    )


class ToolcallConfig(BaseModel):
    """Configuration for the tool-call execution loop."""

    max_iterations: int = Field(
        default=10,
        description="Maximum number of tool-call iterations before stopping (prevents infinite loops).",
    )


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


class ThinkingConfig(BaseModel):
    """Per-request thinking / extended-reasoning configuration.

    This is *not* a global config — it is passed inside
    :class:`CompletionOptions` on a per-request basis.
    """

    budget_tokens: int | None = Field(
        default=None,
        description="Token budget for extended thinking (Anthropic-specific). Ignored by OpenAI.",
    )

    effort: t.Literal["low", "medium", "high"] | None = Field(
        default=None,
        description=(
            "Reasoning effort level. OpenAI maps this to 'reasoning_effort'; Anthropic maps to budget_tokens ranges."
        ),
    )


class ContextBudgetConfig(BaseModel):
    """Configuration for context-window budget management."""

    max_context_tokens: int = Field(
        default=128_000,
        description="Maximum context window in tokens. Exceeding this triggers truncation or error.",
    )

    max_conversations: int = Field(
        default=100,
        description="Maximum number of conversations to retain. Oldest are cleaned up first.",
    )

    max_messages_per_conversation: int = Field(
        default=500,
        description="Maximum messages per conversation. Oldest non-system messages are pruned first.",
    )


class CacheConfig(BaseModel):
    """Configuration for prompt caching."""

    enabled: bool = Field(
        default=True,
        description=(
            "Whether to actively mark cache breakpoints on messages. Both "
            "OpenAI and Anthropic support automatic caching when enabled."
        ),
    )


class LLMConfig(BaseModel):
    """Top-level configuration for :class:`ChatModel`.

    The ``backend`` field is a discriminated union: set ``type`` to
    ``"openai"`` or ``"anthropic"`` to select the provider.
    """

    backend: t.Annotated[
        OpenAIConfig | AnthropicConfig,
        Field(discriminator="type", description="LLM backend configuration (discriminated by 'type')."),
    ]

    toolcall: ToolcallConfig = Field(
        default_factory=ToolcallConfig,
        description="Tool-call execution loop configuration.",
    )

    retry: RetryConfig = Field(
        default_factory=RetryConfig,
        description="LLM call retry configuration.",
    )

    context_budget: ContextBudgetConfig = Field(
        default_factory=ContextBudgetConfig,
        description="Context-window budget configuration.",
    )

    cache: CacheConfig = Field(
        default_factory=CacheConfig,
        description="Prompt caching configuration.",
    )

    db_uri: str = Field(
        default="sqlite+aiosqlite:///./llm.db",
        description="SQLite connection URI for conversation/message persistence. Ignored when stateless=True.",
    )

    stateless: bool = Field(
        default=False,
        description=(
            "When True, the ChatModel operates without local persistence "
            "(no SQLite, no FileStore, no ConversationStore). Only "
            "chat_completion is available; the stateful response() "
            "interface raises RuntimeError. Suitable for compile-time "
            "and other ephemeral use cases where no conversation history "
            "is needed."
        ),
    )

    max_concurrency: int = Field(
        default=10,
        description=(
            "Maximum number of concurrent LLM API calls allowed across all "
            "callers. An ``asyncio.Semaphore`` enforces this limit "
            "transparently — callers that exceed the limit wait "
            "asynchronously until a slot frees up."
        ),
    )
