"""LLM tool-driven ontology builder (spec §8, §9).

Builds a ``BookNode`` tree by feeding tagged content chunks to an LLM and
letting it call tools to express metadata and node operations.

Architecture:
    - :class:`BuildState` holds mutable state (metadata, node stack, TOC ref).
    - Tools (BaseTool subclasses) mutate BuildState via a shared reference.
    - :class:`LlmToolBuilder` orchestrates: split → tagify → context → tool loop.
    - The SKILL document tells the LLM how to use the tools.
"""

from __future__ import annotations

import dataclasses
import typing as t
from typing import Annotated

from bookscout.books import BookNode
from bookscout.core.lib.utils import gen_id
from bookscout.tools import BaseTool
from bookscout.tools import Property

from . import BuildResult
from . import Builder
from .metadata import ExtractedMetadata
from .tagify import TagMap
from .tagify import tagify_chunk

if t.TYPE_CHECKING:
    from bookscout.llm import ChatModel
    from bookscout.logging import Logger

# Default config values.
DEFAULT_CHUNK_CHARS = 3000
DEFAULT_NEIGHBOR_CHARS = 50
DEFAULT_MAX_TOOL_ITERATIONS = 20
DEFAULT_MAX_ROLLBACK_PER_CHUNK = 1
MAX_NEIGHBOR_CHARS = 500


@dataclasses.dataclass(slots=True)
class TocReferenceItem:
    """A single TOC reference entry (spec §9.5)."""

    title: str
    level: int
    order_index: int


@dataclasses.dataclass(slots=True)
class _OpenNode:
    """An in-progress node on the build stack."""

    node_id: str
    book_id: str
    parent_id: str
    level: int
    order_index: int
    title: str
    title_offset: int
    title_length: int
    content_start: int
    content_end: int


@dataclasses.dataclass
class BuildState:
    """Mutable state shared between the builder and all tool instances.

    Attributes:
        book_id: The book being built.
        content: The full CONTENT.md text.
        metadata: Current metadata state.
        node_stack: Stack of open (in-progress) nodes.
        completed_nodes: Nodes that have been completed.
        toc_reference: Optional TOC reference items.
        rollback_used: Whether rollback has been used in the current chunk.
        operation_log: Log of operations for rollback/debugging.
        total_rollback_count: Total rollbacks across all chunks.
        chunk_start: Absolute offset of current chunk start.
        chunk_end: Absolute offset of current chunk end.
        tag_map: Tag map for the current chunk.
        neighbor_chars_prev: Chars before the current chunk.
        neighbor_chars_next: Chars after the current chunk.
    """

    book_id: str
    content: str
    metadata: ExtractedMetadata
    node_stack: list[_OpenNode]
    completed_nodes: list[BookNode]
    toc_reference: list[TocReferenceItem]
    rollback_used: bool
    operation_log: list[str]
    total_rollback_count: int
    chunk_start: int
    chunk_end: int
    tag_map: TagMap | None
    neighbor_chars_prev: str
    neighbor_chars_next: str

    def reset_for_chunk(self) -> None:
        """Reset per-chunk state (rollback flag, tag map, neighbors)."""
        self.rollback_used = False
        self.tag_map = None
        self.neighbor_chars_prev = ""
        self.neighbor_chars_next = ""

    @property
    def tree_state_text(self) -> str:
        """A human-readable summary of the current node tree state."""
        lines: list[str] = []
        if self.node_stack:
            lines.append("Open nodes (stack, top = innermost):")
            for n in self.node_stack:
                indent = "  " * n.level
                lines.append(f"{indent}- [{n.level}] {n.title or '(untitled)'}")
        else:
            lines.append("No open nodes.")
        lines.append(f"Completed nodes: {len(self.completed_nodes)}")
        return "\n".join(lines)

    @property
    def toc_reference_text(self) -> str:
        """A human-readable summary of the TOC reference, if any."""
        if not self.toc_reference:
            return "No TOC reference available."
        lines = ["TOC reference (use this to guide heading levels):"]
        for item in self.toc_reference:
            indent = "  " * item.level
            lines.append(f"{indent}- [{item.level}] {item.title}")
        return "\n".join(lines)


