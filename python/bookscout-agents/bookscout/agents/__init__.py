"""`bookscout.agents` package — Agent abstraction layer.

Defines the core abstractions for building agents, modes, and their
collaboration infrastructure on top of :mod:`bookscout.llm` and
:mod:`bookscout.tools`.

Core types:
    - :class:`Agent` — abstract base for all agents.
    - :class:`Mode` — abstract base for all modes (multi-agent orchestration).
    - :class:`AgentContext` — execution context for an agent.
    - :class:`ModeState` — read-only state snapshot for the REPL.
    - :class:`AgentTool` — wraps an Agent as a :class:`BaseTool`.

Context flow:
    - :meth:`AgentContext.fork` — selective inheritance.
    - :meth:`AgentContext.handoff` — full conversation, new identity.
    - :meth:`AgentContext.delegate` — self-contained task package.

Scheduling:
    - :func:`route` — select an agent and run it.
    - :func:`sequence` — run agents sequentially.
    - :func:`delegate` — run an agent as a sub-task.
"""

from __future__ import annotations

from .agent import Agent
from .agent import PromptBuilder
from .context import AgentContext
from .context import AgentRunState
from .context import StepResult
from .context import TitlePair
from .exceptions import AgentError
from .exceptions import AgentStartupError
from .exceptions import CheckpointError
from .exceptions import CompactError
from .exceptions import ContextError
from .exceptions import ContextForkError
from .exceptions import HandoffError
from .exceptions import ModeError
from .exceptions import ModeStartupError
from .mode import CheckpointInfo
from .mode import Mode
from .mode import ModeResult
from .mode import ModeState
from .scheduling import delegate
from .scheduling import route
from .scheduling import sequence
from .tool import AgentTool

__all__ = [
    "Agent",
    "AgentContext",
    "AgentError",
    "AgentRunState",
    "AgentStartupError",
    "AgentTool",
    "CheckpointError",
    "CheckpointInfo",
    "CompactError",
    "ContextError",
    "ContextForkError",
    "HandoffError",
    "Mode",
    "ModeError",
    "ModeResult",
    "ModeStartupError",
    "ModeState",
    "PromptBuilder",
    "StepResult",
    "TitlePair",
    "delegate",
    "route",
    "sequence",
]
