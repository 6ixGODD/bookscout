from __future__ import annotations

import typing as t


class MessageBase(t.TypedDict):
    type: str
    """Type of message."""

    request_id: str
    """Unique request identifier."""


class ErrorMessage(MessageBase):
    type: t.Literal["error"]  # type: ignore[misc]
    """Error message type."""

    error: str
    """Human readable error message."""


class ShutdownACKMessage(MessageBase):
    type: t.Literal["shutdown_ack"]  # type: ignore[misc]
    """Shutdown ACK message type."""


class _Book(t.TypedDict):
    id: str
    """Unique book identifier."""

    title: str
    """Book title."""

    author: str
    """Author of the book."""

    content_path: str
    """Content path of the book."""

    checksum: str
    """Checksum of the book."""


class BooksListedMessage(MessageBase):
    type: t.Literal["books_listed"]  # type: ignore[misc]
    """Books listed message type."""

    books: list[_Book]
    """List of books."""


class TaskStartedMessage(MessageBase):
    type: t.Literal["task_started"]  # type: ignore[misc]
    """Task started message type."""

    task_id: str
    """Unique task identifier."""


class TaskProgressMessage(MessageBase):
    type: t.Literal["task_progress"]  # type: ignore[misc]
    """Task progress message type."""

    task_id: str
    """Unique task identifier."""

    task_type: str
    """Task type. One of "compile" or "index"."""

    status: str
    """Task status. One of "pending", "running", "succeeded", "failed"."""

    stage: str
    """Current stage (e.g. "parse_source", "build_ontology")."""

    percentage: float
    """0-100 progress estimate."""

    processed: int
    """Items processed in current stage."""

    total: int
    """Total items in current stage."""

    eta_seconds: float | None
    """Estimated time remaining (None if unknown)."""

    elapsed_seconds: float
    """Time elapsed since task start."""

    error: str
    """Error message if failed."""

    result: dict[str, t.Any]
    """Result data if succeeded (e.g. book_id)."""


class StreamChunkMessage(MessageBase):
    type: t.Literal["stream_chunk"]  # type: ignore[misc]
    """Stream chunk message type."""

    kind: str
    """Kind of chunk. One of "text", "status", "tool_call", "tool_result",
    "done"."""

    data: t.Any
    """Chunk payload."""


class ChatDoneMessage(MessageBase):
    type: t.Literal["chat_done"]  # type: ignore[misc]
    """Chat done message type."""

    state: dict[str, t.Any]
    """Mode state in JSON."""