class CreateMetadataTool(  # type: ignore[call-arg]
    BaseTool,
    name="create_metadata",
    description="Create book metadata for the first time. Call this when you first identify the book's title, author, ISBN, publisher, or language.",
):
    """Tool: create_metadata (spec §9.1)."""

    def __init__(self, state: BuildState, logger: Logger) -> None:
        self._state = state
        self._logger = logger

    async def __call__(
        self,
        title: Annotated[str, Property(description="Book title, empty string if unknown")],
        author: Annotated[str, Property(description="Author name(s), empty string if unknown")],
        isbn: Annotated[str, Property(description="ISBN, empty string if unknown")],
        publisher: Annotated[str, Property(description="Publisher name, empty string if unknown")],
        language: Annotated[str, Property(description="Language code or name, empty string if unknown")],
        extras: Annotated[
            dict[str, t.Any] | None, Property(description="Additional metadata fields", default=None)
        ] = None,
    ) -> str:
        self._logger.info("tool: create_metadata", title=title, author=author)
        self._state.metadata = ExtractedMetadata(
            title=title,
            author=author,
            isbn=isbn,
            publisher=publisher,
            language=language,
            extras=extras or {},
            stop_reason="",
        )
        self._state.operation_log.append("create_metadata")
        return f"Metadata created: title={title!r}, author={author!r}, isbn={isbn!r}, publisher={publisher!r}, language={language!r}"


class UpdateMetadataTool(  # type: ignore[call-arg]
    BaseTool,
    name="update_metadata",
    description="Update or supplement book metadata. Use this to correct or add fields after initial creation. Only non-empty values will overwrite existing ones.",
):
    """Tool: update_metadata (spec §9.2)."""

    def __init__(self, state: BuildState, logger: Logger) -> None:
        self._state = state
        self._logger = logger

    async def __call__(
        self,
        title: Annotated[str, Property(description="Updated title, or empty string to keep existing", default="")] = "",
        author: Annotated[
            str, Property(description="Updated author, or empty string to keep existing", default="")
        ] = "",
        isbn: Annotated[str, Property(description="Updated ISBN, or empty string to keep existing", default="")] = "",
        publisher: Annotated[
            str, Property(description="Updated publisher, or empty string to keep existing", default="")
        ] = "",
        language: Annotated[
            str, Property(description="Updated language, or empty string to keep existing", default="")
        ] = "",
        extras: Annotated[
            dict[str, t.Any] | None, Property(description="Additional metadata to merge", default=None)
        ] = None,
    ) -> str:
        old = self._state.metadata
        self._logger.info("tool: update_metadata", title=title, old_title=old.title)

        new_extras = dict(old.extras)
        if extras:
            new_extras.update(extras)

        self._state.metadata = ExtractedMetadata(
            title=title or old.title,
            author=author or old.author,
            isbn=isbn or old.isbn,
            publisher=publisher or old.publisher,
            language=language or old.language,
            extras=new_extras,
            stop_reason=old.stop_reason,
        )
        self._state.operation_log.append("update_metadata")
        return f"Metadata updated. Current: title={self._state.metadata.title!r}, author={self._state.metadata.author!r}, isbn={self._state.metadata.isbn!r}"


class ReadNeighborCharsTool(  # type: ignore[call-arg]
    BaseTool,
    name="read_neighbor_chars",
    description="Read characters from the previous or next chunk boundary. Use this when a title, ISBN, or sentence is split across chunk boundaries.",
):
    """Tool: read_neighbor_chars (spec §9.3)."""

    def __init__(self, state: BuildState, logger: Logger) -> None:
        self._state = state
        self._logger = logger

    async def __call__(
        self,
        direction: Annotated[
            t.Literal["previous", "next"],
            Property(description="'previous' for chars before this chunk, 'next' for chars after"),
        ],
        count: Annotated[
            int, Property(description="Number of characters to read (max 500)", ge=1, le=MAX_NEIGHBOR_CHARS)
        ],
    ) -> str:
        self._logger.debug("tool: read_neighbor_chars", direction=direction, count=count)
        if direction == "previous":
            text = self._state.neighbor_chars_prev[-count:] if self._state.neighbor_chars_prev else ""
        else:
            text = self._state.neighbor_chars_next[:count] if self._state.neighbor_chars_next else ""
        return f"[Neighbor chars ({direction}, {count} requested, {len(text)} returned)]:\n{text}"


