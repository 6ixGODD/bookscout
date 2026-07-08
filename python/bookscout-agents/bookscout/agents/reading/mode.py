"""Reading mode — single-agent orchestration with conversation management.

The Mode owns clean conversation history (user + assistant only).
Each turn:
1. Append user message to history.
2. Auto-compact if needed.
3. Build clean messages and pass to agent.
4. Append assistant response to history.
5. Update ModeState with messages for REPL.
"""

from __future__ import annotations

import typing as t

from bookscout.agents.context import AgentContext
from bookscout.agents.context import AgentRunState
from bookscout.agents.mode import Mode
from bookscout.agents.mode import ModeResult
from bookscout.agents.mode import StreamChunk
from bookscout.llm.types import UserMessage

from .agent import ReadingAgent
from .config import ReadingModeConfig
from .session import ReadingSessionRepository
from .toolset import ReadingAgentToolset

if t.TYPE_CHECKING:
    from bookscout.books import BooksStore
    from bookscout.embedding import EmbeddingSystem
    from bookscout.llm import ChatModel
    from bookscout.logging import Logger


class ReadingMode(Mode):
    """Single-agent mode for interactive book reading.

    The Mode manages clean conversation history and auto-compact.
    The LLM is always stateless.
    """

    def __init__(
        self,
        *,
        config: ReadingModeConfig,
        llm: ChatModel,
        embedding: EmbeddingSystem,
        logger: Logger,
        book_id: str,
        registry: t.Any,
        books_store: BooksStore,
    ) -> None:
        self.config = config
        self._session_repo: ReadingSessionRepository | None = None
        toolset = ReadingAgentToolset(
            config=config,
            llm=llm,
            embedding=embedding,
            logger=logger,
            book_id=book_id,
            registry=registry,
            books_store=books_store,
        )
        agent = ReadingAgent(toolset=toolset, profiles=config.llm_profiles, logger=logger)
        super().__init__(
            name="reading",
            agents={agent.name: agent},
            llm=llm,
            db_uri=config.db_uri,
            logger=logger,
        )

    @property
    def session_repo(self) -> ReadingSessionRepository:
        if self._session_repo is None:
            raise RuntimeError("ReadingMode has not started")
        return self._session_repo

    async def startup(self) -> None:
        await super().startup()
        if self._sqlite is None:
            raise RuntimeError("Mode database not initialized")
        self._session_repo = ReadingSessionRepository(self._sqlite)

    async def _create_schema(self) -> None:
        await super()._create_schema()
        if self._sqlite is not None:
            await ReadingSessionRepository(self._sqlite).create_schema()

    async def handle(self, user_input: str, *, ctx: AgentContext) -> ModeResult:
        """Handle one user input (non-streaming)."""
        agent = self.agents["reading_agent"]
        assert isinstance(agent, ReadingAgent)

        self._update_state(phase="preparing", active_agent=agent.name)
        self.append_user_message(user_input)
        await self._maybe_auto_compact(ctx)

        # Build clean messages for the agent.
        from bookscout.llm.types import AssistantMessage

        clean_messages: list[AssistantMessage | UserMessage] = []
        for msg in self._messages:
            if msg["role"] == "user":
                clean_messages.append(UserMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                clean_messages.append(AssistantMessage(content=msg["content"]))

        ctx.extra.update({"book_id": self.config.book_id})

        self._update_state(
            phase="running_agent",
            agent_states={agent.name: AgentRunState.RUNNING.value},
        )

        try:
            result = await agent.run(clean_messages, ctx=ctx)
        except Exception:
            self._update_state(
                phase="error",
                agent_states={agent.name: AgentRunState.ERROR.value},
            )
            raise

        # Append assistant response to clean history.
        response_text = result.text or ""
        self.append_assistant_message(response_text)

        self._sync_state_messages()
        self._update_state(
            phase="done",
            agent_states={agent.name: AgentRunState.DONE.value},
            last_tool_calls=result.tool_calls,
            last_usage=result.usage,
        )
        return ModeResult(text=response_text, step_results=[result], state=self.state)

    async def handle_stream(
        self,
        user_input: str,
        *,
        ctx: AgentContext,
    ) -> t.AsyncIterator[StreamChunk]:
        """Handle user input with true streaming output."""
        agent = self.agents["reading_agent"]
        assert isinstance(agent, ReadingAgent)

        self._update_state(phase="preparing", active_agent=agent.name)
        yield StreamChunk(kind="status", data={"phase": "preparing", "agent": agent.name})

        self.append_user_message(user_input)
        compacted = await self._maybe_auto_compact(ctx)
        if compacted:
            yield StreamChunk(kind="status", data={"phase": "auto_compacted"})

        # Build clean messages.
        from bookscout.llm.types import AssistantMessage

        clean_messages: list[AssistantMessage | UserMessage] = []
        for msg in self._messages:
            if msg["role"] == "user":
                clean_messages.append(UserMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                clean_messages.append(AssistantMessage(content=msg["content"]))

        ctx.extra.update({"book_id": self.config.book_id})

        self._update_state(
            phase="running_agent",
            agent_states={agent.name: AgentRunState.RUNNING.value},
        )
        yield StreamChunk(kind="status", data={"phase": "running_agent", "agent": agent.name})

        collected_text: list[str] = []
        call_id_to_name: dict[str, str] = {}
        try:
            stream = await agent.run_stream(clean_messages, ctx=ctx)
            async for event in stream:
                event_type = event.get("type", "")

                if event_type == "text_delta":
                    delta = event.get("delta", {})
                    delta_text = delta.get("text", "") if isinstance(delta, dict) else ""
                    collected_text.append(delta_text)
                    yield StreamChunk(kind="text", data=delta_text)

                elif event_type == "tool_call_delta":
                    delta = event.get("delta", {})
                    if isinstance(delta, dict):
                        cid = delta.get("call_id", "")
                        name = delta.get("name", "")
                        if cid and name and cid not in call_id_to_name:
                            call_id_to_name[cid] = name
                            yield StreamChunk(
                                kind="tool_call",
                                data={"tool_name": name, "call_id": cid},
                            )

                elif event_type == "tool_result":
                    result = event.get("result", {})
                    call_id = result.get("call_id", "") if isinstance(result, dict) else ""
                    tool_statuses = ctx.extra.get("tool_call_status", [])
                    status_entry = None
                    for s in tool_statuses:
                        if s.get("call_id") == call_id:
                            status_entry = s
                            break
                    tool_name = call_id_to_name.get(call_id, status_entry.get("tool_name", "") if status_entry else "")
                    yield StreamChunk(
                        kind="tool_result",
                        data={
                            "tool_name": tool_name,
                            "call_id": call_id,
                            "summary": status_entry.get("result_summary", "") if status_entry else "",
                            "retrieval_stats": status_entry.get("retrieval_stats", {}) if status_entry else {},
                        },
                    )
                    yield StreamChunk(
                        kind="status",
                        data={
                            "phase": "tool_executed",
                            "tool_name": tool_name,
                            "retrieval_stats": status_entry.get("retrieval_stats", {}) if status_entry else {},
                        },
                    )

                elif event_type == "response_complete":
                    resp = event.get("response", {})
                    usage = dict(resp.get("usage", {}) or {})
                    ctx.extra["reading_agent"]["usage"] = usage
                    msg = resp.get("message")
                    raw_tool_calls = []
                    if msg is not None:
                        raw_tool_calls = msg.tool_calls or []
                    ctx.extra["reading_agent"]["tool_calls"] = [
                        tc.model_dump() if hasattr(tc, "model_dump") else dict(tc) for tc in raw_tool_calls
                    ]

        except Exception as e:
            self._update_state(
                phase="error",
                agent_states={agent.name: AgentRunState.ERROR.value},
            )
            yield StreamChunk(kind="status", data={"phase": "error", "error": str(e)})
            raise

        response_text = "".join(collected_text)

        # Append assistant response to clean history.
        self.append_assistant_message(response_text)

        # Sync state.
        run_info = ctx.extra.get("reading_agent", {})
        self._sync_state_messages()
        self._update_state(
            phase="done",
            agent_states={agent.name: AgentRunState.DONE.value},
            last_tool_calls=run_info.get("tool_calls", []),
            last_usage=run_info.get("usage", {}),
            extra={
                "book_id": self.config.book_id,
                "reading_agent": run_info,
                "tool_call_status": ctx.extra.get("tool_call_status", []),
            },
        )

        from bookscout.agents.context import StepResult

        final_result = ModeResult(
            text=response_text,
            step_results=[
                StepResult(
                    text=response_text,
                    finish_reason="stop",
                    usage=run_info.get("usage", {}),
                    tool_calls=run_info.get("tool_calls", []),
                )
            ],
            state=self.state,
        )
        yield StreamChunk(kind="done", data=final_result)
