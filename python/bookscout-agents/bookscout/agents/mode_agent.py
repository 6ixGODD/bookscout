"""ModeAgent — structured tool-call status tracking for REPL consumption.

A :class:`ModeAgent` is an :class:`Agent` that records structured, deterministic
status information about each tool call it makes, so the REPL layer can display
real-time progress (e.g. "graph retrieval found 5 entities, 3 relationships",
"chunk search returned 8 results").

The status is written into ``ctx.extra["tool_call_status"]`` as a list of
:class:`ToolCallStatus` dicts. The REPL reads this via :class:`ModeState.extra`.
"""

from __future__ import annotations

import json
import typing as t

import pydantic

from bookscout.agents.agent import Agent
from bookscout.agents.context import AgentContext
from bookscout.agents.context import StepResult

if t.TYPE_CHECKING:
    from bookscout.llm.types import Message


class ToolCallStatus(pydantic.BaseModel):
    """Structured status of a single tool call, for REPL display.

    Attributes:
        tool_name: Name of the tool called.
        call_id: The LLM-assigned tool call ID.
        arguments: The arguments passed to the tool (JSON-serializable dict).
        status: "pending", "executed", "failed".
        result_summary: Human-readable summary of the result.
        retrieval_stats: Structured retrieval statistics (e.g. entity count,
            chunk count, relationship count). Populated by ModeAgent based on
            tool name and result content.
        result_text: Full raw result text returned by the tool.
        error: Error message if status is "failed".
        elapsed_ms: Time from call to result, in milliseconds.
    """

    tool_name: str = ""
    call_id: str = ""
    arguments: dict[str, t.Any] = pydantic.Field(default_factory=dict)
    status: str = "pending"
    result_summary: str = ""
    retrieval_stats: dict[str, int] = pydantic.Field(default_factory=dict)
    result_text: str = ""
    error: str = ""
    elapsed_ms: float = 0.0

    model_config = {"frozen": False}


def _parse_tool_result(tool_name: str, result_text: str) -> tuple[str, dict[str, int]]:
    """Parse a tool result text to extract a summary and retrieval stats.

    Inspects the JSON content of common retrieval tool results and extracts
    structured counts (entity_count, relationship_count, chunk_count, etc.).

    Args:
        tool_name: The name of the tool that was called.
        result_text: The raw result string from the tool.

    Returns:
        Tuple of (human_readable_summary, retrieval_stats_dict).
    """
    stats: dict[str, int] = {}

    try:
        data = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        # Not JSON — return a text preview as summary.
        preview = result_text[:200].replace("\n", " ") if result_text else "(empty)"
        return preview, stats

    if not isinstance(data, list):
        # Single dict result.
        if isinstance(data, dict):
            return _summarize_dict_result(tool_name, data)
        return str(data)[:200], stats

    # List result.
    count = len(data)
    stats["result_count"] = count

    if tool_name in ("get_entities",):
        stats["entity_count"] = count
        types = {}
        for item in data:
            etype = item.get("type", "Unknown") if isinstance(item, dict) else "Unknown"
            types[etype] = types.get(etype, 0) + 1
        type_str = ", ".join(f"{k}: {v}" for k, v in sorted(types.items()))
        summary = f"Retrieved {count} entities ({type_str})"
    elif tool_name in ("get_relationships",):
        stats["relationship_count"] = count
        summary = f"Retrieved {count} relationships"
    elif tool_name in ("chunk_vector_search", "chunk_fts_search"):
        stats["chunk_count"] = count
        summary = f"Found {count} chunks"
    elif tool_name in ("get_tree", "list_nodes_by_level", "get_children"):
        stats["node_count"] = count
        summary = f"Retrieved {count} nodes"
    elif tool_name in ("list_summaries",):
        stats["summary_count"] = count
        summary = f"Retrieved {count} summaries"
    elif tool_name in ("graph_entity_first_retrieval",):
        total_ents = count
        total_rels = sum(len(item.get("relationships", [])) for item in data if isinstance(item, dict))
        stats["entity_count"] = total_ents
        stats["relationship_count"] = total_rels
        max_hop = max((item.get("hop", 0) for item in data if isinstance(item, dict)), default=0)
        stats["max_hop_reached"] = max_hop
        summary = f"Entity-first retrieval: {total_ents} entities, {total_rels} relationships (max hop {max_hop})"
    elif tool_name in ("graph_relationship_first_retrieval",):
        total_rels = count
        stats["relationship_count"] = total_rels
        summary = f"Relationship-first retrieval: {total_rels} relationships"
    elif tool_name in ("graph_fts_entity_retrieval",):
        total_ents = count
        total_rels = sum(len(item.get("relationships", [])) for item in data if isinstance(item, dict))
        stats["entity_count"] = total_ents
        stats["relationship_count"] = total_rels
        summary = f"FTS entity retrieval: {total_ents} entities, {total_rels} relationships"
    elif tool_name in ("list_books",):
        stats["book_count"] = count
        summary = f"Found {count} books"
    else:
        summary = f"Tool returned {count} items"

    return summary, stats