class RollbackLastStepTool(  # type: ignore[call-arg]
    BaseTool,
    name="rollback_last_step",
    description="Undo the last tool operation in this chunk. Each chunk allows at most ONE rollback. Use sparingly when you made a mistake.",
):
    """Tool: rollback_last_step (spec §9.4)."""

    def __init__(self, state: BuildState, logger: Logger) -> None:
        self._state = state
        self._logger = logger

    async def __call__(self) -> str:
        if self._state.rollback_used:
            self._logger.warning("tool: rollback rejected — already used for this chunk")
            return "Rollback rejected: you have already used rollback for this chunk. Each chunk allows at most one rollback."

        if not self._state.operation_log:
            return "Rollback rejected: no operations to undo."

        last_op = self._state.operation_log.pop()
        self._state.rollback_used = True
        self._state.total_rollback_count += 1
        self._logger.info("tool: rollback_last_step", undone_op=last_op)

        # Undo node operations.
        if last_op.startswith("open_node:"):
            node_id = last_op.split(":", 1)[1]
            self._state.node_stack = [n for n in self._state.node_stack if n.node_id != node_id]
        elif last_op.startswith("complete_node:"):
            # Can't easily un-complete a node; just log it.
            pass

        return f"Rolled back operation: {last_op}. You can now re-do your work for this chunk. Remember: no more rollbacks allowed for this chunk."


class CreateTocReferenceTool(  # type: ignore[call-arg]
    BaseTool,
    name="create_toc_reference",
    description="Create a table-of-contents reference from a TOC page. This does NOT create BookNodes — it records heading structure for future reference. Call this when you see a table of contents page.",
):
    """Tool: create_toc_reference (spec §9.5)."""

    def __init__(self, state: BuildState, logger: Logger) -> None:
        self._state = state
        self._logger = logger

    async def __call__(
        self,
        items: Annotated[
            list[dict[str, t.Any]],
            Property(
                description="List of TOC items, each with 'title' (str), 'level' (int, starting from 1), 'order_index' (int, increasing)"
            ),
        ],
    ) -> str:
        self._logger.info("tool: create_toc_reference", items_count=len(items))

        toc_items: list[TocReferenceItem] = []
        for item in items:
            title = str(item.get("title", "")).strip()
            level = int(item.get("level", 1))
            order = int(item.get("order_index", len(toc_items)))
            if not title:
                continue
            level = max(level, 1)
            toc_items.append(TocReferenceItem(title=title, level=level, order_index=order))

        if self._state.toc_reference:
            self._logger.warning("toc reference already exists, overwriting", old_count=len(self._state.toc_reference))

        self._state.toc_reference = toc_items
        self._state.operation_log.append("create_toc_reference")
        return f"TOC reference created with {len(toc_items)} items. These will be included in future chunk contexts to guide heading levels."


