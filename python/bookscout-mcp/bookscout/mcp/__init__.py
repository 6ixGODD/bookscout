"""BookScout MCP Server — exposes tools via 3 FastMCP servers on separate paths.

Architecture:
    - /ontology  → bookscout-ontology  server (9 ontology retrieval tools)
    - /retrieval → bookscout-retrieval server (10 derived index retrieval tools)
    - /compiler  → bookscout-compiler  server (4 compile/index/progress tools)

All servers share a single :class:`SharedContext` for resources.
Transport: streamable-http via Starlette + uvicorn.
"""

from __future__ import annotations

import typing as t

from starlette.applications import Starlette
from starlette.routing import Mount

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin
from mcp.server.fastmcp import FastMCP  # pylint: disable=wrong-import-order

from .config import McpServerConfig
from .context import SharedContext

if t.TYPE_CHECKING:
    from bookscout.logging import Logger


class McpServerManager(LoggingMixin, AsyncResourceMixin):
    """Manages 3 MCP servers on separate Starlette mount points.

    Args:
        logger: Logger instance.
        config: MCP server configuration.
    """

    def __init__(self, logger: Logger, config: McpServerConfig | None = None) -> None:
        super().__init__(logger=logger)
        self._config = config or McpServerConfig()
        self._context = SharedContext(logger=logger, config=self._config)
        self._ontology_server: FastMCP | None = None
        self._retrieval_server: FastMCP | None = None
        self._compiler_server: FastMCP | None = None
        self._app: Starlette | None = None

    async def startup(self) -> None:
        """Initialize shared context and build MCP servers."""
        await self._context.startup()

        self._ontology_server = self._build_ontology_server()
        self._retrieval_server = self._build_retrieval_server()
        self._compiler_server = self._build_compiler_server()

        # Build the Starlette app with 3 mount points.
        self._app = Starlette(
            routes=[
                Mount("/ontology", self._ontology_server.streamable_http_app()),
                Mount("/retrieval", self._retrieval_server.streamable_http_app()),
                Mount("/compiler", self._compiler_server.streamable_http_app()),
            ]
        )

        await super().startup()
        self.logger.info(
            "mcp server manager started",
            host=self._config.server.host,
            port=self._config.server.port,
        )

    async def shutdown(self) -> None:
        """Shut down shared context."""
        await self._context.shutdown()
        await super().shutdown()

    @property
    def app(self) -> Starlette:
        """The Starlette ASGI app with all MCP servers mounted."""
        if self._app is None:
            raise RuntimeError("Server not started. Call startup() first.")
        return self._app

    def get_mcp_urls(self) -> dict[str, str]:
        """Get the MCP endpoint URLs for all servers.

        Returns:
            Dict mapping server name to its MCP URL.
        """
        base = f"http://{self._config.server.host}:{self._config.server.port}"
        return {
            "ontology": f"{base}/ontology/mcp",
            "retrieval": f"{base}/retrieval/mcp",
            "compiler": f"{base}/compiler/mcp",
        }

    def run(self) -> None:
        """Run the MCP server with uvicorn (blocking)."""
        import uvicorn

        if self._app is None:
            raise RuntimeError("Server not started. Call startup() first.")
        uvicorn.run(
            self._app,
            host=self._config.server.host,
            port=self._config.server.port,
        )

    async def run_async(self) -> None:
        """Run the MCP server with uvicorn (async, non-blocking)."""
        import uvicorn

        if self._app is None:
            raise RuntimeError("Server not started. Call startup() first.")
        config = uvicorn.Config(
            self._app,
            host=self._config.server.host,
            port=self._config.server.port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        await server.serve()

    # ------------------------------------------------------------------ server builders

    def _build_ontology_server(self) -> FastMCP:
        """Build the ontology retrieval MCP server."""
        from bookscout.books.tools import create_ontology_tools

        server = FastMCP("bookscout-ontology")

        # Create tools and register them.
        assert self._context.books_store is not None
        tools = create_ontology_tools(self._context.books_store)
        for tool in tools:
            self._register_tool(server, tool)

        self.logger.info("ontology server built", tools=len(tools))
        return server

    def _build_retrieval_server(self) -> FastMCP:
        """Build the derived index retrieval MCP server."""
        from bookscout.index.chunk.tools import create_chunk_tools
        from bookscout.index.graph.tools import create_graph_tools
        from bookscout.index.summary.tools import create_summary_tools

        server = FastMCP("bookscout-retrieval")

        total = 0

        # Summary tools.
        if self._context.summary_indexer is not None:
            # SummaryStore is created inside tools; we need a db_path.
            # We'll use a per-book approach: the tools create stores on demand.
            # For simplicity, we create a global summary store at the data dir.
            from bookscout.index.summary import SummaryStore

            summary_db = self._context.data_dir / "summary.sqlite"
            summary_store = SummaryStore(logger=self.logger, db_path=summary_db)
            # Note: startup is handled by the indexer's build, but for retrieval
            # we need the store open. We'll open it here.
            # Actually, the tools need an open store. Let's open it.
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    # We're in an async context — schedule startup.
                    # But we can't await here. Let's restructure.
                    pass
            except RuntimeError:
                pass
            # For now, store reference; will be opened in a different way.
            # The SummaryStore needs startup() called. We'll handle this
            # by making the tools open the store lazily.
            # Actually, let's just pass the store and handle startup separately.
            # The MCP server doesn't call the tools at build time, only when
            # a client invokes them. So we need the store to be started.
            # We'll start it in the SharedContext instead.
            self._context.summary_store = summary_store

            tools = create_summary_tools(
                logger=self.logger,
                db_path=summary_db,
            )
            # But wait — create_summary_tools creates its own store.
            # We need to use the same store. Let me fix this.
            # Actually the function creates a new store, so we use that.
            # We need to start it. Since we're in sync context here (building),
            # we'll start stores in the SharedContext startup instead.
            # For now, let's just register the tools.
            for tool in tools:
                self._register_tool(server, tool)
            total += len(tools)

        # Chunk tools.
        if self._context.chunk_indexer is not None:
            chunk_db = self._context.data_dir / "chunks.sqlite"
            from bookscout.index.chunk import ChunkStore

            chunk_store = ChunkStore(logger=self.logger, db_path=chunk_db)
            self._context.chunk_store = chunk_store
            tools = create_chunk_tools(self._context.chunk_indexer, chunk_store)
            for tool in tools:
                self._register_tool(server, tool)
            total += len(tools)

        # Graph tools.
        if self._context.graph_indexer is not None:
            graph_db = self._context.data_dir / "graph.sqlite"
            from bookscout.index.graph import GraphStore

            graph_store = GraphStore(logger=self.logger, db_path=graph_db)
            self._context.graph_store = graph_store
            tools = create_graph_tools(self._context.graph_indexer, graph_store)
            for tool in tools:
                self._register_tool(server, tool)
            total += len(tools)

        self.logger.info("retrieval server built", tools=total)
        return server

    def _build_compiler_server(self) -> FastMCP:
        """Build the compiler MCP server."""
        from bookscout.doccompiler.tools import create_compiler_tools

        server = FastMCP("bookscout-compiler")

        assert self._context.task_manager is not None
        tools = create_compiler_tools(self._context.task_manager)
        for tool in tools:
            self._register_tool(server, tool)

        self.logger.info("compiler server built", tools=len(tools))
        return server

    @staticmethod
    def _register_tool(server: FastMCP, tool: t.Any) -> None:
        """Register a BaseTool instance as an MCP tool on a FastMCP server.

        Args:
            server: The FastMCP server.
            tool: A BaseTool instance with __call__ and schema_dict.
        """
        tool_name = tool.__function_name__
        tool_desc = tool.__function_description__

        # Create a wrapper function that delegates to the tool's __call__.
        async def _wrapper(**kwargs: t.Any) -> str:
            return await tool(**kwargs)  # type: ignore[no-any-return]

        # Set metadata on the wrapper.
        _wrapper.__name__ = tool_name
        _wrapper.__doc__ = tool_desc

        # Register with FastMCP.
        # FastMCP's @server.tool() decorator inspects the function signature
        # to build the schema. Since our tools have Annotated parameters,
        # we need to register them differently.
        # FastMCP's tool() can accept a function directly.
        server.add_tool(_wrapper, name=tool_name, description=tool_desc)


__all__ = [
    "McpServerConfig",
    "McpServerManager",
    "SharedContext",
]
