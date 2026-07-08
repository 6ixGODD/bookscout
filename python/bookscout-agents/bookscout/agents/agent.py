"""Agent — the core abstraction for an LLM proxy with identity and tools.

An :class:`Agent` encapsulates **who it is** (name, instructions), **what it
can do** (toolset), and **how it executes** (the ``step`` method).

Key design principle: **Agent does NOT own conversation history.**
The :class:`Mode` owns and manages clean conversation messages (user +
assistant only, no tool calls or tool results). Each invocation, the Mode
passes the current message list to the agent, which builds a system prompt
and calls the LLM statelessly.

The LLM is **always stateless** — no conversation_store, no SQLite persistence.
Conversation management (history, auto-compact, truncation) is the Mode's job.
"""

from __future__ import annotations

import abc
import typing as t

from bookscout.core.lib.stream import AsyncStream
from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin
from bookscout.tools import BaseTool

from .context import AgentContext
from .context import AgentRunState
from .context import StepResult

if t.TYPE_CHECKING:
    from bookscout.llm.types import Message
    from bookscout.llm.types import StreamEvent
    from bookscout.logging import Logger
    from bookscout.tools.toolset import Toolset

# Type alias for the instructions field — string or async callable.
PromptBuilder: t.TypeAlias = t.Callable[[AgentContext], t.Awaitable[str]]  # noqa: UP040


class Agent(LoggingMixin, AsyncResourceMixin, abc.ABC):
    """Abstract base class for all agents.

    An Agent owns its **identity** and **toolset**. It does NOT own
    conversation state — the :class:`Mode` passes messages each call.

    Subclasses must implement :meth:`step`, which performs a single LLM
    invocation. The default :meth:`run` delegates to ``step`` once.

    Args:
        name: Unique agent name within its Mode.
        instructions: System prompt — either a static string or an async
            callable that receives the :class:`AgentContext` and returns
            the prompt text.
        toolset: The :class:`Toolset` of tools this agent can use.
        model: Optional model override.
        logger: Logger instance.
    """

    def __init__(
        self,
        *,
        name: str,
        instructions: str | PromptBuilder,
        toolset: Toolset | None = None,
        model: str | None = None,
        logger: Logger,
    ) -> None:
        super().__init__(logger=logger)
        self.name = name
        self.instructions = instructions
        self.toolset = toolset
        self.model = model

    async def startup(self) -> None:
        """Start the agent (and its toolset, if any)."""
        if self.toolset is not None:
            await self.toolset.startup()
        await super().startup()
        self.logger.info("Agent started", agent=self.name)

    async def shutdown(self) -> None:
        """Shut down the agent (and its toolset, if any)."""
        if self.toolset is not None:
            await self.toolset.shutdown()
        self.logger.info("Agent stopped", agent=self.name)

    async def build_system_prompt(self, ctx: AgentContext) -> str:
        """Build the system prompt from instructions.

        If ``instructions`` is a string, returns it directly. If it is a
        callable, invokes it with the current context and returns the result.
        """
        if isinstance(self.instructions, str):
            return self.instructions
        return await self.instructions(ctx)

    @abc.abstractmethod
    async def step(self, messages: list[Message], *, ctx: AgentContext) -> StepResult:
        """Execute a single LLM invocation.

        Args:
            messages: Clean conversation messages (user + assistant only).
                The system prompt is NOT included — the agent adds it.
            ctx: The execution context.

        Returns:
            A :class:`StepResult` with the assistant's output.
        """

    async def run(self, messages: list[Message], *, ctx: AgentContext) -> StepResult:
        """Execute the agent to completion.

        Default implementation: one step. The Mode manages conversation
        history and auto-compact — the agent just executes one turn.
        """
        ctx.agent_state = AgentRunState.RUNNING
        try:
            result = await self.step(messages, ctx=ctx)
        except Exception:
            ctx.agent_state = AgentRunState.ERROR
            raise
        ctx.agent_state = AgentRunState.DONE
        return result

    async def run_stream(
        self,
        messages: list[Message],
        *,
        ctx: AgentContext,
    ) -> AsyncStream[StreamEvent]:
        """Execute the agent with streaming output.

        Default implementation: fall back to non-streaming ``run`` and
        wrap the result in a single-event stream. Subclasses that need
        true streaming should override this.
        """
        from bookscout.llm.types import AssistantMessage
        from bookscout.llm.types import ResponseCompleteEvent
        from bookscout.llm.types import Usage as UsageTD

        result = await self.run(messages, ctx=ctx)
        usage: UsageTD = {  # type: ignore[typeddict-item]
            "input_tokens": result.usage.get("input_tokens", 0),
            "output_tokens": result.usage.get("output_tokens", 0),
            "cache_read_tokens": result.usage.get("cache_read_tokens", 0),
            "cache_write_tokens": result.usage.get("cache_write_tokens", 0),
        }
        from bookscout.llm.types import CompletionResponse

        response: CompletionResponse = {  # type: ignore[typeddict-item]
            "message": AssistantMessage(content=result.text or ""),
            "usage": usage,
            "model": "",
            "finish_reason": result.finish_reason,
        }
        event: ResponseCompleteEvent = {"type": "response_complete", "response": response}  # type: ignore[typeddict-item]

        async def _gen() -> t.AsyncIterator[StreamEvent]:
            yield event  # type: ignore[misc]

        return AsyncStream(_gen())

    @property
    def tools(self) -> list[BaseTool]:
        """All tools in this agent's toolset."""
        if self.toolset is None:
            return []
        return list(self.toolset.tools)

    def __repr__(self) -> str:
        tool_count = len(self.tools)
        instr_type = "str" if isinstance(self.instructions, str) else "callable"
        return f"{self.__class__.__name__}(name={self.name!r}, tools={tool_count}, instructions={instr_type})"

    __str__ = __repr__