class ApplyNodeOperationTool(  # type: ignore[call-arg]
    BaseTool,
    name="apply_node_operation",
    description="Perform a node tree operation. Use <sN/> tag ranges from the chunk text to specify title/content positions. Operations: 'open_node' (create+push), 'append_content' (extend current node's body), 'complete_node' (finish+pop current node).",
):
    """Tool: apply_node_operation (spec §9.6)."""

    def __init__(self, state: BuildState, logger: Logger) -> None:
        self._state = state
        self._logger = logger

    async def __call__(
        self,
        operation: Annotated[
            t.Literal["open_node", "complete_node", "append_content"],
            Property(description="The node operation to perform"),
        ],
        title_start_tag: Annotated[
            int | None,
            Property(description="For open_node: the <sN/> tag at the title start. None if no title.", default=None),
        ] = None,
        title_end_tag: Annotated[
            int | None,
            Property(
                description="For open_node: the <sN/> tag at the title end (exclusive). None if no title.", default=None
            ),
        ] = None,
        content_start_tag: Annotated[
            int | None,
            Property(description="For open_node/append_content: the <sN/> tag at content start", default=None),
        ] = None,
        content_end_tag: Annotated[
            int | None,
            Property(
                description="For open_node/append_content: the <sN/> tag at content end (exclusive)", default=None
            ),
        ] = None,
        level: Annotated[
            int, Property(description="For open_node: the heading level (1=chapter, 2=section, etc.)", default=1)
        ] = 1,
        title: Annotated[
            str, Property(description="For open_node: the title text (fallback if tags not available)", default="")
        ] = "",
    ) -> str:
        tag_map = self._state.tag_map
        if tag_map is None:
            return "Error: no tag map available for this chunk."

        if operation == "open_node":
            return self._do_open_node(
                tag_map, level, title, title_start_tag, title_end_tag, content_start_tag, content_end_tag
            )
        if operation == "append_content":
            return self._do_append_content(tag_map, content_start_tag, content_end_tag)
        if operation == "complete_node":
            return self._do_complete_node()
        return f"Unknown operation: {operation}"  # type: ignore[unreachable]

    def _do_open_node(
        self,
        tag_map: TagMap,
        level: int,
        title: str,
        title_start_tag: int | None,
        title_end_tag: int | None,
        content_start_tag: int | None,
        content_end_tag: int | None,
    ) -> str:
        # Resolve title range.
        t_offset = 0
        t_length = 0
        if title_start_tag is not None and title_end_tag is not None:
            rng = tag_map.resolve_range(title_start_tag, title_end_tag)
            if rng is None:
                self._logger.warning("open_node: title tag range invalid", start=title_start_tag, end=title_end_tag)
                return f"Error: title tags {title_start_tag}-{title_end_tag} not found in tag map."
            t_offset, end_off = rng
            t_length = end_off - t_offset
            if not title:
                # Extract title from content.
                title = self._state.content[t_offset:end_off].strip().lstrip("#").strip()

        # Resolve content start.
        c_start = tag_map.chunk_end
        if content_start_tag is not None:
            pos = tag_map.resolve_single(content_start_tag)
            if pos is None:
                return f"Error: content start tag {content_start_tag} not found."
            c_start = pos

        c_end = c_start
        if content_end_tag is not None:
            pos = tag_map.resolve_single(content_end_tag)
            if pos is None:
                return f"Error: content end tag {content_end_tag} not found."
            c_end = pos

        # Auto-complete upper nodes if level <= stack top level (§9.7 #3).
        while self._state.node_stack and self._state.node_stack[-1].level >= level:
            self._auto_complete_node()

        # Determine parent.
        parent_id = self._state.node_stack[-1].node_id if self._state.node_stack else ""

        # Order index.
        siblings = [n for n in (self._state.completed_nodes + list(self._state.node_stack)) if n.parent_id == parent_id]
        order = len(siblings)

        node_id = gen_id(prefix="node_")
        open_node = _OpenNode(
            node_id=node_id,
            book_id=self._state.book_id,
            parent_id=parent_id,
            level=level,
            order_index=order,
            title=title,
            title_offset=t_offset,
            title_length=t_length,
            content_start=c_start,
            content_end=c_end,
        )
        self._state.node_stack.append(open_node)
        self._state.operation_log.append(f"open_node:{node_id}")
        self._logger.debug("open_node", node_id=node_id, level=level, title=title)
        return f"Node opened: id={node_id}, level={level}, title={title!r}, parent={parent_id!r}"

    def _do_append_content(
        self,
        tag_map: TagMap,
        content_start_tag: int | None,
        content_end_tag: int | None,
    ) -> str:
        if not self._state.node_stack:
            return "Error: no open node to append content to."

        if content_start_tag is None or content_end_tag is None:
            return "Error: append_content requires content_start_tag and content_end_tag."

        rng = tag_map.resolve_range(content_start_tag, content_end_tag)
        if rng is None:
            return f"Error: content tags {content_start_tag}-{content_end_tag} not found in tag map."

        c_start, c_end = rng
        node = self._state.node_stack[-1]
        if node.content_start == node.content_end:
            node.content_start = c_start
        node.content_end = c_end
        self._state.operation_log.append(f"append_content:{node.node_id}")
        self._logger.debug("append_content", node_id=node.node_id, start=c_start, end=c_end)
        return f"Content appended to node {node.node_id}: range=[{c_start},{c_end})"

    def _do_complete_node(self) -> str:
        if not self._state.node_stack:
            return "Error: no open node to complete."

        # Prevent completing the root node via tool calls.
        if len(self._state.node_stack) == 1 and self._state.node_stack[0].level == 0:
            return "The root node cannot be completed via tools. It is managed automatically. Use finish_chunk when you are done with this chunk."

        return self._auto_complete_node()

    def _auto_complete_node(self) -> str:
        """Complete the top node on the stack and move it to completed_nodes."""
        open_node = self._state.node_stack.pop()
        content_length = max(0, open_node.content_end - open_node.content_start)
        node = BookNode(
            id=open_node.node_id,
            book_id=open_node.book_id,
            parent_id=open_node.parent_id,
            level=open_node.level,
            order_index=open_node.order_index,
            title=open_node.title,
            title_offset=open_node.title_offset,
            title_length=open_node.title_length,
            content_offset=open_node.content_start,
            content_length=content_length,
        )
        self._state.completed_nodes.append(node)
        self._state.operation_log.append(f"complete_node:{node.id}")
        self._logger.debug("complete_node", node_id=node.id, title=node.title)
        return f"Node completed: id={node.id}, title={node.title!r}, content_length={content_length}"


