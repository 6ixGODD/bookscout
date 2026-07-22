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
"""BookScout MCP Server — stdio transport.

A single FastMCP server that exposes all BookScout tools over stdio.
This is the recommended transport for desktop MCP clients (Claude Code,
Cursor, Codex, etc.) — the client starts the process and communicates
via stdin/stdout, no HTTP server needed.

Usage::

    # Direct invocation (client manages the process):
    bookscout-mcp

    # Or via Python:
    python -m bookscout.mcp

    # With custom config:
    BOOKSCOUT_DATA_DIR=/path/to/data bookscout-mcp
"""

from __future__ import annotations

import asyncio
import pathlib
import typing as t

from bookscout.logging import LoggingConfig
from bookscout.logging import build_logger
from mcp.server.fastmcp import FastMCP

from .config import McpServerConfig
from .context import SharedContext


def build_stdio_server(context: SharedContext) -> FastMCP:
    """Build a single FastMCP server with all BookScout tools.

    Tools are registered based on what the *already-started* context
    provides — e.g. if no LLM is configured, summary / graph tools
    are simply omitted.

    Args:
        context: An initialized (startup already called) SharedContext.

    Returns:
        A :class:`FastMCP` server ready to run via stdio.
    """
    logger = context.logger
    server = FastMCP("bookscout")

    # -- Ontology tools --
    if context.books_store is not None:
        from bookscout.books.tools import create_ontology_tools

        for tool in create_ontology_tools(context.books_store):
            _register_tool(server, tool)

    # -- Retrieval tools --
    if context.summary_indexer is not None:
        from bookscout.index.summary.tools import create_summary_tools

        for tool in create_summary_tools(
            logger=logger,
            db_path=context.data_dir / "summary.sqlite",
        ):
            _register_tool(server, tool)

    if context.chunk_indexer is not None and context.chunk_store is not None:
        from bookscout.index.chunk.tools import create_chunk_tools

        for tool in create_chunk_tools(context.chunk_indexer, context.chunk_store):
            _register_tool(server, tool)

    if context.graph_indexer is not None and context.graph_store is not None:
        from bookscout.index.graph.tools import create_graph_tools

        for tool in create_graph_tools(context.graph_indexer, context.graph_store):
            _register_tool(server, tool)

    # -- Compiler tools --
    if context.task_manager is not None:
        from bookscout.doccompiler.tools import create_compiler_tools

        for tool in create_compiler_tools(context.task_manager):
            _register_tool(server, tool)

    return server


def _register_tool(server: FastMCP, tool: t.Any) -> None:
    """Register a BaseTool instance as an MCP tool on a FastMCP server.

    This mirrors the same pattern used by the HTTP MCP server in
    :class:`McpServerManager`.

    Args:
        server: The FastMCP server.
        tool: A BaseTool instance with ``__call__`` and ``__function_name__``.
    """
    tool_name = tool.__function_name__
    tool_desc = tool.__function_description__

    async def _wrapper(**kwargs: t.Any) -> str:
        return await tool(**kwargs)  # type: ignore[no-any-return]

    _wrapper.__name__ = tool_name
    _wrapper.__doc__ = tool_desc

    server.add_tool(_wrapper, name=tool_name, description=tool_desc)


def _load_config() -> McpServerConfig:
    """Load config from env vars and optional YAML file."""
    # Try ~/.bookscout/config.yaml first.
    yaml_path = pathlib.Path.home() / ".bookscout" / "config.yaml"
    if yaml_path.exists():
        return McpServerConfig.from_yaml(yaml_path)
    return McpServerConfig()


async def _run_stdio() -> None:
    """Build and run the stdio MCP server."""
    config = _load_config()
    logger = build_logger(LoggingConfig(name="bookscout.mcp"))
    context = SharedContext(logger=logger, config=config)

    # Start context FIRST so that stores / indexers are available.
    await context.startup()

    server = build_stdio_server(context)

    try:
        await server.run_stdio_async()
    finally:
        await context.shutdown()


def main() -> None:
    """CLI entry point for the stdio MCP server."""
    asyncio.run(_run_stdio())


__all__ = ["build_stdio_server", "main"]
