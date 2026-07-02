"""Lifecycle and discovery for a single MCP server connection.

:class:`McpClient` owns one :class:`mcp.client.session.ClientSession` plus its
transport. ``startup()`` opens the transport (selected by the config
``type``) and initializes the session; ``discover()`` lists the server's tools
and materializes each as an :class:`bookscout.tools.mcp.tool.MCPDiscoveredTool`;
``shutdown()`` closes everything.
"""

from __future__ import annotations

import contextlib
import typing as t

from anyio.streams.memory import MemoryObjectReceiveStream
from anyio.streams.memory import MemoryObjectSendStream
from mcp.client.session import ClientSession
from mcp.shared.message import SessionMessage
from mcp.types import Implementation
from mcp.types import Tool

from bookscout.core.lib.version import Version
from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin

from .config import SseServerConfig
from .config import StdioServerConfig
from .config import StreamableHttpServerConfig
from .exceptions import McpConnectionError
from .exceptions import McpDiscoveryError
from .tool import MCPDiscoveredTool

if t.TYPE_CHECKING:
    from bookscout.logging import Logger

    from .config import McpServerConfig


def _client_info() -> Implementation:
    """Build the client ``Implementation`` sent during ``initialize``."""
    try:
        ver = str(Version("0.1.0"))
    # pylint: disable-next=broad-exception-caught
    except Exception:  # pragma: no cover
        ver = "0.1.0"
    return Implementation(name="bookscout-tools", version=ver)