def _summarize_dict_result(tool_name: str, data: dict[str, t.Any]) -> tuple[str, dict[str, int]]:
    """Summarize a single dict tool result."""
    stats: dict[str, int] = {}
    if tool_name == "get_book":
        title = data.get("title", "?")
        author = data.get("author", "?")
        summary = f"Book: {title} by {author}"
    elif tool_name == "get_node" or tool_name == "get_root_node":
        title = data.get("title", "(untitled)")
        level = data.get("level", 0)
        summary = f"Node [{level}]: {title}"
    elif tool_name == "get_summary":
        summary_text = data.get("summary", "")
        summary = f"Summary: {summary_text[:150]}..."
    elif tool_name == "read_node_content" or tool_name == "read_subtree_content":
        length = len(data) if isinstance(data, str) else len(str(data))
        stats["content_chars"] = length
        summary = f"Content: {length} chars"
    elif tool_name == "get_chunks_by_node":
        # This returns a list, not a dict — handled elsewhere.
        summary = str(data)[:200]
    elif tool_name == "get_task_progress":
        status = data.get("status", "?")
        pct = data.get("percentage", 0)
        summary = f"Task {status}: {pct}%"
    elif tool_name == "compile":
        task_id = data.get("task_id", "?")
        summary = f"Compile started: task_id={task_id}"
    elif tool_name == "build_indexes":
        task_id = data.get("task_id", "?")
        summary = f"Index build started: task_id={task_id}"
    else:
        summary = str(data)[:200]
    return summary, stats


class ModeAgent(Agent):
    """Agent with structured tool-call status tracking for REPL consumption.

    A ModeAgent wraps the normal :meth:`step` execution, intercepting tool
    call results from the LLM response and parsing them into structured
    :class:`ToolCallStatus` entries. These are written to
    ``ctx.extra["tool_call_status"]`` so the Mode can include them in
    :class:`ModeState.extra` for the REPL.

    Subclasses still implement :meth:`step` as usual. The ModeAgent
    automatically enriches the context after each step.
    """

    async def run(self, messages: list[Message], *, ctx: AgentContext) -> StepResult:
        """Execute the agent and record structured tool-call status.

        After the normal :meth:`step` completes, parses each tool call's
        result from the LLM response and writes :class:`ToolCallStatus`
        entries into ``ctx.extra["tool_call_status"]``.
        """
        # Initialize tool_call_status list in context.
        ctx.extra["tool_call_status"] = []

        result = await super().run(messages, ctx=ctx)

        # Parse tool calls and build status entries.
        statuses: list[dict[str, t.Any]] = []
        for tc in result.tool_calls:
            tool_name = tc.get("name", tc.get("function", {}).get("name", ""))
            call_id = tc.get("call_id", tc.get("id", ""))
            arguments_raw = tc.get("arguments", tc.get("function", {}).get("arguments", "{}"))
            if isinstance(arguments_raw, str):
                try:
                    arguments = json.loads(arguments_raw)
                except (json.JSONDecodeError, TypeError):
                    arguments = {"raw": arguments_raw}
            else:
                arguments = arguments_raw if isinstance(arguments_raw, dict) else {}

            # Try to get the tool result from the LLM's tool result cache.
            # The ChatModel executes tools internally and appends results
            # to the message list. We look for ToolResultMessage matching
            # this call_id.
            result_text = ""
            status = "executed"
            error = ""

            # The tool result is in ctx.tool_results if the agent stored it,
            # or we can try to extract it from the messages.
            if call_id in ctx.tool_results:
                result_text = str(ctx.tool_results[call_id])
            else:
                # Scan messages for ToolResultMessage with this call_id.
                for msg in reversed(messages):
                    if hasattr(msg, "tool_call_id") and getattr(msg, "tool_call_id", "") == call_id:
                        content = msg.content
                        result_text = " ".join(str(c) for c in content) if isinstance(content, list) else str(content)
                        break

            if not result_text:
                # The tool was called but result may not be available in
                # the message list we see. Mark as executed (the LLM
                # processed it internally).
                result_text = ""

            # Parse the result for structured stats.
            summary, retrieval_stats = _parse_tool_result(tool_name, result_text)

            status_entry = ToolCallStatus(
                tool_name=tool_name,
                call_id=call_id,
                arguments=arguments,
                status=status,
                result_summary=summary,
                retrieval_stats=retrieval_stats,
                error=error,
            )
            statuses.append(status_entry.model_dump())

        ctx.extra["tool_call_status"] = statuses
        return result
