"""Mode — the orchestration layer that coordinates agents.

A :class:`Mode` is the unit of multi-agent collaboration.  Its lifetime is
the application's lifetime.  It owns the agents, coordinates context flow
between them, and exposes a read-only :class:`ModeState` for the REPL.

Mode is also the **persistence boundary**: each Mode owns its internal
SQLite database for checkpoints, conversation metadata, and state snapshots.
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

from ..context import AgentContext
from ..context import AgentRunState
from ..context import StepResult
from ..exceptions import CheckpointError
from ..exceptions import ModeStartupError

if t.TYPE_CHECKING:
    from bookscout.llm import ChatModel
    from bookscout.logging import Logger

    from ..agent import Agent


class ModeState(pydantic.BaseModel):
    """Read-only snapshot of a Mode's runtime state for the REPL layer.

    The REPL reads this to render UI.  It must **not** be used to
    influence agent behavior — that's what :class:`AgentContext` is for.

    Attributes:
        mode_name: Name of the current mode.
        active_agent: Name of the currently active agent.
        agent_states: Mapping of agent name → run state.
        conversation_id: Current conversation ID (if any).
        phase: Current orchestration phase.
        last_tool_calls: Tool calls from the last step (for rendering).
        last_usage: Token usage from the last step.
        extra: Mode-specific extension state.
    """

    mode_name: str = ""
    active_agent: str = ""
    agent_states: dict[str, str] = pydantic.Field(default_factory=dict)
    conversation_id: str | None = None
    phase: str = "idle"
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
    """The final text output to show the user."""

    step_results: list[StepResult]
    """Step results from all agents that ran."""

    state: ModeState
    """The mode state after this handle() call."""


# ═══════════════════════════════════════════════════════════════════
# Mode
# ═══════════════════════════════════════════════════════════════════


class Mode(LoggingMixin, AsyncResourceMixin, abc.ABC):
    """Abstract base class for all modes.

    A Mode is a **long-lived** orchestration unit.  Its lifetime equals
    the application's lifetime.  It:

    1. Holds all Agent instances for this mode.
    2. Coordinates context flow between agents.
    3. Manages its own SQLite database for persistence and checkpoints.
    4. Exposes a read-only :class:`ModeState` for the REPL.

    Subclasses must implement :meth:`handle`, which processes one user
    input and decides which agents to invoke and how.

    Args:
        name: Unique mode name.
        agents: Mapping of agent name → Agent instance.
        llm: The ChatModel shared by all agents in this mode.
        db_uri: SQLite URI for the mode's internal database.
        logger: Logger instance.
    """

    def __init__(
        self,
        *,
        name: str,
        agents: dict[str, Agent],
        llm: ChatModel,
        db_uri: str = "sqlite+aiosqlite:///./mode.db",
        logger: Logger,
    ) -> None:
        super().__init__(logger=logger)
        self.name = name
        self.agents = agents
        self.llm = llm
        self.db_uri = db_uri
        self._state = ModeState(mode_name=name)
        self._sqlite: SQLite | None = None

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

        # Start all agents
        for agent in self.agents.values():
            await agent.startup()

        # Update state
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

        This is the Mode's main entry point.  The implementation decides:
        1. Which agent(s) to invoke.
        2. How to flow context between them.
        3. Whether to delegate, handoff, or fork.
        4. When to return the result.

        Args:
            user_input: Raw user input string.
            ctx: The current agent context.

        Returns:
            A :class:`ModeResult` with the final output and updated state.
        """

    @property
    def state(self) -> ModeState:
        """Read-only snapshot of the mode's current state."""
        return self._state

    def _update_state(self, **overrides: t.Any) -> None:
        """Update the internal mode state (Mode-internal use only)."""
        current = self._state.model_dump()
        current.update(overrides)
        self._state = ModeState(**current)

    async def save_checkpoint(self) -> str:
        """Save the current runtime state as a checkpoint.

        Returns:
            The checkpoint ID.
        """
        if self._sqlite is None:
            raise CheckpointError("Mode database not initialized")

        checkpoint_id: str = gen_id(prefix="ckpt_")

        # We store the ModeState as the checkpoint payload.
        # The Mode subclass is responsible for tracking active AgentContexts
        # — the base checkpoint only persists ModeState.
        state_data = self._state.model_dump_json()

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
            ts=__import__("time").time(),
        )

        self.logger.info("Checkpoint saved", checkpoint_id=checkpoint_id, mode=self.name)
        return checkpoint_id

    async def load_checkpoint(self, checkpoint_id: str) -> None:
        """Restore state from a checkpoint.

        Args:
            checkpoint_id: The checkpoint to load.

        Raises:
            CheckpointError: If the checkpoint doesn't exist or loading fails.
        """
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

    async def get_conversation_titles(self) -> dict[str, t.Any]:
        """Return conversation titles for all conversations in this mode.

        External modules can call this to sync data to their own databases.
        """
        # This requires the LLM's ConversationStore — delegate to the
        # Mode subclass or the application layer for actual implementation.
        return {}

    async def get_conversation_history(self, conversation_id: str) -> list[t.Any]:
        """Return messages for a conversation.

        Args:
            conversation_id: The conversation to query.

        Returns:
            List of message dicts.
        """
        messages = await self.llm.get_messages(conversation_id)
        return [{"role": m.role, "content": m.content} for m in messages]  # type: ignore[union-attr]

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, agents={list(self.agents.keys())})"
