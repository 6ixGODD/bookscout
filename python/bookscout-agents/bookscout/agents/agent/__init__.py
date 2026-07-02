"""Agent — the core abstraction for an LLM proxy with identity and tools.

An :class:`Agent` encapsulates **who it is** (name, instructions), **what it
can do** (toolset), and **how it executes** (the ``step`` method).  It does
**not** own a lifecycle — whether it runs statefully or ephemerally is
determined by how the caller constructs the :class:`AgentContext`.

Built-in capabilities (``generate_title``, ``compact``) are methods on
Agent, not tools — their invocation is decided by the Mode or the agent
itself, not by the LLM.
"""

from __future__ import annotations

import abc
import typing as t

from bookscout.core.lib.stream import AsyncStream
from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin
from bookscout.tools import BaseTool

from ..context import AgentContext
from ..context import AgentRunState
from ..context import StepResult
from ..context import TitlePair
from ..exceptions import CompactError

if t.TYPE_CHECKING:
    from bookscout.llm.types import CompletionResponse
    from bookscout.llm.types import Message
    from bookscout.llm.types import StreamEvent
    from bookscout.logging import Logger
    from bookscout.tools.toolset import Toolset

# Type alias for the instructions field — string or async callable.
PromptBuilder: t.TypeAlias = t.Callable[[AgentContext], t.Awaitable[str]]  # noqa: UP040


class Agent(LoggingMixin, AsyncResourceMixin, abc.ABC):
    """Abstract base class for all agents.

    An Agent owns its **identity** and **toolset**, but not its lifecycle
    or conversation state — those are determined by the :class:`AgentContext`
    passed at call time.

    Subclasses must implement :meth:`step`, which performs a single LLM
    invocation.  The default :meth:`run` delegates to ``step`` once;
    subclasses may override it for autonomous loops.

    Args:
        name: Unique agent name within its Mode.
        instructions: System prompt — either a static string or an async
            callable that receives the :class:`AgentContext` and returns
            the prompt text.
        toolset: The :class:`Toolset` of tools this agent can use.
        model: Optional model override.  ``None`` means use the default
            from the ChatModel config.
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

        If ``instructions`` is a string, returns it directly.  If it is a
        callable, invokes it with the current context and returns the result.
        """
        if isinstance(self.instructions, str):
            return self.instructions
        return await self.instructions(ctx)

    @abc.abstractmethod
    async def step(self, messages: list[Message], *, ctx: AgentContext) -> StepResult:
        """Execute a single LLM invocation.

        This is the only method subclasses **must** implement.  The caller
        (typically the Mode) decides whether to loop.

        Args:
            messages: The conversation messages so far.
            ctx: The execution context.

        Returns:
            A :class:`StepResult` with the assistant's output.
        """

    async def run(self, messages: list[Message], *, ctx: AgentContext) -> StepResult:
        """Execute the agent to completion.

        Default implementation: one step.  Subclasses may override for
        autonomous tool-call loops.
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
        wrap the result in a single-event stream.  Subclasses that need
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

    async def generate_title(self, messages: list[Message], *, ctx: AgentContext) -> TitlePair:
        """Generate a short and long title from recent messages.

        This is **not** a tool call — it is invoked by the Mode or REPL
        at their discretion, not by the LLM.

        Args:
            messages: Recent messages to summarize into a title.
            ctx: Execution context.

        Returns:
            A :class:`TitlePair` with ``short`` (≤20 chars) and ``long``
            titles.
        """
        from bookscout.llm.types import CompletionOptions as _Opts
        from bookscout.llm.types import SystemMessage as _Sys
        from bookscout.llm.types import UserMessage as _Usr

        # Take the last N messages to keep the prompt small
        recent = messages[-10:]
        content_parts: list[str] = []
        for msg in recent:
            content_parts.append(f"[{msg.role}] {msg.content}")  # type: ignore[union-attr]
        conversation_text = "\n".join(content_parts)

        prompt_messages: list[Message] = [
            _Sys(
                content=(
                    "Generate a title for the following conversation. "
                    "Return exactly two lines:\n"
                    "Line 1: Short title (≤20 characters)\n"
                    "Line 2: Long title (descriptive, ≤100 characters)\n"
                    "No other text."
                ),
            ),
            _Usr(content=conversation_text),
        ]
        response = await ctx.llm.chat_completion(
            prompt_messages,
            options=_Opts(max_tokens=100, temperature=0.3),
        )
        text = response["message"].content.strip()
        lines = text.split("\n", maxsplit=1)
        short = lines[0].strip()[:20] if lines else "Untitled"
        long_ = lines[1].strip()[:100] if len(lines) > 1 else short
        return TitlePair(short=short, long=long_)

    async def compact(self, *, ctx: AgentContext, max_messages: int = 20) -> None:
        """Compact the agent's conversation by summarizing older messages.

        This is **not** a tool call — it directly modifies the conversation
        store, replacing old messages with a summary.  The LLM should not
        decide when to compact; the Mode or the agent's own policy decides.

        The implementation works at the ConversationStore level, fetching
        raw MessageRows so we can delete them by their ``message_id``.

        Args:
            ctx: Execution context (must have a ``conversation_id``).
            max_messages: Keep at most this many recent messages after
                compaction.  Older messages are summarized and replaced.

        Raises:
            CompactError: If the context has no conversation ID or
                compaction fails.
        """
        from bookscout.llm.types import CompletionOptions as _Opts
        from bookscout.llm.types import SystemMessage as _Sys
        from bookscout.llm.types import UserMessage as _Usr

        if ctx.conversation_id is None:
            raise CompactError("Cannot compact a stateless (no conversation_id) context")

        ctx.agent_state = AgentRunState.COMPACTING

        try:
            conv_store = ctx.llm.conversation_store
            if conv_store is None:
                raise CompactError("ConversationStore not available")

            # Work at the raw level to get message_ids for deletion
            all_rows = await conv_store.get_messages(ctx.conversation_id)

            if len(all_rows) <= max_messages:
                self.logger.debug("No compaction needed", message_count=len(all_rows))
                return

            old_rows = all_rows[:-max_messages]

            # Build summary text from old rows
            old_text = "\n".join(f"[{r.role}] {r.content}" for r in old_rows)
            summary_messages: list[Message] = [
                _Sys(
                    content=(
                        "Summarize the following conversation history concisely. "
                        "Preserve key facts, decisions, and context. "
                        "Output only the summary, no preamble."
                    ),
                ),
                _Usr(content=old_text),
            ]
            summary_response = await ctx.llm.chat_completion(
                summary_messages,
                options=_Opts(max_tokens=500, temperature=0.3),
            )
            summary_text = summary_response["message"].content.strip()

            # Delete old messages by their message_id
            for row in old_rows:
                await conv_store.delete_message(row.message_id)

            # Insert the summary as a system message
            summary_msg = _Sys(content=f"[Conversation summary]\n{summary_text}")
            await conv_store.add_message(ctx.conversation_id, summary_msg)

            self.logger.info(
                "Compacted conversation",
                conversation_id=ctx.conversation_id,
                compacted=len(old_rows),
                kept=max_messages,
            )
        except CompactError:
            raise
        except Exception as exc:
            raise CompactError(f"Compaction failed: {exc}") from exc
        finally:
            ctx.agent_state = AgentRunState.IDLE

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
