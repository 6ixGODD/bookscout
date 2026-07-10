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
"""Wraps external MCP server tools as :class:`~bookscout.tools.BaseTool` instances.

Usage::

    from bookscout.tools.mcp_toolset import ExternalMcpToolset

    toolset = ExternalMcpToolset(
        configs=[McpServerConfig(name="math", url="http://localhost:8080/mcp")],
        logger=logger,
    )
    await toolset.startup()
    # toolset.tools now contains BaseTool wrappers for each MCP tool
"""

from __future__ import annotations

import inspect
import json
import typing as t
from typing import Annotated

from bookscout.logging import Logger
from bookscout.tools import BaseTool
from bookscout.tools import FunctionDict
from bookscout.tools import Property
from bookscout.tools import SchemaDict
from bookscout.tools.toolset import Toolset

from .mcp_client import McpClient
from .mcp_client import McpClientError


def _make_mcp_wrapper(client: McpClient, tool_def: dict) -> BaseTool:
    """Generate a :class:`BaseTool` subclass instance wrapping a single MCP tool.

    The returned instance's ``__call__`` establishes a fresh connection to the
    MCP server on every invocation and closes it afterwards.  This keeps the
    lifecycle simple at the cost of a per-call handshake — subclasses that need
    persistent connections should manage their own session pool.

    Args:
        client: An :class:`McpClient` instance (used as a reusable context
            manager — each call enters/exits its own ``async with`` block).
        tool_def: A tool definition dict from ``tools/list``, expected to
            contain ``name``, ``description``, and ``inputSchema`` keys.

    Returns:
        A started :class:`BaseTool` instance whose ``__function_name__`` and
        ``__function_description__`` match the MCP tool definition.
    """
    name = tool_def["name"]
    description = tool_def.get("description", f"MCP tool: {name}")
    input_schema = tool_def.get("inputSchema", {})
    properties = input_schema.get("properties", {})

    async def _call(self, **kwargs: t.Any) -> str:  # noqa: ANN401
        try:
            async with client as c:
                await c.initialize()
                return await c.call_tool(name, kwargs)
        except McpClientError as e:
            return f"MCP tool '{name}' error: {e}"
        except Exception as e:
            return f"MCP tool '{name}' unexpected error: {e}"

    def _schema_dict(self) -> SchemaDict:
        """Return the OpenAI-compatible schema using the MCP inputSchema."""
        return SchemaDict(
            type="function",
            function=FunctionDict(
                name=self.__function_name__,  # noqa: F821
                description=self.__function_description__,  # noqa: F821
                parameters=input_schema,
            ),
        )

    # Build the tool class dynamically.
    attrs: dict[str, t.Any] = {
        "__call__": _call,
        "schema_dict": property(_schema_dict),
        "__function_name__": name,
        "__function_description__": description,
        "_tool_def": tool_def,
    }

    # Create Annotated parameters from the JSON Schema properties.
    params: list[inspect.Parameter] = []
    for prop_name, prop_schema in properties.items():
        prop_desc = prop_schema.get("description", "")
        prop_type = prop_schema.get("type", "string")
        if prop_type == "string":
            ann: object = Annotated[str, Property(description=prop_desc)]
        elif prop_type in ("number", "integer"):
            ann = Annotated[float, Property(description=prop_desc)]
        elif prop_type == "boolean":
            ann = Annotated[bool, Property(description=prop_desc)]
        else:
            ann = Annotated[str, Property(description=prop_desc)]
        params.append(
            inspect.Parameter(
                prop_name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=ann,
            )
        )

    # Store params so introspection / schema builders can find them.
    attrs["__mcp_params__"] = params

    # Create the class and instantiate it.
    tool_cls = type(
        f"MCP_{name}",
        (BaseTool,),
        attrs,
        name=name,
        description=description,
    )
    return tool_cls()


class ExternalMcpToolset(Toolset):
    """Toolset connecting to external MCP servers.

    Each server's tools are wrapped as :class:`BaseTool` instances and
    registered in this toolset.  Connection failures are non-fatal — the
    server is skipped with a warning logged.

    Args:
        configs: List of config objects.  Each is expected to have at least
            ``name`` (str) and ``url`` (str | None) attributes, matching the
            shape of :class:`~bookscout.repl.config.McpServerConfig`.
        logger: Logger instance.
    """

    def __init__(
        self,
        configs: list[t.Any],
        logger: Logger,
    ) -> None:
        super().__init__(
            name="external_mcp",
            description="External MCP server tools.",
            tools=[],
            logger=logger,
        )
        self._configs = configs

    async def startup(self) -> None:
        """Connect to each configured MCP server and discover its tools.

        Servers that are unreachable or return errors are skipped — a warning
        is logged but no exception is raised.
        """
        tools: list[BaseTool] = []
        for cfg in self._configs:
            self.logger.info("Connecting to MCP server", name=cfg.name)
            try:
                if cfg.url:
                    async with McpClient(cfg.url) as client:
                        await client.initialize()
                        tool_list = await client.list_tools()
                        for tool_def in tool_list:
                            wrapped = _make_mcp_wrapper(client, tool_def)
                            tools.append(wrapped)
                            self.logger.debug(
                                "Registered MCP tool",
                                server=cfg.name,
                                tool=tool_def["name"],
                            )
                elif cfg.command:
                    self.logger.warning(
                        "stdio MCP not yet implemented",
                        name=cfg.name,
                    )
            except McpClientError as e:
                self.logger.warning(
                    "MCP server unavailable",
                    name=cfg.name,
                    error=str(e),
                )
            except Exception as e:
                self.logger.warning(
                    "MCP server error",
                    name=cfg.name,
                    error=str(e),
                )

        self.internal_tools = tools  # type: ignore[assignment]
        await super().startup()


__all__ = [
    "ExternalMcpToolset",
    "_make_mcp_wrapper",
]
