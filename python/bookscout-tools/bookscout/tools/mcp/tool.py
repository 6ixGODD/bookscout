"""MCP-discovered tool — a :class:`bookscout.tools.BaseTool` backed by a live MCP session.

Each discovered tool is materialised as a dynamically-built subclass of
:class:`MCPDiscoveredTool` (see :mod:`bookscout.tools.mcp.session`), so the
class-level metadata (``__function_name__`` etc.) reflects the MCP ``Tool``
definition, and the instance's ``_session`` references the live
:class:`mcp.client.session.ClientSession`.
"""

from __future__ import annotations

import typing as t

from bookscout.tools import BaseTool
from bookscout.tools import FunctionDict
from bookscout.tools import SchemaDict

from .exceptions import McpToolCallError

if t.TYPE_CHECKING:
    from mcp.client.session import ClientSession


class MCPDiscoveredTool(BaseTool):
    """A :class:`BaseTool` whose definition comes from an MCP server.

    Subclasses are built dynamically at discovery time (one per MCP tool).
    The dynamic subclass sets these class variables in its namespace dict:

    - ``__function_name__`` — the namespaced name ``<server_id>__<raw_name>``
      (what the LLM sees and calls).
    - ``__function_description__`` — the MCP tool's description.
    - ``__raw_name__`` — the original MCP tool name (used for ``call_tool``).
    - ``__server_id__`` — the owning server's id.
    - ``__input_schema__`` — the MCP tool's ``inputSchema`` (a JSON Schema dict).

    The instance's ``_session`` is wired to the live ``ClientSession`` by the
    owning :class:`bookscout.tools.mcp.session.McpClient`.
    """

    # Declared here for type-checkers; the real values are set on each
    # dynamic subclass at discovery time.
    __function_name__: t.ClassVar[str]
    __function_description__: t.ClassVar[str]
    __raw_name__: t.ClassVar[str]
    __server_id__: t.ClassVar[str]
    __input_schema__: t.ClassVar[dict[str, t.Any]]

    # Live session reference, set after the owning McpClient has started.
    _session: ClientSession

    def __init_subclass__(cls, **kwargs: t.Any) -> None:
        # Override BaseTool.__init_subclass__: MCP tools get their name /
        # description from the dynamic-subclass namespace dict, NOT from the
        # ``name=`` / ``description=`` class kwargs. Skip the
        # ``_pascal_to_snake(cls.__name__)`` fallback so our explicit values win.
        super(BaseTool, cls).__init_subclass__(**kwargs)  # pylint: disable=bad-super-call

    @property
    def schema_dict(self) -> SchemaDict:
        """OpenAI-compatible schema dict built directly from the MCP inputSchema.

        Overrides :class:`BaseTool.schema_dict` because MCP tools already carry a
        JSON Schema — there is no Python ``__call__`` signature to introspect.
        """
        # return {
        #     "type": "function",
        #     "function": {
        #         "name": self.__function_name__,
        #         "description": self.__function_description__,
        #         "parameters": self.__input_schema__,
        #     },
        # }
        return SchemaDict(
            type="function",
            function=FunctionDict(
                name=self.__function_name__,
                description=self.__function_description__,
                parameters=self.__input_schema__,
            ),
        )

    async def __call__(self, **kwargs: t.Any) -> str:
        """Execute the tool against the live MCP session.

        Returns the concatenated ``text`` content of the result blocks (a
        ``str``), so the existing :class:`bookscout.tools.ToolExecutor`
        contract (``str | list[str]``) is preserved unchanged. If the server
        reports ``isError`` the returned string is prefixed with
        ``"[tool error] "``.

        Args:
            **kwargs: Tool arguments, forwarded as the MCP ``arguments`` dict.

        Returns:
            The tool's text result.

        Raises:
            McpToolCallError: If the ``call_tool`` request itself fails at the
                transport/session level (not when the tool reports ``isError``).
        """
        session = getattr(self, "_session", None)
        if session is None:
            raise McpToolCallError(
                self.__server_id__,
                self.__raw_name__,
                "MCP session not wired — was the owning Toolset started?",
            )

        try:
            result = await session.call_tool(self.__raw_name__, kwargs)
        except Exception as exc:  # transport / protocol level failure
            raise McpToolCallError(self.__server_id__, self.__raw_name__, str(exc)) from exc

        # Extract text from content blocks.
        parts: list[str] = []
        for block in result.content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
            else:
                # Non-text blocks (image/audio/resource) are summarised as a
                # placeholder to keep the str contract simple.
                block_type = getattr(block, "type", "unknown")
                parts.append(f"[{block_type} content omitted]")

        text = "".join(parts).strip()
        if getattr(result, "isError", False):
            text = f"[tool error] {text}".strip()
        return text or "[no content]"
