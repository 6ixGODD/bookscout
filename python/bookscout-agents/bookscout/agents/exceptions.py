"""Agent-layer exception hierarchy."""

from __future__ import annotations


class AgentError(Exception):
    """Base exception for all agent-related errors."""


class AgentStartupError(AgentError):
    """Raised when an agent fails to start up."""


class ModeError(Exception):
    """Base exception for all mode-related errors."""


class ModeStartupError(ModeError):
    """Raised when a mode fails to start up."""


class CheckpointError(ModeError):
    """Raised when a checkpoint operation fails."""


class ContextError(AgentError):
    """Raised when an agent context operation fails."""


class ContextForkError(ContextError):
    """Raised when forking an agent context fails."""


class HandoffError(ContextError):
    """Raised when a handoff operation fails."""


class CompactError(AgentError):
    """Raised when compacting an agent's conversation fails."""
