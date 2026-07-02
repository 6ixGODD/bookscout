"""Toolset — a governance primitive for grouping tools.

A :class:`Toolset` is the unit of tool visibility for an agent. It groups:

- *internal* tools (plain :class:`bookscout.tools.BaseTool` instances), and/or
- *MCP-discovered* tools (built from a set of :data:`McpServerConfig`s).

Multiple toolsets can be merged (``+`` / :meth:`merge`) to build the agent's
final, deduplicated tool list. The merge conflict policy is configurable
per-merge (``"raise"`` or ``"keep_first"``).
"""

from __future__ import annotations

import typing as t

from bookscout.core.lib.utils import gen_id
from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin
from bookscout.tools import BaseTool

from .mcp.config import McpServerConfig
from .mcp.session import McpClient
from .mcp.tool import MCPDiscoveredTool

if t.TYPE_CHECKING:
    from bookscout.logging import Logger


class ToolConflictError(Exception):
    """Raised when two tools share a name during a merge with
    ``on_conflict='raise'``.

    Attributes:
        name: The conflicting tool name.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Tool name conflict: {name!r} (use on_conflict='keep_first' to override)")


OnConflict = t.Literal["raise", "keep_first"]
"""Conflict resolution policy for merges and appends."""


class Toolset(LoggingMixin, AsyncResourceMixin):
    """A named, metadata-bearing collection of tools.

    Args:
        id: Stable toolset identifier. Auto-generated if omitted.
        name: Human-readable toolset name.
        description: Optional description.
        tools: Internal :class:`BaseTool` instances (source-agnostic).
        mcp_servers: MCP server configs to discover tools from at startup.
        logger: Logger instance.
    """

    def __init__(
        self,
        *,
        id: str | None = None,  # pylint: disable=redefined-builtin
        name: str,
        description: str = "",
        tools: t.Sequence[BaseTool] = (),
        mcp_servers: t.Sequence[McpServerConfig] = (),
        logger: Logger | None = None,
    ) -> None:
        if logger is None:
            raise ValueError("logger is required")
        super().__init__(logger=logger)
        self.id = id or gen_id(prefix="toolset_")
        self.name = name
        self.description = description
        self.internal_tools: list[BaseTool] = list(tools)
        self.mcp_servers: list[McpServerConfig] = list(mcp_servers)
        self.mcp_clients: list[McpClient] = []
        self.discovered_tools: list[MCPDiscoveredTool] = []

        # Validate server id uniqueness within this toolset up front.
        seen_ids: set[str] = set()
        for srv in self.mcp_servers:
            if srv.id in seen_ids:
                raise ValueError(f"Duplicate MCP server id within Toolset: {srv.id!r}")
            seen_ids.add(srv.id)

    async def startup(self) -> None:
        """Connect each MCP server and discover its tools."""
        for srv_config in self.mcp_servers:
            client = McpClient(logger=self.logger, config=srv_config)
            try:
                await client.startup()
                await client.discover()
            except Exception:
                # Best-effort: shut down already-started clients before propagating.
                await client.shutdown()
                raise
            self.mcp_clients.append(client)
            self.discovered_tools.extend(client.tools)

        self.logger.info(
            "Toolset started",
            toolset_id=self.id,
            name=self.name,
            internal_tools=len(self.internal_tools),
            mcp_tools=len(self.discovered_tools),
        )
        await super().startup()

    async def shutdown(self) -> None:
        """Close all MCP clients (in reverse order of startup)."""
        for client in reversed(self.mcp_clients):
            await client.shutdown()
        self.mcp_clients = []
        self.discovered_tools = []
        self.logger.info("Toolset stopped", toolset_id=self.id)

    @property
    def tools(self) -> list[BaseTool]:
        """All tools in this toolset: internal + MCP-discovered, deduped by name.

        Internal tools take precedence over discovered tools with the same name
        (an internal tool intentionally shadows an MCP one).
        """
        merged: list[BaseTool] = []
        seen: set[str] = set()
        for tool in [*self.internal_tools, *self.discovered_tools]:
            name = tool.__function_name__
            if name in seen:
                continue
            seen.add(name)
            merged.append(tool)
        return merged

    def __iter__(self) -> t.Iterator[BaseTool]:
        return iter(self.tools)

    def __len__(self) -> int:
        return len(self.tools)

    def merge(
        self,
        other: Toolset,
        *,
        on_conflict: OnConflict = "raise",
    ) -> Toolset:
        """Merge another Toolset into a NEW Toolset and return it.

        The result is a *plain* toolset (no MCP servers of its own) that borrows
        the tool instances of both operands — including any
        :class:`MCPDiscoveredTool` instances, which keep referencing their
        original live sessions. **The operands must outlive the merged result.**

        Args:
            other: The toolset to merge in.
            on_conflict: ``"raise"`` (default) raises :class:`ToolConflictError`
                on a name collision; ``"keep_first"`` keeps ``self``'s tool and
                logs a warning.

        Returns:
            A new started :class:`Toolset` (lifecycle-independent — its tools
            are plain refs, so it needs no startup of its own).
        """
        merged_tools: list[BaseTool] = []
        seen: dict[str, BaseTool] = {}
        for tool in [*self.tools, *other.tools]:
            name = tool.__function_name__
            if name in seen:
                if on_conflict == "raise":
                    raise ToolConflictError(name)
                self.logger.warning(
                    "Tool name conflict resolved with keep_first",
                    name=name,
                    kept=self.__class__.__name__,
                )
                continue
            seen[name] = tool
            merged_tools.append(tool)

        result = Toolset(
            id=gen_id(prefix="toolset_"),
            name=f"{self.name}+{other.name}",
            description=f"Merged toolset ({self.name} + {other.name})",
            tools=merged_tools,
            logger=self.logger,
        )
        # Mark as started (no MCP servers → startup is a no-op, but keep the
        # lifecycle consistent so .tools works without an explicit startup()).
        result._mark_started()
        self.logger.info(
            "Merged toolsets",
            left=self.id,
            right=other.id,
            result=result.id,
            tool_count=len(merged_tools),
        )
        return result

    def __add__(self, other: Toolset) -> Toolset:
        """Merge two toolsets, raising on name conflict.

        Equivalent to ``self.merge(other, on_conflict="raise")``.
        """
        return self.merge(other, on_conflict="raise")

    def append(
        self,
        tool: BaseTool,
        *,
        on_conflict: OnConflict = "raise",
    ) -> None:
        """Append a single :class:`BaseTool` to this toolset.

        Args:
            tool: The tool to add.
            on_conflict: ``"raise"`` (default) raises :class:`ToolConflictError`
                if a tool with the same name already exists; ``"keep_first"``
                keeps the existing tool and logs a warning.
        """
        name = tool.__function_name__
        for existing in self.internal_tools:
            if existing.__function_name__ == name:
                if on_conflict == "raise":
                    raise ToolConflictError(name)
                self.logger.warning(
                    "Tool name conflict resolved with keep_first on append",
                    name=name,
                )
                return
        self.internal_tools.append(tool)
        self.logger.debug("Appended tool", name=name, toolset_id=self.id)

    def _mark_started(self) -> None:
        """Mark a plain (no-MCP) toolset as started without running startup."""
        import time

        self._startup_time = time.perf_counter()  # pylint: disable=attribute-defined-outside-init
