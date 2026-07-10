"""Mode — the orchestration layer that coordinates agents and owns conversation.

A :class:`Mode` is the unit of multi-agent collaboration. Its lifetime is
the application's lifetime. It owns the agents, coordinates context flow
between them, and manages **clean conversation history** (user + assistant
messages only — no tool calls or tool results persisted).

Key design: **The Mode owns conversation, not the LLM.** The LLM is always
stateless. Each turn, the Mode:
1. Builds clean messages (system prompt excluded — agent adds it).
2. Checks token budget and auto-compacts if needed.
3. Passes messages to the agent for execution.
4. Appends the assistant's response to the clean history.

Mode is also the **persistence boundary**: each Mode owns its internal
SQLite database for checkpoints and session metadata.
"""

from __future__ import annotations

import abc
import typing as t

import pydantic

from bookscout.core.lib.utils import gen_id
from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin
from bookscout.sqlite import SQLite
from bookscout.sqlite import SQLiteConfig

from .context import AgentContext
from .context import AgentRunState
from .context import StepResult
from .exceptions import CheckpointError
from .exceptions import ModeStartupError

if t.TYPE_CHECKING:
    from bookscout.llm import ChatModel
    from bookscout.logging import Logger

    from .agent import Agent


class ModeState(pydantic.BaseModel):
    """Read-only snapshot of a Mode's runtime state for the REPL layer.

    Attributes:
        mode_name: Name of the current mode.
        active_agent: Name of the currently active agent.
        agent_states: Mapping of agent name → run state.
        conversation_id: Current conversation ID (if any).
        phase: Current orchestration phase.
        messages: Clean conversation history (user + assistant only).
            Each item is ``{"role": "user"|"assistant", "content": str}``.
        last_tool_calls: Tool calls from the last step (for rendering).
        last_usage: Token usage from the last step.
        extra: Mode-specific extension state.
    """

    mode_name: str = ""
    active_agent: str = ""
    agent_states: dict[str, str] = pydantic.Field(default_factory=dict)
    conversation_id: str | None = None
    phase: str = "idle"
    messages: list[dict[str, str]] = pydantic.Field(default_factory=list)
    last_tool_calls: list[dict[str, t.Any]] = pydantic.Field(default_factory=list)
    last_usage: dict[str, int] = pydantic.Field(default_factory=dict)
    extra: dict[str, t.Any] = pydantic.Field(default_factory=dict)

    model_config = {"frozen": True}


class CheckpointInfo(pydantic.BaseModel):
    """Metadata for a saved checkpoint."""

    checkpoint_id: str
    mode_name: str
    created_at: float
    phase: str
    active_agent: str

    model_config = {"frozen": True}


class ModeResult(t.NamedTuple):
    """Result of a Mode handling one user input."""

    text: str | None
    step_results: list[StepResult]
    state: ModeState


class StreamChunk(t.NamedTuple):
    """A chunk yielded by :meth:`Mode.handle_stream`.

    Attributes:
        kind: "text", "status", "tool_call", "tool_result", "done".
        data: The chunk payload.
    """

    kind: str
    data: t.Any


# Auto-compact threshold: compact when token count exceeds 80% of budget.
_COMPACT_THRESHOLD = 0.8
# Messages to keep after compaction.
_COMPACT_KEEP_MESSAGES = 20


