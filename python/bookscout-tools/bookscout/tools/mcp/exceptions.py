"""Exception hierarchy for the MCP tool-discovery sub-feature.

Kept separate from ``bookscout.tools`` proper so the base ``__init__.py``
stays stable.
"""

from __future__ import annotations


class McpError(Exception):
    """Base exception for all MCP-related errors."""


class McpConnectionError(McpError):
    """Raised when a connection to an MCP server cannot be established or is
    lost.

    Attributes:
        server_id: The id of the server config that failed to connect.
    """

    def __init__(self, server_id: str, message: str) -> None:
        self.server_id = server_id
        super().__init__(f"[server={server_id!r}] {message}")


class McpDiscoveryError(McpError):
    """Raised when tool discovery (``list_tools``) fails for a server.

    Attributes:
        server_id: The id of the server config whose discovery failed.
    """

    def __init__(self, server_id: str, message: str) -> None:
        self.server_id = server_id
        super().__init__(f"[server={server_id!r}] {message}")


class McpToolCallError(McpError):
    """Raised when calling a discovered MCP tool fails at the transport/session
    level.

    (Distinct from an MCP ``CallToolResult.isError=True`` — that is surfaced as
    a normal string return prefixed with ``[tool error]``; this is raised when
    the call itself cannot complete.)

    Attributes:
        server_id: The id of the server that owns the tool.
        tool_name: The raw MCP tool name that was called.
    """

    def __init__(self, server_id: str, tool_name: str, message: str) -> None:
        self.server_id = server_id
        self.tool_name = tool_name
        super().__init__(f"[server={server_id!r}, tool={tool_name!r}] {message}")
