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
"""Minimal MCP streamable HTTP client for tool discovery and invocation.

This client implements the `streamable HTTP`_ transport for the Model Context
Protocol.  It is intentionally minimal — it does not depend on the ``mcp`` SDK
and only supports the ``tools/list`` and ``tools/call`` methods.

.. _streamable HTTP:
    https://spec.modelcontextprotocol.io/specification/2025-03-26/basic/transports/
"""

from __future__ import annotations

import json
import typing as t

import httpx


class McpClientError(Exception):
    """Raised when an MCP request fails (HTTP error, protocol error)."""


class McpClient:
    """Connects to an MCP server over streamable HTTP, discovers and invokes tools.

    Usage as an async context manager (supports re-entry)::

        async with McpClient("http://localhost:8080/mcp") as client:
            await client.initialize()
            tools = await client.list_tools()
            result = await client.call_tool("some_tool", {"arg": "val"})

    Args:
        url: The MCP server endpoint URL.
        timeout: HTTP timeout in seconds (default 30.0).
    """

    def __init__(self, url: str, *, timeout: float = 30.0) -> None:
        self._url = url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> McpClient:
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *args: t.Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post(self, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC POST request and return the parsed response."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": 1,
        }
        resp = await self._client.post(self._url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise McpClientError(f"MCP server returned {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    async def initialize(self) -> dict:
        """Send an ``initialize`` request to the MCP server.

        Returns:
            The server's ``InitializeResult`` as a raw dict.
        """
        return await self._post(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "bookscout", "version": "0.2.0"},
            },
        )

    async def list_tools(self) -> list[dict]:
        """List all tools exposed by the MCP server.

        Returns:
            A list of tool definition dicts, each containing ``name``,
            ``description``, and ``inputSchema`` keys.
        """
        result = await self._post("tools/list")
        return result.get("result", {}).get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> str:
        """Call a tool on the MCP server.

        Args:
            name: The tool name.
            arguments: A dict of keyword arguments for the tool.

        Returns:
            The concatenated text content of the result blocks.
        """
        result = await self._post("tools/call", {"name": name, "arguments": arguments})
        content = result.get("result", {}).get("content", [])
        if content and isinstance(content, list):
            return "\n".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
        return json.dumps(content)


__all__ = [
    "McpClient",
    "McpClientError",
]
