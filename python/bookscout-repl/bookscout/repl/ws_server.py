"""WebSocket server for the BookScout Electron desktop client.

Exposes a single ``ws://localhost:{port}/ws`` endpoint that wraps
:class:`ReplServer` request handling over a :class:`WebSocketTransport`.

Start via CLI::

    python -m bookscout.repl ws --port 18732

Or programmatically::

    from bookscout.repl.ws_server import run_ws_server

    asyncio.run(run_ws_server(port=18732))
"""

from __future__ import annotations

import asyncio
import typing as t

from bookscout.logging.mixin import LoggingMixin

from .config import BookScoutConfig
from .context import ReplContext
from .server import ReplServer
from .transport import WebSocketTransport

if t.TYPE_CHECKING:
    from bookscout.logging import Logger

_DEFAULT_PORT = 18732


class WsReplServer(LoggingMixin):
    """FastAPI WebSocket server that delegates to :class:`ReplServer`.

    Each incoming WebSocket connection gets its own :class:`WebSocketTransport`
    and runs the same request loop as the stdio server. The :class:`ReplContext`
    is shared across all connections (session-level state is per-session-id).

    Args:
        config: BookScout configuration.
        port: Port to listen on.
    """

    def __init__(self, config: BookScoutConfig, *, port: int = _DEFAULT_PORT) -> None:
        self._config = config
        self._port = port
        self._context: ReplContext | None = None
        self._app: t.Any = None

    @property
    def port(self) -> int:
        return self._port

    async def startup(self) -> None:
        """Initialize ReplContext and the FastAPI app."""
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware

        self._context = ReplContext(config=self._config)
        await self._context.startup()
        super().__init__(logger=self._context.logger)

        app = FastAPI(title="BookScout REPL", version="0.1.0")

        # Allow the Electron renderer (served from file://) to connect.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Store context on the app state so the endpoint can access it.
        app.state.repl_context = self._context

        @app.websocket("/ws")
        async def websocket_endpoint(websocket: t.Any) -> None:
            """Handle a single WebSocket connection."""
            await websocket.accept()
            ctx: ReplContext = websocket.app.state.repl_context
            logger: Logger = ctx.logger

            transport = WebSocketTransport(websocket, logger)
            await transport.startup()

            # Build a lightweight ReplServer that shares the same context
            # but uses this connection's transport.
            server = ReplServer.__new__(ReplServer)
            server._config = self._config
            server._context = ctx
            super(ReplServer, server).__init__(logger=logger)
            server._transport = transport
            server._pending_tasks: set[asyncio.Task[t.Any]] = set()

            try:
                await server.run()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error("WebSocket connection error", error=str(exc))
            finally:
                await transport.shutdown()

        # Health-check endpoint for the Electron client to probe readiness.
        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        self._app = app
        self.logger.info("WebSocket REPL server configured", port=self._port)

    async def shutdown(self) -> None:
        """Shut down the ReplContext."""
        if self._context is not None:
            await self._context.shutdown()

    async def run(self) -> None:
        """Start the uvicorn server (blocking)."""
        import uvicorn

        assert self._app is not None
        config = uvicorn.Config(
            self._app,
            host="127.0.0.1",
            port=self._port,
            log_level="warning",
            loop="asyncio",
        )
        server = uvicorn.Server(config)
        await server.serve()


async def run_ws_server(
    config: BookScoutConfig,
    *,
    port: int = _DEFAULT_PORT,
) -> None:
    """Convenience entry point — create and run the WebSocket server."""
    ws_server = WsReplServer(config=config, port=port)
    await ws_server.startup()
    try:
        await ws_server.run()
    finally:
        await ws_server.shutdown()


__all__ = ["WsReplServer", "run_ws_server"]
