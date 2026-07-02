"""Configuration models for MCP server connections.

Each server carries an explicit ``id`` — used as the namespace prefix for
discovered tools (``<id>__<tool_name>``). The union is discriminated by
``type`` to select the transport (stdio / streamable-http / SSE).
"""

from __future__ import annotations

import os
import typing as t

from pydantic import BaseModel
from pydantic import Field


class _ServerConfigBase(BaseModel):
    """Shared fields for every MCP server config."""

    id: str = Field(
        description=(
            "Stable, user-provided server identifier. Used as the namespace prefix "
            "for discovered tools (``<id>__<tool_name>``). Must be unique within a Toolset."
        ),
    )


class StdioServerConfig(_ServerConfigBase):
    """Connect to an MCP server by spawning a subprocess (stdin/stdout transport)."""

    type: t.Literal["stdio"] = Field(
        default="stdio",
        description="Discriminator — must be 'stdio'.",
    )

    command: str = Field(
        ...,
        description="The executable to run to start the server.",
    )

    args: list[str] = Field(
        default_factory=list,
        description="Command line arguments to pass to the executable.",
    )

    env: dict[str, str] | None = Field(
        default=None,
        description="Environment for the subprocess (merged over the current env).",
    )

    cwd: str | os.PathLike[str] | None = Field(
        default=None,
        description="Working directory for the subprocess.",
    )

    encoding: str = Field(
        default="utf-8",
        description="Text encoding used when talking to the server over stdio.",
    )

    encoding_error_handler: t.Literal["strict", "ignore", "replace"] = Field(
        default="strict",
        description="Codec error handler for stdio I/O.",
    )


class StreamableHttpServerConfig(_ServerConfigBase):
    """Connect to an MCP server via the Streamable HTTP transport."""

    type: t.Literal["streamable_http"] = Field(
        default="streamable_http",
        description="Discriminator — must be 'streamable_http'.",
    )

    url: str = Field(
        ...,
        description="The MCP server endpoint URL.",
    )

    headers: dict[str, str] | None = Field(
        default=None,
        description="Optional headers to include in requests (e.g. auth).",
    )

    timeout: float = Field(
        default=30.0,
        description="HTTP timeout in seconds for regular operations.",
    )


class SseServerConfig(_ServerConfigBase):
    """Connect to an MCP server via the (legacy) SSE transport."""

    type: t.Literal["sse"] = Field(
        default="sse",
        description="Discriminator — must be 'sse'.",
    )

    url: str = Field(..., description="The SSE endpoint URL.")

    headers: dict[str, str] | None = Field(
        default=None,
        description="Optional headers to include in requests.",
    )

    timeout: float = Field(
        default=5.0,
        description="HTTP timeout in seconds for regular operations.",
    )

    sse_read_timeout: float = Field(
        default=300.0,
        description="How long (seconds) to wait for a new SSE event before disconnecting.",
    )


McpServerConfig = t.Annotated[
    StdioServerConfig | StreamableHttpServerConfig | SseServerConfig,
    Field(discriminator="type", description="MCP server configuration (discriminated by 'type')."),
]
"""Discriminated union of all MCP server transport configs."""