class FinishChunkTool(  # type: ignore[call-arg]
    BaseTool,
    name="finish_chunk",
    description="Signal that you are done processing this chunk. Call this when you have finished all metadata and node operations for the current chunk and are ready to move to the next one.",
):
    """Tool: finish_chunk — signals the builder to advance to the next chunk."""

    def __init__(self, state: BuildState, logger: Logger) -> None:
        self._state = state
        self._logger = logger

    async def __call__(
        self,
        summary: Annotated[str, Property(description="Brief summary of what you did in this chunk", default="")] = "",
    ) -> str:
        self._logger.info("tool: finish_chunk", summary=summary)
        return f"DONE: Chunk processing complete. {summary}"


_SKILL_DOC = """\
# BookScout LLM Tool-Driven Builder — Tool Usage Manual

You are building a BookScout ontology (BookNode tree) from a book's content.
You receive content chunks with `<sN/>` tags that mark character positions.
Use the tools below to express your understanding of the document structure.

## Context You Always Have

1. **Current node tree state**: Shows open nodes on the stack and completed node count.
2. **TOC reference** (if available): Heading structure from a detected table of contents.
3. **Current chunk text**: Tagged with `<sN/>` markers at sentence/punctuation boundaries.
4. **Neighbor chars**: A few characters from the previous and next chunk boundaries.

## How to Use Tags

Tags like `<s0/>`, `<s1/>`, `<s3/>` mark positions in the text. When calling
`apply_node_operation`, use tag numbers to specify where titles and content are:
- `title_start_tag=5, title_end_tag=7` means the title spans from tag 5 to tag 7.
- `content_start_tag=7, content_end_tag=12` means the body text is between tags 7 and 12.

## Tools

### create_metadata
Call this the FIRST time you identify book metadata (title, author, ISBN, publisher, language).
Pass empty strings for unknown fields.

### update_metadata
Call this to correct or supplement metadata after initial creation.
Only non-empty values will overwrite existing ones.

### read_neighbor_chars
Call this when a title, ISBN, or sentence is split across chunk boundaries.
- direction="previous": read chars from before this chunk.
- direction="next": read chars from after this chunk.

### rollback_last_step
Undo your last tool operation. **Each chunk allows at most ONE rollback.**
Use sparingly. After rollback, redo your work correctly.

### create_toc_reference
When you see a TABLE OF CONTENTS page, call this with the heading items.
This does NOT create BookNodes — it records the structure for future reference.
TOC pages should NOT become content nodes.

### apply_node_operation
Perform node tree operations:
- **open_node**: Create a new node and push it onto the stack. Specify the
  title and content positions using tags, and the heading level (1=chapter,
  2=section, 3=subsection, etc.).
- **append_content**: Extend the current (top-of-stack) node's body text
  with a tag range.
- **complete_node**: Finish the current node and pop it from the stack.

When you open a node with a level ≤ the current stack top's level, upper
nodes will be auto-completed first.

### finish_chunk
Call this when you are done with the current chunk. Provide a brief summary.

## Rules

1. Cover/copyright/title page info → use `create_metadata` or `update_metadata`,
   NOT `apply_node_operation`.
2. Table of contents pages → use `create_toc_reference`, NOT `apply_node_operation`.
3. Only create content nodes for actual book content (chapters, sections, etc.).
4. If content starts without a clear heading, create a node with a reasonable title.
5. Always use `<sN/>` tag numbers for positions — never raw character offsets.
6. If boundary info is insufficient, use `read_neighbor_chars`.
7. If you made a mistake, use `rollback_last_step` (max once per chunk).
8. When done with the chunk, call `finish_chunk`.
9. Never invent database IDs — the system assigns them.
10. Never output SQL or raw database objects.
"""


