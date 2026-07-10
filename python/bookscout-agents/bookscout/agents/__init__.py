# Copyright 2026 BoChen SHEN
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""`bookscout.agents` package 鈥?Agent abstraction layer.

Defines the core abstractions for building agents, modes, and their
collaboration infrastructure on top of :mod:`bookscout.llm` and
:mod:`bookscout.tools`.

Core types:
    - :class:`Agent` 鈥?abstract base for all agents.
    - :class:`Mode` 鈥?abstract base for all modes (multi-agent orchestration).
    - :class:`AgentContext` 鈥?execution context for an agent.
    - :class:`ModeState` 鈥?read-only state snapshot for the REPL.
    - :class:`AgentTool` 鈥?wraps an Agent as a :class:`BaseTool`.

Context flow:
    - :meth:`AgentContext.fork` 鈥?selective inheritance.
    - :meth:`AgentContext.handoff` 鈥?full conversation, new identity.
    - :meth:`AgentContext.delegate` 鈥?self-contained task package.

Scheduling:
    - :func:`route` 鈥?select an agent and run it.
    - :func:`sequence` 鈥?run agents sequentially.
    - :func:`delegate` 鈥?run an agent as a sub-task.
"""

from __future__ import annotations

from .agent import Agent
from .agent import PromptBuilder
from .agenttool import AgentTool
from .context import AgentContext
from .context import AgentRunState
from .context import StepResult
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
from .mode import StreamChunk
from .mode_agent import ModeAgent
from .mode_agent import ToolCallStatus
from .reading import ReadingAgent
from .reading import ReadingAgentToolset
from .reading import ReadingLLMProfiles
from .reading import ReadingMode
from .reading import ReadingModeConfig
from .reading import ReadingSession
from .reading import ReadingSessionRepository
from .scheduling import delegate
from .scheduling import route
from .scheduling import sequence

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
    "ModeAgent",
    "ModeError",
    "ModeResult",
    "ModeStartupError",
    "ModeState",
    "PromptBuilder",
    "ReadingAgent",
    "ReadingAgentToolset",
    "ReadingLLMProfiles",
    "ReadingMode",
    "ReadingModeConfig",
    "ReadingSession",
    "ReadingSessionRepository",
    "StepResult",
    "StreamChunk",
    "ToolCallStatus",
    "delegate",
    "route",
    "sequence",
]
