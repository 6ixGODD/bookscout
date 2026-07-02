"""Exception hierarchy for the LLM subsystem.

Includes a ``handle_errors`` decorator (same pattern as ``bookscout.filestore``)
that wraps async methods and re-raises non-LLM errors as typed exceptions.
"""

from __future__ import annotations

import inspect
import typing as t

LLMMethodT = t.TypeVar("LLMMethodT", bound=t.Callable[..., t.Any])


class LLMError(Exception):
    """Base exception for all LLM-related errors."""


class ContextOverflowError(LLMError):
    """Raised when the context window budget is exceeded."""

    def __init__(self, message: str, *, token_count: int, budget: int) -> None:
        self.token_count = token_count
        self.budget = budget
        super().__init__(f"{message} (tokens={token_count}, budget={budget})")


class CompletionError(LLMError):
    """Raised when an LLM API call fails."""


class ToolExecutionError(LLMError):
    """Raised when tool execution fails."""


class FileUploadError(LLMError):
    """Raised when a file upload operation fails."""


class FileNotFoundLLMError(LLMError):
    """Raised when a file is not found in the LLM file store."""


class ConversationNotFoundError(LLMError):
    """Raised when a conversation is not found."""

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        super().__init__(f"Conversation not found: {conversation_id!r}")


class ModelNotSupportedError(LLMError):
    """Raised when the requested model does not support the requested feature (e.g., multimodal)."""


@t.overload
def handle_errors(  # noqa: UP047
    method: LLMMethodT,
    *,
    exc_type: type[LLMError] = ...,
    msg: str | None = ...,
) -> LLMMethodT: ...


@t.overload
def handle_errors(
    method: None = ...,
    *,
    exc_type: type[LLMError] = ...,
    msg: str | None = ...,
) -> t.Callable[[LLMMethodT], LLMMethodT]: ...


def handle_errors(  # noqa: UP047
    method: LLMMethodT | None = None,
    *,
    exc_type: type[LLMError] = LLMError,
    msg: str | None = None,
) -> LLMMethodT | t.Callable[[LLMMethodT], LLMMethodT]:
    """Decorator to handle LLM-related errors and re-raise them as typed exceptions.

    Supports both regular async methods and async generator methods.
    Already-typed :class:`LLMError` exceptions are re-raised without wrapping.

    Args:
        method: The LLM method to wrap.
        exc_type: The exception type to raise on error.
        msg: Optional custom message for the exception.

    Returns:
        A wrapped method that raises :class:`LLMError` on exceptions.
    """

    def decorator(method: LLMMethodT) -> LLMMethodT:
        if inspect.isasyncgenfunction(method):

            async def wrapper(self: t.Any, *args: t.Any, **kwargs: t.Any) -> t.Any:
                try:
                    async for item in method(self, *args, **kwargs):
                        yield item
                except LLMError:
                    raise
                except Exception as e:
                    self.logger.error(msg or f"Error in LLM operation {method.__name__}: {e}")
                    raise exc_type(msg or f"An error occurred in LLM operation {method.__name__}: {e}") from e

        else:

            async def wrapper(self: t.Any, *args: t.Any, **kwargs: t.Any) -> t.Any:  # type: ignore[misc]
                try:
                    return await method(self, *args, **kwargs)
                except LLMError:
                    raise
                except Exception as e:
                    self.logger.error(msg or f"Error in LLM operation {method.__name__}: {e}")
                    raise exc_type(msg or f"An error occurred in LLM operation {method.__name__}: {e}") from e

        return t.cast(LLMMethodT, wrapper)

    if method is not None:
        return decorator(method)
    return decorator