class McpClient(LoggingMixin, AsyncResourceMixin):
    """Lifecycle + discovery wrapper for a single MCP server.

    Args:
        logger: Logger instance.
        config: One of the discriminated :data:`McpServerConfig` configs.
    """

    def __init__(self, logger: Logger, config: McpServerConfig) -> None:
        super().__init__(logger=logger)
        self.config = config
        self.exit_stack: contextlib.AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._tools: list[MCPDiscoveredTool] = []

    @property
    def server_id(self) -> str:
        """The namespace prefix for this server's discovered tools."""
        return self.config.id

    @property
    def tools(self) -> list[MCPDiscoveredTool]:
        """The tools discovered during the last :meth:`discover` call."""
        return list(self._tools)

    async def startup(self) -> None:
        """Open the transport and initialize the MCP session."""
        # noinspection PyAbstractClass
        self.exit_stack = contextlib.AsyncExitStack()
        try:
            read_stream, write_stream = await self._open_transport(self.exit_stack)
        except Exception as exc:
            await self.exit_stack.aclose()
            self.exit_stack = None
            raise McpConnectionError(self.config.id, f"transport open failed: {exc}") from exc

        try:
            session = ClientSession(
                read_stream,
                write_stream,
                client_info=_client_info(),
            )
            await self.exit_stack.enter_async_context(session)
            await session.initialize()
        except Exception as exc:
            await self.exit_stack.aclose()
            self.exit_stack = None
            self._session = None
            raise McpConnectionError(self.config.id, f"initialize failed: {exc}") from exc

        self._session = session
        self.logger.info(
            "MCP server connected",
            server_id=self.config.id,
            transport=self.config.type,
        )
        await super().startup()

    async def shutdown(self) -> None:
        """Close the transport and session."""
        if self.exit_stack is not None:
            await self.exit_stack.aclose()
            self.exit_stack = None
        self._session = None
        self._tools = []
        self.logger.info("MCP server disconnected", server_id=self.config.id)

    async def discover(self) -> list[MCPDiscoveredTool]:
        """List the server's tools and build :class:`MCPDiscoveredTool` instances.

        Honours server capabilities: if the server did not advertise a tools
        capability during ``initialize``, returns an empty list.

        Returns:
            The freshly-discovered tools (also stored on ``self.tools``).
        """
        if self._session is None:
            raise McpDiscoveryError(self.config.id, "session not started")

        capabilities = self._session.get_server_capabilities()
        if capabilities is None or getattr(capabilities, "tools", None) is None:
            self.logger.info(
                "MCP server has no tools capability — discovered 0 tools",
                server_id=self.config.id,
            )
            self._tools = []
            return []

        try:
            result = await self._session.list_tools()
        except Exception as exc:
            raise McpDiscoveryError(self.config.id, f"list_tools failed: {exc}") from exc

        tools: list[MCPDiscoveredTool] = []
        for raw_tool in result.tools:
            tool = self._build_tool(raw_tool)
            tools.append(tool)

        self._tools = tools
        self.logger.info(
            "MCP discovery complete",
            server_id=self.config.id,
            tool_count=len(tools),
            tools=[t.__function_name__ for t in tools],
        )
        return tools

    def _build_tool(self, raw_tool: Tool) -> MCPDiscoveredTool:
        """Build a dynamically-typed :class:`MCPDiscoveredTool` for one MCP tool.

        Uses :func:`type` to create a subclass whose ``ClassVar`` metadata mirrors
        the MCP ``Tool``; wires the instance to the live session.
        """
        raw_name = raw_tool.name
        namespaced = f"{self.server_id}__{raw_name}"
        class_name = f"MCPTool_{self.server_id}_{raw_name}"

        tool_cls = type(
            class_name,
            (MCPDiscoveredTool,),
            {
                "__function_name__": namespaced,
                "__function_description__": raw_tool.description or "",
                "__raw_name__": raw_name,
                "__server_id__": self.server_id,
                "__input_schema__": dict(raw_tool.inputSchema),
            },
        )
        instance = tool_cls()  # type: MCPDiscoveredTool
        # Wire the live session so __call__ can reach it.
        instance._session = self._session  # type: ignore[assignment]
        return t.cast(MCPDiscoveredTool, instance)  # type: ignore[redundant-cast]

    async def _open_transport(
        self,
        stack: contextlib.AsyncExitStack,
    ) -> tuple[MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage]]:
        """Open the transport for this server's config type.

        Returns a ``(read_stream, write_stream)`` tuple. The third element
        yielded by the streamable-http transport (``get_session_id``) is
        discarded — :class:`ClientSession` does not need it.
        """
        config = self.config

        if isinstance(config, StdioServerConfig):
            from mcp.client.stdio import StdioServerParameters  # pylint: disable=import-outside-toplevel
            from mcp.client.stdio import stdio_client  # pylint: disable=import-outside-toplevel

            params = StdioServerParameters(
                command=config.command,
                args=config.args,
                env=config.env,
                cwd=config.cwd,  # type: ignore[arg-type]
                encoding=config.encoding,
                encoding_error_handler=config.encoding_error_handler,
            )
            transport = await stack.enter_async_context(stdio_client(params))
            return transport[0], transport[1]

        if isinstance(config, StreamableHttpServerConfig):
            import httpx  # pylint: disable=import-outside-toplevel
            from mcp.client.streamable_http import streamable_http_client  # pylint: disable=import-outside-toplevel

            http_client = httpx.AsyncClient(
                headers=config.headers or {},
                timeout=config.timeout,
            )
            await stack.enter_async_context(http_client)
            transport = await stack.enter_async_context(
                streamable_http_client(config.url, http_client=http_client),
            )
            return transport[0], transport[1]

        if isinstance(config, SseServerConfig):
            from mcp.client.sse import sse_client  # pylint: disable=import-outside-toplevel

            # ``headers`` already covers any Authorization header; the optional
            # ``auth`` (an httpx.Auth handler) is left as None for simplicity.
            transport = await stack.enter_async_context(
                sse_client(
                    config.url,
                    headers=config.headers,
                    timeout=config.timeout,
                    sse_read_timeout=config.sse_read_timeout,
                ),
            )
            return transport[0], transport[1]

        # Unreachable — the discriminated union covers all three types.
        raise McpConnectionError(self.config.id, f"unsupported config type: {type(config).__name__}")