class LlmToolBuilder(Builder):
    """LLM tool-driven ontology builder.

    Splits CONTENT.md into chunks, tagifies each chunk, sends it to the LLM
    with tools and a SKILL document, and collects the resulting metadata and
    BookNode tree.

    Args:
        logger: Logger instance.
        model: A started :class:`bookscout.llm.ChatModel` instance.
        chunk_chars: Max characters per chunk (default 3000).
        neighbor_chars: Chars from prev/next chunk to include (default 50).
        max_tool_iterations: Max tool call rounds per chunk (default 20).
    """

    def __init__(
        self,
        logger: Logger,
        model: ChatModel,
        chunk_chars: int = DEFAULT_CHUNK_CHARS,
        neighbor_chars: int = DEFAULT_NEIGHBOR_CHARS,
        max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
    ) -> None:
        super().__init__(logger=logger)
        self._model = model
        self._chunk_chars = chunk_chars
        self._neighbor_chars = neighbor_chars
        self._max_tool_iterations = max_tool_iterations

    async def build(
        self,
        book_id: str,
        content: str,
        book_title: str = "",
    ) -> BuildResult:
        """Build a BookNode tree using LLM tool calls.

        Args:
            book_id: The book id.
            content: Full CONTENT.md text.
            book_title: Initial book title hint.

        Returns:
            A :class:`BuildResult` with the built tree, metadata, and rollback count.
        """
        nodes, metadata, _toc_ref, rollback_count = await self.build_nodes_async(
            book_id,
            content,
            book_title,
        )
        return BuildResult(
            nodes=nodes,
            metadata={
                "title": metadata.title,
                "author": metadata.author,
                "isbn": metadata.isbn,
                "publisher": metadata.publisher,
                "language": metadata.language,
                "extras": metadata.extras,
            },
            rollback_count=rollback_count,
        )

    async def build_nodes_async(
        self,
        book_id: str,
        content: str,
        book_title: str = "",
    ) -> tuple[list[BookNode], ExtractedMetadata, list[TocReferenceItem], int]:
        """Build a BookNode tree using LLM tool calls (async).

        Args:
            book_id: The book id.
            content: Full CONTENT.md text.
            book_title: Initial book title hint.

        Returns:
            Tuple of (nodes, metadata, toc_reference, rollback_count).
        """
        from bookscout.llm.types import CompletionOptions
        from bookscout.llm.types import SystemMessage
        from bookscout.llm.types import UserMessage

        # Initialize build state with a pre-created root node on the stack.
        root_id = gen_id(prefix="node_")
        root_open = _OpenNode(
            node_id=root_id,
            book_id=book_id,
            parent_id="",
            level=0,
            order_index=0,
            title=book_title,
            title_offset=0,
            title_length=0,
            content_start=0,
            content_end=0,
        )
        state = BuildState(
            book_id=book_id,
            content=content,
            metadata=ExtractedMetadata(
                title=book_title,
                author="",
                isbn="",
                publisher="",
                language="",
                extras={},
                stop_reason="",
            ),
            node_stack=[root_open],
            completed_nodes=[],
            toc_reference=[],
            rollback_used=False,
            operation_log=[],
            total_rollback_count=0,
            chunk_start=0,
            chunk_end=0,
            tag_map=None,
            neighbor_chars_prev="",
            neighbor_chars_next="",
        )

        # Split content into chunks.
        chunks = self._split_chunks(content)
        self.logger.info("llm tool builder starting", chunks=len(chunks), content_chars=len(content))

        # Create tools with shared state.
        tools = [
            CreateMetadataTool(state=state, logger=self.logger),
            UpdateMetadataTool(state=state, logger=self.logger),
            ReadNeighborCharsTool(state=state, logger=self.logger),
            RollbackLastStepTool(state=state, logger=self.logger),
            CreateTocReferenceTool(state=state, logger=self.logger),
            ApplyNodeOperationTool(state=state, logger=self.logger),
            FinishChunkTool(state=state, logger=self.logger),
        ]

        # Process each chunk.
        for chunk_idx, (chunk_start, chunk_text) in enumerate(chunks):
            chunk_end = chunk_start + len(chunk_text)
            state.reset_for_chunk()
            state.chunk_start = chunk_start
            state.chunk_end = chunk_end

            # Tagify chunk.
            tag_map = tagify_chunk(chunk_text, chunk_start)
            state.tag_map = tag_map

            # Prepare neighbor chars.
            if chunk_start > 0:
                state.neighbor_chars_prev = content[max(0, chunk_start - self._neighbor_chars) : chunk_start]
            if chunk_end < len(content):
                state.neighbor_chars_next = content[chunk_end : chunk_end + self._neighbor_chars]

            self.logger.info(
                "processing chunk",
                chunk_idx=chunk_idx,
                start=chunk_start,
                end=chunk_end,
                chars=len(chunk_text),
                tags=len(tag_map.tags),
            )

            # Build context messages.
            system_msg = SystemMessage(content=self._build_system_message(state))
            user_msg = UserMessage(content=self._build_user_message(state, tag_map, chunk_idx, len(chunks)))

            # Call LLM with tools.
            try:
                response = await self._model.chat_completion(
                    [system_msg, user_msg],
                    tools=tools,
                    options=CompletionOptions(max_tokens=4096, temperature=0.0),
                )
                self.logger.info(
                    "chunk processed",
                    chunk_idx=chunk_idx,
                    finish_reason=response.get("finish_reason", ""),
                    response_preview=str(response["message"].content)[:200],
                )
            except Exception as e:  # pylint: disable=broad-exception-caught
                self.logger.error("chunk processing failed", chunk_idx=chunk_idx, error=str(e))
                # Continue to next chunk rather than failing entirely.
                continue

        # Complete any remaining open non-root nodes (§9.7 #5).
        # The root (level 0) is handled separately below.
        while len(state.node_stack) > 1:
            open_node = state.node_stack.pop()
            content_length = max(0, open_node.content_end - open_node.content_start)
            node = BookNode(
                id=open_node.node_id,
                book_id=book_id,
                parent_id=open_node.parent_id,
                level=open_node.level,
                order_index=open_node.order_index,
                title=open_node.title,
                title_offset=open_node.title_offset,
                title_length=open_node.title_length,
                content_offset=open_node.content_start,
                content_length=content_length,
            )
            state.completed_nodes.append(node)
            self.logger.info("auto-completed remaining node", node_id=node.id, title=node.title)

        # Complete the root node (it should still be on the stack).
        if state.node_stack:
            root_open = state.node_stack.pop()
            root_content_length = max(0, root_open.content_end - root_open.content_start)
            root = BookNode(
                id=root_open.node_id,
                book_id=book_id,
                parent_id="",
                level=0,
                order_index=0,
                title=state.metadata.title or book_title,
                title_offset=0,
                title_length=0,
                content_offset=0,
                content_length=root_content_length,
            )
        else:
            # Root was somehow already popped; create a new one.
            root = BookNode(
                id=gen_id(prefix="node_"),
                book_id=book_id,
                parent_id="",
                level=0,
                order_index=0,
                title=state.metadata.title or book_title,
                title_offset=0,
                title_length=0,
                content_offset=0,
                content_length=0,
            )

        all_nodes = [root, *state.completed_nodes]
        self.logger.info(
            "llm tool builder finished",
            nodes=len(all_nodes),
            rollbacks=state.total_rollback_count,
            title=state.metadata.title,
        )
        return all_nodes, state.metadata, state.toc_reference, state.total_rollback_count

    def _split_chunks(self, content: str) -> list[tuple[int, str]]:
        """Split content into chunks of at most ``chunk_chars`` characters.

        Tries to split on paragraph boundaries (double newlines) when possible.

        Args:
            content: Full content text.

        Returns:
            List of (absolute_offset, chunk_text) tuples.
        """
        if not content:
            return []

        chunks: list[tuple[int, str]] = []
        pos = 0
        while pos < len(content):
            end = min(pos + self._chunk_chars, len(content))
            if end < len(content):
                # Try to find a paragraph break near the end.
                search_start = max(pos, end - 200)
                break_pos = content.rfind("\n\n", search_start, end)
                if break_pos > pos:
                    end = break_pos + 2  # Include the double newline.
                else:
                    # Try single newline.
                    break_pos = content.rfind("\n", search_start, end)
                    if break_pos > pos:
                        end = break_pos + 1
            chunks.append((pos, content[pos:end]))
            pos = end
        return chunks

    def _build_system_message(self, state: BuildState) -> str:
        """Build the system message with SKILL doc + persistent context.

        Args:
            state: Current build state.

        Returns:
            The system message text.
        """
        parts: list[str] = [
            _SKILL_DOC,
            "",
            "## Current Node Tree State",
            "```",
            state.tree_state_text,
            "```",
            "",
            "## TOC Reference",
            "```",
            state.toc_reference_text,
            "```",
        ]
        return "\n".join(parts)

    def _build_user_message(
        self,
        state: BuildState,
        tag_map: TagMap,
        chunk_idx: int,
        total_chunks: int,
    ) -> str:
        """Build the user message with the tagged chunk and neighbor context.

        Args:
            state: Current build state.
            tag_map: Tag map for this chunk.
            chunk_idx: Current chunk index.
            total_chunks: Total number of chunks.

        Returns:
            The user message text.
        """
        parts: list[str] = [
            f"## Chunk {chunk_idx + 1} of {total_chunks}",
            f"Character range: [{state.chunk_start}, {state.chunk_end})",
            "",
        ]

        if state.neighbor_chars_prev:
            parts.append(f"### Previous chunk tail (last {len(state.neighbor_chars_prev)} chars):")
            parts.append(f"```{state.neighbor_chars_prev}```")
            parts.append("")

        parts.append("### Current chunk (tagged):")
        parts.append(f"```{tag_map.tagged_text}```")
        parts.append("")

        if state.neighbor_chars_next:
            parts.append(f"### Next chunk head (first {len(state.neighbor_chars_next)} chars):")
            parts.append(f"```{state.neighbor_chars_next}```")
            parts.append("")

        parts.append("Analyze this chunk and use the tools to:")
        parts.append("1. Extract or update metadata if you see book title/author/ISBN/publisher/language.")
        parts.append("2. If this is a TOC page, call create_toc_reference with the headings.")
        parts.append("3. If this is content, use apply_node_operation to open/append/complete nodes.")
        parts.append("4. When done, call finish_chunk.")
        parts.append("")

        return "\n".join(parts)
