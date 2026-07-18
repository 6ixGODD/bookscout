"""ReadingAgent — interactive reading assistant for one indexed book.

Inherits :class:`ModeAgent` for structured tool-call status tracking.
Does NOT classify intent via keyword matching — the system prompt guides
the LLM to handle different question types naturally.

Conversation is managed by :class:`ReadingMode`, not this agent.
The LLM is always stateless.
"""

from __future__ import annotations

import json
import typing as t

from bookscout.agents.context import AgentContext
from bookscout.agents.context import StepResult
from bookscout.agents.mode_agent import ModeAgent
from bookscout.llm.types import CompletionOptions
from bookscout.llm.types import Message
from bookscout.llm.types import SystemMessage

from .config import ReadingLLMProfiles

if t.TYPE_CHECKING:
    from bookscout.core.lib.stream import AsyncStream
    from bookscout.llm.types import StreamEvent
    from bookscout.logging import Logger
    from bookscout.tools.toolset import Toolset


READING_SYSTEM_PROMPT = """\
You are ReadingAgent, the interactive reading assistant for one indexed book.
Your job is to help the user understand, navigate, and analyze the book's content.

## How to Use Tools

You have access to ontology, summary, chunk, graph, and computation tools.
Always ground your answers in retrieval — do not guess.

**Tool selection guide:**
- **Structure/navigation**: Use `get_tree`, `list_nodes_by_level`, `get_root_node`, `get_children` to understand the book's structure.
- **Reading content**: Use `read_node_content` or `read_subtree_content` to read specific sections.
- **Broad overviews**: Use `get_summary` or `list_summaries` for LLM-generated section summaries.
- **Textual evidence**: Use `chunk_vector_search` (semantic) or `chunk_fts_search` (keyword) to find relevant passages.
- **Entities & relationships**: Use `get_entities`, `get_relationships`, or graph retrieval tools (`graph_entity_first_retrieval`, `graph_relationship_first_retrieval`) to explore the knowledge graph.
- **Computation**: Use `python_execute` for calculations, data processing, or scientific computing. Use `wolfram_execute` for mathematical formula evaluation.

**Use the current book_id from context and pass it to tools that require it.**
Do NOT use compile, build_indexes, or task progress tools — those are for the compiler, not the reader.

## How to Cite Sources

When you reference information from retrieval tools, cite it using XML tags:

- **Entities**: `<entity id="ent_xxx" />`
- **Relationships**: `<relationship id="rel_xxx" />`
- **Chunks**: `<chunk id="chunk_xxx" />`
- **Nodes**: `<node id="node_xxx" />`

Place citations inline where the information is used. Example:
> According to <node id="node_abc" />, Kant distinguishes three meanings of "intellectual intuition" <chunk id="chunk_def" />. The concept is linked to <entity id="ent_ghi" /> and <entity id="ent_jkl" />.

## Answer Quality

- For factual questions: retrieve and cite specific evidence.
- For "where" questions: include node_id, chunk_id, and content offsets.
- For explanations: synthesize evidence from multiple retrieved sources.
- For comparisons: retrieve evidence for all sides before comparing.
- For summaries: use existing summaries as a base, enrich with details if needed.
- If retrieval does not contain enough evidence: say what is missing instead of guessing.
- For calculation requests: use the computation tools and show the result.
"""


