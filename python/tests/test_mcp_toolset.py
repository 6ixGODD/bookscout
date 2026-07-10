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
"""Tests for MCP toolset integration."""

from __future__ import annotations

import typing as t

import pytest

from bookscout.repl.config import McpServerConfig
from bookscout.tools import BaseTool
from bookscout.tools import McpClient
from bookscout.tools import McpClientError
from bookscout.tools.mcp_client import McpClient as McpClientDirect


class TestMcpServerConfig:
    """Model-level tests for the REPL ``McpServerConfig``."""

    def test_url_construct(self) -> None:
        cfg = McpServerConfig(name="test", url="http://localhost:8080/mcp")
        assert cfg.name == "test"
        assert cfg.url == "http://localhost:8080/mcp"

    def test_optional_fields(self) -> None:
        cfg = McpServerConfig(name="test")
        assert cfg.url is None
        assert cfg.command is None

    def test_defaults(self) -> None:
        cfg = McpServerConfig(name="math")
        assert cfg.args == []
        assert cfg.env == {}


class TestMcpClientConstruction:
    """Construction and reusability of the minimal HTTP client."""

    def test_construct(self) -> None:
        client = McpClient("http://localhost:8080/mcp")
        assert client._url == "http://localhost:8080/mcp"  # type: ignore[attr-defined]

    def test_construct_trail_slash_stripped(self) -> None:
        client = McpClient("http://localhost:8080/mcp/")
        assert client._url == "http://localhost:8080/mcp"  # type: ignore[attr-defined]

    def test_construct_direct_import(self) -> None:
        """The re-export from ``bookscout.tools`` is the same class."""
        from bookscout.tools.mcp_client import McpClient as McpClientDirectSrc

        assert McpClient is McpClientDirectSrc
        assert McpClient is McpClientDirect

    def test_async_context_manager_reusable(self) -> None:
        """The client can be entered/exited multiple times."""
        client = McpClient("http://localhost:9999/")
        # Simulating multiple enter/exit without actual HTTP calls.
        # This just verifies no internal state prevents re-entry.
        import asyncio

        async def run() -> None:
            async with client:
                pass
            async with client:
                pass

        asyncio.run(run())

    async def test_list_tools_before_init_raises(self) -> None:
        """Calling ``list_tools`` without ``initialize`` should not crash."""
        # This test just verifies the method can be called (HTTP will fail in
        # unit test context but the code path should be exercised).
        client = McpClient("http://localhost:9999/")
        async with client:
            with pytest.raises(Exception):
                await client.list_tools()


class TestMakeMcpWrapper:
    """Tests for ``_make_mcp_wrapper`` function."""

    def test_wrapper_type(self) -> None:
        """Verify the wrapper is a BaseTool subclass with metadata."""
        from bookscout.tools.mcp_toolset import _make_mcp_wrapper

        client = McpClient("http://localhost:9999/")
        tool_def = {
            "name": "add",
            "description": "Add two numbers",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "a": {"type": "integer", "description": "First operand"},
                    "b": {"type": "integer", "description": "Second operand"},
                },
                "required": ["a", "b"],
            },
        }
        tool = _make_mcp_wrapper(client, tool_def)
        assert isinstance(tool, BaseTool)
        assert tool.__function_name__ == "add"
        assert tool.__function_description__ == "Add two numbers"

    def test_wrapper_schema(self) -> None:
        """The wrapper's schema_dict should use the MCP inputSchema."""
        from bookscout.tools.mcp_toolset import _make_mcp_wrapper

        client = McpClient("http://localhost:9999/")
        input_schema = {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "A value"},
            },
        }
        tool_def = {
            "name": "double",
            "description": "Double a number",
            "inputSchema": input_schema,
        }
        tool = _make_mcp_wrapper(client, tool_def)
        schema = tool.schema_dict
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "double"
        assert schema["function"]["parameters"] is input_schema

    def test_wrapper_no_params(self) -> None:
        """Tools with no input schema properties still work."""
        from bookscout.tools.mcp_toolset import _make_mcp_wrapper

        client = McpClient("http://localhost:9999/")
        tool_def = {
            "name": "ping",
            "description": "Health check",
            "inputSchema": {"type": "object", "properties": {}},
        }
        tool = _make_mcp_wrapper(client, tool_def)
        assert isinstance(tool, BaseTool)
        assert tool.__function_name__ == "ping"

    def test_wrapper_call_returns_error_on_no_connection(self) -> None:
        """Calling a wrapper without a server returns an error string (no crash)."""
        from bookscout.tools.mcp_toolset import _make_mcp_wrapper

        client = McpClient("http://localhost:9999/")
        tool_def = {
            "name": "fail_tool",
            "description": "Will fail",
            "inputSchema": {"type": "object", "properties": {}},
        }
        tool = _make_mcp_wrapper(client, tool_def)

        import asyncio

        async def run() -> None:
            result = await tool()
            assert isinstance(result, str)
            # Should contain an error message (connection refused).
            assert "error" in result.lower() or "MCP tool" in result

        asyncio.run(run())


class TestExternalMcpToolset:
    """Tests for the ExternalMcpToolset itself."""

    @staticmethod
    def _make_logger() -> t.Any:
        """Build a minimal project logger for tests."""
        from bookscout.logging import LoggingConfig
        from bookscout.logging import build_logger

        return build_logger(LoggingConfig(name="test", level="ERROR", targets=[]))

    def test_empty_configs(self) -> None:
        """An ExternalMcpToolset with no configs has no tools."""
        from bookscout.tools.mcp_toolset import ExternalMcpToolset

        import asyncio

        async def run() -> None:
            toolset = ExternalMcpToolset(
                configs=[],
                logger=self._make_logger(),
            )
            await toolset.startup()
            assert len(toolset.tools) == 0
            await toolset.shutdown()

        asyncio.run(run())

    def test_with_config_no_url(self) -> None:
        """A config with command (no URL) logs a warning but doesn't crash."""
        from bookscout.repl.config import McpServerConfig
        from bookscout.tools.mcp_toolset import ExternalMcpToolset

        import asyncio

        async def run() -> None:
            cfg = McpServerConfig(name="stdio_only", command="some-command")
            toolset = ExternalMcpToolset(
                configs=[cfg],
                logger=self._make_logger(),
            )
            # Should not raise — the startup handles it gracefully.
            await toolset.startup()
            assert len(toolset.tools) == 0
            await toolset.shutdown()

        asyncio.run(run())
