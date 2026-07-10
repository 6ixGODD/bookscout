"""Agent execution context — the state container that flows through agents.

:class:`AgentContext` is the **mutable, agent-owned** context that controls
how an agent executes.  It carries conversation state, prompt parameters,
tool results, and any extra state the agent needs.  It also provides the
convenience methods for context flow between agents: ``fork``, ``handoff``,
``delegate``, and ``evolve``.

This is distinct from :class:`ModeState`, which is a **read-only snapshot**
for the REPL layer and carries no control authority.
"""

from __future__ import annotations

import copy
import enum
import typing as t

if t.TYPE_CHECKING:
    from bookscout.llm import ChatModel


class AgentRunState(enum.StrEnum):
    """Lifecycle state of an agent within a mode's run loop."""

    IDLE = "idle"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    COMPACTING = "compacting"
    PAUSED = "paused"
    DONE = "done"
    ERROR = "error"


class StepResult(t.NamedTuple):
    """Result of a single agent step."""

    text: str | None
    finish_reason: str
    usage: dict[str, int]
    tool_calls: list[dict[str, t.Any]]


_ExtraT_co = t.TypeVar("_ExtraT_co", bound=dict[str, t.Any], covariant=True)


class AgentContext:
    """Execution context for an agent — carries all state the agent needs.

    This is the **control context**: it determines how the agent runs.
    The REPL layer should read :class:`ModeState` instead.

    Args:
        llm: The ChatModel instance to use for LLM calls.
        conversation_id: Optional conversation ID for persistent agents.
            ``None`` means stateless (one-shot) execution.
        prompt_params: Parameters to render into the agent's prompt function.
        agent_state: Current run state of the agent.
        tool_results: Cache of tool-call results from this run.
        extra: Free-form extension dict for agent-specific state.
    """

    __slots__ = (
        "_agent_state",
        "_conversation_id",
        "_extra",
        "_llm",
        "_prompt_params",
        "_tool_results",
    )

    def __init__(
        self,
        *,
        llm: ChatModel,
        conversation_id: str | None = None,
        prompt_params: dict[str, t.Any] | None = None,
        agent_state: AgentRunState = AgentRunState.IDLE,
        tool_results: dict[str, t.Any] | None = None,
        extra: dict[str, t.Any] | None = None,
    ) -> None:
        self._llm = llm
        self._conversation_id = conversation_id
        self._prompt_params = prompt_params or {}
        self._agent_state = agent_state
        self._tool_results = tool_results or {}
        self._extra = extra or {}

    @property
    def llm(self) -> ChatModel:
        """The ChatModel instance for LLM calls."""
        return self._llm

    @property
    def conversation_id(self) -> str | None:
        """Conversation ID — ``None`` for stateless execution."""
        return self._conversation_id

    @conversation_id.setter
    def conversation_id(self, value: str | None) -> None:
        self._conversation_id = value

    @property
    def prompt_params(self) -> dict[str, t.Any]:
        """Parameters for the agent's prompt function."""
        return self._prompt_params

    @prompt_params.setter
    def prompt_params(self, value: dict[str, t.Any]) -> None:
        self._prompt_params = value

    @property
    def agent_state(self) -> AgentRunState:
        """Current run state of the agent."""
        return self._agent_state

    @agent_state.setter
    def agent_state(self, value: AgentRunState) -> None:
        self._agent_state = value

    @property
    def tool_results(self) -> dict[str, t.Any]:
        """Cache of tool-call results from this run."""
        return self._tool_results

    @property
    def extra(self) -> dict[str, t.Any]:
        """Free-form extension dict."""
        return self._extra

    def fork(
        self,
        *,
        conversation_id: str | None = None,
        prompt_params: dict[str, t.Any] | None = None,
        inherit_tool_results: bool = False,
        inherit_extra: bool = True,
        extra: dict[str, t.Any] | None = None,
    ) -> AgentContext:
        """Create a **derived** context — selective inheritance.

        The forked context is independent: mutations to it do not affect
        the parent, and vice versa.

        Args:
            conversation_id: Override conversation ID.  By default, forks
                get a **new** conversation (no inheritance), because a
                forked agent is a separate execution.
            prompt_params: Override prompt params.  Merged on top of
                parent's params if provided.
            inherit_tool_results: Whether to copy the parent's tool result
                cache.  Default ``False`` — forked agents start fresh.
            inherit_extra: Whether to copy the parent's extra dict.
                Default ``True`` — contextual info like current_book
                should carry over.
            extra: Additional extra entries merged on top of inherited ones.

        Returns:
            A new :class:`AgentContext` with selective inheritance.
        """
        merged_params = {**self._prompt_params}
        if prompt_params:
            merged_params.update(prompt_params)

        merged_extra: dict[str, t.Any] = {}
        if inherit_extra:
            merged_extra.update(copy.deepcopy(self._extra))
        if extra:
            merged_extra.update(extra)

        return AgentContext(
            llm=self._llm,
            conversation_id=conversation_id,
            prompt_params=merged_params,
            agent_state=AgentRunState.IDLE,
            tool_results=copy.deepcopy(self._tool_results) if inherit_tool_results else {},
            extra=merged_extra,
        )

    def handoff(
        self,
        *,
        prompt_params: dict[str, t.Any] | None = None,
        extra: dict[str, t.Any] | None = None,
    ) -> AgentContext:
        """Create a **handoff** context — full conversation, new identity.

        The conversation continues under a new agent.  The conversation ID
        is preserved, and the new agent picks up where the old one left off.

        Args:
            prompt_params: New prompt params for the receiving agent.
                Replaces the current params entirely (handoff = identity
                switch).
            extra: Additional extra entries merged on top of current ones.

        Returns:
            A new :class:`AgentContext` with the same conversation ID
            but switched identity.
        """
        merged_extra = copy.deepcopy(self._extra)
        if extra:
            merged_extra.update(extra)

        return AgentContext(
            llm=self._llm,
            conversation_id=self._conversation_id,
            prompt_params=prompt_params or {},
            agent_state=AgentRunState.IDLE,
            tool_results={},
            extra=merged_extra,
        )

    def delegate(
        self,
        *,
        task: str,
        prompt_params: dict[str, t.Any] | None = None,
        extra: dict[str, t.Any] | None = None,
    ) -> AgentContext:
        """Create a **delegation** context — a self-contained task package.

        The delegated agent gets no conversation history.  It receives a
        fresh context with the task injected via ``prompt_params``.

        Args:
            task: The task description for the delegated agent.
            prompt_params: Additional prompt params for the delegated agent.
            extra: Extra context entries for the delegated agent.

        Returns:
            A new :class:`AgentContext` with no conversation ID and the
            task injected.
        """
        params: dict[str, t.Any] = {"task": task}
        if prompt_params:
            params.update(prompt_params)

        merged_extra = copy.deepcopy(self._extra)
        if extra:
            merged_extra.update(extra)

        return AgentContext(
            llm=self._llm,
            conversation_id=None,
            prompt_params=params,
            agent_state=AgentRunState.IDLE,
            tool_results={},
            extra=merged_extra,
        )

    def evolve(
        self,
        *,
        agent_state: AgentRunState | None = None,
        prompt_params: dict[str, t.Any] | None = None,
        tool_results: dict[str, t.Any] | None = None,
        extra: dict[str, t.Any] | None = None,
    ) -> AgentContext:
        """Mutate this context in place and return ``self``.

        Unlike ``fork`` / ``handoff`` / ``delegate``, this does **not**
        create a new instance — it updates the current one.

        Args:
            agent_state: New agent run state.
            prompt_params: Merged on top of current params.
            tool_results: Merged on top of current tool results.
            extra: Merged on top of current extra.

        Returns:
            ``self``, after mutations.
        """
        if agent_state is not None:
            self._agent_state = agent_state
        if prompt_params:
            self._prompt_params.update(prompt_params)
        if tool_results:
            self._tool_results.update(tool_results)
        if extra:
            self._extra.update(extra)
        return self

    def to_dict(self) -> dict[str, t.Any]:
        """Serialize this context to a JSON-compatible dict."""
        return {
            "conversation_id": self._conversation_id,
            "prompt_params": copy.deepcopy(self._prompt_params),
            "agent_state": self._agent_state.value,
            "tool_results": copy.deepcopy(self._tool_results),
            "extra": copy.deepcopy(self._extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, t.Any], *, llm: ChatModel) -> AgentContext:
        """Deserialize a context from a dict produced by :meth:`to_dict`.

        Args:
            data: Serialized context data.
            llm: The ChatModel to inject (not serialized).

        Returns:
            A reconstructed :class:`AgentContext`.
        """
        return cls(
            llm=llm,
            conversation_id=data.get("conversation_id"),
            prompt_params=data.get("prompt_params", {}),
            agent_state=AgentRunState(data.get("agent_state", "idle")),
            tool_results=data.get("tool_results", {}),
            extra=data.get("extra", {}),
        )

    def __repr__(self) -> str:
        return (
            f"AgentContext("
            f"conversation_id={self._conversation_id!r}, "
            f"state={self._agent_state.value!r}, "
            f"params={list(self._prompt_params.keys())}, "
            f"extra={list(self._extra.keys())}"
            f")"
        )