class ReadingAgent(ModeAgent):
    """LLM-backed reading assistant that delegates retrieval to tools.

    Inherits :class:`ModeAgent` for structured tool-call status tracking.
    Does NOT manage conversation — that's the Mode's job.
    """

    def __init__(
        self,
        *,
        name: str = "reading_agent",
        toolset: Toolset,
        profiles: ReadingLLMProfiles | None = None,
        logger: Logger,
        instructions: str | None = None,
    ) -> None:
        super().__init__(
            name=name,
            instructions=instructions or READING_SYSTEM_PROMPT,
            toolset=toolset,
            logger=logger,
        )
        self.profiles = profiles or ReadingLLMProfiles()

    async def step(self, messages: list[Message], *, ctx: AgentContext) -> StepResult:
        """Execute one LLM turn.

        The Mode passes clean conversation messages (user + assistant).
        The agent prepends the system prompt and calls the LLM statelessly.
        """
        prompt = await self.build_system_prompt(ctx)
        # Add runtime context to the system prompt.
        book_id = ctx.extra.get("book_id", "")
        session_id = ctx.extra.get("reading_session_id", "")
        prompt += f"\n\n## Runtime Context\n- book_id: {book_id}\n- reading_session_id: {session_id}\n"

        # Select model based on configured profiles.
        model = self.profiles.standard

        options = CompletionOptions(model=model, temperature=0.2, max_tool_iterations=50)
        response = await ctx.llm.chat_completion(
            [SystemMessage(content=prompt), *messages],
            tools=self.tools,
            options=options,
        )

        assistant = response["message"]
        tool_calls = [tc.model_dump() for tc in assistant.tool_calls or []]
        usage = dict(response["usage"])
        ctx.extra["reading_agent"] = {
            "model": response.get("model", model or ""),
            "tool_count": len(self.tools),
            "tool_calls": tool_calls,
            "usage": usage,
        }
        return StepResult(
            text=assistant.content if isinstance(assistant.content, str) else "",
            finish_reason=response["finish_reason"],
            usage=usage,
            tool_calls=tool_calls,
        )

    async def run_stream(
        self,
        messages: list[Message],
        *,
        ctx: AgentContext,
    ) -> AsyncStream[StreamEvent]:
        """Execute with true streaming via chat_completion_stream."""
        from bookscout.agents.mode_agent import ToolCallStatus
        from bookscout.agents.mode_agent import _parse_tool_result
        from bookscout.core.lib.stream import AsyncStream

        prompt = await self.build_system_prompt(ctx)
        book_id = ctx.extra.get("book_id", "")
        session_id = ctx.extra.get("reading_session_id", "")
        prompt += f"\n\n## Runtime Context\n- book_id: {book_id}\n- reading_session_id: {session_id}\n"

        model = self.profiles.standard
        ctx.extra["reading_agent"] = {
            "model": model or "",
            "tool_count": len(self.tools),
            "tool_calls": [],
            "usage": {},
        }
        ctx.extra["tool_call_status"] = []

        options = CompletionOptions(model=model, temperature=0.2, stream=True, max_tool_iterations=50)
        full_messages: list[Message] = [SystemMessage(content=prompt), *messages]

        async def _gen() -> t.AsyncIterator[StreamEvent]:
            collected_text: list[str] = []
            tool_calls: list[dict[str, t.Any]] = []
            usage: dict[str, int] = {}
            call_id_to_name: dict[str, str] = {}
            call_id_to_args: dict[str, list[str]] = {}

            stream = await ctx.llm.chat_completion_stream(
                full_messages,
                tools=self.tools,
                options=options,
            )
            async for event in stream:
                event_type = event.get("type", "")

                if event_type == "text_delta":
                    delta = event.get("delta", {})
                    delta_text = delta.get("text", "") if isinstance(delta, dict) else ""
                    collected_text.append(delta_text)

                elif event_type == "tool_call_delta":
                    delta = event.get("delta", {})
                    if isinstance(delta, dict):
                        cid = delta.get("call_id", "")
                        name = delta.get("name", "")
                        args_delta = delta.get("arguments_delta", "")
                        if cid:
                            if name:
                                call_id_to_name[cid] = name
                            call_id_to_args.setdefault(cid, []).append(args_delta)
                            if not any(s.get("call_id") == cid for s in ctx.extra["tool_call_status"]):
                                ctx.extra["tool_call_status"].append(
                                    ToolCallStatus(
                                        tool_name=name or call_id_to_name.get(cid, ""),
                                        call_id=cid,
                                        status="pending",
                                    ).model_dump()
                                )

                elif event_type == "tool_result":
                    result = event.get("result", {})
                    call_id = result.get("call_id", "") if isinstance(result, dict) else ""
                    content = result.get("content", "") if isinstance(result, dict) else ""
                    if isinstance(content, list):
                        content = " ".join(str(c) for c in content)
                    tool_name = call_id_to_name.get(call_id, "")
                    full_args = "".join(call_id_to_args.get(call_id, []))
                    parsed_args: dict[str, t.Any] = {}
                    try:
                        parsed_args = json.loads(full_args)
                    except (json.JSONDecodeError, TypeError):
                        parsed_args = {"_raw": full_args}
                    tool_calls.append({
                        "call_id": call_id,
                        "name": tool_name,
                        "arguments": full_args,
                    })
                    summary, stats = _parse_tool_result(tool_name, str(content))
                    for s in ctx.extra["tool_call_status"]:
                        if s.get("call_id") == call_id:
                            s["status"] = "executed"
                            s["result_summary"] = summary
                            s["retrieval_stats"] = stats
                            s["arguments"] = parsed_args
                            s["result_text"] = str(content)
                            break

                elif event_type == "response_complete":
                    resp = event.get("response", {})
                    usage = dict(resp.get("usage", {}) or {})

                yield event

            ctx.extra["reading_agent"]["tool_calls"] = tool_calls
            ctx.extra["reading_agent"]["usage"] = usage

        return AsyncStream(_gen())
