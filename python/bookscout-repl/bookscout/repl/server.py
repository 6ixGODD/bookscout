"""REPL Server — stdio front-end over :class:`ReplContext`.

Receives JSON requests over a :class:`Transport` and dispatches them to
the shared :class:`ReplContext`. Streaming results are sent back as
individual events. The TUI uses the same context directly (no transport);
this module exists to support external clients such as the MCP server or
a future web front-end.
"""

from __future__ import annotations

import asyncio
import typing as t

from bookscout.logging.mixin import LoggingMixin

from .config import BookScoutConfig
from .context import ReplContext
from .transport import StdioTransport
from .transport import Transport


class ReplServer(LoggingMixin):
    """The stdio REPL server — a thin transport layer over ReplContext.

    Args:
        config: BookScout configuration.
    """

    def __init__(self, config: BookScoutConfig) -> None:
        self._config = config
        self._context = ReplContext(config=config)
        super().__init__(logger=self._context.logger)
        self._transport: Transport | None = None
        self._pending_tasks: set[asyncio.Task[t.Any]] = set()

    async def startup(self) -> None:
        """Initialize the context and transport."""
        await self._context.startup()
        self._transport = StdioTransport(logger=self.logger)
        await self._transport.startup()
        self.logger.info("REPL server started", data_dir=str(self._context.data_dir))

    async def shutdown(self) -> None:
        """Shut down transport and context."""
        if self._transport is not None:
            await self._transport.shutdown()
        await self._context.shutdown()

    async def run(self) -> None:
        """Main loop: receive requests, handle them, send events back."""
        assert self._transport is not None

        while True:
            try:
                request = await self._transport.receive()
            except Exception as e:  # pylint: disable=broad-exception-caught
                self.logger.error("transport receive failed", error=str(e))
                break

            if request is None:
                self.logger.info("client disconnected")
                break

            task = asyncio.create_task(self._handle_request(request))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)

        self.logger.info("REPL server loop ended")

    async def _handle_request(self, request: dict[str, t.Any]) -> None:
        assert self._transport is not None

        req_type = request.get("type", "")
        req_id = request.get("request_id", "")

        try:
            if req_type == "list_books":
                await self._handle_list_books(req_id)
            elif req_type == "compile":
                await self._handle_compile(req_id, request)
            elif req_type == "build_indexes":
                await self._handle_build_indexes(req_id, request)
            elif req_type == "get_task_progress":
                await self._handle_get_progress(req_id, request)
            elif req_type == "chat":
                await self._handle_chat(req_id, request)
            elif req_type == "shutdown":
                await self._transport.send({"type": "shutdown_ack", "request_id": req_id})
                asyncio.get_event_loop().stop()
            else:
                await self._send_error(req_id, f"Unknown request type: {req_type}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            await self._send_error(req_id, str(e))

    async def _send(self, payload: dict[str, t.Any]) -> None:
        assert self._transport is not None
        await self._transport.send(payload)

    async def _send_error(self, req_id: str, error: str) -> None:
        await self._send({"type": "error", "request_id": req_id, "error": error})

    async def _handle_list_books(self, req_id: str) -> None:
        books = await self._context.list_books()
        await self._send({
            "type": "books_listed",
            "request_id": req_id,
            "books": [
                {
                    "id": b.id,
                    "title": b.title,
                    "author": b.author,
                    "content_path": b.content_path,
                    "checksum": b.checksum,
                }
                for b in books
            ],
        })

    async def _handle_compile(self, req_id: str, request: dict[str, t.Any]) -> None:
        source_path = request.get("source_path", "")
        if not source_path:
            await self._send_error(req_id, "source_path required")
            return
        task_id = await self._context.compile(source_path)
        await self._send({"type": "task_started", "request_id": req_id, "task_id": task_id})

    async def _handle_build_indexes(self, req_id: str, request: dict[str, t.Any]) -> None:
        book_id = request.get("book_id", "")
        if not book_id:
            await self._send_error(req_id, "book_id required")
            return
        index_types = request.get("index_types")
        task_id = await self._context.build_indexes(book_id, index_types)
        await self._send({"type": "task_started", "request_id": req_id, "task_id": task_id})

    async def _handle_get_progress(self, req_id: str, request: dict[str, t.Any]) -> None:
        task_id = request.get("task_id", "")
        progress = self._context.get_task_progress(task_id)
        if progress is None:
            await self._send_error(req_id, "Task not found")
            return
        await self._send({
            "type": "task_progress",
            "request_id": req_id,
            "task_id": progress.task_id,
            "task_type": progress.task_type,
            "status": progress.status,
            "stage": progress.stage,
            "percentage": progress.percentage,
            "processed": progress.processed,
            "total": progress.total,
            "eta_seconds": progress.eta_seconds,
            "elapsed_seconds": progress.elapsed_seconds,
            "error": progress.error,
            "result": progress.result,
        })

    async def _handle_chat(self, req_id: str, request: dict[str, t.Any]) -> None:
        book_id = request.get("book_id", "")
        user_input = request.get("user_input", "")
        if not book_id or not user_input:
            await self._send_error(req_id, "book_id and user_input required")
            return

        try:
            async for chunk in self._context.chat(book_id, user_input):
                await self._send({
                    "type": "stream_chunk",
                    "request_id": req_id,
                    "kind": chunk.kind,
                    "data": chunk.data
                    if isinstance(chunk.data, str | dict | list | int | float | type(None))
                    else str(chunk.data),
                })
        except RuntimeError as e:
            await self._send_error(req_id, str(e))
            return

        mode = await self._context.get_or_create_mode(book_id)
        if mode is not None:
            await self._send({
                "type": "chat_done",
                "request_id": req_id,
                "state": mode.state.model_dump(),
            })
        else:
            await self._send({"type": "chat_done", "request_id": req_id})


__all__ = ["ReplServer"]
