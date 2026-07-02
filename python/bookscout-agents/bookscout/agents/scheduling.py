"""Scheduling primitives for Mode orchestration.

These are ordinary ``async def`` functions — not a framework.  They
encapsulate common patterns for multi-agent coordination so that Mode
implementations don't have to reinvent them.

Modes are free to ignore these and write raw ``await agent.run(...)``
calls if the primitives don't fit their needs.
"""

from __future__ import annotations

import typing as t

from .agent import Agent
from .context import AgentContext
from .context import StepResult

if t.TYPE_CHECKING:
    from bookscout.llm.types import Message


async def route(
    user_input: str,
    agents: dict[str, Agent],
    *,
    ctx: AgentContext,
    classifier: t.Callable[[str, dict[str, Agent]], t.Awaitable[str]] | None = None,
    default: str | None = None,
) -> tuple[Agent, StepResult]:
    """Select an agent and run it on the user input.

    If ``classifier`` is provided, it is an async callable that receives
    the user input and the agent dict, and returns the selected agent
    name.  Otherwise, the ``default`` agent is used.

    Args:
        user_input: Raw user input string.
        agents: Available agents by name.
        ctx: The current agent context.
        classifier: Optional async function that picks an agent name.
        default: Fallback agent name if no classifier or classifier
            returns an unknown name.

    Returns:
        A tuple of (the selected Agent, its StepResult).

    Raises:
        ValueError: If no agent could be selected.
    """
    from bookscout.llm.types import UserMessage as _Usr

    if classifier is not None:
        agent_name = await classifier(user_input, agents)
    elif default is not None:
        agent_name = default
    else:
        raise ValueError("route: either classifier or default must be provided")

    agent = agents.get(agent_name)
    if agent is None:
        if default is not None:
            agent = agents[default]
        else:
            raise ValueError(f"route: agent {agent_name!r} not found and no default")

    messages: list[Message] = [_Usr(content=user_input)]
    result = await agent.run(messages, ctx=ctx)
    return agent, result


async def sequence(
    agents_and_inputs: list[tuple[Agent, str]],
    *,
    ctx: AgentContext,
) -> list[StepResult]:
    """Run agents sequentially, piping each output as context to the next.

    Each agent receives the result of the previous agent's execution
    injected into its context via ``prompt_params``.

    Args:
        agents_and_inputs: List of (agent, input_text) pairs.
        ctx: The initial agent context (will be forked for each agent).

    Returns:
        List of StepResults, one per agent.
    """
    from bookscout.llm.types import UserMessage as _Usr

    results: list[StepResult] = []
    current_ctx = ctx
    prev_text: str | None = None

    for agent, input_text in agents_and_inputs:
        # If there's a previous result, inject it as context
        if prev_text is not None:
            current_ctx = current_ctx.fork(
                prompt_params={"previous_result": prev_text},
            )

        messages: list[Message] = [_Usr(content=input_text)]
        result = await agent.run(messages, ctx=current_ctx)
        results.append(result)
        prev_text = result.text
        # Evolve the context for the next agent
        current_ctx = current_ctx.evolve(
            prompt_params={"previous_result": result.text or ""},
        )

    return results


async def delegate(
    agent: Agent,
    *,
    task: str,
    ctx: AgentContext,
    prompt_params: dict[str, t.Any] | None = None,
    extra: dict[str, t.Any] | None = None,
) -> StepResult:
    """Run an agent as a delegated sub-task.

    Creates a fresh context with the task injected — the delegated agent
    does not inherit the calling agent's conversation.

    Args:
        agent: The agent to delegate to.
        task: The task description for the delegated agent.
        ctx: The current context (used as a base for fork/delegate).
        prompt_params: Additional prompt params for the delegated agent.
        extra: Additional extra context for the delegated agent.

    Returns:
        The delegated agent's StepResult.
    """
    from bookscout.llm.types import UserMessage as _Usr

    delegate_ctx = ctx.delegate(task=task, prompt_params=prompt_params, extra=extra)
    messages: list[Message] = [_Usr(content=task)]
    return await agent.run(messages, ctx=delegate_ctx)