class Mode(LoggingMixin, AsyncResourceMixin, abc.ABC):
    """Abstract base class for all modes.

    A Mode is a **long-lived** orchestration unit that:
        1. Holds all Agent instances.
        2. Manages clean conversation history (user + assistant only).
        3. Auto-compacts when conversation approaches token budget.
        4. Manages its own SQLite for checkpoints.
        5. Exposes read-only :class:`ModeState` for the REPL.

    Args:
        name: Unique mode name.
        agents: Mapping of agent name → Agent instance.
        llm: The ChatModel shared by all agents (always stateless).
        db_uri: SQLite URI for the mode's internal database.
        max_context_tokens: Token budget for auto-compact.
        logger: Logger instance.
    """

    def __init__(
        self,
        *,
        name: str,
        agents: dict[str, Agent],
        llm: ChatModel,
        db_uri: str = "sqlite+aiosqlite:///./mode.db",
        max_context_tokens: int = 128_000,
        logger: Logger,
    ) -> None:
        super().__init__(logger=logger)
        self.name = name
        self.agents = agents
        self.llm = llm
        self.db_uri = db_uri
        self._max_context_tokens = max_context_tokens
        self._state = ModeState(mode_name=name)
        self._sqlite: SQLite | None = None
        # Mutable conversation history (clean: user + assistant only).
        self._messages: list[dict[str, str]] = []

    async def startup(self) -> None:
        """Start the mode: init SQLite, start agents, create schema."""
        try:
            self._sqlite = SQLite(
                config=SQLiteConfig(uri=self.db_uri),
                logger=self.logger,
            )
            await self._sqlite.startup()
            await self._create_schema()
        except Exception as exc:
            raise ModeStartupError(f"Mode {self.name!r} startup failed: {exc}") from exc

        for agent in self.agents.values():
            await agent.startup()

        agent_states = dict.fromkeys(self.agents, AgentRunState.IDLE.value)
        self._state = ModeState(
            mode_name=self.name,
            agent_states=agent_states,
        )

        await super().startup()
        self.logger.info("Mode started", mode=self.name, agents=list(self.agents.keys()))

    async def shutdown(self) -> None:
        """Shut down the mode: stop agents, close SQLite."""
        for agent in self.agents.values():
            await agent.shutdown()

        if self._sqlite is not None:
            await self._sqlite.shutdown()
            self._sqlite = None

        self.logger.info("Mode stopped", mode=self.name)

    @abc.abstractmethod
    async def handle(self, user_input: str, *, ctx: AgentContext) -> ModeResult:
        """Process one user input.

        Args:
            user_input: Raw user input string.
            ctx: The current agent context.

        Returns:
            A :class:`ModeResult` with the final output and updated state.
        """

    async def handle_stream(
        self,
        user_input: str,
        *,
        ctx: AgentContext,
    ) -> t.AsyncIterator[StreamChunk]:
        """Process user input with streaming output.

        Default: delegates to :meth:`handle` and wraps the result.
        Subclasses with true streaming should override this.
        """
        result = await self.handle(user_input, ctx=ctx)
        if result.text:
            yield StreamChunk(kind="text", data=result.text)
        yield StreamChunk(kind="done", data=result)

    @property
    def state(self) -> ModeState:
        """Read-only snapshot of the mode's current state."""
        return self._state

    def _update_state(self, **overrides: t.Any) -> None:
        """Update the internal mode state."""
        current = self._state.model_dump()
        current.update(overrides)
        self._state = ModeState(**current)

    # ------------------------------------------------------------------ conversation management

    def get_conversation_messages(self) -> list[dict[str, str]]:
        """Return the clean conversation history (user + assistant only)."""
        return list(self._messages)

    def append_user_message(self, content: str) -> None:
        """Append a user message to the conversation history."""
        self._messages.append({"role": "user", "content": content})

    def append_assistant_message(self, content: str) -> None:
        """Append an assistant message to the conversation history."""
        self._messages.append({"role": "assistant", "content": content})

    async def _maybe_auto_compact(self) -> bool:
        """Check if conversation needs compaction and do it if so.

        Compaction: if the clean conversation's token count exceeds
        80% of ``max_context_tokens``, summarize the oldest messages
        via the LLM and replace them with a compact summary.

        Returns:
            True if compaction was performed.
        """
        if len(self._messages) <= _COMPACT_KEEP_MESSAGES:
            return False

        # Estimate token count of the conversation.
        text = "\n".join(m["content"] for m in self._messages)
        token_count = self.llm.estimate_token(text)

        if token_count <= self._max_context_tokens * _COMPACT_THRESHOLD:
            return False

        self.logger.info(
            "auto-compacting conversation",
            messages=len(self._messages),
            tokens=token_count,
            budget=self._max_context_tokens,
        )

        # Split: keep the most recent messages, summarize the rest.
        old_messages = self._messages[:-_COMPACT_KEEP_MESSAGES]
        recent_messages = self._messages[-_COMPACT_KEEP_MESSAGES:]

        # Build summary text.
        old_text = "\n".join(f"[{m['role']}] {m['content']}" for m in old_messages)

        from bookscout.llm.types import CompletionOptions
        from bookscout.llm.types import SystemMessage
        from bookscout.llm.types import UserMessage

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

        # Replace old messages with a summary as a system message.
        self._messages = [
            {"role": "user", "content": f"[Conversation summary]\n{summary_text}"},
            *recent_messages,
        ]

        self.logger.info("auto-compact done", kept=len(self._messages))
        return True

    # ------------------------------------------------------------------ checkpoint

    async def save_checkpoint(self) -> str:
        """Save the current runtime state as a checkpoint."""
        if self._sqlite is None:
            raise CheckpointError("Mode database not initialized")

        checkpoint_id: str = gen_id(prefix="ckpt_")
        state_data = self._state.model_dump_json()

        import time

        await self._sqlite.exec(
            """INSERT OR REPLACE INTO checkpoint
               (checkpoint_id, mode_name, state_data, phase, active_agent, created_at)
               VALUES (:cid, :mode, :state, :phase, :agent, :ts)""",
            readonly=False,
            cid=checkpoint_id,
            mode=self.name,
            state=state_data,
            phase=self._state.phase,
            agent=self._state.active_agent,
            ts=time.time(),
        )

        self.logger.info("Checkpoint saved", checkpoint_id=checkpoint_id, mode=self.name)
        return checkpoint_id

    async def load_checkpoint(self, checkpoint_id: str) -> None:
        """Restore state from a checkpoint."""
        if self._sqlite is None:
            raise CheckpointError("Mode database not initialized")

        result = await self._sqlite.exec(
            "SELECT state_data FROM checkpoint WHERE checkpoint_id = :cid",
            readonly=True,
            cid=checkpoint_id,
        )
        row = result.fetchone()
        if row is None:
            raise CheckpointError(f"Checkpoint {checkpoint_id!r} not found")

        state_json: str = row[0]
        self._state = ModeState.model_validate_json(state_json)
        # Restore conversation from state.
        self._messages = list(self._state.messages)
        self.logger.info("Checkpoint loaded", checkpoint_id=checkpoint_id, mode=self.name)

    async def list_checkpoints(self) -> list[CheckpointInfo]:
        """List all available checkpoints for this mode."""
        if self._sqlite is None:
            return []

        result = await self._sqlite.exec(
            "SELECT checkpoint_id, mode_name, created_at, phase, active_agent FROM checkpoint ORDER BY created_at DESC",
            readonly=True,
        )
        rows = result.fetchall()
        return [
            CheckpointInfo(
                checkpoint_id=row[0],
                mode_name=row[1],
                created_at=row[2],
                phase=row[3],
                active_agent=row[4],
            )
            for row in rows
        ]

    async def _create_schema(self) -> None:
        """Create internal database tables."""
        if self._sqlite is None:
            return
        await self._sqlite.exec(
            """CREATE TABLE IF NOT EXISTS checkpoint (
                checkpoint_id TEXT PRIMARY KEY,
                mode_name TEXT NOT NULL,
                state_data TEXT NOT NULL,
                phase TEXT NOT NULL DEFAULT 'idle',
                active_agent TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            )""",
            readonly=False,
        )

    def _sync_state_messages(self) -> None:
        """Sync ``self._messages`` into ``self._state.messages``."""
        self._update_state(messages=list(self._messages))

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, agents={list(self.agents.keys())})"
